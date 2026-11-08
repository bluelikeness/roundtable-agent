"""허브 WebSocket 이벤트 모델 (docs/05-ui.md 6장). 봉투와 type 목록은 바꾸지 않는다.

모든 이벤트는 {type, session_id, seq, ts, payload} 봉투다. bot.status 는 세션 무관이라
session_id 와 seq 가 null 이다. 이 모듈이 `make types` 로 web 의 TS 타입이 된다.
"""

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- payload: 서버 → 클라이언트 ---


class MessageAckPayload(_Model):
    message_id: uuid.UUID
    run_id: uuid.UUID
    status: Literal["queued", "running"]


class RunStartedPayload(_Model):
    run_id: uuid.UUID
    claude_session_id: str | None


class ClaudeNotePayload(_Model):
    run_id: uuid.UUID
    text: str


class BotPendingPayload(_Model):
    run_id: uuid.UUID
    bot: str
    tool_call_id: str
    queue_position: int


class BotReplyPayload(_Model):
    run_id: uuid.UUID
    bot: str
    tool_call_id: str
    ok: bool
    raw: str
    # structured 의 세부 스키마 기준은 docs/03-adapters.md 3장 (P1-2 에서 모델화)
    structured: dict[str, Any] | None
    needs_input: bool
    error: str | None
    latency_ms: int
    message_id: uuid.UUID


class ClaudeFinalPayload(_Model):
    run_id: uuid.UUID
    text: str
    message_id: uuid.UUID
    usage: dict[str, Any] | None


class RunFinishedPayload(_Model):
    run_id: uuid.UUID
    status: Literal["done", "failed", "timeout"]
    error: str | None = None


class BotStatusPayload(_Model):
    bot: str
    available: bool
    reason: str | None
    last_ok_at: datetime | None
    queue_depth: int
    avg_latency_ms: int | None
    enabled: bool


class SessionSystemPayload(_Model):
    text: str


# --- payload: 클라이언트 → 서버 ---


class SubscribePayload(_Model):
    session_id: uuid.UUID
    since_seq: int = 0


class SendPayload(_Model):
    text: str
    model: Literal["sonnet", "opus"] | None = None


class CancelQueuedPayload(_Model):
    run_id: uuid.UUID


class StopRunPayload(_Model):
    run_id: uuid.UUID


# --- 봉투 ---


class _Envelope(_Model):
    session_id: uuid.UUID | None
    seq: int | None
    ts: datetime


class MessageAck(_Envelope):
    type: Literal["message.ack"]
    payload: MessageAckPayload


class RunStarted(_Envelope):
    type: Literal["run.started"]
    payload: RunStartedPayload


class ClaudeNote(_Envelope):
    type: Literal["claude.note"]
    payload: ClaudeNotePayload


class BotPending(_Envelope):
    type: Literal["bot.pending"]
    payload: BotPendingPayload


class BotReply(_Envelope):
    type: Literal["bot.reply"]
    payload: BotReplyPayload


class ClaudeFinal(_Envelope):
    type: Literal["claude.final"]
    payload: ClaudeFinalPayload


class RunFinished(_Envelope):
    type: Literal["run.finished"]
    payload: RunFinishedPayload


class BotStatus(_Envelope):
    type: Literal["bot.status"]
    payload: BotStatusPayload


class SessionSystem(_Envelope):
    type: Literal["session.system"]
    payload: SessionSystemPayload


HubEvent = Annotated[
    MessageAck
    | RunStarted
    | ClaudeNote
    | BotPending
    | BotReply
    | ClaudeFinal
    | RunFinished
    | BotStatus
    | SessionSystem,
    Field(discriminator="type"),
]
"""서버 → 클라이언트 이벤트. 러너 인터페이스 Runner.start(...) -> AsyncIterator[HubEvent] 의 HubEvent."""


class _ClientEnvelope(_Model):
    """클라이언트가 보내는 메시지. 같은 봉투 모양이지만 seq·ts 는 서버가 매기므로 선택값이다."""

    session_id: uuid.UUID | None = None
    seq: int | None = None
    ts: datetime | None = None


class Subscribe(_ClientEnvelope):
    type: Literal["subscribe"]
    payload: SubscribePayload


class Send(_ClientEnvelope):
    type: Literal["send"]
    payload: SendPayload


class CancelQueued(_ClientEnvelope):
    type: Literal["cancel_queued"]
    payload: CancelQueuedPayload


class StopRun(_ClientEnvelope):
    type: Literal["stop_run"]
    payload: StopRunPayload


ClientMessage = Annotated[Subscribe | Send | CancelQueued | StopRun, Field(discriminator="type")]



def _type_literals(union) -> tuple[str, ...]:
    """Annotated[A | B | ..., Field(...)] 에서 각 모델의 type 리터럴 값을 뽑는다."""
    return tuple(get_args(m.model_fields["type"].annotation)[0] for m in get_args(get_args(union)[0]))


# 유니온에서 계산한다. 목록이 05-ui 6장과 맞는지는 tests/test_events.py 가 확인한다
SERVER_EVENT_TYPES = _type_literals(HubEvent)
CLIENT_MESSAGE_TYPES = _type_literals(ClientMessage)
