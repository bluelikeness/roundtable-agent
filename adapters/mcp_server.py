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

# 03 2장 inputSchema 그대로. 바꾸지 않는다(attachments 는 2026-11-26 승인을 받아 더한 필드).
ASK_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["text", "thread_id"],
    "properties": {
        "text": {"type": "string", "maxLength": 4000},
        "thread_id": {"type": "string", "description": "허브 세션 ID. 같은 ID면 같은 봇 대화를 잇는다"},
        "timeout_s": {"type": "integer", "default": 120, "minimum": 10, "maximum": 240},
        "reset": {"type": "boolean", "default": False, "description": "true면 봇 대화를 새로 시작한 뒤 보낸다"},
        "attachments": {
            "type": "array",
            "maxItems": 5,
            "items": {"type": "string", "pattern": "^f_[A-Za-z0-9_-]{16,64}$"},
            "description": "허브 파일 ID. 사용자 글의 [첨부 파일] 목록이나 다른 봇 답의 structured.files[].id 를 그대로 넣는다",
        },
    },
}

STATUS_INPUT_SCHEMA: dict[str, Any] = {"type": "object", "properties": {}}
STATUS_DESCRIPTION = "ask 호출 전 가용성 확인. available, reason, last_ok_at, queue_depth, avg_latency_ms 를 돌려준다."

# 입출력 스키마와 별개인 힌트. Claude Code 는 readOnlyHint 가 없는 MCP 툴을 한 번에 하나씩 실행해서
# 봇 넷에 동시에 물어도 앞 봇이 끝나야 다음 봇이 시작됐다(2026-11-26 노트9: 질문 하나에 2분 30초).
# ask 는 허브 밖 상태(봇 대화)를 바꾸지만 허브·다른 봇에는 영향이 없어 서로 겹쳐 불러도 안전하다.
# 순서가 필요한 호출은 Claude 가 앞 결과를 받은 뒤 부르므로 이 힌트와 상관없이 차례로 간다.
PARALLEL_SAFE = types.ToolAnnotations(read_only_hint=True)


def _result(payload: dict[str, Any]) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))],
        structured_content=payload,
        is_error=False,
    )


def ask_description(adapter: "BotAdapter") -> str:
    """봇 설명 끝에 첨부를 받는지 한 줄 붙인다. Claude 가 보내기 전에 고를 수 있게."""
    if adapter.accepts_files:
        kinds = ", ".join(k + "*" if k.endswith("/") else k for k in adapter.accepts_files)
        return f"{adapter.description}\n\n첨부(attachments): {kinds} 를 받는다. 만든 파일은 structured.files 로 돌려준다."
    return f"{adapter.description}\n\n첨부(attachments)는 받지 않는다(넣으면 error=unsupported)."


def build_server(adapter: "BotAdapter") -> Server:
    async def list_tools(ctx, params) -> types.ListToolsResult:
        return types.ListToolsResult(
            tools=[
                types.Tool(
                    name="ask", description=ask_description(adapter), input_schema=ASK_INPUT_SCHEMA,
                    annotations=PARALLEL_SAFE,
                ),
                types.Tool(
                    name="status", description=STATUS_DESCRIPTION, input_schema=STATUS_INPUT_SCHEMA, annotations=PARALLEL_SAFE
                ),
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
                    attachments=args.get("attachments"),
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
