"""러너 HTTP 서비스: 토큰, NDJSON 스트림(이벤트 + tool_call 기록), 중단."""

import httpx
import pytest

from runner.app import build_app
from runner.shared import RunRequest, StreamLine
from tests.conftest import FIXTURES, TOKEN, make_session


@pytest.fixture
def app(settings):
    return build_app(settings)


@pytest.fixture
async def client(app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://runner.test") as c:
        yield c


def _body(**kw) -> str:
    return RunRequest(session=make_session(**kw), text="날씨").model_dump_json()


async def test_token_required(client):
    r = await client.post("/runs", content=_body())
    assert r.status_code == 401
    r = await client.post("/runs", content=_body(), headers={"X-Hub-Token": "wrong"})
    assert r.status_code == 401
    r = await client.post("/runs/00000000-0000-0000-0000-000000000000/stop")
    assert r.status_code == 401
    assert (await client.get("/healthz")).status_code == 200


async def test_bad_body_422(client):
    r = await client.post("/runs", content="{}", headers={"X-Hub-Token": TOKEN})
    assert r.status_code == 422


async def test_stream_lines(client, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_SCRIPT", str(FIXTURES / "fanout.jsonl"))
    r = await client.post("/runs", content=_body(), headers={"X-Hub-Token": TOKEN})
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/x-ndjson")
    lines = [StreamLine.model_validate_json(x) for x in r.text.splitlines()]
    kinds = ["rec" if x.tool_call else x.event.type for x in lines]
    assert kinds[0] == "run.started" and kinds[-1] == "run.finished"
    assert kinds.count("rec") == 6
    # tool_call 시작 기록이 bot.pending 보다 먼저 온다
    assert kinds.index("rec") < kinds.index("bot.pending")


async def test_stop_unknown_run_404(client):
    r = await client.post("/runs/00000000-0000-0000-0000-000000000000/stop", headers={"X-Hub-Token": TOKEN})
    assert r.status_code == 404
