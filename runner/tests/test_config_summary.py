"""mcp.json·허용 툴 생성, 세션 분할·요약 주입 (02 4장, 04 9장)."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from runner.mcp_config import allowed_tools, build_mcp_config, parse_tool_name
from runner.shared import BotEndpoint
from runner.summary import build_summary, inject_summary, split_reason
from tests.conftest import make_session

NOW = datetime(2026, 11, 8, 12, tzinfo=UTC)


def test_mcp_config_and_allowed_tools():
    bots = [
        BotEndpoint(name="mock-a", mcp_url="http://mock-a:9000/mcp"),
        BotEndpoint(name="vooy", mcp_url="http://vooy:9000/mcp"),
    ]
    cfg = build_mcp_config(bots)
    assert cfg["mcpServers"]["mock-a"] == {
        "type": "http",
        "url": "http://mock-a:9000/mcp",
        "headers": {"X-Hub-Token": "${HUB_ADAPTER_TOKEN}"},
        "timeout": 270_000,  # Claude Code 기본 60초 툴 제한을 덮는다
    }
    assert build_mcp_config(bots, 90)["mcpServers"]["vooy"]["timeout"] == 90_000
    assert allowed_tools(bots) == ["mcp__mock-a__ask", "mcp__mock-a__status", "mcp__vooy__ask", "mcp__vooy__status"]


@pytest.mark.parametrize("name", ["Mock", "a__b", "-a", "a-", "", "a" * 33, "a b"])
def test_bad_bot_names_rejected(name):
    with pytest.raises(ValidationError):
        BotEndpoint(name=name, mcp_url="http://x")


def test_parse_tool_name():
    assert parse_tool_name("mcp__mock-a__ask") == ("mock-a", "ask")
    assert parse_tool_name("mcp__vooy__status") == ("vooy", "status")
    assert parse_tool_name("Bash") is None
    assert parse_tool_name("mcp__x") is None


def test_split_reasons():
    assert split_reason(make_session(), NOW) is None  # 첫 실행은 분할이 아니다
    s = make_session(claude_session_id="c", last_message_at=NOW - timedelta(hours=1), tool_calls=20)
    assert split_reason(s, NOW) is None
    assert split_reason(s.model_copy(update={"tool_calls": 21}), NOW) == "tool_calls"
    assert split_reason(s.model_copy(update={"last_message_at": NOW - timedelta(hours=24)}), NOW) == "idle"
    assert split_reason(s.model_copy(update={"force_new": True}), NOW) == "user"


def test_summary_format():
    s = make_session(last_final="내일 맑음. 예약할까요?", last_bot_raws={"mock-a": "가" * 400, "mock-b": "짧은 답"})
    text = inject_summary(build_summary(s), "그럼 예약해줘")
    assert text.startswith("<session_summary>\n이전 대화 요약(허브가 자동 생성):\n- 마지막 정리: 내일 맑음. 예약할까요?\n")
    assert f"- mock-a 마지막 응답(일부): {'가' * 300}…\n" in text
    assert text.endswith("- mock-b 마지막 응답(일부): 짧은 답\n</session_summary>\n\n그럼 예약해줘")


def test_empty_summary_is_omitted():
    assert build_summary(make_session()) is None
    assert inject_summary(None, "안녕") == "안녕"
