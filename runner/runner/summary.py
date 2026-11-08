"""세션 분할과 요약 주입 (docs/04-orchestrator.md 9장).

러너가 기계적으로 조립한다. 추가 Claude 호출은 하지 않는다.
미해결 확정 건은 [진행] 감지 방식(05 9장 미결)이 정해지는 P1-4 이후에 넣는다.
"""

from datetime import datetime, timedelta

from runner.shared import RunnerSession

IDLE_SPLIT = timedelta(hours=24)
TOOL_CALL_SPLIT = 20
BOT_RAW_CHARS = 300

REASON_TEXT = {
    "user": "새로 시작 요청",
    "idle": "마지막 메시지 후 24시간 경과",
    "tool_calls": f"봇 호출 {TOOL_CALL_SPLIT}건 초과",
}


def split_reason(session: RunnerSession, now: datetime) -> str | None:
    """이어받을 Claude 세션이 있는데 새로 시작해야 하면 사유 키. 이어받거나 첫 실행이면 None."""
    if session.claude_session_id is None:
        return None
    if session.force_new:
        return "user"
    if session.last_message_at is not None and now - session.last_message_at >= IDLE_SPLIT:
        return "idle"
    if session.tool_calls > TOOL_CALL_SPLIT:
        return "tool_calls"
    return None


def _clip(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit] + "…"


def build_summary(session: RunnerSession) -> str | None:
    """<session_summary> 블록. 넣을 재료가 없으면 None."""
    lines: list[str] = []
    if session.last_final:
        lines.append(f"- 마지막 정리: {session.last_final.strip()}")
    for bot, raw in session.last_bot_raws.items():
        if raw.strip():
            lines.append(f"- {bot} 마지막 응답(일부): {_clip(raw, BOT_RAW_CHARS)}")
    if not lines:
        return None
    return "<session_summary>\n이전 대화 요약(허브가 자동 생성):\n" + "\n".join(lines) + "\n</session_summary>"


def inject_summary(summary: str | None, text: str) -> str:
    return f"{summary}\n\n{text}" if summary else text
