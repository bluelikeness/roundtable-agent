"""DB 테이블 (docs/02-architecture.md 6장). 자격증명은 operator 의 해시·TOTP 시크릿 외에는 넣지 않는다."""

import enum
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    pass


Base.metadata.naming_convention = NAMING


def _ts(nullable: bool = False, default_now: bool = True) -> Mapped[Any]:
    return mapped_column(
        DateTime(timezone=True),
        nullable=nullable,
        server_default=func.now() if default_now else None,
    )


class MessageRole(str, enum.Enum):
    user = "user"
    claude = "claude"
    bot = "bot"
    system = "system"


class RunStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    done = "done"
    failed = "failed"
    timeout = "timeout"


class SessionGroup(Base):
    """세션 그룹(목록의 폴더). 그룹을 지우면 속한 세션은 그룹 없음이 된다(FK SET NULL)."""

    __tablename__ = "session_group"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0", default=0)
    created_at: Mapped[datetime] = _ts()


class ChatSession(Base):
    """허브 세션. 테이블 이름은 설계대로 session."""

    __tablename__ = "session"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title: Mapped[str | None] = mapped_column(Text)
    claude_session_id: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = _ts()
    last_message_at: Mapped[datetime | None] = _ts(nullable=True, default_now=False)
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false", default=False)
    # 설계 표에 없는 컬럼: WebSocket 이벤트 seq 의 세션별 카운터(05 6장). message 가 없는 이벤트
    # (run.started·bot.pending 등)도 seq 를 쓰므로 message.seq 최대값으로는 이어 붙일 수 없다.
    last_seq: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0", default=0)
    # 세션 관리(2026-11-17): 그룹, 세션별 Claude 모델(sonnet|opus, 비면 기본), 휴지통으로 옮긴 시각
    group_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("session_group.id", ondelete="SET NULL"), index=True
    )
    model: Mapped[str | None] = mapped_column(String(16))
    trashed_at: Mapped[datetime | None] = _ts(nullable=True, default_now=False)


class Message(Base):
    __tablename__ = "message"
    __table_args__ = (UniqueConstraint("session_id", "seq", name="uq_message_session_seq"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # session_id 조회는 uq_message_session_seq (session_id, seq) 인덱스가 맡으므로 단독 인덱스를 두지 않는다
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("session.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[MessageRole] = mapped_column(Enum(MessageRole, name="message_role"), nullable=False)
    bot_name: Mapped[str | None] = mapped_column(String(64))
    raw: Mapped[str] = mapped_column(Text, nullable=False, default="")
    structured: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = _ts()


class Run(Base):
    __tablename__ = "run"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("session.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_message_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("message.id", ondelete="SET NULL"))
    status: Mapped[RunStatus] = mapped_column(
        Enum(RunStatus, name="run_status"), nullable=False, default=RunStatus.queued
    )
    # 설계 표에 없는 created_at 을 둔다: queued 상태(started_at 없음)의 대기 순서를 정하기 위해.
    created_at: Mapped[datetime] = _ts()
    started_at: Mapped[datetime | None] = _ts(nullable=True, default_now=False)
    ended_at: Mapped[datetime | None] = _ts(nullable=True, default_now=False)
    usage: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    error: Mapped[str | None] = mapped_column(Text)
    # 설계 표에 없는 컬럼: 이 run 이 쓴 Claude Code 세션. 세션 분할(04 9장)의
    # "현재 Claude 세션 누적 tool_call 20건" 을 세는 데 쓴다.
    claude_session_id: Mapped[str | None] = mapped_column(String(128))


class ToolCall(Base):
    """어댑터 호출 로그. id 는 Claude Code 의 tool_use id 를 그대로 쓴다."""

    __tablename__ = "tool_call"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("run.id", ondelete="CASCADE"), nullable=False, index=True)
    bot_name: Mapped[str] = mapped_column(String(64), nullable=False)
    input: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    output: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = _ts()


class Bot(Base):
    """헬스체크 결과 캐시."""

    __tablename__ = "bot"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true", default=True)
    available: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false", default=False)
    reason: Mapped[str | None] = mapped_column(String(64))
    last_ok_at: Mapped[datetime | None] = _ts(nullable=True, default_now=False)
    updated_at: Mapped[datetime] = _ts()


class BotThread(Base):
    __tablename__ = "bot_thread"

    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("session.id", ondelete="CASCADE"), primary_key=True)
    bot_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    thread_ref: Mapped[str] = mapped_column(String(256), nullable=False)


class Operator(Base):
    """허브 운영자(1명). 비밀번호는 bcrypt 해시, TOTP 시크릿은 base32."""

    __tablename__ = "operator"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    totp_secret: Mapped[str] = mapped_column(String(64), nullable=False)
    failed_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0", default=0)
    locked_until: Mapped[datetime | None] = _ts(nullable=True, default_now=False)
    # 비밀번호·TOTP 재설정 시 올려서 기존 로그인 쿠키를 무효화한다.
    auth_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1", default=1)
    # 마지막으로 받아 준 TOTP 시간 단계. 같은 코드의 재사용을 막는다
    last_totp_step: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = _ts()


# --- 열린 방 (docs/proposals/open-rooms.md) ---
# 기존 1인 방(session)과 따로 둔다. 참가자마다 키가 곧 신원이고, 키는 sha256 해시만 저장한다.


class ParticipantKind(str, enum.Enum):
    human = "human"
    ai = "ai"


class ParticipantRole(str, enum.Enum):
    member = "member"
    leader = "leader"


class Room(Base):
    __tablename__ = "room"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = _ts()
    # 닫은 시각. 닫힌 방은 참가자 API 가 410 을 돌려준다
    closed_at: Mapped[datetime | None] = _ts(nullable=True, default_now=False)
    # 사람 초대 링크 키의 sha256. 비면 초대가 꺼져 있다
    invite_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)


class Participant(Base):
    __tablename__ = "participant"
    __table_args__ = (UniqueConstraint("room_id", "name", name="uq_participant_room_name"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    room_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("room.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[ParticipantKind] = mapped_column(Enum(ParticipantKind, name="participant_kind"), nullable=False)
    name: Mapped[str] = mapped_column(String(40), nullable=False)
    # 키 원문은 저장하지 않는다. 발급 응답에서 한 번만 보여 준다
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    role: Mapped[ParticipantRole] = mapped_column(
        Enum(ParticipantRole, name="participant_role"), nullable=False, default=ParticipantRole.member
    )
    # 이 참가자에게 권하는 확인 간격(초). 읽기 응답에 실어 보낸다
    check_every_s: Mapped[int] = mapped_column(Integer, nullable=False, server_default="60", default=60)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true", default=True)
    last_seen_at: Mapped[datetime | None] = _ts(nullable=True, default_now=False)
    created_at: Mapped[datetime] = _ts()


class RoomMessage(Base):
    __tablename__ = "room_message"

    # 전역 단조 증가. 참가자는 since=<마지막 id> 로 이어 읽는다
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    room_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("room.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # 참가자를 지워도 글은 남긴다. 그때를 위해 보낸 이름·종류를 같이 적어 둔다
    participant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("participant.id", ondelete="SET NULL"), nullable=True
    )
    who: Mapped[str] = mapped_column(String(40), nullable=False)
    kind: Mapped[ParticipantKind] = mapped_column(Enum(ParticipantKind, name="participant_kind"), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = _ts()
