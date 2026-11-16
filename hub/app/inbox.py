"""바깥 입구: 다른 AI 비서(피클 등)가 아무 때나 허브의 방에 의견을 넣는다.

POST /api/inbox  Authorization: Bearer <HUB_INBOX_TOKEN>
  {"room": "방 이름", "text": "의견", "from": "피클"}
받은 글은 그 방의 사용자 메시지로 넣고(앞에 "(피클에서 보낸 의견)"), 오케스트레이터가 평소처럼 실행된다.
WebSocket 이벤트는 기존 message.ack 이후 흐름 그대로라 05 6장 계약을 바꾸지 않는다.
방은 보관하지 않은 세션의 제목으로 찾는다: 대소문자·공백 무시 일치 → 없으면 유일한 부분 일치.
못 찾으면 404 와 함께 방 이름 목록을 돌려줘 부른 쪽이 사용자에게 되물을 수 있게 한다.
"""

import hmac
import re
import time
from collections import deque

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db import get_db
from app.models import ChatSession
from app.runs import SendError

router = APIRouter(prefix="/api")

_recent: deque[float] = deque()


class InboxIn(BaseModel):
    room: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1)
    sender: str = Field(default="외부", alias="from", max_length=40)


def _norm(s: str | None) -> str:
    return re.sub(r"\s+", "", (s or "")).lower()


def _check_token(request: Request, settings: Settings) -> None:
    if not settings.inbox_token:
        # 입구를 켜지 않았으면 있는지도 알리지 않는다
        raise HTTPException(404, "not_found")
    auth = request.headers.get("authorization", "")
    given = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    if not given or not hmac.compare_digest(
        given.encode(), settings.inbox_token.encode()
    ):
        raise HTTPException(401, "unauthorized")


def _rate_ok(limit: int) -> bool:
    now = time.monotonic()
    while _recent and now - _recent[0] > 60:
        _recent.popleft()
    if len(_recent) >= limit:
        return False
    _recent.append(now)
    return True


async def _find_room(
    db: AsyncSession, room: str
) -> tuple[ChatSession | None, list[str]]:
    rows = (
        (
            await db.execute(
                select(ChatSession)
                .where(ChatSession.archived.is_(False))
                .order_by(ChatSession.last_message_at.desc().nulls_last())
            )
        )
        .scalars()
        .all()
    )
    want = _norm(room)
    exact = [s for s in rows if _norm(s.title) == want]
    if len(exact) >= 1:
        return exact[0], []
    part = [s for s in rows if want and want in _norm(s.title)]
    if len(part) == 1:
        return part[0], []
    names = [s.title for s in (part or rows) if s.title][:20]
    return None, names


@router.post("/inbox")
async def inbox(
    body: InboxIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    _check_token(request, settings)
    text = body.text.strip()
    if not text:
        raise HTTPException(422, "empty_text")
    if len(text) > settings.inbox_max_chars:
        raise HTTPException(413, "text_too_long")
    if not _rate_ok(settings.inbox_per_minute):
        raise HTTPException(429, "too_many_requests")
    session, names = await _find_room(db, body.room)
    if session is None:
        return JSONResponse(
            {"detail": "room_not_found", "rooms": names}, status_code=404
        )
    sender = body.sender.strip() or "외부"
    manager = request.app.state.run_manager
    try:
        ack = await manager.send(session.id, f"({sender}에서 보낸 의견) {text}", None)
    except SendError as e:
        raise HTTPException(409, e.code) from None
    return JSONResponse(
        {
            "room": session.title,
            "session_id": str(session.id),
            "message_id": str(ack.payload.message_id),
            "run_id": str(ack.payload.run_id),
            "status": ack.payload.status,
        },
        status_code=202,
    )
