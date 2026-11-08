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


class ChatSession(Base):
    """허브 세션. 테이블 이름은 설계대로 session."""

    __tablename__ = "session"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title: Mapped[str | None] = mapped_column(Text)
    claude_session_id: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = _ts()
    last_message_at: Mapped[datetime | None] = _ts(nullable=True, default_now=False)
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false", default=False)


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
