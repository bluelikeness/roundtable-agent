"""허브↔러너 계약 (docs/02-architecture.md 4장). 러너 인터페이스는 바꾸지 않는다.

    Runner.start(session, text) -> AsyncIterator[HubEvent]

허브(hub/app/runner_client.py 의 RemoteRunner)와 러너 컨테이너(runner/ 의 ClaudeCodeRunner)가
같은 모델을 쓰도록 이 파일과 events.py 는 pydantic 외 의존성을 두지 않는다.
러너 이미지는 두 파일을 hubshared 패키지로 복사해 쓴다(runner/Dockerfile).

러너가 내보내는 HubEvent 는 seq 가 비어 있다. seq 는 허브가 DB 에 쓰면서 매긴다.
bot.reply·claude.final 의 message_id 는 러너가 만들고 허브가 message 행 id 로 그대로 쓴다.
"""

import re
import uuid
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .events import HubEvent

# MCP 서버 이름 = 봇 이름. Claude Code 툴 이름 mcp__<bot>__ask 에 들어가므로 "__" 를 막는다
BOT_NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9]|-(?=[a-z0-9])){0,31}$")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BotEndpoint(_Model):
    """mcp.json 한 줄. 허브가 bots.yaml 과 헬스체크 결과로 골라 넘긴다."""

    name: str
    mcp_url: str

    @field_validator("name")
    @classmethod
    def _check_name(cls, v: str) -> str:
        if not BOT_NAME_RE.match(v):
            raise ValueError("봇 이름은 소문자·숫자·단일 하이픈만, 32자 이하")
        return v


class RunnerSession(_Model):
    """러너가 실행 한 번에 필요한 세션 사실. 러너는 DB 를 보지 않으므로 허브가 모아 넘긴다.

    세션 분할(04 9장) 판단과 요약 조립은 러너가 이 값으로 한다.
    """

    session_id: uuid.UUID
    run_id: uuid.UUID
    claude_session_id: str | None = None
    # 이번 사용자 메시지 직전의 마지막 메시지 시각(24시간 분할 판단)
    last_message_at: datetime | None = None
    # 현재 Claude Code 세션에서 누적된 tool_call 수(20건 초과 분할 판단)
    tool_calls: int = 0
    # 사용자가 "새로 시작"을 요청함
    force_new: bool = False
    # 요약 주입 재료: 직전 claude.final 전문, 봇별 마지막 원문
    last_final: str | None = None
    last_bot_raws: dict[str, str] = Field(default_factory=dict)
    bots: list[BotEndpoint] = Field(default_factory=list)
    model: Literal["sonnet", "opus"] = "sonnet"


class ToolCallRecord(_Model):
    """tool_call 테이블 한 행의 갱신분. ask 뿐 아니라 status 호출도 기록한다(이벤트는 없음)."""

    id: str
    run_id: uuid.UUID
    bot: str
    tool: str
    input: dict[str, Any] | None = None
    # done=false: tool_use 시점(시작). done=true: tool_result 시점(결과)
    done: bool = False
    output: dict[str, Any] | None = None
    latency_ms: int | None = None
    error: str | None = None


class RunRequest(_Model):
    """POST /runs 본문."""

    session: RunnerSession
    text: str


class StreamLine(_Model):
    """POST /runs 응답(NDJSON) 한 줄. 둘 중 하나만 채운다."""

    event: HubEvent | None = None
    tool_call: ToolCallRecord | None = None


ToolCallSink = Callable[[ToolCallRecord], Awaitable[None]]


class Runner(ABC):
    """Claude Code 호출 계층. 지인 공유 시 API 키 구현으로 교체할 수 있게 둔다(02 4장 교체 가능성).

    on_tool_call 이 있으면 tool_call 기록을 그쪽으로 넘긴다. 이벤트 스트림과 별개의 보조 경로다.
    """

    on_tool_call: ToolCallSink | None = None

    @abstractmethod
    def start(self, session: RunnerSession, text: str) -> AsyncIterator[HubEvent]:
        """사용자 메시지 한 건을 실행하고 허브 이벤트를 차례로 낸다. 마지막은 항상 run.finished."""

    @abstractmethod
    async def stop(self, run_id: uuid.UUID) -> bool:
        """실행 중인 run 을 멈춘다(SIGTERM). 해당 run 이 없으면 False."""
