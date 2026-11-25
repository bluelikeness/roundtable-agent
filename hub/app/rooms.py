"""열린 방 (docs/proposals/open-rooms.md R1).

사람 여러 명 + AI 여러 개가 직접 읽고 쓰는 방. 기존 1인 방(session)·WebSocket 계약은 건드리지 않는다.

운영자(로그인 쿠키):
  POST   /api/rooms                                  방 만들기 {name}
  GET    /api/rooms                                  방 목록
  PATCH  /api/rooms/{room_id}                        이름 바꾸기·'부를 때만' {name?, call_only?}
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
  GET  /api/p/msgs?since=<id>   → {me, kind, role, room, room_id, check_every_s, call_only, my_turn,
                                   msgs:[{id, who, kind, text, ts, to_me}]}
     to_me: 그 글이 나를(@내이름 또는 @모두) 불렀는지. my_turn: 내 마지막 글 이후 아직 답 안 한 부름이 있는지.
  '부를 때만' 방에서 팀장이 아닌 AI 가 부름 없이 보내면 409 not_called (docs/proposals/open-rooms.md 3.4).
  POST /api/p/send {text}       → {id}
  GET  /api/p/participants      → [{name, kind, role, enabled, last_seen_at}]
"""

import hashlib
import secrets
import time
import uuid
from collections import deque
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
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


class RoomPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    call_only: bool | None = None


class RoomOut(BaseModel):
    id: uuid.UUID
    name: str
    call_only: bool
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
        call_only=r.call_only,
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


def _msg_out(m: RoomMessage, me: str | None = None) -> dict:
    out = {
        "id": m.id,
        "who": m.who,
        "kind": m.kind.value,
        "text": m.text,
        "ts": m.created_at.isoformat(),
    }
    if me is not None:
        out["to_me"] = m.who != me and calls(m.text, me)
    return out


EVERYONE = ("모두", "all")


def mentioned(text: str, name: str) -> bool:
    """@name 이 글에 있고 바로 뒤가 이름을 잇는 글자가 아니면 참(@클로 가 @클로드 에 걸리지 않게)."""
    tag = "@" + name
    start = 0
    while (i := text.find(tag, start)) >= 0:
        j = i + len(tag)
        if j >= len(text) or not (text[j].isalnum() or text[j] == "_"):
            return True
        start = i + 1
    return False


def calls(text: str, name: str) -> bool:
    """이 글이 name 을 불렀는지: @name 또는 @모두."""
    return mentioned(text, name) or any(mentioned(text, e) for e in EVERYONE)


async def _pending_call(db: AsyncSession, p: Participant) -> bool:
    """내 마지막 글 이후, 남이 나를 부른 글이 있는지."""
    mine = (
        await db.execute(
            select(func.max(RoomMessage.id)).where(
                RoomMessage.room_id == p.room_id, RoomMessage.participant_id == p.id
            )
        )
    ).scalar() or 0
    q = select(RoomMessage).where(
        RoomMessage.room_id == p.room_id, RoomMessage.id > mine
    )
    for m in (await db.execute(q)).scalars():
        if m.participant_id != p.id and calls(m.text, p.name):
            return True
    return False


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
    room_id: uuid.UUID, body: RoomPatch, db: AsyncSession = Depends(get_db)
) -> RoomOut:
    room = await _get_room(db, room_id)
    if body.name is not None:
        room.name = body.name.strip()
    if body.call_only is not None:
        room.call_only = body.call_only
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


def _key(x_key: str | None, k: str | None) -> str | None:
    """헤더(x-key)가 기본. 헤더를 못 붙이는 앱(폰 비서 등)을 위해 ?k= 도 받는다."""
    return x_key or k


async def _read_state(
    db: AsyncSession, p: Participant, room: Room, since: int, settings: Settings
) -> dict:
    msgs = await _read_msgs(db, room.id, since, settings)
    my_turn = await _pending_call(db, p)
    p.last_seen_at = datetime.now(UTC)
    await db.commit()
    return {
        "me": p.name,
        "kind": p.kind.value,
        "role": p.role.value,
        "room": room.name,
        "room_id": str(room.id),
        "check_every_s": p.check_every_s,
        "call_only": room.call_only,
        "my_turn": my_turn,
        "msgs": [_msg_out(m, p.name) for m in msgs],
    }


async def _say(
    db: AsyncSession, p: Participant, room: Room, raw: str, settings: Settings
) -> int | JSONResponse:
    text = _clean_text(raw, settings)
    if (
        room.call_only
        and p.kind == ParticipantKind.ai
        and p.role != ParticipantRole.leader
        and not await _pending_call(db, p)
    ):
        raise HTTPException(409, "not_called")
    retry = _rate(_sends, p.id, settings.room_sends_per_minute)
    if retry:
        return _too_many(retry)
    m = RoomMessage(
        room_id=room.id, participant_id=p.id, who=p.name, kind=p.kind, text=text
    )
    db.add(m)
    p.last_seen_at = datetime.now(UTC)
    await db.commit()
    return m.id


@p_router.get("/msgs")
async def p_msgs(
    since: int = Query(0, ge=0),
    x_key: str | None = Header(default=None),
    k: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    p, room = await _participant_by_key(db, _key(x_key, k))
    retry = _rate(_reads, p.id, settings.room_reads_per_minute)
    if retry:
        return _too_many(retry)
    return await _read_state(db, p, room, since, settings)


@p_router.post("/send")
async def p_send(
    body: SendIn,
    x_key: str | None = Header(default=None),
    k: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    p, room = await _participant_by_key(db, _key(x_key, k))
    out = await _say(db, p, room, body.text, settings)
    return out if isinstance(out, JSONResponse) else {"id": out}


# --- 링크만 여는 AI 용(헤더·POST 를 못 쓰는 폰 비서 앱 등). 키가 주소에 들어가니 믿는 앱에만 준다 ---

_LINK_ERR = {
    "invalid_key": "키가 맞지 않아요. 방 주인에게 새 링크를 받아 주세요.",
    "room_closed": "방이 닫혔어요. 더 이상 읽고 쓸 수 없어요.",
    "participant_paused": "방 주인이 잠깐 빼 두었어요. 나중에 다시 확인해 주세요.",
    "not_called": "아직 당신을 부르지 않았어요. 누가 @이름 으로 부르면 그때 답해 주세요.",
    "empty_text": "보낼 말(text)이 비었어요.",
    "too_long": "글이 너무 길어요. 줄여 주세요.",
}


def _plain(text: str, status_code: int = 200) -> PlainTextResponse:
    return PlainTextResponse(
        text, status_code=status_code, headers={"Cache-Control": "no-store"}
    )


def _link_error(e: HTTPException) -> PlainTextResponse:
    return _plain(
        f"오류({e.status_code}): {_LINK_ERR.get(str(e.detail), str(e.detail))}",
        e.status_code,
    )


@p_router.get("/read")
async def p_read(
    request: Request,
    k: str = Query(...),
    since: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """읽기 쉬운 글자 페이지: 방 상황 + 최근 글 + 답하는 방법(링크)."""
    try:
        p, room = await _participant_by_key(db, k)
    except HTTPException as e:
        return _link_error(e)
    if _rate(_reads, p.id, settings.room_reads_per_minute):
        return _plain("너무 자주 확인했어요. 1분 뒤 다시 열어 주세요.", 429)
    st = await _read_state(db, p, room, since, settings)
    base = settings.public_url.rstrip("/") or str(request.base_url).rstrip("/")
    say = f"{base}/api/p/say?k={k}"
    again = f"{base}/api/p/read?k={k}&since="
    lines = [
        f"방: {st['room']}  /  나: {st['me']}"
        + ("  /  팀장" if st["role"] == "leader" else ""),
        "규칙: "
        + (
            "나를 @이름 으로 부른 글에만 답한다."
            if st["call_only"]
            else "나를 부르면 답한다."
        )
        + " 짧게(5문장 이내), 한 번에 한 번.",
        f"지금 내 차례: {'예 — 아래 [나를 부름] 글에 답해 주세요' if st['my_turn'] else '아니요 — 기다려 주세요'}",
        "",
        "— 최근 글 (아래가 최신) —",
    ]
    for m in st["msgs"]:
        mark = " [나를 부름]" if m.get("to_me") else ""
        lines.append(f"#{m['id']} {m['who']}{mark}: {m['text']}")
    if not st["msgs"]:
        lines.append("(새 글 없음)")
    last = st["msgs"][-1]["id"] if st["msgs"] else since
    lines += [
        "",
        "— 답하는 방법 —",
        f"이 주소 끝에 &text=<할 말(URL 인코딩)> 을 붙여 열면 방에 올라가요:\n{say}&text=",
        f"새 글만 다시 보려면: {again}{last}",
    ]
    return _plain("\n".join(lines))


@p_router.get("/say", name="p_say")
async def p_say(
    k: str = Query(...),
    text: str = Query(""),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """링크를 열면 글이 올라간다(GET). 같은 글을 1분 안에 다시 열면 한 번만 올린다."""
    try:
        p, room = await _participant_by_key(db, k)
        recent = (
            await db.execute(
                select(RoomMessage)
                .where(RoomMessage.participant_id == p.id)
                .order_by(RoomMessage.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if (
            recent is not None
            and recent.text == text.strip()
            and (datetime.now(UTC) - recent.created_at).total_seconds() < 60
        ):
            # 미리보기·새로고침으로 같은 링크가 또 열려도 중복으로 올리지 않는다
            return _plain(f"이미 올렸어요 (#{recent.id}).")
        out = await _say(db, p, room, text, settings)
    except HTTPException as e:
        return _link_error(e)
    if isinstance(out, JSONResponse):
        return _plain("너무 자주 보냈어요. 1분 뒤 다시 해 주세요.", 429)
    return _plain(f"방에 올렸어요 (#{out}).")


@p_router.get("/participants")
async def p_participants(
    x_key: str | None = Header(default=None),
    k: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    p, room = await _participant_by_key(db, _key(x_key, k))
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
