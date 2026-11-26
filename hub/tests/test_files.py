"""허브 파일 보관함과 첨부 보내기(docs/03-adapters.md 2장 첨부, docs/05-ui.md 6장 send.attachments)."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import pytest
from httpx_ws import aconnect_ws
from sqlalchemy import update

from app.config import get_settings
from app.db import get_sessionmaker
from app.files import describe_for_claude, purge_old
from app.main import app
from app.models import HubFile, Message, MessageRole
from tests.test_ws import FakeRunner, _login, _recv_until, _session

TOKEN = "files-test-adapter-token"
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 64


@pytest.fixture
def settings():
    s = get_settings().model_copy(update={"adapter_token": TOKEN, "file_max_mb": 1})
    app.dependency_overrides[get_settings] = lambda: s
    yield s
    app.dependency_overrides.pop(get_settings, None)


async def _upload(c, data=PNG, name="사진 1.png", mime="image/png"):
    return await c.post("/api/files", content=data, headers={"Content-Type": mime, "X-File-Name": quote(name)})


async def test_upload_and_download(authed, settings):
    r = await _upload(authed)
    assert r.status_code == 201, r.text
    f = r.json()
    assert f["id"].startswith("f_") and f["name"] == "사진 1.png" and f["mime"] == "image/png" and f["size"] == len(PNG)
    assert set(f) == {"id", "name", "mime", "size"}
    assert (Path(settings.files_dir) / f["id"]).read_bytes() == PNG

    g = await authed.get(f"/api/files/{f['id']}")
    assert g.status_code == 200 and g.content == PNG
    assert g.headers["content-type"] == "image/png"
    assert g.headers["content-disposition"].startswith("inline")
    assert g.headers["x-content-type-options"] == "nosniff"
    # 영상 앞부분만(재생 위치 옮기기)
    part = await authed.get(f"/api/files/{f['id']}", headers={"Range": "bytes=0-3"})
    assert part.status_code == 206 and part.content == PNG[:4]


async def test_upload_rules(client, authed, settings):
    # 로그인 없이는 못 올리고 못 본다
    authed.cookies.clear()
    assert (await _upload(client)).status_code == 401
    assert (await client.get("/api/files/f_aaaaaaaaaaaaaaaaaaaa")).status_code == 401


async def test_limits_and_unsafe_types(authed, settings):
    assert (await _upload(authed, data=b"")).status_code == 400
    big = b"x" * (settings.file_max_mb * 1024 * 1024 + 1)
    assert (await _upload(authed, data=big)).status_code == 413
    assert list(Path(settings.files_dir).glob("*.part")) == []
    # html 은 바로 열지 않고 내려받기로, 이름의 경로는 버린다
    r = await _upload(authed, data=b"<script>alert(1)</script>", name="../../etc/x.html", mime="text/html")
    f = r.json()
    assert f["name"] == "x.html"
    g = await authed.get(f"/api/files/{f['id']}")
    assert g.headers["content-disposition"].startswith("attachment")
    assert "sandbox" in g.headers["content-security-policy"]
    # 모양이 틀린 MIME 은 octet-stream
    r = await _upload(authed, data=b"abc", name="a.bin", mime="not a mime")
    assert r.json()["mime"] == "application/octet-stream"
    assert (await authed.get("/api/files/../../etc/passwd")).status_code == 404
    assert (await authed.get("/api/files/f_doesnotexist000000000")).status_code == 404


async def test_internal_upload_and_download_need_adapter_token(client, settings):
    h = {"Content-Type": "video/mp4", "X-File-Name": "clip.mp4", "X-Bot": "Pickle"}
    assert (await client.post("/internal/files", content=b"mp4", headers=h)).status_code == 401
    r = await client.post("/internal/files", content=b"mp4data", headers={**h, "X-Hub-Token": TOKEN})
    assert r.status_code == 201, r.text
    f = r.json()
    async with get_sessionmaker()() as db:
        assert (await db.get(HubFile, f["id"])).source == "bot:pickle"
    assert (await client.get(f"/internal/files/{f['id']}")).status_code == 401
    g = await client.get(f"/internal/files/{f['id']}", headers={"X-Hub-Token": TOKEN})
    assert g.status_code == 200 and g.content == b"mp4data"


async def test_purge_old_files(authed, settings):
    old = (await _upload(authed)).json()["id"]
    new = (await _upload(authed)).json()["id"]
    async with get_sessionmaker()() as db:
        await db.execute(
            update(HubFile).where(HubFile.id == old).values(created_at=datetime.now(UTC) - timedelta(days=31))
        )
        await db.commit()
    assert await purge_old(get_sessionmaker(), settings) == 1
    assert (await authed.get(f"/api/files/{old}")).status_code == 404
    assert (await authed.get(f"/api/files/{new}")).status_code == 200
    assert not (Path(settings.files_dir) / old).exists()


class RecordingRunner(FakeRunner):
    def __init__(self):
        super().__init__()
        self.texts: list[str] = []

    async def start(self, session, text):
        self.texts.append(text)
        async for ev in super().start(session, text):
            yield ev


@pytest.fixture
async def recording_runner(operator, settings):
    from app.runs import RunManager

    runner = RecordingRunner()
    manager = RunManager(get_sessionmaker(), runner, settings)
    app.state.run_manager = manager
    yield runner
    await manager.shutdown()
    del app.state.run_manager


async def test_send_with_attachments_reaches_claude(recording_runner, operator):
    from httpx_ws.transport import ASGIWebSocketTransport
    import httpx

    async with httpx.AsyncClient(transport=ASGIWebSocketTransport(app), base_url="http://hub.test") as c:
        await _login(c, operator)
        sid = await _session(c)
        f = (await _upload(c)).json()
        async with aconnect_ws("http://hub.test/ws", c) as ws:
            await ws.send_json({"type": "subscribe", "payload": {"session_id": sid, "since_seq": 0}})
            # 글 없이 첨부만 보내도 된다
            await ws.send_json({"type": "send", "session_id": sid, "payload": {"text": "", "attachments": [f["id"]]}})
            await _recv_until(ws, "run.finished")
            await ws.send_json({
                "type": "send", "session_id": sid,
                "payload": {"text": "이걸로 영상 만들어 줘", "attachments": [f["id"], f["id"]]},
            })
            await _recv_until(ws, "run.finished")
            # 없는 파일·첨부 6개는 거절(보내지지 않음)
            await ws.send_json({
                "type": "send", "session_id": sid, "payload": {"text": "x", "attachments": ["f_nope0000000000000000"]},
            })
            await ws.send_json({"type": "send", "session_id": sid, "payload": {"text": "", "attachments": []}})

        msgs = (await c.get(f"/api/sessions/{sid}/messages")).json()
    users = [m for m in msgs if m["role"] == MessageRole.user.value]
    assert len(users) == 2
    assert users[0]["structured"] == {"attachments": [f]}
    assert users[1]["raw"] == "이걸로 영상 만들어 줘" and users[1]["structured"] == {"attachments": [f]}
    # 러너에는 글 끝에 ID 목록이 붙어 간다
    block = describe_for_claude([f])
    assert recording_runner.texts == [block, "이걸로 영상 만들어 줘\n\n" + block]
    assert f["id"] in block and "attachments" in block
    assert Message  # noqa: B018
