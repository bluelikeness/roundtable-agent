"""관리 HTTP (03 5장). 허브 API 전용. 모든 경로가 X-Hub-Token 을 요구한다."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from adapters.auth import HubTokenGuard

if TYPE_CHECKING:
    from adapters.base import BotAdapter

log = logging.getLogger("adapters.admin")


async def _body(request: Request) -> dict[str, Any] | None:
    raw = await request.body()
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _bad(msg: str) -> JSONResponse:
    return JSONResponse({"detail": msg}, status_code=400)


def build_admin_app(adapter: "BotAdapter"):
    async def health(request: Request) -> JSONResponse:
        return JSONResponse(await adapter.health())

    async def probe(request: Request) -> JSONResponse:
        return JSONResponse(await adapter.probe())

    async def login_start(request: Request) -> JSONResponse:
        return JSONResponse(await adapter.login_start())

    async def login_submit(request: Request) -> JSONResponse:
        body = await _body(request)
        if body is None:
            return _bad("JSON 객체가 필요합니다")
        return JSONResponse(await adapter.login_submit(body))

    async def login_complete(request: Request) -> JSONResponse:
        return JSONResponse(await adapter.login_complete())

    async def reset(request: Request) -> JSONResponse:
        body = await _body(request)
        if body is None:
            return _bad("JSON 객체가 필요합니다")
        tid = body.get("thread_id")
        if tid is not None and not isinstance(tid, str):
            return _bad("thread_id 는 문자열이어야 합니다")
        await adapter.reset(tid)
        return JSONResponse({"ok": True})

    async def enable(request: Request) -> JSONResponse:
        adapter.enabled = True
        return JSONResponse({"enabled": True})

    async def disable(request: Request) -> JSONResponse:
        adapter.enabled = False
        return JSONResponse({"enabled": False})

    async def threads(request: Request) -> JSONResponse:
        return JSONResponse(adapter.list_threads())

    async def logs(request: Request) -> JSONResponse:
        return JSONResponse(adapter.list_logs(request.query_params.get("since")))

    async def image(request: Request) -> Response:
        found = adapter.load_image(request.path_params["image_id"])
        if found is None:
            return JSONResponse({"detail": "not found"}, status_code=404)
        data, ctype = found
        return Response(data, media_type=ctype)

    routes = [
        Route("/health", health, methods=["GET"]),
        Route("/probe", probe, methods=["POST"]),
        Route("/login/start", login_start, methods=["POST"]),
        Route("/login/submit", login_submit, methods=["POST"]),
        Route("/login/complete", login_complete, methods=["POST"]),
        Route("/reset", reset, methods=["POST"]),
        Route("/enable", enable, methods=["POST"]),
        Route("/disable", disable, methods=["POST"]),
        Route("/threads", threads, methods=["GET"]),
        Route("/logs", logs, methods=["GET"]),
        Route("/images/{image_id}", image, methods=["GET"]),
    ]
    return HubTokenGuard(Starlette(routes=routes), adapter.token)
