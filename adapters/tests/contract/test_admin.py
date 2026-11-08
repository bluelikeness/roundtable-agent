"""관리 HTTP 동작 (docs/03-adapters.md 5장). 인증 401 은 test_contract.py 에서 본다."""

from __future__ import annotations

import uuid

import pytest

from adapters.tests.contract.client import ask, status
from adapters.tests.contract.conftest import Target


async def test_health_shape(target: Target):
    async with target.admin() as c:
        h = (await c.get("/health")).json()
    st = await status(target)
    assert set(h) == set(st) | {"version", "uptime_s"}
    assert isinstance(h["uptime_s"], int) and h["version"]


async def test_reset_threads_and_logs(target: Target):
    thread = f"reset-{uuid.uuid4().hex[:8]}"
    r = await ask(target, target.driver.normal_text("리셋 전"), thread)
    assert r["ok"]
    async with target.admin() as c:
        threads = (await c.get("/threads")).json()
        mine = [x for x in threads if x["thread_id"] == thread]
        assert mine and set(mine[0]) == {"thread_id", "last_at", "message_count"}

        logs = (await c.get("/logs")).json()
        last_at = logs[-1]["at"]
        assert (await c.get("/logs", params={"since": last_at})).json() == []
        assert any(x["thread_id"] == thread for x in (await c.get("/logs", params={"since": "2000-01-01"})).json())

        assert (await c.post("/reset", json={"thread_id": thread})).json() == {"ok": True}
        assert all(x["thread_id"] != thread for x in (await c.get("/threads")).json())
        assert (await c.post("/reset", json={"thread_id": 3})).status_code == 400


async def test_reset_flag_starts_new_conversation(target: Target):
    thread = f"rflag-{uuid.uuid4().hex[:8]}"
    await ask(target, target.driver.normal_text("첫 대화"), thread)
    r = await ask(target, target.driver.normal_text("새 대화"), thread, reset=True)
    assert r["ok"]
    async with target.admin() as c:
        mine = [x for x in (await c.get("/threads")).json() if x["thread_id"] == thread]
    # reset 뒤에는 이번 왕복만 남는다(사용자 1 + 봇 1)
    assert mine[0]["message_count"] == 2


async def test_login_code_flow(target: Target):
    async with target.admin() as c:
        started = (await c.post("/login/start", json={})).json()
        if started["flow"] != "code":
            pytest.skip("코드 입력형 로그인이 아닌 어댑터")
        assert (await c.post("/login/submit", json={})).json()["ok"] is False
        assert (await c.post("/login/submit", json={"code": "12345"})).json() == {"ok": True}
        assert (await c.post("/login/submit", content=b"not json")).status_code == 400


async def test_image_endpoint(target: Target):
    text = target.driver.image_text()
    if text is None:
        pytest.skip("이미지를 유도할 수 없는 어댑터")
    r = await ask(target, text, f"img-{uuid.uuid4().hex[:8]}")
    assert r["ok"], r
    url = r["structured"]["images"][0]["url"]
    assert url.startswith("/images/")
    async with target.admin() as c:
        got = await c.get(url)
        assert got.status_code == 200
        assert got.headers["content-type"] == "image/png"
        assert got.content.startswith(b"\x89PNG")
        assert (await c.get("/images/ffffffffffffffff")).status_code == 404
        assert (await c.get("/images/..%2F..%2Fthreads.json")).status_code == 404


async def test_confirmation_is_not_retried(target: Target):
    thread = f"conf-{uuid.uuid4().hex[:8]}"
    r = await ask(target, target.driver.confirm_text(), thread)
    assert r["ok"], r
    conf = r["structured"]["confirmation_required"]
    assert conf["summary"] and conf["action_id"]
    # 확정 단계의 다음 메시지는 응답이 없어도 재전송하지 않는다
    r2 = await ask(target, target.driver.slow_text("확정"), thread, timeout_s=10)
    assert r2["ok"] is False and r2["error"] == "timeout"
    async with target.admin() as c:
        last = [x for x in (await c.get("/logs")).json() if x["thread_id"] == thread][-1]
    assert not last.get("retried")
