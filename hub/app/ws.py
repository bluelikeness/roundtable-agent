"""WebSocket /ws (docs/05-ui.md 6장). 로그인 쿠키로 인증하고 세션 구독·메시지 전송을 받는다.

봉투와 type 목록은 app/events.py 그대로다. 해석할 수 없는 클라이언트 메시지는 버린다
(프로토콜에 오류 이벤트가 없다).
"""

import asyncio
import contextlib
import logging
from urllib.parse import urlsplit

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import TypeAdapter, ValidationError

from app.auth import read_cookie
from app.config import get_settings
from app.db import get_sessionmaker
from app.events import CancelQueued, ClientMessage, Send, StopRun, Subscribe
from app.models import Operator
from app.runs import Connection, RunManager, SendError

log = logging.getLogger("hub.ws")
router = APIRouter()
_client_msg = TypeAdapter(ClientMessage)

# 정책 위반(인증 실패)
CLOSE_POLICY = 1008


async def _operator_version(websocket: WebSocket) -> tuple[int, int] | None:
    data = read_cookie(websocket, get_settings())  # type: ignore[arg-type]  # cookies 만 읽는다
    if data is None:
        return None
    async with get_sessionmaker()() as db:
        op = await db.get(Operator, data.get("op"))
        if op is None or op.auth_version != data.get("v"):
            return None
        return op.id, op.auth_version


async def _still_authorized(op: tuple[int, int]) -> bool:
    async with get_sessionmaker()() as db:
        row = await db.get(Operator, op[0])
        return row is not None and row.auth_version == op[1]


def _same_origin(websocket: WebSocket) -> bool:
    """다른 사이트가 로그인 쿠키로 소켓을 여는 것(CSWSH)을 막는다. Origin 이 없으면(비브라우저) 허용."""
    origin = websocket.headers.get("origin")
    if not origin:
        return True
    host = websocket.headers.get("x-forwarded-host") or websocket.headers.get("host") or ""
    return urlsplit(origin).netloc == host


@router.websocket("/ws")
async def ws_endpoint(websocket: WebSocket) -> None:
    op = await _operator_version(websocket)
    if op is None or not _same_origin(websocket):
        await websocket.close(code=CLOSE_POLICY)
        return
    manager: RunManager = websocket.app.state.run_manager
    await websocket.accept()
    conn = Connection()
    manager.connect(conn)
    # 봇 상태 패널은 폴링하지 않는다: 연결할 때 현재 상태를 한 번 보내고 이후 바뀔 때만 보낸다
    monitor = getattr(websocket.app.state, "bot_monitor", None)
    if monitor is not None:
        for ev in monitor.snapshot():
            conn.push(ev.model_dump_json())

    async def pump() -> None:
        while True:
            data = await conn.queue.get()
            await websocket.send_text(data)
            if conn.overflowed and conn.queue.empty():
                await websocket.close(code=1013)  # 재연결 후 since_seq 로 따라잡는다
                return

    sender = asyncio.create_task(pump())
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = _client_msg.validate_json(raw)
            except ValidationError:
                log.info("해석할 수 없는 클라이언트 메시지를 버림")
                continue
            await _dispatch(manager, conn, op, msg)
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(conn)
        sender.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await sender


async def _dispatch(manager: RunManager, conn: Connection, op: tuple[int, int], msg) -> None:
    match msg:
        case Subscribe():
            await manager.subscribe(conn, msg.payload.session_id, msg.payload.since_seq)
        case Send():
            sid = msg.session_id or conn.session_id
            if sid is None or not await _still_authorized(op):
                return
            if conn.session_id != sid:
                # 보낸 쪽이 message.ack 와 이후 이벤트를 받도록 그 세션을 구독시킨다
                if not await manager.subscribe(conn, sid, since_seq=_NO_REPLAY):
                    return
            try:
                await manager.send(sid, msg.payload.text, msg.payload.model, msg.payload.attachments)
            except SendError as e:
                log.info("send 거절: %s", e.code)
        case CancelQueued():
            await manager.cancel_queued(msg.payload.run_id)
        case StopRun():
            await manager.stop_run(msg.payload.run_id)


# 자동 구독 시 과거 이벤트는 다시 보내지 않는다
_NO_REPLAY = 2**62
