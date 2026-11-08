"""허브 REST: 로그인, 세션 CRUD, 메시지 조회. 메시지 전송은 WebSocket(P1-3)에서 한다."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import LoginError, authenticate, clear_cookie, current_operator, issue_cookie
from app.config import Settings, get_settings
from app.db import get_db
from app.models import ChatSession, Message, Operator
from app.schemas import LoginIn, MessageOut, OperatorOut, SessionCreate, SessionOut, SessionUpdate

router = APIRouter(prefix="/api")


# --- 인증 ---


@router.post("/auth/login", response_model=OperatorOut)
async def login(
    body: LoginIn,
    response: Response,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Operator:
    try:
        op = await authenticate(db, body.username, body.password, body.totp, settings)
    except LoginError as e:
        raise HTTPException(e.status_code, e.code) from None
    issue_cookie(response, op, settings)
    return op


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response, settings: Settings = Depends(get_settings)) -> None:
    clear_cookie(response, settings)


@router.get("/auth/me", response_model=OperatorOut)
async def me(op: Operator = Depends(current_operator)) -> Operator:
    return op


# --- 세션 ---

authed = [Depends(current_operator)]


async def _get_session(db: AsyncSession, session_id: uuid.UUID) -> ChatSession:
    s = await db.get(ChatSession, session_id)
    if s is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session_not_found")
    return s


@router.get("/sessions", response_model=list[SessionOut], dependencies=authed)
async def list_sessions(
    archived: bool = Query(False, description="true 면 보관된 세션만"),
    db: AsyncSession = Depends(get_db),
) -> list[ChatSession]:
    # 최신 활동순: 메시지가 없으면 생성 시각 기준
    q = (
        select(ChatSession)
        .where(ChatSession.archived.is_(archived))
        .order_by(ChatSession.last_message_at.desc().nulls_last(), ChatSession.created_at.desc())
    )
    return list((await db.execute(q)).scalars())


@router.post("/sessions", response_model=SessionOut, status_code=status.HTTP_201_CREATED, dependencies=authed)
async def create_session(body: SessionCreate, db: AsyncSession = Depends(get_db)) -> ChatSession:
    s = ChatSession(title=body.title)
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return s


@router.get("/sessions/{session_id}", response_model=SessionOut, dependencies=authed)
async def get_session(session_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> ChatSession:
    return await _get_session(db, session_id)


@router.patch("/sessions/{session_id}", response_model=SessionOut, dependencies=authed)
async def update_session(
    session_id: uuid.UUID, body: SessionUpdate, db: AsyncSession = Depends(get_db)
) -> ChatSession:
    s = await _get_session(db, session_id)
    fields = body.model_dump(exclude_unset=True)
    if "title" in fields:
        s.title = fields["title"]
    if fields.get("archived") is not None:
        s.archived = fields["archived"]
    await db.commit()
    await db.refresh(s)
    return s


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=authed)
async def delete_session(session_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> None:
    # 메시지·run·tool_call·bot_thread 는 FK CASCADE 로 함께 지워진다
    s = await _get_session(db, session_id)
    await db.delete(s)
    await db.commit()


# --- 메시지 ---


@router.get("/sessions/{session_id}/messages", response_model=list[MessageOut], dependencies=authed)
async def list_messages(
    session_id: uuid.UUID,
    since_seq: int = Query(0, ge=0, description="이 seq 보다 큰 메시지만"),
    db: AsyncSession = Depends(get_db),
) -> list[Message]:
    await _get_session(db, session_id)
    q = (
        select(Message)
        .where(Message.session_id == session_id, Message.seq > since_seq)
        .order_by(Message.seq)
    )
    return list((await db.execute(q)).scalars())
