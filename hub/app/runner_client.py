"""허브 → 러너 호출 (docs/02-architecture.md 2·4장).

RemoteRunner 는 러너 컨테이너의 POST /runs NDJSON 스트림을 Runner 인터페이스로 감싼다.
러너에 닿지 못하거나 스트림이 run.finished 없이 끊기면 run.finished(failed) 를 대신 낸다.
"""

import logging
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import httpx
from pydantic import ValidationError

from app.events import HubEvent, RunFinished, RunFinishedPayload
from app.runner_protocol import RunRequest, Runner, RunnerSession, StreamLine, ToolCallSink

log = logging.getLogger("hub.runner_client")

# 러너 전체 타임아웃(600초)과 SIGTERM 유예보다 넉넉하게. 줄 사이 대기 상한이다
READ_TIMEOUT_S = 660


class RemoteRunner(Runner):
    def __init__(self, base_url: str, token: str, on_tool_call: ToolCallSink | None = None,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.on_tool_call = on_tool_call
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"X-Hub-Token": token},
            timeout=httpx.Timeout(10, read=READ_TIMEOUT_S),
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def ping(self) -> bool:
        """러너 컨테이너가 응답하는지(봇 상태 패널 Claude Code 섹션). Claude Code 로그인 여부는 모른다."""
        try:
            r = await self._client.get("/healthz", timeout=3)
        except httpx.HTTPError:
            return False
        return r.status_code == 200

    async def stop(self, run_id: uuid.UUID) -> bool:
        try:
            r = await self._client.post(f"/runs/{run_id}/stop")
        except httpx.HTTPError as e:
            log.warning("러너 중단 요청 실패: %s", e)
            return False
        return r.status_code == 202

    async def start(self, session: RunnerSession, text: str) -> AsyncIterator[HubEvent]:
        body = RunRequest(session=session, text=text).model_dump_json()
        finished = False
        error = "runner_unavailable"
        try:
            async with self._client.stream(
                "POST", "/runs", content=body, headers={"Content-Type": "application/json"}
            ) as r:
                if r.status_code != 200:
                    log.error("러너 응답 %s", r.status_code)
                    error = f"runner_http_{r.status_code}"
                else:
                    error = "runner_stream_ended"
                    async for raw in r.aiter_lines():
                        if not raw.strip():
                            continue
                        try:
                            line = StreamLine.model_validate_json(raw)
                        except ValidationError:
                            log.warning("러너 스트림에서 해석 못 한 줄을 건너뜀")
                            continue
                        if line.tool_call is not None and self.on_tool_call is not None:
                            await self.on_tool_call(line.tool_call)
                        if line.event is not None:
                            yield line.event
                            if line.event.type == "run.finished":
                                finished = True
                                return
        except httpx.HTTPError as e:
            log.error("러너 호출 실패: %s", type(e).__name__)
            error = "runner_unavailable" if isinstance(e, httpx.ConnectError) else "runner_stream_ended"
        if not finished:
            yield RunFinished(
                type="run.finished", session_id=session.session_id, seq=None, ts=datetime.now(UTC),
                payload=RunFinishedPayload(run_id=session.run_id, status="failed", error=error),
            )
