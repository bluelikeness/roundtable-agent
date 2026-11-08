"""Claude Code stream-json → 허브 이벤트 변환 (docs/02-architecture.md 4장 변환 표, 05 6장 payload).

한 줄씩 feed() 에 넣으면 HubEvent 와 ToolCallRecord 를 순서대로 돌려준다.

- system/init                → run.started {claude_session_id}
- assistant 의 tool_use(ask) → bot.pending (+ tool_call 시작 기록)
- assistant 의 tool_use(status) → 이벤트 없음, tool_call 기록만
- user 의 tool_result(ask)   → bot.reply (+ tool_call 결과 기록)
- assistant 의 text          → claude.note. 단 마지막 text 는 result 의 최종 답과 같으므로,
                               다음 tool_use·tool_result 가 올 때까지 잡아 두었다가 note 로 내보낸다
- result success             → claude.final + run.finished done
- result error_* / 비정상 종료 → run.finished failed (타임아웃은 timeout)
"""

import json
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from runner.mcp_config import parse_tool_name
from runner.shared import (
    BotPending,
    BotPendingPayload,
    BotReply,
    BotReplyPayload,
    ClaudeFinal,
    ClaudeFinalPayload,
    ClaudeNote,
    ClaudeNotePayload,
    HubEvent,
    RunFinished,
    RunFinishedPayload,
    RunStarted,
    RunStartedPayload,
    ToolCallRecord,
)

log = logging.getLogger("runner.stream")

Item = HubEvent | ToolCallRecord
# 어댑터 계약(03 2장)의 error 값. 결과를 해석할 수 없을 때는 internal
ASK_ERRORS = {"timeout", "unavailable", "login_required", "rate_limited", "internal"}
# claude.final usage 에 함께 담는 result 필드(run.usage·cost_usd 로 저장)
RESULT_STATS = ("total_cost_usd", "num_turns", "duration_ms", "duration_api_ms", "modelUsage")


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class _Call:
    bot: str
    tool: str
    started: datetime


@dataclass
class StreamParser:
    session_id: uuid.UUID
    run_id: uuid.UUID
    now: Callable[[], datetime] = _utcnow
    new_id: Callable[[], uuid.UUID] = uuid.uuid4

    claude_session_id: str | None = None
    finished: bool = False
    _started: bool = False
    _pending_text: list[str] = field(default_factory=list)
    _calls: dict[str, _Call] = field(default_factory=dict)
    _open_asks: dict[str, set[str]] = field(default_factory=dict)

    # --- 공개 API ---

    def feed(self, line: str) -> list[Item]:
        line = line.strip()
        if not line or self.finished:
            return []
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            log.warning("stream-json 이 아닌 줄을 건너뜀 (%d자)", len(line))
            return []
        if not isinstance(msg, dict):
            return []
        kind = msg.get("type")
        if kind == "system" and msg.get("subtype") == "init":
            return self._on_init(msg)
        if kind == "assistant":
            return self._on_assistant(msg)
        if kind == "user":
            return self._on_user(msg)
        if kind == "result":
            return self._on_result(msg)
        return []

    def finish(self, status: Literal["failed", "timeout"], error: str | None) -> list[Item]:
        """result 없이 끝났을 때(타임아웃·중단·비정상 종료). 남은 노트와 대기 중 봇을 닫는다."""
        if self.finished:
            return []
        out: list[Item] = self._flush_note()
        ask_error = "timeout" if status == "timeout" else "internal"
        for call_id in list(self._calls):
            out += self._close_call(call_id, None, ask_error)
        out.append(self._event(RunFinished, "run.finished", RunFinishedPayload(run_id=self.run_id, status=status, error=error)))
        self.finished = True
        return out

    @property
    def started(self) -> bool:
        return self._started

    # --- 내부 ---

    def _event(self, cls, type_: str, payload) -> HubEvent:
        return cls(type=type_, session_id=self.session_id, seq=None, ts=self.now(), payload=payload)

    def _flush_note(self) -> list[Item]:
        if not self._pending_text:
            return []
        text = "\n\n".join(t for t in self._pending_text if t.strip())
        self._pending_text.clear()
        if not text.strip():
            return []
        return [self._event(ClaudeNote, "claude.note", ClaudeNotePayload(run_id=self.run_id, text=text))]

    def _on_init(self, msg: dict[str, Any]) -> list[Item]:
        if self._started:
            return []
        self._started = True
        self.claude_session_id = msg.get("session_id")
        return [
            self._event(
                RunStarted, "run.started", RunStartedPayload(run_id=self.run_id, claude_session_id=self.claude_session_id)
            )
        ]

    def _content(self, msg: dict[str, Any]) -> list[dict[str, Any]]:
        content = (msg.get("message") or {}).get("content")
        return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []

    def _on_assistant(self, msg: dict[str, Any]) -> list[Item]:
        out: list[Item] = []
        for block in self._content(msg):
            btype = block.get("type")
            if btype == "text":
                self._pending_text.append(block.get("text") or "")
            elif btype == "tool_use":
                out += self._flush_note()
                out += self._on_tool_use(block)
        return out

    def _on_tool_use(self, block: dict[str, Any]) -> list[Item]:
        call_id = block.get("id")
        parsed = parse_tool_name(block.get("name") or "")
        if not call_id or parsed is None:
            return []  # 봇 툴이 아니다. --disallowedTools 로 막혀 있어 정상 흐름에는 없다
        bot, tool = parsed
        args = block.get("input") if isinstance(block.get("input"), dict) else None
        self._calls[call_id] = _Call(bot=bot, tool=tool, started=self.now())
        out: list[Item] = [ToolCallRecord(id=call_id, run_id=self.run_id, bot=bot, tool=tool, input=args)]
        if tool == "ask":
            waiting = self._open_asks.setdefault(bot, set())
            out.append(
                self._event(
                    BotPending,
                    "bot.pending",
                    BotPendingPayload(run_id=self.run_id, bot=bot, tool_call_id=call_id, queue_position=len(waiting)),
                )
            )
            waiting.add(call_id)
        return out

    def _on_user(self, msg: dict[str, Any]) -> list[Item]:
        out: list[Item] = []
        for block in self._content(msg):
            if block.get("type") != "tool_result":
                continue
            call_id = block.get("tool_use_id")
            if call_id not in self._calls:
                continue
            out += self._flush_note()
            output = _tool_output(block)
            error = None
            if block.get("is_error") or output is None:
                error = "internal"
            out += self._close_call(call_id, output, error)
        return out

    def _close_call(self, call_id: str, output: dict[str, Any] | None, error: str | None) -> list[Item]:
        call = self._calls.pop(call_id)
        elapsed = int((self.now() - call.started).total_seconds() * 1000)
        if call.tool != "ask":
            return [
                ToolCallRecord(
                    id=call_id, run_id=self.run_id, bot=call.bot, tool=call.tool, done=True,
                    output=output, latency_ms=elapsed, error=error,
                )
            ]
        self._open_asks.get(call.bot, set()).discard(call_id)
        reply = _ask_reply(output, error, elapsed)
        record = ToolCallRecord(
            id=call_id, run_id=self.run_id, bot=call.bot, tool=call.tool, done=True,
            output=output, latency_ms=reply["latency_ms"], error=reply["error"],
        )
        event = self._event(
            BotReply,
            "bot.reply",
            BotReplyPayload(run_id=self.run_id, bot=call.bot, tool_call_id=call_id, message_id=self.new_id(), **reply),
        )
        return [record, event]

    def _on_result(self, msg: dict[str, Any]) -> list[Item]:
        out: list[Item] = []
        ok = msg.get("subtype") == "success" and not msg.get("is_error")
        if ok:
            # 잡아 둔 마지막 text 는 result 의 최종 답과 같은 내용이다
            self._pending_text.clear()
            usage = dict(msg.get("usage") or {})
            for k in RESULT_STATS:
                if k in msg:
                    usage[k] = msg[k]
            out.append(
                self._event(
                    ClaudeFinal,
                    "claude.final",
                    ClaudeFinalPayload(
                        run_id=self.run_id, text=str(msg.get("result") or ""), message_id=self.new_id(), usage=usage or None
                    ),
                )
            )
            out.append(self._event(RunFinished, "run.finished", RunFinishedPayload(run_id=self.run_id, status="done")))
            self.finished = True
            return out
        # error_max_turns·error_during_execution 등. 지금까지의 노트는 살려서 보여준다.
        # subtype=success 인데 is_error 인 경우(미로그인·API 오류)는 claude_error 로 적는다
        subtype = str(msg.get("subtype") or "")
        return self.finish("failed", subtype if subtype.startswith("error") else "claude_error")


def _tool_output(block: dict[str, Any]) -> dict[str, Any] | None:
    """tool_result 의 content 에서 어댑터가 돌려준 JSON 객체를 꺼낸다."""
    content = block.get("content")
    texts: list[str] = []
    if isinstance(content, str):
        texts.append(content)
    elif isinstance(content, list):
        texts += [c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"]
    for t in texts:
        try:
            obj = json.loads(t)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(obj, dict):
            return obj
    return None


def _ask_reply(output: dict[str, Any] | None, error: str | None, elapsed_ms: int) -> dict[str, Any]:
    """AskResult(03 2장) → bot.reply payload 필드. 해석이 안 되면 ok=false, error=internal."""
    if output is None or error is not None:
        return {
            "ok": False, "raw": "", "structured": None, "needs_input": False,
            "error": error or "internal", "latency_ms": elapsed_ms,
        }
    err = output.get("error")
    if err is not None and err not in ASK_ERRORS:
        err = "internal"
    structured = output.get("structured")
    latency = output.get("latency_ms")
    return {
        "ok": bool(output.get("ok")),
        "raw": str(output.get("raw") or ""),
        "structured": structured if isinstance(structured, dict) else None,
        "needs_input": bool(output.get("needs_input")),
        "error": err,
        "latency_ms": latency if isinstance(latency, int) else elapsed_ms,
    }
