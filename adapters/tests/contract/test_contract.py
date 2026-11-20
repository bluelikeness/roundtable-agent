"""어댑터 계약 테스트 (docs/03-adapters.md 11장 표). 모든 어댑터 공통.

Mock 은 응답에 2~8초가 걸리므로 한 대상에 1~2분 걸린다. 대상끼리는 xdist 로 병렬.
"""

from __future__ import annotations

import asyncio
import time
import uuid

import httpx
import pytest

from adapters.tests.contract.client import ASK_INPUT_SCHEMA, ask, call, list_tools, status
from adapters.tests.contract.conftest import SINK_URL, TOKEN, Target


def tid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


async def last_log(t: Target, thread_id: str) -> dict:
    async with t.admin() as c:
        logs = (await c.get("/logs")).json()
    mine = [r for r in logs if r["thread_id"] == thread_id]
    assert mine, f"{thread_id} 의 ask 기록이 없습니다"
    return mine[-1]


async def thread_info(t: Target, thread_id: str) -> dict | None:
    async with t.admin() as c:
        return next((x for x in (await c.get("/threads")).json() if x["thread_id"] == thread_id), None)


async def test_tools_are_exactly_ask_and_status(target: Target):
    tools = {tool.name: tool for tool in await list_tools(target)}
    assert set(tools) == {"ask", "status"}
    assert tools["ask"].input_schema == ASK_INPUT_SCHEMA
    assert tools["ask"].description  # 봇 설명이 Claude Code 가 읽는 유일한 단서


async def test_ask_ok(target: Target):
    r = await ask(target, target.driver.normal_text("날씨 알려줘"), tid("ok"))
    assert r["ok"] is True
    assert r["error"] is None
    assert r["raw"].strip()
    assert r["latency_ms"] > 0
    assert r["bot"] == target.name


async def test_ask_timeout_retries_once_then_fails(target: Target):
    """에뮬레이터·웹: 절반 시점에 한 번 다시 보낸다. 휴대폰: 보낸 질문이 화면에 있으면 다시 보내지 않는다."""
    thread = tid("timeout")
    t0 = time.monotonic()
    r = await ask(target, target.driver.slow_text("천천히 답해"), thread, timeout_s=10)
    elapsed = time.monotonic() - t0
    assert r["ok"] is False
    assert r["error"] == "timeout"
    assert r["raw"] == ""
    # ask 한 번은 큐·재시도를 포함해 timeout_s 를 넘지 않는다 (HTTP 왕복 여유 1.5초)
    assert elapsed < 11.5
    assert r["latency_ms"] <= 10_500
    # 휴대폰 경로: 질문은 갔고 답만 없다 → 다시 보내지 않는다(질문이 없을 때만 재전송, 03 4장)
    phone = target.name.endswith("-phone")
    assert bool((await last_log(target, thread)).get("retried")) is not phone


async def test_ask_login_expired(target: Target):
    async with httpx.AsyncClient(base_url=SINK_URL) as sink:
        await sink.delete("/calls")
        target.driver.expire_login()
        try:
            r = await ask(target, target.driver.normal_text("로그인 만료 확인"), tid("login"))
            assert r["ok"] is False
            assert r["error"] == "login_required"
            st = await status(target)
            assert st["available"] is False
            assert st["reason"] == "login_required"

            # 웹훅은 기다리지 않고 보내므로 잠깐 폴링한다
            calls = []
            for _ in range(20):
                calls = [
                    c
                    for c in (await sink.get("/calls")).json()
                    if c["body"]["bot"] == target.name and c["body"]["reason"] == "login_required"
                ]
                if calls:
                    break
                await asyncio.sleep(0.25)
            assert calls, "허브 웹훅이 호출되지 않았습니다"
            assert calls[0]["token_ok"] is True
            assert calls[0]["body"]["available"] is False
        finally:
            # 관리 HTTP 로그인 흐름으로 복구(휴대폰 경로는 사람이 휴대폰에서 다시 로그인하는 단계가 먼저)
            target.driver.restore_login()
            async with target.admin() as c:
                started = (await c.post("/login/start", json={})).json()
                assert started["flow"] in ("code", "vnc")
                done = (await c.post("/login/complete", json={})).json()
                assert done == {"ok": True}
    st = await status(target)
    assert st["available"] is True and st["reason"] is None


async def test_concurrent_asks_are_serial_and_ordered(target: Target):
    thread = tid("queue")
    texts = [target.driver.normal_text(f"순서 {i}") for i in range(3)]
    finished: list[int] = []
    depths: list[int] = []

    async def one(i: int) -> dict:
        r = await ask(target, texts[i], thread)
        finished.append(i)
        return r

    async def watch(stop: asyncio.Event) -> None:
        while not stop.is_set():
            depths.append((await status(target))["queue_depth"])
            await asyncio.sleep(0.2)

    stop = asyncio.Event()
    watcher = asyncio.create_task(watch(stop))
    tasks = []
    for i in range(3):
        tasks.append(asyncio.create_task(one(i)))
        await asyncio.sleep(0.3)  # 도착 순서를 확정한다
    results = await asyncio.gather(*tasks)
    stop.set()
    await watcher

    assert all(r["ok"] for r in results), results
    assert finished == [0, 1, 2]
    assert max(depths) == 2
    for i, r in enumerate(results):
        assert f"순서 {i}" in r["raw"]
    async with target.admin() as c:
        logged = [r["text"] for r in (await c.get("/logs")).json() if r["thread_id"] == thread]
    assert logged == texts


async def test_threads_do_not_mix(target: Target):
    ta, tb = tid("ta"), tid("tb")
    a1 = await ask(target, target.driver.normal_text("사과 하나"), ta)
    b1 = await ask(target, target.driver.normal_text("바나나 하나"), tb)
    a2 = await ask(target, target.driver.normal_text("사과 둘"), ta)
    assert all(r["ok"] for r in (a1, b1, a2))
    if target.driver.shared_thread:
        pytest.skip("봇 쪽 대화방이 하나인 어댑터: 문서화된 공유 동작")
    assert "사과 하나" in a1["raw"] and "바나나" not in a1["raw"]
    assert "바나나 하나" in b1["raw"] and "사과" not in b1["raw"]
    assert "사과 둘" in a2["raw"] and "바나나" not in a2["raw"]
    assert not set(a1["message_ids"]) & set(b1["message_ids"])
    ia, ib = await thread_info(target, ta), await thread_info(target, tb)
    assert ia["message_count"] > ib["message_count"] > 0


async def test_tap_button(target: Target):
    thread = tid("tap")
    r = await ask(target, target.driver.buttons_text(), thread)
    assert r["ok"], r
    buttons = r["structured"].get("buttons") or []
    assert buttons, r
    assert r["needs_input"] is True
    # 원문 보존: 버튼 라벨이 raw 에도 [버튼: X] 로 남는다
    assert f"[버튼: {buttons[0]['label']}]" in r["raw"]

    tapped = await ask(target, f"[tap:{buttons[0]['action_id']}]", thread)
    assert tapped["ok"] is True, tapped
    assert buttons[0]["label"] in tapped["raw"]

    bad = await ask(target, "[tap:btn_does_not_exist]", thread)
    assert bad["ok"] is False
    assert bad["error"] == "internal"
    assert "action_id" in (await last_log(target, thread))["detail"]


async def test_status_changes(target: Target):
    before = await status(target)
    async with target.admin() as c:
        await asyncio.sleep(0.01)
        probed = (await c.post("/probe")).json()
        assert probed["available"] is True
        assert probed["last_ok_at"] > (before["last_ok_at"] or "")

        assert (await c.post("/disable", json={})).json() == {"enabled": False}
        st = await status(target)
        assert st["available"] is False and st["reason"] == "disabled"
        r = await ask(target, target.driver.normal_text("꺼져 있니"), tid("off"))
        assert r["ok"] is False and r["error"] == "unavailable"
        assert (await c.post("/enable", json={})).json() == {"enabled": True}

    r = await ask(target, target.driver.normal_text("평균 지연 갱신"), tid("avg"))
    assert r["ok"]
    after = await status(target)
    assert after["available"] is True
    assert after["avg_latency_ms"] > 0
    assert after["last_ok_at"] >= probed["last_ok_at"]


ADMIN_ROUTES = [
    ("GET", "/health"),
    ("POST", "/probe"),
    ("POST", "/login/start"),
    ("POST", "/login/submit"),
    ("POST", "/login/complete"),
    ("POST", "/reset"),
    ("POST", "/enable"),
    ("POST", "/disable"),
    ("GET", "/threads"),
    ("GET", "/logs"),
    ("GET", "/images/0123456789abcdef"),
]


@pytest.mark.parametrize("token", [None, "wrong-token"], ids=["missing", "wrong"])
async def test_requests_without_valid_token_are_401(target: Target, token):
    async with target.admin(token=token) as c:
        for method, path in ADMIN_ROUTES:
            r = await c.request(method, path, json={} if method == "POST" else None)
            assert r.status_code == 401, (method, path, r.status_code)
    headers = {"Accept": "application/json, text/event-stream"}
    if token:
        headers["X-Hub-Token"] = token
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.post(
            target.mcp_url,
            headers=headers,
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        )
    assert r.status_code == 401
    # 상태가 바뀌지 않았는지(disable 이 먹지 않았는지)
    assert (await status(target))["reason"] != "disabled"


async def test_restart_restores_threads(target: Target):
    thread = tid("restart")
    r = await ask(target, target.driver.normal_text("재시작 전"), thread)
    assert r["ok"]
    count_before = (await thread_info(target, thread))["message_count"]

    target.driver.restart()
    target.wait_healthy()

    st = await status(target)
    assert st["available"] is True
    r2 = await ask(target, target.driver.normal_text("재시작 후"), thread)
    assert r2["ok"], r2
    assert not set(r["message_ids"]) & set(r2["message_ids"])
    assert (await thread_info(target, thread))["message_count"] > count_before


async def test_call_never_raises_on_bad_input(target: Target):
    """스키마를 어긴 입력도 MCP 예외가 아니라 AskResult(error=internal)."""
    for args in (
        {"text": "x" * 4001, "thread_id": tid("bad")},
        {"text": "hi", "thread_id": tid("bad"), "timeout_s": 5},
        {"text": "", "thread_id": tid("bad")},
        {"text": "hi"},
    ):
        r = await call(target, "ask", args)
        assert r["ok"] is False and r["error"] == "internal", args
