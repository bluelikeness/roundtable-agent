"""계약 테스트 전용 허브 웹훅 수신기. POST /internal/bot-status 를 기록하고 GET /calls 로 보여 준다.

허브의 실제 웹훅 대신 테스트 compose 오버레이(tests/contract/compose.yml)에서만 띄운다.
"""

import hmac
import os

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from adapters.base import read_token

TOKEN = read_token()
CALLS: list[dict] = []


async def bot_status(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except ValueError:
        body = None
    token_ok = hmac.compare_digest(request.headers.get("x-hub-token", "").encode(), TOKEN.encode())
    CALLS.append({"body": body, "token_ok": token_ok})
    return JSONResponse({"ok": True})


async def calls(request: Request) -> JSONResponse:
    if request.method == "DELETE":
        CALLS.clear()
    return JSONResponse(CALLS)


app = Starlette(
    routes=[
        Route("/internal/bot-status", bot_status, methods=["POST"]),
        Route("/calls", calls, methods=["GET", "DELETE"]),
    ]
)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
