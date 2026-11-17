"""바깥 입구: 다른 AI 비서(피클 등)가 아무 때나 허브의 방에 의견을 넣는다.

POST /api/inbox  Authorization: Bearer <HUB_INBOX_TOKEN>
  {"room": "방 이름", "text": "의견", "from": "피클"}
받은 글은 그 방의 사용자 메시지로 넣고(앞에 "(피클에서 보낸 의견)"), 오케스트레이터가 평소처럼 실행된다.
WebSocket 이벤트는 기존 message.ack 이후 흐름 그대로라 05 6장 계약을 바꾸지 않는다.
방은 보관하지 않은 세션의 제목으로 찾는다: 대소문자·공백 무시 일치 → 없으면 유일한 부분 일치.
못 찾으면 404 와 함께 방 이름 목록을 돌려줘 부른 쪽이 사용자에게 되물을 수 있게 한다.

답 돌려주기: POST 는 실행이 끝나기를 최대 inbox_wait_s(기본 15초) 기다린다.
  끝나면 200 {status: done, answer}. 실패면 200 {status: failed|timeout, error}.
  아직이면 202 {status: queued|running, result_url, retry_after_s} — 부른 쪽이 나중에
GET /api/inbox/runs/{run_id} (같은 토큰) 로 다시 받아 간다. 응답 모양은 POST 와 같다.
GET 은 이 입구로 들어온 실행만 보여 준다(다른 방 대화를 토큰으로 읽지 못하게).
"""

import asyncio
import hmac
import re
import time
import uuid
from collections import deque

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db import get_db, get_sessionmaker
from app.models import ChatSession, Message, MessageRole, Run, RunStatus
from app.runs import SendError

router = APIRouter(prefix="/api")

_recent: deque[float] = deque()

# 입구로 들어온 사용자 메시지 표시. GET 이 이 실행만 보여 주는 데도 쓴다
_PREFIX_RE = re.compile(r"^\([^()\n]{1,40}에서 보낸 의견\) ")
_FINISHED = {RunStatus.done, RunStatus.failed, RunStatus.timeout}


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
                .where(ChatSession.archived.is_(False), ChatSession.trashed_at.is_(None))
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


def _result_url(run_id: uuid.UUID) -> str:
    return f"/api/inbox/runs/{run_id}"


async def _result(run_id: uuid.UUID) -> dict | None:
    """실행 상태와(끝났으면) 정리 답. 입구로 들어온 실행이 아니면 None."""
    async with get_sessionmaker()() as db:
        run = await db.get(Run, run_id)
        if run is None or run.user_message_id is None:
            return None
        um = await db.get(Message, run.user_message_id)
        if um is None or not _PREFIX_RE.match(um.raw):
            return None
        session = await db.get(ChatSession, run.session_id)
        out: dict = {
            "room": session.title if session else None,
            "session_id": str(run.session_id),
            "run_id": str(run.id),
            "status": run.status.value,
        }
        if run.status not in _FINISHED:
            return out
        if run.status != RunStatus.done:
            out["error"] = run.error or run.status.value
            return out
        # 정리 답은 run 과 직접 이어져 있지 않다: 이 실행의 사용자 메시지 다음, 다음 사용자 메시지 전의
        # 마지막 final. 세션 안의 실행은 차례로 돌므로 그 사이가 이 실행의 몫이다
        nxt = (
            await db.execute(
                select(Message.seq)
                .where(
                    Message.session_id == run.session_id,
                    Message.role == MessageRole.user,
                    Message.seq > um.seq,
                )
                .order_by(Message.seq)
                .limit(1)
            )
        ).scalar()
        q = select(Message).where(
            Message.session_id == run.session_id,
            Message.role == MessageRole.claude,
            Message.seq > um.seq,
        )
        if nxt is not None:
            q = q.where(Message.seq < nxt)
        msgs = (await db.execute(q.order_by(Message.seq))).scalars().all()
        finals = [m for m in msgs if (m.structured or {}).get("kind") == "final"]
        out["answer"] = finals[-1].raw if finals else ""
        return out


def _reply(res: dict, wait_s: float) -> JSONResponse:
    if res["status"] in {s.value for s in _FINISHED}:
        return JSONResponse(res, status_code=200)
    retry = max(5, int(wait_s))
    res = {
        **res,
        "result_url": _result_url(uuid.UUID(res["run_id"])),
        "retry_after_s": retry,
        "message": f"아직 진행 중입니다. {retry}초쯤 뒤 result_url 을 같은 토큰으로 GET 해서 답을 받아 가세요.",
    }
    return JSONResponse(res, status_code=202, headers={"Retry-After": str(retry)})


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
    run_id = ack.payload.run_id
    deadline = time.monotonic() + settings.inbox_wait_s
    res = await _result(run_id)
    while (
        res is not None
        and res["status"] not in {s.value for s in _FINISHED}
        and time.monotonic() < deadline
    ):
        await asyncio.sleep(0.5)
        res = await _result(run_id)
    if res is None:  # 방금 넣은 실행이라 없을 수 없다
        raise HTTPException(500, "run_missing")
    return _reply(res, settings.inbox_wait_s)


@router.get("/inbox/runs/{run_id}")
async def inbox_result(
    run_id: uuid.UUID,
    request: Request,
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    _check_token(request, settings)
    res = await _result(run_id)
    if res is None:
        raise HTTPException(404, "run_not_found")
    return _reply(res, settings.inbox_wait_s)
