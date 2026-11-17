"""허브 REST: 로그인, 세션 CRUD, 메시지 조회. 메시지 전송은 WebSocket(P1-3)에서 한다."""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import (
    LoginError,
    authenticate,
    clear_cookie,
    current_operator,
    issue_cookie,
    read_cookie,
    revoke_sessions,
)
from app.bots import load_bots
from app.config import Settings, get_settings
from app.db import get_db
from app.models import ChatSession, Message, Operator, SessionGroup
from app.schemas import (
    BotOut,
    GroupCreate,
    GroupOut,
    GroupUpdate,
    LoginIn,
    MessageOut,
    OperatorOut,
    SessionCreate,
    SessionOut,
    SessionUpdate,
)

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
async def logout(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> None:
    # 쿠키를 지우는 것만으로는 복사된 토큰이 남으므로 서버에서도 무효화한다.
    # 서버 쪽 세션 표가 없어 운영자의 모든 기기가 함께 로그아웃된다(운영자 1명이라 허용).
    data = read_cookie(request, settings)
    if data is not None and isinstance(data.get("op"), int):
        await revoke_sessions(db, data["op"])
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
    trashed: bool = Query(False, description="true 면 휴지통의 세션만(보관 여부와 무관)"),
    db: AsyncSession = Depends(get_db),
) -> list[ChatSession]:
    q = select(ChatSession)
    if trashed:
        # 휴지통: 최근에 버린 순
        q = q.where(ChatSession.trashed_at.is_not(None)).order_by(ChatSession.trashed_at.desc())
    else:
        # 최신 활동순: 메시지가 없으면 생성 시각 기준
        q = q.where(ChatSession.trashed_at.is_(None), ChatSession.archived.is_(archived)).order_by(
            ChatSession.last_message_at.desc().nulls_last(), ChatSession.created_at.desc()
        )
    return list((await db.execute(q)).scalars())


async def _check_group(db: AsyncSession, group_id: uuid.UUID | None) -> None:
    if group_id is not None and await db.get(SessionGroup, group_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "group_not_found")


@router.post("/sessions", response_model=SessionOut, status_code=status.HTTP_201_CREATED, dependencies=authed)
async def create_session(body: SessionCreate, db: AsyncSession = Depends(get_db)) -> ChatSession:
    await _check_group(db, body.group_id)
    s = ChatSession(title=body.title, group_id=body.group_id, model=body.model)
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
    if "group_id" in fields:
        await _check_group(db, fields["group_id"])
        s.group_id = fields["group_id"]
    if "model" in fields:
        s.model = fields["model"]
    if fields.get("trashed") is not None:
        s.trashed_at = datetime.now(UTC) if fields["trashed"] else None
    await db.commit()
    await db.refresh(s)
    return s


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=authed)
async def delete_session(session_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> None:
    # 영구 삭제는 휴지통에 있는 세션만. 메시지·run·tool_call·bot_thread 는 FK CASCADE 로 함께 지워진다
    s = await _get_session(db, session_id)
    if s.trashed_at is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "not_in_trash")
    await db.delete(s)
    await db.commit()


@router.delete("/trash", status_code=status.HTTP_204_NO_CONTENT, dependencies=authed)
async def empty_trash(db: AsyncSession = Depends(get_db)) -> None:
    """휴지통 비우기: 휴지통의 세션을 모두 영구 삭제한다."""
    await db.execute(delete(ChatSession).where(ChatSession.trashed_at.is_not(None)))
    await db.commit()


# --- 세션 그룹 ---


@router.get("/groups", response_model=list[GroupOut], dependencies=authed)
async def list_groups(db: AsyncSession = Depends(get_db)) -> list[SessionGroup]:
    q = select(SessionGroup).order_by(SessionGroup.position, SessionGroup.created_at)
    return list((await db.execute(q)).scalars())


@router.post("/groups", response_model=GroupOut, status_code=status.HTTP_201_CREATED, dependencies=authed)
async def create_group(body: GroupCreate, db: AsyncSession = Depends(get_db)) -> SessionGroup:
    last = (await db.execute(select(func.max(SessionGroup.position)))).scalar()
    g = SessionGroup(name=body.name.strip(), position=(last or 0) + 1)
    db.add(g)
    await db.commit()
    await db.refresh(g)
    return g


async def _get_group(db: AsyncSession, group_id: uuid.UUID) -> SessionGroup:
    g = await db.get(SessionGroup, group_id)
    if g is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "group_not_found")
    return g


@router.patch("/groups/{group_id}", response_model=GroupOut, dependencies=authed)
async def update_group(group_id: uuid.UUID, body: GroupUpdate, db: AsyncSession = Depends(get_db)) -> SessionGroup:
    g = await _get_group(db, group_id)
    fields = body.model_dump(exclude_unset=True)
    if fields.get("name"):
        g.name = fields["name"].strip()
    if fields.get("position") is not None:
        g.position = fields["position"]
    await db.commit()
    await db.refresh(g)
    return g


@router.delete("/groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=authed)
async def delete_group(group_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> None:
    """그룹만 지운다. 속한 세션은 그룹 없음이 된다(FK SET NULL)."""
    g = await _get_group(db, group_id)
    await db.delete(g)
    await db.commit()


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


# --- 봇 ---


@router.get("/bots", response_model=list[BotOut], dependencies=authed)
async def list_bots(settings: Settings = Depends(get_settings)) -> list[BotOut]:
    # bots.yaml 순서 그대로(봇 색 팔레트 순서). 실행 때와 같이 요청마다 다시 읽는다
    return [BotOut(name=b.name, aliases=b.aliases, color=b.color) for b in load_bots(settings.bots_file)]
