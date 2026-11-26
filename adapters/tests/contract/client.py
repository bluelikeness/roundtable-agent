"""계약 테스트용 MCP 클라이언트와 스키마. 스키마는 구현(pydantic)이 아니라 문서(03 2·3장)에서 옮겼다."""

from __future__ import annotations

from typing import Any

import httpx2
import jsonschema
from mcp.client.client import Client
from mcp.client.streamable_http import streamable_http_client

from adapters.tests.contract.conftest import TOKEN, Target

STRUCTURED_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "buttons": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["label", "action_id", "kind"],
                "additionalProperties": False,
                "properties": {
                    "label": {"type": "string"},
                    "action_id": {"type": "string"},
                    "kind": {"enum": ["callback", "url", "reply"]},
                },
            },
        },
        "links": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["title", "url"],
                "additionalProperties": False,
                "properties": {"title": {"type": "string"}, "url": {"type": "string"}},
            },
        },
        "cards": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["title"],
                "additionalProperties": False,
                "properties": {k: {"type": "string"} for k in ["title", "subtitle", "body", "url", "image_url"]},
            },
        },
        "images": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["url"],
                "additionalProperties": False,
                "properties": {"url": {"type": "string"}, "alt": {"type": "string"}},
            },
        },
        "files": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "name", "mime", "size"],
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string", "pattern": "^f_[A-Za-z0-9_-]{16,64}$"},
                    "name": {"type": "string"},
                    "mime": {"type": "string"},
                    "size": {"type": "integer", "minimum": 0},
                },
            },
        },
        "choices": {"type": "array", "items": {"type": "string"}},
        "confirmation_required": {
            "type": "object",
            "required": ["summary", "action_id"],
            "additionalProperties": False,
            "properties": {"summary": {"type": "string"}, "action_id": {"type": "string"}},
        },
    },
}

ASK_RESULT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["bot", "ok", "raw", "structured", "needs_input", "error", "latency_ms", "message_ids"],
    "properties": {
        "bot": {"type": "string"},
        "ok": {"type": "boolean"},
        "raw": {"type": "string"},
        "structured": STRUCTURED_SCHEMA,
        "needs_input": {"type": "boolean"},
        "error": {"enum": [None, "timeout", "unavailable", "login_required", "rate_limited", "internal", "unsupported"]},
        "latency_ms": {"type": "integer", "minimum": 0},
        "message_ids": {"type": "array", "items": {"type": "string"}},
    },
}

STATUS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["available", "reason", "last_ok_at", "queue_depth", "avg_latency_ms"],
    "properties": {
        "available": {"type": "boolean"},
        "reason": {"enum": [None, "login_required", "disabled", "backend_down", "cooldown"]},
        "last_ok_at": {"type": ["string", "null"]},
        "queue_depth": {"type": "integer", "minimum": 0},
        "avg_latency_ms": {"type": "integer", "minimum": 0},
    },
}

ASK_INPUT_SCHEMA = {
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


def _http(token: str | None = TOKEN) -> httpx2.AsyncClient:
    headers = {"X-Hub-Token": token} if token else {}
    return httpx2.AsyncClient(headers=headers, timeout=httpx2.Timeout(30, read=300))


async def list_tools(t: Target) -> list[Any]:
    async with _http() as http, Client(streamable_http_client(t.mcp_url, http_client=http)) as c:
        return (await c.list_tools()).tools


async def call(t: Target, tool: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    """툴을 부르고 structured 결과를 돌려준다. MCP 레벨 예외·is_error 는 계약 위반이라 그대로 실패시킨다."""
    async with _http() as http, Client(streamable_http_client(t.mcp_url, http_client=http)) as c:
        r = await c.call_tool(tool, args or {})
    assert not r.is_error, r
    data = r.structured_content
    jsonschema.validate(data, ASK_RESULT_SCHEMA if tool == "ask" else STATUS_SCHEMA)
    return data


async def ask(t: Target, text: str, thread_id: str, **kw: Any) -> dict[str, Any]:
    return await call(t, "ask", {"text": text, "thread_id": thread_id, **kw})


async def status(t: Target) -> dict[str, Any]:
    return await call(t, "status")
