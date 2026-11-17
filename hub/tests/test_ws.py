"""WebSocket /ws 와 RunManager: 가짜 러너로 send → 이벤트 중계·DB 기록·큐잉·중단·재전송을 확인한다."""

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import httpx
import pytest
from httpx_ws import WebSocketDisconnect, aconnect_ws
from httpx_ws.transport import ASGIWebSocketTransport
from sqlalchemy import select

from app.bots import load_bots
from app.config import get_settings
from app.db import get_sessionmaker
from app.events import (
    BotPending,
    BotPendingPayload,
    BotReply,
    BotReplyPayload,
    ClaudeFinal,
    ClaudeFinalPayload,
    ClaudeNote,
    ClaudeNotePayload,
    HubEvent,
    RunFinished,
    RunFinishedPayload,
    RunStarted,
    RunStartedPayload,
)
from app.main import app
from app.models import ChatSession, Message, Run, RunStatus, ToolCall
from app.runner_protocol import Runner, RunnerSession, ToolCallRecord
from app.runs import RunManager, SendError
from tests.conftest import PASSWORD, USERNAME, totp_now


def _now():
    return datetime.now(UTC)


class FakeRunner(Runner):
    """러너 대역. text 가 "block" 이면 stop() 이나 release 까지 멈춰 있는다."""

    def __init__(self):
        self.sessions: list[RunnerSession] = []
        self.release = asyncio.Event()
        self.stopped: set[uuid.UUID] = set()
        self.claude_session_id = "cs-1"

    async def stop(self, run_id):
        self.stopped.add(run_id)
        self.release.set()
        return True

    async def start(self, session: RunnerSession, text: str) -> AsyncIterator[HubEvent]:
        self.sessions.append(session)
        sid, rid = session.session_id, session.run_id

        def ev(cls, type_, payload):
            return cls(type=type_, session_id=sid, seq=None, ts=_now(), payload=payload)

        yield ev(RunStarted, "run.started", RunStartedPayload(run_id=rid, claude_session_id=self.claude_session_id))
        if text == "block":
            await self.release.wait()
            if rid in self.stopped:
                yield ev(RunFinished, "run.finished", RunFinishedPayload(run_id=rid, status="failed", error="stopped"))
                return
        yield ev(ClaudeNote, "claude.note", ClaudeNotePayload(run_id=rid, text="mock-a 에 묻습니다"))
        call = f"toolu_{uuid.uuid4().hex[:8]}"
        await self.on_tool_call(ToolCallRecord(id=call, run_id=rid, bot="mock-a", tool="ask", input={"text": text}))
        yield ev(BotPending, "bot.pending", BotPendingPayload(run_id=rid, bot="mock-a", tool_call_id=call, queue_position=0))
        await self.on_tool_call(
            ToolCallRecord(id=call, run_id=rid, bot="mock-a", tool="ask", done=True, output={"ok": True, "raw": "맑음"}, latency_ms=5)
        )
        yield ev(BotReply, "bot.reply", BotReplyPayload(
            run_id=rid, bot="mock-a", tool_call_id=call, ok=True, raw="맑음", structured={}, needs_input=False,
            error=None, latency_ms=5, message_id=uuid.uuid4(),
        ))
        yield ev(ClaudeFinal, "claude.final", ClaudeFinalPayload(
            run_id=rid, text="내일은 맑습니다", message_id=uuid.uuid4(), usage={"input_tokens": 3, "total_cost_usd": 0.01},
        ))
        yield ev(RunFinished, "run.finished", RunFinishedPayload(run_id=rid, status="done"))


@pytest.fixture
async def fake_runner(operator):
    runner = FakeRunner()
    manager = RunManager(get_sessionmaker(), runner, get_settings())
    app.state.run_manager = manager
    yield runner
    runner.release.set()
    await manager.shutdown()
    del app.state.run_manager


@pytest.fixture
def wsclient(fake_runner):
    """ASGIWebSocketTransport 는 anyio 취소 범위를 쓰므로 픽스처가 아니라 테스트 태스크 안에서 연다."""
    return lambda: httpx.AsyncClient(transport=ASGIWebSocketTransport(app), base_url="http://hub.test")


async def _login(c, operator):
    _, secret = operator
    r = await c.post("/api/auth/login", json={"username": USERNAME, "password": PASSWORD, "totp": totp_now(secret)})
    assert r.status_code == 200, r.text


async def _session(c) -> str:
    r = await c.post("/api/sessions", json={"title": "t"})
    return r.json()["id"]


async def _recv_until(ws, type_: str, limit=30) -> list[dict]:
    out = []
    for _ in range(limit):
        msg = await asyncio.wait_for(ws.receive_json(), timeout=5)
        out.append(msg)
        if msg["type"] == type_:
            return out
    raise AssertionError(f"{type_} 가 오지 않음: {[m['type'] for m in out]}")


async def test_ws_requires_login(wsclient):
    async with wsclient() as wsc:
        with pytest.raises(WebSocketDisconnect):
            async with aconnect_ws("http://hub.test/ws", wsc) as ws:
                await ws.receive_json()


async def test_ws_rejects_cross_origin(wsclient, operator):
    async with wsclient() as wsc:
        await _login(wsc, operator)
        with pytest.raises(WebSocketDisconnect):
            async with aconnect_ws("http://hub.test/ws", wsc, headers={"Origin": "http://evil.test"}) as ws:
                await ws.receive_json()


async def test_send_relays_events_and_persists(wsclient, operator, fake_runner):
    async with wsclient() as wsc:
        await _login(wsc, operator)
        sid = await _session(wsc)
        async with aconnect_ws("http://hub.test/ws", wsc) as ws:
            await ws.send_json({"type": "subscribe", "payload": {"session_id": sid, "since_seq": 0}})
            await ws.send_json({"type": "send", "session_id": sid, "payload": {"text": "내일 날씨"}})
            msgs = await _recv_until(ws, "run.finished")

        types = [m["type"] for m in msgs]
        assert types == ["message.ack", "run.started", "claude.note", "bot.pending", "bot.reply", "claude.final", "run.finished"]
        seqs = [m["seq"] for m in msgs]
        assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs) and seqs[0] == 1
        assert all(m["session_id"] == sid for m in msgs)
        assert msgs[0]["payload"]["status"] == "running"
        run_id = msgs[0]["payload"]["run_id"]

        # 러너에 넘긴 세션 사실
        rs = fake_runner.sessions[0]
        assert str(rs.session_id) == sid and str(rs.run_id) == run_id
        assert rs.claude_session_id is None and rs.tool_calls == 0
        # bots.yaml 에 등록된 봇 전부(봇을 추가해도 이 테스트를 고치지 않게 설정에서 읽는다)
        assert {b.name for b in rs.bots} == {b.name for b in load_bots(get_settings().bots_file)}

        async with get_sessionmaker()() as db:
            rows = (await db.execute(select(Message).where(Message.session_id == uuid.UUID(sid)).order_by(Message.seq))).scalars().all()
            assert [(m.role.value, m.structured) for m in rows] == [
                ("user", None), ("claude", {"kind": "note"}), ("bot", {}), ("claude", {"kind": "final"}),
            ]
            by_type = {m["type"]: m for m in msgs}
            assert rows[2].id == uuid.UUID(by_type["bot.reply"]["payload"]["message_id"])
            assert rows[3].seq == by_type["claude.final"]["seq"]
            run = await db.get(Run, uuid.UUID(run_id))
            assert run.status == RunStatus.done and run.claude_session_id == "cs-1"
            assert float(run.cost_usd) == 0.01 and run.usage["input_tokens"] == 3
            s = await db.get(ChatSession, uuid.UUID(sid))
            assert s.claude_session_id == "cs-1" and s.last_seq == seqs[-1]
            tc = (await db.execute(select(ToolCall))).scalars().one()
            assert tc.input == {"tool": "ask", "arguments": {"text": "내일 날씨"}} and tc.output["raw"] == "맑음"

        # 두 번째 메시지: resume 재료가 채워진다
        async with aconnect_ws("http://hub.test/ws", wsc) as ws:
            await ws.send_json({"type": "send", "session_id": sid, "payload": {"text": "모레는?", "model": "opus"}})
            await _recv_until(ws, "run.finished")
        rs2 = fake_runner.sessions[1]
        assert rs2.claude_session_id == "cs-1" and rs2.tool_calls == 1 and rs2.model == "opus"
        assert rs2.last_final == "내일은 맑습니다" and rs2.last_bot_raws == {"mock-a": "맑음"}
        assert rs2.last_message_at is not None


async def test_queue_cancel_and_stop(wsclient, operator, fake_runner):
    async with wsclient() as wsc:
        await _login(wsc, operator)
        sid = await _session(wsc)
        async with aconnect_ws("http://hub.test/ws", wsc) as ws:
            await ws.send_json({"type": "send", "session_id": sid, "payload": {"text": "block"}})
            first = await _recv_until(ws, "run.started")
            await ws.send_json({"type": "send", "session_id": sid, "payload": {"text": "둘째"}})
            (ack2,) = await _recv_until(ws, "message.ack")
            assert ack2["payload"]["status"] == "queued"
            await ws.send_json({"type": "cancel_queued", "payload": {"run_id": ack2["payload"]["run_id"]}})
            (cancelled,) = await _recv_until(ws, "run.finished")
            assert cancelled["payload"] == {"run_id": ack2["payload"]["run_id"], "status": "failed", "error": "cancelled"}
            run1 = first[0]["payload"]["run_id"]
            await ws.send_json({"type": "stop_run", "payload": {"run_id": run1}})
            (stopped,) = await _recv_until(ws, "run.finished")
            assert stopped["payload"]["run_id"] == run1 and stopped["payload"]["error"] == "stopped"
        assert len(fake_runner.sessions) == 1  # 취소된 run 은 러너로 가지 않았다


async def test_subscribe_replays_since_seq(wsclient, operator, fake_runner):
    async with wsclient() as wsc:
        await _login(wsc, operator)
        sid = await _session(wsc)
        async with aconnect_ws("http://hub.test/ws", wsc) as ws:
            await ws.send_json({"type": "send", "session_id": sid, "payload": {"text": "a"}})
            msgs = await _recv_until(ws, "run.finished")
        async with aconnect_ws("http://hub.test/ws", wsc) as ws:
            await ws.send_json({"type": "subscribe", "payload": {"session_id": sid, "since_seq": 3}})
            replay = await _recv_until(ws, "run.finished")
        assert [m["seq"] for m in replay] == [m["seq"] for m in msgs if m["seq"] > 3]


async def test_invalid_messages_ignored(wsclient, operator, fake_runner):
    async with wsclient() as wsc:
        await _login(wsc, operator)
        sid = await _session(wsc)
        async with aconnect_ws("http://hub.test/ws", wsc) as ws:
            await ws.send_text("not json")
            await ws.send_json({"type": "bogus", "payload": {}})
            await ws.send_json({"type": "send", "session_id": str(uuid.uuid4()), "payload": {"text": "없는 세션"}})
            await ws.send_json({"type": "send", "session_id": sid, "payload": {"text": "ok"}})
            msgs = await _recv_until(ws, "run.finished")
        assert msgs[0]["type"] == "message.ack" and msgs[0]["session_id"] == sid


async def test_recover_marks_unfinished_runs_failed(db):
    s = ChatSession(title="x")
    db.add(s)
    await db.flush()
    db.add(Run(session_id=s.id, status=RunStatus.running))
    await db.commit()
    await RunManager(get_sessionmaker(), FakeRunner(), get_settings()).recover()
    await db.rollback()
    run = (await db.execute(select(Run))).scalars().one()
    await db.refresh(run)
    assert run.status == RunStatus.failed and run.error == "hub_restarted"


async def test_session_model_used_and_trashed_session_rejected(wsclient, operator, fake_runner):
    """세션에 저장한 모델로 돈다(보낼 때 고른 모델이 있으면 그것이 먼저). 휴지통의 세션에는 보낼 수 없다."""
    async with wsclient() as wsc:
        await _login(wsc, operator)
        sid = (await wsc.post("/api/sessions", json={"title": "t", "model": "opus"})).json()["id"]
        async with aconnect_ws("http://hub.test/ws", wsc) as ws:
            await ws.send_json({"type": "subscribe", "payload": {"session_id": sid, "since_seq": 0}})
            await ws.send_json({"type": "send", "session_id": sid, "payload": {"text": "a"}})
            await _recv_until(ws, "run.finished")
            await ws.send_json({"type": "send", "session_id": sid, "payload": {"text": "b", "model": "sonnet"}})
            await _recv_until(ws, "run.finished")
        assert [s.model for s in fake_runner.sessions] == ["opus", "sonnet"]

        await wsc.patch(f"/api/sessions/{sid}", json={"trashed": True})
        with pytest.raises(SendError, match="session_trashed"):
            await app.state.run_manager.send(uuid.UUID(sid), "c", None)
