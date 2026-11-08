"""러너 HTTP 서비스. 허브만 부른다(내부망, X-Hub-Token).

POST /runs            RunRequest → NDJSON 스트림. 줄마다 StreamLine {event} 또는 {tool_call}
POST /runs/{id}/stop  실행 중인 run 에 SIGTERM
GET  /healthz         토큰 없이 응답(컨테이너 헬스체크)

허브가 연결을 끊으면 스트림이 취소되면서 claude 프로세스도 종료된다.
"""

import hmac
import logging
import uuid
from collections import defaultdict
from collections.abc import AsyncIterator

from pydantic import ValidationError
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from runner.config import RunnerSettings, get_settings
from runner.shared import RunRequest, StreamLine, ToolCallRecord
from runner.spawn import ClaudeCodeRunner

log = logging.getLogger("runner.app")


def build_app(settings: RunnerSettings | None = None) -> Starlette:
    settings = settings or get_settings()
    # tool_call 기록은 run 별로 모았다가 이벤트 앞에 끼워 보낸다
    records: dict[uuid.UUID, list[ToolCallRecord]] = defaultdict(list)

    async def sink(rec: ToolCallRecord) -> None:
        records[rec.run_id].append(rec)

    runner = ClaudeCodeRunner(settings, on_tool_call=sink)

    def authorized(request: Request) -> bool:
        token = settings.read_token()
        got = request.headers.get("x-hub-token", "")
        return bool(token) and hmac.compare_digest(got.encode(), token.encode())

    async def start_run(request: Request) -> Response:
        if not authorized(request):
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        try:
            req = RunRequest.model_validate_json(await request.body())
        except ValidationError as e:
            return JSONResponse({"detail": e.errors(include_url=False, include_input=False)}, status_code=422)
        run_id = req.session.run_id

        def line(obj: StreamLine) -> bytes:
            return (obj.model_dump_json() + "\n").encode("utf-8")

        async def body() -> AsyncIterator[bytes]:
            try:
                async for ev in runner.start(req.session, req.text):
                    for rec in records.pop(run_id, []):
                        yield line(StreamLine(tool_call=rec))
                    yield line(StreamLine(event=ev))
                for rec in records.pop(run_id, []):
                    yield line(StreamLine(tool_call=rec))
            finally:
                records.pop(run_id, None)

        return StreamingResponse(body(), media_type="application/x-ndjson")

    async def stop_run(request: Request) -> Response:
        if not authorized(request):
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        try:
            run_id = uuid.UUID(request.path_params["run_id"])
        except ValueError:
            return JSONResponse({"detail": "bad_run_id"}, status_code=422)
        if await runner.stop(run_id):
            return JSONResponse({"stopped": True}, status_code=202)
        return JSONResponse({"detail": "run_not_found"}, status_code=404)

    async def healthz(_: Request) -> Response:
        return JSONResponse({"status": "ok"})

    app = Starlette(
        routes=[
            Route("/runs", start_run, methods=["POST"]),
            Route("/runs/{run_id}/stop", stop_run, methods=["POST"]),
            Route("/healthz", healthz, methods=["GET"]),
        ]
    )
    app.state.runner = runner
    return app
