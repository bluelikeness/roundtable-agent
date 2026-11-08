"""X-Hub-Token 공유 토큰 검사 (03 5장). MCP·관리 포트 둘 다 같은 토큰을 요구한다."""

from __future__ import annotations

import hmac
import json

from starlette.types import ASGIApp, Receive, Scope, Send


class HubTokenGuard:
    """HTTP 요청마다 X-Hub-Token 을 비교하고 틀리거나 없으면 401. lifespan 은 그대로 통과."""

    def __init__(self, app: ASGIApp, token: str):
        if not token:
            raise ValueError("빈 토큰으로는 기동하지 않습니다")
        self.app = app
        self._token = token.encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            got = b""
            for k, v in scope.get("headers", []):
                if k == b"x-hub-token":
                    got = v
                    break
            if not hmac.compare_digest(got, self._token):
                body = json.dumps({"detail": "unauthorized"}).encode()
                await send(
                    {
                        "type": "http.response.start",
                        "status": 401,
                        "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
                    }
                )
                await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)
