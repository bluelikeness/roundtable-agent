"""봇 상태 감시·관리 프록시(app/bot_monitor.py, app/bots_api.py)와 /restart.

어댑터는 httpx.MockTransport 로 흉내 낸다. 실제 어댑터와의 계약은 adapters/tests/contract 가 본다.
"""

import json
import uuid

import httpx
import pytest
from httpx_ws import aconnect_ws
from sqlalchemy import select

from app.bot_monitor import BotMonitor
from app.bots import available_endpoints, load_bots
from app.config import get_settings
from app.db import get_sessionmaker
from app.main import app
from app.models import Bot, ChatSession, Run, RunStatus
from tests.test_ws import _login, _recv_until, _session, fake_runner, wsclient  # noqa: F401 (픽스처)

TOKEN = "adapter-token-for-tests"
PNG = b"\x89PNG\r\n\x1a\nfake"


class FakeAdapter:
    """관리 HTTP 대역. 받은 요청을 기록한다."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.health = {"available": True, "reason": None, "last_ok_at": "2026-11-09T01:00:00Z",
                       "queue_depth": 1, "avg_latency_ms": 5200, "version": "t", "uptime_s": 3}
        self.down = False
        self.login_flow = "code"

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.down:
            raise httpx.ConnectError("down", request=request)
        if request.headers.get("x-hub-token") != TOKEN:
            return httpx.Response(401)
        path = request.url.path
        match (request.method, path):
            case ("GET", "/health") | ("POST", "/probe"):
                return httpx.Response(200, json=self.health)
            case ("GET", "/images/abc123"):
                return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})
            case ("GET", "/images/svg1"):
                return httpx.Response(200, content=b"<svg/>", headers={"content-type": "image/svg+xml"})
            case ("POST", "/login/start"):
                if self.login_flow == "vnc":
                    return httpx.Response(200, json={"flow": "vnc", "hint": "noVNC", "vnc_url": "http://mock-a:6080/"})
                return httpx.Response(200, json={"flow": "code", "hint": "코드를 넣으세요"})
            case ("POST", "/login/submit"):
                return httpx.Response(200, json={"ok": True})
            case ("POST", "/login/complete") | ("POST", "/reset"):
                return httpx.Response(200, json={"ok": True})
            case ("POST", "/disable"):
                self.health = {**self.health, "available": False, "reason": "disabled"}
                return httpx.Response(200, json={"enabled": False})
            case ("POST", "/enable"):
                self.health = {**self.health, "available": True, "reason": None}
                return httpx.Response(200, json={"enabled": True})
            case ("GET", "/logs"):
                return httpx.Response(200, json=[{"ok": True, "latency_ms": 10}])
        return httpx.Response(404, json={"detail": "not found"})


@pytest.fixture
def settings(tmp_path):
    f = tmp_path / "bots.yaml"
    f.write_text(
        "bots:\n"
        "  - {name: mock-a, mcp_url: 'http://mock-a:9000/mcp', admin_url: 'http://mock-a:9100'}\n"
        "  - {name: mock-b, mcp_url: 'http://mock-b:9000/mcp'}\n",
        encoding="utf-8",
    )
    s = get_settings().model_copy(update={"bots_file": str(f), "adapter_token": TOKEN, "health_interval_s": 0})
    app.dependency_overrides[get_settings] = lambda: s
    yield s
    app.dependency_overrides.pop(get_settings, None)


@pytest.fixture
async def adapter(settings):
    fake = FakeAdapter()
    sent: list = []
    monitor = BotMonitor(get_sessionmaker(), settings, sent.append, transport=httpx.MockTransport(fake.handler))
    fake.sent = sent
    fake.monitor = monitor
    app.state.bot_monitor = monitor
    yield fake
    await monitor.aclose()
    del app.state.bot_monitor


async def test_requires_login(client, adapter):
    assert (await client.get("/api/bots/mock-a/images/abc123")).status_code == 401
    assert (await client.post("/api/bots/mock-a/login/start")).status_code == 401
    assert adapter.requests == []


async def test_image_proxy(authed, adapter):
    r = await authed.get("/api/bots/mock-a/images/abc123")
    assert r.status_code == 200 and r.content == PNG
    assert r.headers["content-type"] == "image/png"
    assert "private" in r.headers["cache-control"] and r.headers["x-content-type-options"] == "nosniff"
    # 어댑터에는 허브 토큰으로 간다(브라우저는 토큰을 모른다)
    assert adapter.requests[-1].headers["x-hub-token"] == TOKEN
    assert adapter.requests[-1].url == "http://mock-a:9100/images/abc123"


async def test_image_proxy_rejects(authed, adapter):
    assert (await authed.get("/api/bots/mock-a/images/nope")).status_code == 404
    assert (await authed.get("/api/bots/mock-a/images/svg1")).status_code == 502  # SVG 는 내보내지 않는다
    n = len(adapter.requests)
    assert (await authed.get("/api/bots/mock-a/images/..%2Fsecret")).status_code == 404
    assert len(adapter.requests) == n  # 형식이 틀린 id 는 어댑터까지 가지 않는다
    assert (await authed.get("/api/bots/zzz/images/abc123")).json()["detail"] == "bot_not_found"
    assert (await authed.get("/api/bots/mock-b/images/abc123")).json()["detail"] == "bot_has_no_admin"
    adapter.down = True
    r = await authed.get("/api/bots/mock-a/images/abc123")
    assert r.status_code == 502 and r.json()["detail"] == "adapter_unreachable"


async def test_login_code_flow(authed, adapter):
    r = await authed.post("/api/bots/mock-a/login/start")
    assert r.json() == {"flow": "code", "hint": "코드를 넣으세요", "vnc_url": None}
    assert (await authed.post("/api/bots/mock-a/login/submit", json={})).status_code == 422
    r = await authed.post("/api/bots/mock-a/login/submit", json={"code": "12345"})
    assert r.json() == {"ok": True}
    submit = next(q for q in adapter.requests if q.url.path == "/login/submit")
    assert json.loads(submit.content) == {"code": "12345"}
    # 성공하면 상태를 다시 읽어 패널에 보낸다
    assert adapter.requests[-1].url.path == "/health"
    assert adapter.sent and adapter.sent[-1].payload.available is True


async def test_login_vnc_hides_internal_url(authed, adapter):
    adapter.login_flow = "vnc"
    r = await authed.post("/api/bots/mock-a/login/start")
    assert r.json() == {"flow": "vnc", "hint": "noVNC", "vnc_url": None}
    assert (await authed.post("/api/bots/mock-a/login/complete")).json() == {"ok": True}


async def test_reset_forwards_thread(authed, adapter):
    sid = str(uuid.uuid4())
    assert (await authed.post("/api/bots/mock-a/reset", json={"session_id": sid})).json() == {"ok": True}
    assert json.loads(adapter.requests[-1].content) == {"thread_id": sid}
    await authed.post("/api/bots/mock-a/reset", json={})
    assert json.loads(adapter.requests[-1].content) == {}


async def test_logs_proxy(authed, adapter):
    assert (await authed.get("/api/bots/mock-a/logs")).json() == [{"ok": True, "latency_ms": 10}]


async def test_poll_updates_db_and_broadcasts_changes(adapter):
    m = adapter.monitor
    await m.poll_once()
    assert len(adapter.sent) == 1
    p = adapter.sent[0].payload
    assert adapter.sent[0].type == "bot.status" and adapter.sent[0].session_id is None and adapter.sent[0].seq is None
    assert (p.bot, p.available, p.queue_depth, p.avg_latency_ms, p.enabled) == ("mock-a", True, 1, 5200, True)
    # 같은 값이면 다시 보내지 않는다
    await m.poll_once()
    assert len(adapter.sent) == 1
    # 어댑터가 응답하지 않으면 backend_down
    adapter.down = True
    await m.poll_once()
    assert adapter.sent[-1].payload.reason == "backend_down" and not adapter.sent[-1].payload.available
    async with get_sessionmaker()() as db:
        row = await db.get(Bot, "mock-a")
        assert row.available is False and row.reason == "backend_down" and row.last_ok_at is not None
    # 연결 시 보낼 스냅샷
    assert [e.payload.bot for e in m.snapshot()] == ["mock-a"]


async def test_disable_is_hub_truth(authed, adapter, settings):
    r = await authed.post("/api/bots/mock-a/disable")
    assert r.json()["enabled"] is False and r.json()["reason"] == "disabled"
    async with get_sessionmaker()() as db:
        eps = await available_endpoints(db, load_bots(settings.bots_file))
    assert [e.name for e in eps] == ["mock-b"]
    # 어댑터가 재시작해 enabled 로 돌아와도 허브에서 끈 봇은 disabled
    adapter.health = {**adapter.health, "available": True, "reason": None}
    p = await adapter.monitor.refresh("mock-a")
    assert p.reason == "disabled" and p.enabled is False
    r = await authed.post("/api/bots/mock-a/enable")
    assert r.json()["enabled"] is True and r.json()["available"] is True


async def test_probe(authed, adapter):
    r = await authed.post("/api/bots/mock-a/probe")
    assert r.status_code == 200 and r.json()["available"] is True
    assert adapter.requests[-1].url.path == "/probe"


async def test_webhook(client, adapter):
    body = {"bot": "mock-a", "available": False, "reason": "login_required", "at": "2026-11-09T00:00:00Z"}
    assert (await client.post("/internal/bot-status", json=body)).status_code == 401
    assert (await client.post("/internal/bot-status", json=body, headers={"X-Hub-Token": "x"})).status_code == 401
    r = await client.post("/internal/bot-status", json={**body, "bot": "zzz"}, headers={"X-Hub-Token": TOKEN})
    assert r.status_code == 404
    r = await client.post("/internal/bot-status", json=body, headers={"X-Hub-Token": TOKEN})
    assert r.status_code == 204
    assert adapter.sent[-1].payload.reason == "login_required"


async def test_ws_sends_bot_status_snapshot_and_updates(adapter, operator):
    from tests.test_ws import FakeRunner
    from app.runs import RunManager
    from httpx_ws.transport import ASGIWebSocketTransport

    manager = RunManager(get_sessionmaker(), FakeRunner(), get_settings())
    app.state.run_manager = manager
    adapter.monitor._broadcast = manager.broadcast_global
    await adapter.monitor.poll_once()
    try:
        async with httpx.AsyncClient(transport=ASGIWebSocketTransport(app), base_url="http://hub.test") as wsc:
            await _login(wsc, operator)
            async with aconnect_ws("http://hub.test/ws", wsc) as ws:
                first = await _recv_until(ws, "bot.status")
                assert first[0]["payload"]["bot"] == "mock-a" and first[0]["session_id"] is None
                # 세션을 구독하지 않은 연결도 상태 변화를 받는다
                await adapter.monitor.apply("mock-a", available=False, reason="login_required")
                (ev,) = await _recv_until(ws, "bot.status")
                assert ev["payload"]["reason"] == "login_required"
    finally:
        await manager.shutdown()
        del app.state.run_manager


async def test_restart_forces_new_claude_session(wsclient, operator, fake_runner):
    async with wsclient() as wsc:
        await _login(wsc, operator)
        sid = await _session(wsc)
        # 아직 Claude 세션이 없으면 표시만 하고 아무것도 하지 않는다
        r = await wsc.post(f"/api/sessions/{sid}/restart")
        assert r.json() == {"ok": True, "had_session": False}
        async with aconnect_ws("http://hub.test/ws", wsc) as ws:
            await ws.send_json({"type": "send", "session_id": sid, "payload": {"text": "a"}})
            await _recv_until(ws, "run.finished")
            r = await wsc.post(f"/api/sessions/{sid}/restart")
            assert r.json() == {"ok": True, "had_session": True}
            await ws.send_json({"type": "send", "session_id": sid, "payload": {"text": "b"}})
            await _recv_until(ws, "run.finished")
            await ws.send_json({"type": "send", "session_id": sid, "payload": {"text": "c"}})
            await _recv_until(ws, "run.finished")
        assert [s.force_new for s in fake_runner.sessions] == [False, True, False]
        assert (await wsc.post(f"/api/sessions/{uuid.uuid4()}/restart")).status_code == 404


async def test_claude_status(authed, db):
    s = ChatSession(title="x")
    db.add(s)
    await db.flush()
    db.add(Run(session_id=s.id, status=RunStatus.done, usage={"input_tokens": 10, "output_tokens": 5}))
    db.add(Run(session_id=s.id, status=RunStatus.done, usage=None))
    await db.commit()
    r = await authed.get("/api/claude/status")
    # 테스트에는 러너가 없다
    assert r.json() == {"runner_ok": False, "runs_today": 2, "tokens_today": 15}


async def test_start_loads_last_status_from_db(settings, db):
    db.add(Bot(name="mock-a", enabled=False, available=False, reason="disabled"))
    db.add(Bot(name="gone", enabled=True, available=True))
    await db.commit()
    m = BotMonitor(get_sessionmaker(), settings, lambda ev: None, transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    await m.start()
    try:
        assert list(m.statuses) == ["mock-a"] and m.statuses["mock-a"].enabled is False
    finally:
        await m.aclose()
    rows = (await db.execute(select(Bot.name))).scalars().all()
    assert set(rows) == {"mock-a", "gone"}
