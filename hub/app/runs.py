"""run 큐·실행·이벤트 저장과 WebSocket 중계 (docs/02-architecture.md 3·6장, docs/05-ui.md 6장).

- 메시지 한 건 = run 한 건. 세션당 동시 실행 1개, 겹치면 queued 로 대기(02 4장 동시 실행)
- 모든 서버 이벤트는 DB 에 먼저 쓰고 그다음 보낸다. seq 는 session.last_seq 로 매긴다
- 러너가 내는 HubEvent 의 seq 는 비어 있고, 여기서 채운다
- 재연결 시 since_seq 이후를 세션별 메모리 버퍼에서 재전송한다(버퍼보다 오래된 것은 REST 로 다시 읽는다)

message 행 매핑
- send          → role=user
- claude.note   → role=claude, structured={"kind": "note"}
- claude.final  → role=claude, structured={"kind": "final"}, id=payload.message_id
- bot.reply     → role=bot, bot_name, raw, structured, id=payload.message_id
- session.system→ role=system
"""

import asyncio
import contextlib
import logging
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bots import available_endpoints, load_bots
from app.config import Settings
from app.events import HubEvent, MessageAck, MessageAckPayload, RunFinished, RunFinishedPayload
from app.models import ChatSession, Message, MessageRole, Run, RunStatus, ToolCall
from app.runner_protocol import Runner, RunnerSession, ToolCallRecord

log = logging.getLogger("hub.runs")

REPLAY_BUFFER = 500
CONN_QUEUE = 1000


def _now() -> datetime:
    return datetime.now(UTC)


class Connection:
    """WebSocket 하나. 느린 클라이언트가 브로드캐스트를 막지 않도록 큐로 보낸다."""

    def __init__(self) -> None:
        self.queue: asyncio.Queue[str] = asyncio.Queue(maxsize=CONN_QUEUE)
        self.session_id: uuid.UUID | None = None
        self.overflowed = False

    def push(self, data: str) -> None:
        try:
            self.queue.put_nowait(data)
        except asyncio.QueueFull:
            # 따라오지 못하는 연결은 끊고 재연결(since_seq)로 복구하게 한다
            self.overflowed = True


@dataclass
class QueuedRun:
    run_id: uuid.UUID
    model: str | None = None


@dataclass
class _SessionState:
    subs: set[Connection] = field(default_factory=set)
    buffer: deque[tuple[int, str]] = field(default_factory=lambda: deque(maxlen=REPLAY_BUFFER))
    queue: deque[QueuedRun] = field(default_factory=deque)
    worker: asyncio.Task | None = None
    current: uuid.UUID | None = None


class SendError(Exception):
    def __init__(self, code: str):
        self.code = code


class RunManager:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession], runner: Runner, settings: Settings):
        self._sm = sessionmaker
        self.runner = runner
        self.settings = settings
        runner.on_tool_call = self._record_tool_call
        self._sessions: dict[uuid.UUID, _SessionState] = {}
        # 세션 무관 이벤트(bot.status)를 받는 모든 연결
        self._conns: set[Connection] = set()
        # /restart 요청: 다음 실행을 새 Claude 세션으로(force_new). 허브가 재시작되면 사라진다
        self._force_new: set[uuid.UUID] = set()

    def _state(self, sid: uuid.UUID) -> _SessionState:
        return self._sessions.setdefault(sid, _SessionState())

    # --- 기동·종료 ---

    async def recover(self) -> None:
        """허브가 재시작되면 메모리 큐가 사라지므로 끝나지 않은 run 을 실패로 닫는다."""
        async with self._sm() as db:
            await db.execute(
                update(Run)
                .where(Run.status.in_([RunStatus.queued, RunStatus.running]))
                .values(status=RunStatus.failed, ended_at=func.now(), error="hub_restarted")
            )
            await db.commit()

    async def shutdown(self) -> None:
        tasks = [st.worker for st in self._sessions.values() if st.worker is not None]
        for t in tasks:
            t.cancel()
        for t in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t

    async def wait_idle(self) -> None:
        """테스트용: 실행 중인 worker 가 모두 끝날 때까지."""
        while tasks := [st.worker for st in self._sessions.values() if st.worker is not None]:
            await asyncio.gather(*tasks, return_exceptions=True)

    # --- 연결·구독 ---

    def connect(self, conn: Connection) -> None:
        self._conns.add(conn)

    def disconnect(self, conn: Connection) -> None:
        self._conns.discard(conn)
        self.unsubscribe(conn)

    def broadcast_global(self, ev: HubEvent) -> None:
        """세션 무관 이벤트(bot.status). seq 가 없어 재전송 버퍼에 넣지 않는다."""
        data = ev.model_dump_json()
        for conn in list(self._conns):
            conn.push(data)

    def request_restart(self, sid: uuid.UUID) -> None:
        """/restart: 다음 실행이 새 Claude 세션으로 시작하게 한다(요약 주입은 러너가 한다)."""
        self._force_new.add(sid)

    def restart_pending(self, sid: uuid.UUID) -> bool:
        return sid in self._force_new

    async def subscribe(self, conn: Connection, sid: uuid.UUID, since_seq: int) -> bool:
        async with self._sm() as db:
            if await db.get(ChatSession, sid) is None:
                return False
        self.unsubscribe(conn)
        st = self._state(sid)
        # 버퍼 스냅샷·구독 등록·재전송 사이에 await 가 없어 빠지거나 겹치는 이벤트가 없다
        for seq, data in st.buffer:
            if seq > since_seq:
                conn.push(data)
        st.subs.add(conn)
        conn.session_id = sid
        return True

    def unsubscribe(self, conn: Connection) -> None:
        if conn.session_id is not None:
            st = self._sessions.get(conn.session_id)
            if st is not None:
                st.subs.discard(conn)
        conn.session_id = None

    def _broadcast(self, sid: uuid.UUID, ev: HubEvent) -> None:
        data = ev.model_dump_json()
        st = self._state(sid)
        assert ev.seq is not None
        st.buffer.append((ev.seq, data))
        for conn in list(st.subs):
            conn.push(data)

    async def _publish(self, db: AsyncSession, ev: HubEvent, *rows: Any) -> HubEvent | None:
        """seq 를 매기고 rows 와 함께 커밋한 뒤 보낸다. 세션이 지워졌으면 None."""
        sid = ev.session_id
        seq = (
            await db.execute(
                update(ChatSession)
                .where(ChatSession.id == sid)
                .values(last_seq=ChatSession.last_seq + 1)
                .returning(ChatSession.last_seq)
            )
        ).scalar_one_or_none()
        if seq is None:
            await db.rollback()
            return None
        for row in rows:
            if isinstance(row, Message):
                row.seq = seq
            db.add(row)
        await db.commit()
        ev = ev.model_copy(update={"seq": seq})
        assert sid is not None
        self._broadcast(sid, ev)
        return ev

    # --- 클라이언트 명령 ---

    async def send(self, sid: uuid.UUID, text: str, model: str | None) -> HubEvent:
        if not text.strip():
            raise SendError("empty_text")
        st = self._state(sid)
        async with self._sm() as db:
            s = await db.get(ChatSession, sid)
            if s is None:
                raise SendError("session_not_found")
            msg = Message(id=uuid.uuid4(), session_id=sid, role=MessageRole.user, raw=text)
            run = Run(id=uuid.uuid4(), session_id=sid, user_message_id=msg.id, status=RunStatus.queued)
            s.last_message_at = _now()
            busy = st.current is not None or bool(st.queue)
            ack = MessageAck(
                type="message.ack", session_id=sid, seq=None, ts=_now(),
                payload=MessageAckPayload(message_id=msg.id, run_id=run.id, status="queued" if busy else "running"),
            )
            published = await self._publish(db, ack, msg, run)
            if published is None:
                raise SendError("session_not_found")
        st.queue.append(QueuedRun(run_id=run.id, model=model))
        if st.worker is None:
            st.worker = asyncio.create_task(self._worker(sid), name=f"runs:{sid}")
        return published

    async def cancel_queued(self, run_id: uuid.UUID) -> bool:
        for sid, st in self._sessions.items():
            for q in list(st.queue):
                if q.run_id == run_id:
                    st.queue.remove(q)
                    await self._finish_run(sid, run_id, "failed", "cancelled")
                    return True
        return False

    async def stop_run(self, run_id: uuid.UUID) -> bool:
        if any(st.current == run_id for st in self._sessions.values()):
            return await self.runner.stop(run_id)
        return False

    # --- 실행 ---

    async def _worker(self, sid: uuid.UUID) -> None:
        st = self._state(sid)
        try:
            while st.queue:
                q = st.queue.popleft()
                st.current = q.run_id
                try:
                    await self._execute(sid, q)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("run 실행 오류")
                    with contextlib.suppress(Exception):
                        await self._finish_run(sid, q.run_id, "failed", "internal")
                finally:
                    st.current = None
        finally:
            st.worker = None

    async def _execute(self, sid: uuid.UUID, q: QueuedRun) -> None:
        async with self._sm() as db:
            run = await db.get(Run, q.run_id)
            s = await db.get(ChatSession, sid)
            if run is None or s is None or run.status != RunStatus.queued:
                return
            user_msg = await db.get(Message, run.user_message_id) if run.user_message_id else None
            if user_msg is None:
                await self._finish_run(sid, q.run_id, "failed", "internal")
                return
            run.status = RunStatus.running
            run.started_at = _now()
            await db.commit()
            rs = await self._runner_session(db, s, run, user_msg, q)
            text = user_msg.raw

        finished = False
        # 중간에 빠져나와도 러너 스트림(HTTP 연결 → claude 프로세스)이 바로 닫히게 aclosing
        async with contextlib.aclosing(self.runner.start(rs, text)) as events:
            async for ev in events:
                if ev.session_id != sid:
                    continue
                if await self._persist(sid, q.run_id, ev) is None:
                    return  # 세션이 지워졌다
                if ev.type == "run.finished":
                    finished = True
                    break
        if not finished:
            await self._finish_run(sid, q.run_id, "failed", "runner_stream_ended")

    async def _runner_session(
        self, db: AsyncSession, s: ChatSession, run: Run, user_msg: Message, q: QueuedRun
    ) -> RunnerSession:
        last_at = (
            await db.execute(
                select(func.max(Message.created_at)).where(Message.session_id == s.id, Message.seq < user_msg.seq)
            )
        ).scalar_one_or_none()
        tool_calls = 0
        if s.claude_session_id:
            tool_calls = (
                await db.execute(
                    select(func.count(ToolCall.id))
                    .join(Run, Run.id == ToolCall.run_id)
                    .where(Run.session_id == s.id, Run.claude_session_id == s.claude_session_id)
                )
            ).scalar_one()
        last_final = (
            await db.execute(
                select(Message.raw)
                .where(
                    Message.session_id == s.id,
                    Message.role == MessageRole.claude,
                    Message.structured["kind"].astext == "final",
                )
                .order_by(Message.seq.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        # 봇별 마지막 원문(빈 원문 = 실패 응답은 뺀다)
        latest = (
            select(func.max(Message.seq).label("seq"))
            .where(Message.session_id == s.id, Message.role == MessageRole.bot, Message.raw != "")
            .group_by(Message.bot_name)
            .subquery()
        )
        bot_rows = (
            await db.execute(
                select(Message.bot_name, Message.raw)
                .join(latest, Message.seq == latest.c.seq)
                .where(Message.session_id == s.id)
                .order_by(Message.bot_name)
            )
        ).all()
        bots = await available_endpoints(db, load_bots(self.settings.bots_file))
        force_new = s.id in self._force_new
        self._force_new.discard(s.id)
        return RunnerSession(
            session_id=s.id,
            run_id=run.id,
            claude_session_id=s.claude_session_id,
            last_message_at=last_at,
            tool_calls=tool_calls,
            last_final=last_final,
            last_bot_raws={name: raw for name, raw in bot_rows if name},
            bots=bots,
            model=q.model or "sonnet",
            force_new=force_new,
        )

    async def _persist(self, sid: uuid.UUID, run_id: uuid.UUID, ev: HubEvent) -> HubEvent | None:
        p = ev.payload
        async with self._sm() as db:
            rows: list[Any] = []
            match ev.type:
                case "run.started":
                    run = await db.get(Run, run_id)
                    s = await db.get(ChatSession, sid)
                    if p.claude_session_id and run is not None and s is not None:
                        run.claude_session_id = p.claude_session_id
                        s.claude_session_id = p.claude_session_id
                case "claude.note":
                    rows.append(Message(session_id=sid, role=MessageRole.claude, raw=p.text, structured={"kind": "note"}))
                case "bot.reply":
                    rows.append(
                        Message(
                            id=p.message_id, session_id=sid, role=MessageRole.bot, bot_name=p.bot,
                            raw=p.raw, structured=p.structured,
                        )
                    )
                case "claude.final":
                    rows.append(
                        Message(
                            id=p.message_id, session_id=sid, role=MessageRole.claude, raw=p.text,
                            structured={"kind": "final"},
                        )
                    )
                    run = await db.get(Run, run_id)
                    if run is not None:
                        run.usage = p.usage
                        run.cost_usd = _cost(p.usage)
                case "run.finished":
                    run = await db.get(Run, run_id)
                    if run is not None:
                        run.status = RunStatus(p.status)
                        run.ended_at = _now()
                        run.error = p.error
                case "session.system":
                    rows.append(Message(session_id=sid, role=MessageRole.system, raw=p.text))
                case "bot.pending":
                    pass  # tool_call 행은 on_tool_call 기록이 만든다
                case _:
                    log.warning("러너가 보낼 수 없는 이벤트 %s 를 건너뜀", ev.type)
                    return ev
            return await self._publish(db, ev, *rows)

    async def _finish_run(self, sid: uuid.UUID, run_id: uuid.UUID, status: str, error: str | None) -> None:
        ev = RunFinished(
            type="run.finished", session_id=sid, seq=None, ts=_now(),
            payload=RunFinishedPayload(run_id=run_id, status=status, error=error),
        )
        await self._persist(sid, run_id, ev)

    async def _record_tool_call(self, rec: ToolCallRecord) -> None:
        """tool_call 행 upsert. tool 종류(ask·status)를 구분하려고 input 에 함께 담는다."""
        async with self._sm() as db:
            stmt = pg_insert(ToolCall).values(
                id=rec.id,
                run_id=rec.run_id,
                bot_name=rec.bot,
                input={"tool": rec.tool, "arguments": rec.input} if not rec.done else None,
                output=rec.output if rec.done else None,
                latency_ms=rec.latency_ms,
                error=rec.error,
            )
            if rec.done:
                stmt = stmt.on_conflict_do_update(
                    index_elements=[ToolCall.id],
                    set_={"output": stmt.excluded.output, "latency_ms": stmt.excluded.latency_ms, "error": stmt.excluded.error},
                )
            else:
                stmt = stmt.on_conflict_do_nothing(index_elements=[ToolCall.id])
            try:
                await db.execute(stmt)
                await db.commit()
            except Exception:
                # run 이 지워진 경우 등. 기록 실패가 실행을 멈추게 하지 않는다
                log.exception("tool_call 기록 실패")


def _cost(usage: dict[str, Any] | None) -> Decimal | None:
    if not usage or usage.get("total_cost_usd") is None:
        return None
    try:
        return Decimal(str(usage["total_cost_usd"]))
    except InvalidOperation:
        return None
