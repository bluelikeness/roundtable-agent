"""ask·status 두 툴만 여는 MCP 서버 (03 2장). streamable HTTP, 무상태.

무상태(stateless)로 여는 이유: 어댑터가 재시작해도 Claude Code 쪽 MCP 세션이 끊기지 않는다.
ask·status 호출은 어떤 경우에도 is_error=False 의 정상 결과다. 오류는 AskResult.error 필드.
목록에 없는 툴 이름만 is_error=True 로 돌려준다(MCP 예외는 아니다).
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

import mcp_types as types
from mcp.server import Server

from adapters.auth import HubTokenGuard

if TYPE_CHECKING:
    from adapters.base import BotAdapter

log = logging.getLogger("adapters.mcp")

# 03 2장 inputSchema 그대로. 바꾸지 않는다.
ASK_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["text", "thread_id"],
    "properties": {
        "text": {"type": "string", "maxLength": 4000},
        "thread_id": {"type": "string", "description": "허브 세션 ID. 같은 ID면 같은 봇 대화를 잇는다"},
        "timeout_s": {"type": "integer", "default": 120, "minimum": 10, "maximum": 240},
        "reset": {"type": "boolean", "default": False, "description": "true면 봇 대화를 새로 시작한 뒤 보낸다"},
    },
}

STATUS_INPUT_SCHEMA: dict[str, Any] = {"type": "object", "properties": {}}
STATUS_DESCRIPTION = "ask 호출 전 가용성 확인. available, reason, last_ok_at, queue_depth, avg_latency_ms 를 돌려준다."


def _result(payload: dict[str, Any]) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))],
        structured_content=payload,
        is_error=False,
    )


def build_server(adapter: "BotAdapter") -> Server:
    async def list_tools(ctx, params) -> types.ListToolsResult:
        return types.ListToolsResult(
            tools=[
                types.Tool(name="ask", description=adapter.description, input_schema=ASK_INPUT_SCHEMA),
                types.Tool(name="status", description=STATUS_DESCRIPTION, input_schema=STATUS_INPUT_SCHEMA),
            ]
        )

    async def call_tool(ctx, params: types.CallToolRequestParams) -> types.CallToolResult:
        args = params.arguments or {}
        try:
            if params.name == "ask":
                res = await adapter.ask(
                    args.get("text"),
                    args.get("thread_id"),
                    timeout_s=args.get("timeout_s"),
                    reset=args.get("reset", False),
                )
                return _result(res.model_dump())
            if params.name == "status":
                return _result((await adapter.status()).model_dump())
        except Exception:  # adapter.ask 는 예외를 던지지 않지만 마지막 방어선
            log.exception("툴 처리 오류")
            if params.name == "ask":
                from adapters.base import AskResult

                return _result(AskResult(bot=adapter.name, ok=False, error="internal").model_dump())
            return _result({"available": False, "reason": "backend_down", "last_ok_at": None, "queue_depth": 0, "avg_latency_ms": 0})
        # 없는 툴: 예외 대신 툴 오류 결과
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=f"unknown tool: {params.name}")], is_error=True
        )

    return Server(adapter.name, version="0.1.0", on_list_tools=list_tools, on_call_tool=call_tool)


def build_mcp_app(adapter: "BotAdapter"):
    server = build_server(adapter)
    # host 를 0.0.0.0 으로 줘서 DNS 리바인딩 보호(로컬 Host 만 허용)를 끈다. 내부망 호스트명으로 불리고, 토큰으로 막는다.
    app = server.streamable_http_app(
        streamable_http_path="/mcp", stateless_http=True, json_response=True, host="0.0.0.0"
    )
    return HubTokenGuard(app, adapter.token)
