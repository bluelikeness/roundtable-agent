"""계약 테스트 전용 허브 웹훅 수신기. POST /internal/bot-status 를 기록하고 GET /calls 로 보여 준다.

허브 파일 보관함(/internal/files)의 메모리 대역도 둔다: 계약 테스트가 첨부를 올리고(POST),
어댑터가 받아 가고(GET), 봇이 만든 파일을 올린다(POST). GET /files 로 올라온 목록을 본다.

허브의 실제 웹훅 대신 테스트 compose 오버레이(tests/contract/compose.yml)에서만 띄운다.
"""

import hmac
import os
import secrets
from urllib.parse import quote, unquote

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from adapters.base import read_token

TOKEN = read_token()
CALLS: list[dict] = []
FILES: dict[str, dict] = {}


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


def _token_ok(request: Request) -> bool:
    return hmac.compare_digest(request.headers.get("x-hub-token", "").encode(), TOKEN.encode())


async def files_post(request: Request) -> Response:
    if not _token_ok(request):
        return JSONResponse({"detail": "bad_token"}, status_code=401)
    data = await request.body()
    fid = "f_" + secrets.token_urlsafe(16)
    meta = {
        "id": fid,
        "name": unquote(request.headers.get("x-file-name") or "file"),
        "mime": (request.headers.get("content-type") or "application/octet-stream").split(";")[0],
        "size": len(data),
    }
    FILES[fid] = {**meta, "data": data, "bot": request.headers.get("x-bot")}
    return JSONResponse(meta, status_code=201)


async def files_get(request: Request) -> Response:
    if not _token_ok(request):
        return JSONResponse({"detail": "bad_token"}, status_code=401)
    f = FILES.get(request.path_params["fid"])
    if f is None:
        return JSONResponse({"detail": "file_not_found"}, status_code=404)
    cd = f"attachment; filename*=utf-8''{quote(f['name'])}"
    return Response(f["data"], media_type=f["mime"], headers={"Content-Disposition": cd})


async def files_list(request: Request) -> JSONResponse:
    return JSONResponse([{k: v for k, v in f.items() if k != "data"} for f in FILES.values()])


app = Starlette(
    routes=[
        Route("/internal/bot-status", bot_status, methods=["POST"]),
        Route("/calls", calls, methods=["GET", "DELETE"]),
        Route("/internal/files", files_post, methods=["POST"]),
        Route("/internal/files/{fid}", files_get, methods=["GET"]),
        Route("/files", files_list, methods=["GET"]),
    ]
)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
