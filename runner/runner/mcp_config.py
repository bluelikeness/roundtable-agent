"""실행 직전 mcp.json 과 허용 툴 목록 생성 (02 4장 툴 제한, 6장 bot 상태 머신).

허브가 enabled·available 둘 다 참인 봇만 넘기므로, 여기 들어간 봇만 Claude 가 본다.
토큰은 파일에 쓰지 않고 ${HUB_ADAPTER_TOKEN} 로 두어 Claude Code 가 환경변수에서 펼치게 한다.
"""

from typing import Any

from runner.shared import BotEndpoint

TOKEN_ENV = "HUB_ADAPTER_TOKEN"
BOT_TOOLS = ("ask", "status")


def build_mcp_config(bots: list[BotEndpoint]) -> dict[str, Any]:
    return {
        "mcpServers": {
            b.name: {
                "type": "http",
                "url": b.mcp_url,
                "headers": {"X-Hub-Token": "${" + TOKEN_ENV + "}"},
            }
            for b in bots
        }
    }


def allowed_tools(bots: list[BotEndpoint]) -> list[str]:
    return [f"mcp__{b.name}__{t}" for b in bots for t in BOT_TOOLS]


def parse_tool_name(name: str) -> tuple[str, str] | None:
    """mcp__<bot>__<tool> → (bot, tool). 봇 이름에는 "__" 가 없다(BOT_NAME_RE)."""
    if not name.startswith("mcp__"):
        return None
    bot, sep, tool = name[len("mcp__"):].rpartition("__")
    if not sep or not bot or not tool:
        return None
    return bot, tool
