"""RemoteRunner: 러너 NDJSON 스트림 → HubEvent, tool_call 기록 전달, 실패 시 run.finished(failed)."""

import uuid
from datetime import UTC, datetime

import httpx

from app.events import BotPending, BotPendingPayload, RunFinished, RunFinishedPayload, RunStarted, RunStartedPayload
from app.runner_client import RemoteRunner
from app.runner_protocol import RunnerSession, StreamLine, ToolCallRecord

SID = uuid.uuid4()
RUN = uuid.uuid4()
NOW = datetime(2026, 11, 8, tzinfo=UTC)


def _session() -> RunnerSession:
    return RunnerSession(session_id=SID, run_id=RUN)


def _lines() -> str:
    items = [
        StreamLine(event=RunStarted(type="run.started", session_id=SID, seq=None, ts=NOW,
                                    payload=RunStartedPayload(run_id=RUN, claude_session_id="cs"))),
        StreamLine(tool_call=ToolCallRecord(id="toolu_1", run_id=RUN, bot="mock-a", tool="ask", input={"text": "x"})),
        StreamLine(event=BotPending(type="bot.pending", session_id=SID, seq=None, ts=NOW,
                                    payload=BotPendingPayload(run_id=RUN, bot="mock-a", tool_call_id="toolu_1", queue_position=0))),
        StreamLine(event=RunFinished(type="run.finished", session_id=SID, seq=None, ts=NOW,
                                     payload=RunFinishedPayload(run_id=RUN, status="done"))),
    ]
    return "".join(i.model_dump_json() + "\n" for i in items) + "garbage\n"


async def test_stream_events_and_records():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["token"] = request.headers.get("x-hub-token")
        seen["body"] = request.content
        return httpx.Response(200, text=_lines(), headers={"content-type": "application/x-ndjson"})

    records = []

    async def sink(r):
        records.append(r)

    runner = RemoteRunner("http://runner", "tok", on_tool_call=sink, transport=httpx.MockTransport(handler))
    events = [e async for e in runner.start(_session(), "안녕")]
    await runner.aclose()
    assert [e.type for e in events] == ["run.started", "bot.pending", "run.finished"]
    assert [r.id for r in records] == ["toolu_1"]
    assert seen["token"] == "tok"
    assert b'"text":"\xec\x95\x88\xeb\x85\x95"' in seen["body"]


async def test_connect_error_yields_failed():
    def handler(request):
        raise httpx.ConnectError("down")

    runner = RemoteRunner("http://runner", "tok", transport=httpx.MockTransport(handler))
    events = [e async for e in runner.start(_session(), "x")]
    assert len(events) == 1 and events[0].type == "run.finished"
    assert (events[0].payload.status, events[0].payload.error) == ("failed", "runner_unavailable")
    assert events[0].payload.run_id == RUN


async def test_stream_without_finish_yields_failed():
    def handler(request):
        return httpx.Response(200, text="")

    runner = RemoteRunner("http://runner", "tok", transport=httpx.MockTransport(handler))
    events = [e async for e in runner.start(_session(), "x")]
    assert events[-1].payload.error == "runner_stream_ended"


async def test_http_error_status():
    runner = RemoteRunner("http://runner", "tok", transport=httpx.MockTransport(lambda r: httpx.Response(401)))
    events = [e async for e in runner.start(_session(), "x")]
    assert events[-1].payload.error == "runner_http_401"
    assert not await runner.stop(RUN)
