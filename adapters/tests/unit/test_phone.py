"""휴대폰 워커 경로 단위 테스트 (docs/proposals/phone-worker.md).

게이트웨이를 프로세스 안 uvicorn 으로 띄우고, 가짜 휴대폰(adapters.testing.fake_phone)이 실제
WebSocket 으로 붙는다. 어댑터는 PhoneDevice 로 게이트웨이를 거쳐 가짜 Vooy 화면을 조종한다.
"""

from __future__ import annotations

import asyncio
import socket

import httpx
import pytest

pytest.importorskip("PIL", reason="android 의존성 그룹 필요: uv run --group android pytest")

import uvicorn  # noqa: E402

from adapters.base import AdapterConfig  # noqa: E402
from adapters.phone.device import PhoneDevice  # noqa: E402
from adapters.phone.gateway import Gateway  # noqa: E402
from adapters.testing.fake_phone import FakePhone  # noqa: E402
from adapters.testing.fake_vooy import FakeVooyDevice  # noqa: E402
from adapters.vooy.adapter import VooyAdapter  # noqa: E402

HUB = "hub-token-for-tests"
DEV = "device-token-for-tests"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
async def stack(tmp_path, monkeypatch):
    """게이트웨이 + 가짜 휴대폰. (base_url, gateway, phone) 을 돌려준다."""
    monkeypatch.setenv("FAKE_VOOY_DIR", str(tmp_path / "fake"))
    monkeypatch.setenv("FAKE_VOOY_LATENCY", "0.3")
    port = _free_port()
    gw = Gateway(HUB, DEV, ["com.vooy.mobile"])
    server = uvicorn.Server(uvicorn.Config(gw.app(), host="127.0.0.1", port=port, log_level="warning", ws="websockets-sansio"))
    srv_task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.02)
    phone = FakePhone(f"ws://127.0.0.1:{port}/phone/ws", DEV, FakeVooyDevice(), locked_file=str(tmp_path / "locked"))
    ph_task = asyncio.create_task(phone.run_forever())
    await asyncio.wait_for(phone.connected.wait(), 5)
    # watch 명령이 끝날 때까지
    for _ in range(100):
        if phone.watch:
            break
        await asyncio.sleep(0.02)
    yield f"http://127.0.0.1:{port}", gw, phone
    ph_task.cancel()
    server.should_exit = True
    await srv_task


def _client(base: str, token: str | None = HUB) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=base, headers={"X-Hub-Token": token} if token else {}, timeout=10)


async def test_internal_api_needs_hub_token(stack):
    base, _, _ = stack
    async with _client(base, None) as c:
        assert (await c.get("/state")).status_code == 401
    async with _client(base) as c:
        st = (await c.get("/state")).json()
    assert st["connected"] is True and st["device"] == "fake-phone"


async def test_phone_ws_rejects_wrong_device_token(stack):
    base, gw, _ = stack
    bad = FakePhone(base.replace("http", "ws") + "/phone/ws", "wrong", FakeVooyDevice())
    with pytest.raises(Exception):
        await asyncio.wait_for(bad.run_once(), 5)
    assert gw.link is not None and gw.link.device == "fake-phone"  # 기존 연결은 그대로


async def test_screen_commands_need_lease(stack):
    base, _, _ = stack
    async with _client(base) as c:
        r = await c.post("/rpc", json={"cmd": "tree", "args": {}})
        assert r.status_code == 409
        lease = (await c.post("/lease", json={"holder": "a", "wait_s": 1})).json()["lease"]
        r = await c.post("/rpc", json={"cmd": "tree", "args": {}, "lease": lease})
        assert r.status_code == 200 and r.json()["result"]["xml"].startswith("<?xml")
        # 화면을 안 쓰는 명령은 임대 없이
        assert (await c.post("/rpc", json={"cmd": "state"})).status_code == 200
        # 모르는 명령
        assert (await c.post("/rpc", json={"cmd": "rm -rf"})).status_code == 400


async def test_lease_is_exclusive_and_expires(stack):
    base, _, _ = stack
    async with _client(base) as c:
        a = (await c.post("/lease", json={"holder": "a", "wait_s": 0, "ttl_s": 1})).json()["lease"]
        r = await c.post("/lease", json={"holder": "b", "wait_s": 0})
        assert r.status_code == 409 and r.json()["holder"] == "a"
        # a 가 돌려주면 기다리던 b 가 받는다
        waiting = asyncio.create_task(c.post("/lease", json={"holder": "b", "wait_s": 5}))
        await asyncio.sleep(0.2)
        await c.delete(f"/lease/{a}")
        b = (await waiting).json()["lease"]
        # b 가 돌려주지 않고 죽어도 ttl 이 지나면 c 가 받는다
        r = await c.post("/lease", json={"holder": "c", "wait_s": 0})
        assert r.status_code == 409
        await asyncio.sleep(0)  # b 의 ttl 기본 30초 → 짧은 ttl 로 다시 확인
        await c.delete(f"/lease/{b}")
        short = (await c.post("/lease", json={"holder": "d", "wait_s": 0, "ttl_s": 0.5})).json()["lease"]
        assert short
        got = await c.post("/lease", json={"holder": "e", "wait_s": 3})
        assert got.status_code == 200


async def test_phone_device_and_notifications(stack):
    base, gw, phone = stack
    dev = PhoneDevice(base_url=base, token=HUB, holder="t")
    dev.IDLE_RELEASE_S = 0.2
    try:
        assert await dev.is_booted()
        cursor = await dev.events_cursor()
        await dev.app_start("com.vooy.mobile")
        assert await dev.current_package() == "com.vooy.mobile"
        await dev.set_text({"descriptionMatches": "^(Message|메시지 입력)$"}, "안녕")
        assert phone.vooy.input == "안녕"
        await dev.click(970, 2110)  # 전송 버튼
        await dev.press("home")
        posted, cursor = await dev.wait_notification("com.vooy.mobile", cursor, 5)
        assert posted and posted[0]["data"]["channel"] == "human-room-messages-private"
        # 쉬면 화면 임대를 놓는다
        await asyncio.sleep(0.5)
        assert gw.leases.current is None
        # 잠기면 조종할 수 없음
        phone.locked_file.write_text("1")
        assert not await dev.is_booted()
    finally:
        await dev.aclose()


async def test_rpc_when_phone_disconnected(stack):
    base, gw, phone = stack
    await gw.link.ws.close()
    for _ in range(100):
        if gw.link is None:
            break
        await asyncio.sleep(0.02)
    dev = PhoneDevice(base_url=base, token=HUB, holder="t")
    try:
        assert not await dev.is_booted()
    finally:
        await dev.aclose()


@pytest.fixture
async def vooy_phone(stack, tmp_path, monkeypatch):
    base, gw, phone = stack
    monkeypatch.setenv("ANDROID_DEVICE_FACTORY", "")
    cfg = AdapterConfig.load(__import__("adapters.vooy", fromlist=["x"]).__path__[0] + "/config.yaml")
    cfg = cfg.model_copy(update={"min_interval_s": 0})
    cfg.timeouts.quiet_s = 0.3
    a = VooyAdapter(cfg, tmp_path / "state")
    a.device = PhoneDevice(base_url=base, token=HUB, holder="vooy")
    a.device.IDLE_RELEASE_S = 0.3
    a.poll_s = 0.05
    a.android.app_start_wait_s = 1
    a.SEND_SETTLE_S = 0
    a.SEND_CHECK_S = 0.05
    yield a, gw, phone
    await a.device.aclose()


async def test_vooy_over_phone_waits_for_notification(vooy_phone):
    a, gw, phone = vooy_phone
    res = await a.ask("오늘 날씨", "t1", timeout_s=30)
    assert res.ok, res
    assert res.raw == "echo: 오늘 날씨"
    # 보낸 뒤 홈으로 나갔다가(알림), 읽고 다시 홈으로 나왔다
    assert phone.handled.count("global") >= 2
    assert not phone.vooy.foreground
    assert a._signaled  # 알림 신호로 읽었다


async def test_vooy_over_phone_tap_button(vooy_phone):
    a, _, phone = vooy_phone
    res = await a.ask("[vooy:buttons] 식당", "t1", timeout_s=30)
    assert res.ok and [b["label"] for b in res.structured["buttons"]] == ["예약", "다른 곳"]
    aid = res.structured["buttons"][0]["action_id"]
    res2 = await a.ask(f"[tap:{aid}]", "t1", timeout_s=30)
    assert res2.ok and "'예약' 선택하셨습니다." in res2.raw


async def test_vooy_over_phone_locked_is_unavailable(vooy_phone):
    a, _, phone = vooy_phone
    phone.locked_file.write_text("1")
    res = await a.ask("안녕", "t1", timeout_s=20)
    assert not res.ok and res.error == "unavailable"
    st = await a.status()
    assert st.reason == "backend_down"


async def test_vooy_over_phone_fast_reply_before_home(vooy_phone):
    """홈으로 나가기 전에 답이 오면(알림이 안 뜬다) 기다리지 않고 바로 읽는다."""
    a, _, phone = vooy_phone
    phone.vooy.latency = 0.0
    a.android.notify_fallback_s = 60
    res = await a.ask("빠른 답", "t1", timeout_s=120)  # 대기 끝 근처 규칙(10초)에 걸리지 않게 길게
    assert res.ok and res.raw == "echo: 빠른 답"
    assert res.latency_ms < 15_000
