"""열린 방 (docs/proposals/open-rooms.md R1).

사람 여러 명 + AI 여러 개가 직접 읽고 쓰는 방. 기존 1인 방(session)·WebSocket 계약은 건드리지 않는다.

운영자(로그인 쿠키):
  POST   /api/rooms                                  방 만들기 {name}
  GET    /api/rooms                                  방 목록
  PATCH  /api/rooms/{room_id}                        이름 바꾸기 {name}
  DELETE /api/rooms/{room_id}                        방 닫기(닫힌 방은 참가자 API 410)
  GET    /api/rooms/{room_id}/messages?since=        글 읽기
  GET    /api/rooms/{room_id}/participants           참가자 목록(키는 안 보임)
  POST   /api/rooms/{room_id}/participants           참가자 넣기 {name, kind, role?, check_every_s?} → key(한 번만)
  PATCH  /api/rooms/{room_id}/participants/{pid}     역할·확인 간격·켜기/끄기
  POST   /api/rooms/{room_id}/participants/{pid}/rotate  키 재발급(옛 키 즉시 무효) → key
  DELETE /api/rooms/{room_id}/participants/{pid}     끊고 지우기(글은 남는다)
  POST   /api/rooms/{room_id}/messages               운영자로 글 쓰기 {text}
  POST   /api/rooms/{room_id}/invite                 사람 초대 링크 키 발급(다시 부르면 바뀌고 옛 링크 무효) → invite
  DELETE /api/rooms/{room_id}/invite                 초대 링크 끄기

초대받은 사람(로그인 없음, 초대 키가 곧 입장권):
  GET  /api/join/{invite}           → {room, people:[{name, kind}]}
  POST /api/join/{invite} {name}    → {key, me, room} 사람 참가자로 넣고 키를 한 번 돌려준다

참가자(헤더 x-key: <참가자 키>). 키가 곧 신원이라 다른 이름으로 쓸 수 없다:
  GET  /api/p/msgs?since=<id>   → {me, kind, role, room, room_id, check_every_s, msgs:[{id, who, kind, text, ts}]}
  POST /api/p/send {text}       → {id}
  GET  /api/p/participants      → [{name, kind, role, enabled, last_seen_at}]
"""

import hashlib
import secrets
import time
import uuid
from collections import deque
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import current_operator
from app.config import Settings, get_settings
from app.db import get_db
from app.models import (
    Operator,
    Participant,
    ParticipantKind,
    ParticipantRole,
    Room,
    RoomMessage,
)

KEY_PREFIX = "rk_"
INVITE_PREFIX = "ri_"

# 참가자별 최근 요청 시각(단일 프로세스 기준, inbox 와 같은 방식)
_reads: dict[uuid.UUID, deque[float]] = {}
_sends: dict[uuid.UUID, deque[float]] = {}
# 방별 초대 입장 시도(초대 링크가 퍼졌을 때 참가자 폭주 방지)
_joins: dict[uuid.UUID, deque[float]] = {}


def new_key() -> str:
    return KEY_PREFIX + secrets.token_urlsafe(32)


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def _rate(bucket: dict[uuid.UUID, deque[float]], pid: uuid.UUID, limit: int) -> int:
    """허용이면 0, 넘었으면 다시 시도할 때까지 초."""
    now = time.monotonic()
    q = bucket.setdefault(pid, deque())
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= limit:
        return max(1, int(60 - (now - q[0])) + 1)
    q.append(now)
    return 0


def _too_many(retry: int) -> JSONResponse:
    return JSONResponse(
        {"detail": "rate_limited", "retry_after_s": retry},
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        headers={"Retry-After": str(retry)},
    )


# --- 스키마 ---


class RoomIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class RoomOut(BaseModel):
    id: uuid.UUID
    name: str
    created_at: datetime
    closed_at: datetime | None
    invite_on: bool


class InviteOut(BaseModel):
    # 원문 초대 키. 발급 응답에서만 보인다
    invite: str


class JoinIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)


class ParticipantIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    kind: ParticipantKind
    role: ParticipantRole = ParticipantRole.member
    check_every_s: int = Field(default=60, ge=15, le=3600)


class ParticipantPatch(BaseModel):
    role: ParticipantRole | None = None
    check_every_s: int | None = Field(default=None, ge=15, le=3600)
    enabled: bool | None = None


class ParticipantOut(BaseModel):
    id: uuid.UUID
    name: str
    kind: ParticipantKind
    role: ParticipantRole
    check_every_s: int
    enabled: bool
    last_seen_at: datetime | None
    created_at: datetime


class ParticipantKeyOut(ParticipantOut):
    # 원문 키는 발급·재발급 응답에서만 한 번 보여 준다
    key: str


class SendIn(BaseModel):
    text: str = Field(min_length=1)


def _room_out(r: Room) -> RoomOut:
    return RoomOut(
        id=r.id,
        name=r.name,
        created_at=r.created_at,
        closed_at=r.closed_at,
        invite_on=r.invite_hash is not None,
    )


def _p_out(p: Participant) -> ParticipantOut:
    return ParticipantOut(
        id=p.id,
        name=p.name,
        kind=p.kind,
        role=p.role,
        check_every_s=p.check_every_s,
        enabled=p.enabled,
        last_seen_at=p.last_seen_at,
        created_at=p.created_at,
    )


def _msg_out(m: RoomMessage) -> dict:
    return {
        "id": m.id,
        "who": m.who,
        "kind": m.kind.value,
        "text": m.text,
        "ts": m.created_at.isoformat(),
    }


async def _read_msgs(
    db: AsyncSession, room_id: uuid.UUID, since: int, settings: Settings
) -> list[RoomMessage]:
    q = select(RoomMessage).where(RoomMessage.room_id == room_id)
    if since <= 0:
        # 처음 읽기: 최근 글 일부만
        rows = (
            (
                await db.execute(
                    q.order_by(RoomMessage.id.desc()).limit(settings.room_history)
                )
            )
            .scalars()
            .all()
        )
        return list(reversed(rows))
    q = (
        q.where(RoomMessage.id > since)
        .order_by(RoomMessage.id)
        .limit(settings.room_page)
    )
    return list((await db.execute(q)).scalars().all())


def _clean_text(raw: str, settings: Settings) -> str:
    text = raw.strip()
    if not text:
        raise HTTPException(422, "empty_text")
    if len(text) > settings.room_max_chars:
        raise HTTPException(413, "too_long")
    return text


async def _people(db: AsyncSession, room_id: uuid.UUID) -> list[Participant]:
    q = (
        select(Participant)
        .where(Participant.room_id == room_id)
        .order_by(Participant.created_at)
    )
    return list((await db.execute(q)).scalars().all())


# --- 운영자 ---

op_router = APIRouter(prefix="/api/rooms", dependencies=[Depends(current_operator)])


async def _get_room(db: AsyncSession, room_id: uuid.UUID) -> Room:
    room = await db.get(Room, room_id)
    if room is None:
        raise HTTPException(404, "room_not_found")
    return room


async def _get_participant(
    db: AsyncSession, room_id: uuid.UUID, pid: uuid.UUID
) -> Participant:
    p = await db.get(Participant, pid)
    if p is None or p.room_id != room_id:
        raise HTTPException(404, "participant_not_found")
    return p


@op_router.post("", response_model=RoomOut, status_code=201)
async def create_room(body: RoomIn, db: AsyncSession = Depends(get_db)) -> RoomOut:
    room = Room(name=body.name.strip())
    db.add(room)
    await db.commit()
    await db.refresh(room)
    return _room_out(room)


@op_router.get("", response_model=list[RoomOut])
async def list_rooms(db: AsyncSession = Depends(get_db)) -> list[RoomOut]:
    rows = (
        (await db.execute(select(Room).order_by(Room.created_at.desc())))
        .scalars()
        .all()
    )
    return [_room_out(r) for r in rows]


@op_router.patch("/{room_id}", response_model=RoomOut)
async def rename_room(
    room_id: uuid.UUID, body: RoomIn, db: AsyncSession = Depends(get_db)
) -> RoomOut:
    room = await _get_room(db, room_id)
    room.name = body.name.strip()
    await db.commit()
    return _room_out(room)


@op_router.delete("/{room_id}", response_model=RoomOut)
async def close_room(room_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> RoomOut:
    room = await _get_room(db, room_id)
    if room.closed_at is None:
        room.closed_at = datetime.now(UTC)
        await db.commit()
    return _room_out(room)


@op_router.get("/{room_id}/messages")
async def operator_messages(
    room_id: uuid.UUID,
    since: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    room = await _get_room(db, room_id)
    msgs = await _read_msgs(db, room.id, since, settings)
    return {
        "room": room.name,
        "room_id": str(room.id),
        "msgs": [_msg_out(m) for m in msgs],
    }


@op_router.get("/{room_id}/participants", response_model=list[ParticipantOut])
async def list_participants(
    room_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> list[ParticipantOut]:
    await _get_room(db, room_id)
    rows = (
        (
            await db.execute(
                select(Participant)
                .where(Participant.room_id == room_id)
                .order_by(Participant.created_at)
            )
        )
        .scalars()
        .all()
    )
    return [_p_out(p) for p in rows]


@op_router.post(
    "/{room_id}/participants", response_model=ParticipantKeyOut, status_code=201
)
async def add_participant(
    room_id: uuid.UUID,
    body: ParticipantIn,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ParticipantKeyOut:
    room = await _get_room(db, room_id)
    if room.closed_at is not None:
        raise HTTPException(409, "room_closed")
    if len(await _people(db, room.id)) >= settings.room_max_participants:
        raise HTTPException(409, "room_full")
    key = new_key()
    p = Participant(
        room_id=room.id,
        name=body.name.strip(),
        kind=body.kind,
        role=body.role,
        check_every_s=body.check_every_s,
        key_hash=hash_key(key),
    )
    db.add(p)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "name_taken") from None
    await db.refresh(p)
    return ParticipantKeyOut(**_p_out(p).model_dump(), key=key)


@op_router.patch("/{room_id}/participants/{pid}", response_model=ParticipantOut)
async def update_participant(
    room_id: uuid.UUID,
    pid: uuid.UUID,
    body: ParticipantPatch,
    db: AsyncSession = Depends(get_db),
) -> ParticipantOut:
    p = await _get_participant(db, room_id, pid)
    if body.role is not None:
        p.role = body.role
    if body.check_every_s is not None:
        p.check_every_s = body.check_every_s
    if body.enabled is not None:
        p.enabled = body.enabled
    await db.commit()
    return _p_out(p)


@op_router.post(
    "/{room_id}/participants/{pid}/rotate", response_model=ParticipantKeyOut
)
async def rotate_key(
    room_id: uuid.UUID, pid: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> ParticipantKeyOut:
    p = await _get_participant(db, room_id, pid)
    key = new_key()
    p.key_hash = hash_key(key)
    await db.commit()
    return ParticipantKeyOut(**_p_out(p).model_dump(), key=key)


@op_router.delete("/{room_id}/participants/{pid}", status_code=204)
async def remove_participant(
    room_id: uuid.UUID, pid: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> None:
    p = await _get_participant(db, room_id, pid)
    await db.delete(p)
    await db.commit()
    _reads.pop(pid, None)
    _sends.pop(pid, None)


@op_router.post("/{room_id}/messages")
async def operator_send(
    room_id: uuid.UUID,
    body: SendIn,
    op: Operator = Depends(current_operator),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    room = await _get_room(db, room_id)
    if room.closed_at is not None:
        raise HTTPException(409, "room_closed")
    text = _clean_text(body.text, settings)
    m = RoomMessage(
        room_id=room.id,
        participant_id=None,
        who=op.username[:40],
        kind=ParticipantKind.human,
        text=text,
    )
    db.add(m)
    await db.commit()
    return {"id": m.id}


@op_router.post("/{room_id}/invite", response_model=InviteOut)
async def make_invite(
    room_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> InviteOut:
    room = await _get_room(db, room_id)
    if room.closed_at is not None:
        raise HTTPException(409, "room_closed")
    invite = INVITE_PREFIX + secrets.token_urlsafe(24)
    room.invite_hash = hash_key(invite)
    await db.commit()
    return InviteOut(invite=invite)


@op_router.delete("/{room_id}/invite", response_model=RoomOut)
async def stop_invite(
    room_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> RoomOut:
    room = await _get_room(db, room_id)
    room.invite_hash = None
    await db.commit()
    return _room_out(room)


# --- 초대받은 사람 ---

join_router = APIRouter(prefix="/api/join")


async def _room_by_invite(db: AsyncSession, invite: str) -> Room:
    if not invite.startswith(INVITE_PREFIX):
        raise HTTPException(404, "invite_not_found")
    room = (
        await db.execute(select(Room).where(Room.invite_hash == hash_key(invite)))
    ).scalar_one_or_none()
    if room is None:
        raise HTTPException(404, "invite_not_found")
    if room.closed_at is not None:
        raise HTTPException(410, "room_closed")
    return room


@join_router.get("/{invite}")
async def join_info(invite: str, db: AsyncSession = Depends(get_db)) -> dict:
    room = await _room_by_invite(db, invite)
    people = await _people(db, room.id)
    return {
        "room": room.name,
        "people": [{"name": p.name, "kind": p.kind.value} for p in people],
    }


@join_router.post("/{invite}", status_code=201)
async def join(
    invite: str,
    body: JoinIn,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    room = await _room_by_invite(db, invite)
    retry = _rate(_joins, room.id, settings.room_joins_per_minute)
    if retry:
        return _too_many(retry)
    if len(await _people(db, room.id)) >= settings.room_max_participants:
        raise HTTPException(409, "room_full")
    key = new_key()
    p = Participant(
        room_id=room.id,
        name=body.name.strip(),
        kind=ParticipantKind.human,
        key_hash=hash_key(key),
    )
    db.add(p)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "name_taken") from None
    return JSONResponse({"key": key, "me": p.name, "room": room.name}, status_code=201)


# --- 참가자(키) ---

p_router = APIRouter(prefix="/api/p")


async def _participant_by_key(
    db: AsyncSession, key: str | None
) -> tuple[Participant, Room]:
    if not key or not key.startswith(KEY_PREFIX):
        raise HTTPException(401, "invalid_key")
    p = (
        await db.execute(
            select(Participant).where(Participant.key_hash == hash_key(key))
        )
    ).scalar_one_or_none()
    if p is None:
        raise HTTPException(401, "invalid_key")
    room = await db.get(Room, p.room_id)
    if room is None or room.closed_at is not None:
        raise HTTPException(410, "room_closed")
    if not p.enabled:
        raise HTTPException(403, "participant_paused")
    return p, room


@p_router.get("/msgs")
async def p_msgs(
    since: int = Query(0, ge=0),
    x_key: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    p, room = await _participant_by_key(db, x_key)
    retry = _rate(_reads, p.id, settings.room_reads_per_minute)
    if retry:
        return _too_many(retry)
    msgs = await _read_msgs(db, room.id, since, settings)
    p.last_seen_at = datetime.now(UTC)
    await db.commit()
    return {
        "me": p.name,
        "kind": p.kind.value,
        "role": p.role.value,
        "room": room.name,
        "room_id": str(room.id),
        "check_every_s": p.check_every_s,
        "msgs": [_msg_out(m) for m in msgs],
    }


@p_router.post("/send")
async def p_send(
    body: SendIn,
    x_key: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    p, room = await _participant_by_key(db, x_key)
    text = _clean_text(body.text, settings)
    retry = _rate(_sends, p.id, settings.room_sends_per_minute)
    if retry:
        return _too_many(retry)
    m = RoomMessage(
        room_id=room.id, participant_id=p.id, who=p.name, kind=p.kind, text=text
    )
    db.add(m)
    p.last_seen_at = datetime.now(UTC)
    await db.commit()
    return {"id": m.id}


@p_router.get("/participants")
async def p_participants(
    x_key: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    p, room = await _participant_by_key(db, x_key)
    retry = _rate(_reads, p.id, settings.room_reads_per_minute)
    if retry:
        return _too_many(retry)
    return [
        {
            "name": x.name,
            "kind": x.kind.value,
            "role": x.role.value,
            "enabled": x.enabled,
            "last_seen_at": x.last_seen_at.isoformat() if x.last_seen_at else None,
        }
        for x in await _people(db, room.id)
    ]
