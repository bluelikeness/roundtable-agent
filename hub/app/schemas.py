"""REST 요청·응답 스키마."""

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models import MessageRole


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)
    totp: str = Field(min_length=6, max_length=8)


class OperatorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    username: str


ModelName = Literal["sonnet", "opus"]


class SessionCreate(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    group_id: uuid.UUID | None = None
    model: ModelName | None = None


class SessionUpdate(BaseModel):
    """보낸 필드만 바꾼다. group_id·model 은 null 로 보내면 그룹 없음·기본 모델로 되돌린다."""

    title: str | None = Field(default=None, max_length=200)
    archived: bool | None = None
    group_id: uuid.UUID | None = None
    model: ModelName | None = None
    # true: 휴지통으로, false: 휴지통에서 되살리기
    trashed: bool | None = None


class SessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str | None
    created_at: datetime
    last_message_at: datetime | None
    archived: bool
    group_id: uuid.UUID | None = None
    model: str | None = None
    trashed_at: datetime | None = None


class GroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=60)


class GroupUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=60)
    position: int | None = None


class GroupOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    position: int
    created_at: datetime


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    session_id: uuid.UUID
    role: MessageRole
    bot_name: str | None
    raw: str
    structured: dict[str, Any] | None
    seq: int
    created_at: datetime


class BotOut(BaseModel):
    """GET /api/bots: @자동완성·봇 색에 쓰는 공개 필드만(mcp_url·admin_url 은 내보내지 않는다)."""

    name: str
    aliases: list[str]
    color: str | None
