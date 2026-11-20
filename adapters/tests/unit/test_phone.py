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
from adapters.learned import LearnedSelectors, app_key, device_id  # noqa: E402
from adapters.phonechat import PhoneChatAdapter, is_own  # noqa: E402
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
    gw = Gateway(
        HUB, DEV, ["com.vooy.mobile", "com.pickle.client", "com.facebook.aura", "ai.manus.agents"], state_dir=tmp_path / "gw"
    )
    server = uvicorn.Server(uvicorn.Config(gw.app(), host="127.0.0.1", port=port, log_level="warning", ws="websockets-sansio"))
    srv_task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.02)
    monkeypatch.setenv("FAKE_CHAT_LATENCY", "0.3")
    phone = FakePhone(
        f"ws://127.0.0.1:{port}/phone/ws", DEV, FakeVooyDevice(), locked_file=str(tmp_path / "locked"), chat_dir=tmp_path / "chats"
    )
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


async def test_other_phone_is_turned_away_while_first_is_alive(stack):
    """같은 토큰의 다른 휴대폰은 거절(4423)한다. 서로 밀어내며 끝없이 재연결하지 않게. 같은 휴대폰 재연결은 교체."""
    base, gw, _ = stack
    other = FakePhone(base.replace("http", "ws") + "/phone/ws", DEV, FakeVooyDevice())
    other.device_name = "other-phone"
    with pytest.raises(Exception):
        await asyncio.wait_for(other.run_once(), 5)
    assert gw.link is not None and gw.link.device == "fake-phone" and not gw.link.closed


async def test_preferred_phone_takes_over(stack, monkeypatch):
    """PHONE_PREFER_DEVICE 의 휴대폰은 붙어 있던 다른 휴대폰을 끊고 자리를 넘겨받는다. 끊긴 쪽은 다시 거절된다."""
    base, gw, _ = stack
    monkeypatch.setenv("PHONE_PREFER_DEVICE", "ULTRA")
    pref = FakePhone(base.replace("http", "ws") + "/phone/ws", DEV, FakeVooyDevice())
    pref.device_name = "samsung ULTRA-25"
    task = asyncio.create_task(pref.run_once())
    for _ in range(100):
        if gw.link is not None and gw.link.device == "samsung ULTRA-25":
            break
        await asyncio.sleep(0.05)
    assert gw.link is not None and gw.link.device == "samsung ULTRA-25"
    # 밀려난 휴대폰이 다시 붙으려 하면 거절
    again = FakePhone(base.replace("http", "ws") + "/phone/ws", DEV, FakeVooyDevice())
    again.device_name = "fake-phone"
    with pytest.raises(Exception):
        await asyncio.wait_for(again.run_once(), 5)
    assert gw.link.device == "samsung ULTRA-25"
    task.cancel()


async def test_prefer_phone_changed_at_runtime(stack, tmp_path):
    """화면에서 우선 휴대폰을 바꾸면 지금 폰을 끊고, 우선 폰이 붙으면 자리를 넘긴다. 고른 값과 목록은 재시작 뒤에도 남는다."""
    base, gw, phone = stack
    ws_url = base.replace("http", "ws") + "/phone/ws"
    # 거절된 폰도 목록에 남는다
    other = FakePhone(ws_url, DEV, FakeVooyDevice())
    other.device_name = "samsung NOTE-9"
    with pytest.raises(Exception):
        await asyncio.wait_for(other.run_once(), 5)
    async with _client(base) as c:
        d = (await c.get("/devices")).json()
        assert d["current"] == "fake-phone" and d["prefer"] == ""
        assert {r["device"] for r in d["devices"]} == {"fake-phone", "samsung NOTE-9"}
        assert (await c.put("/prefer", json={"device": 3})).status_code == 400

        first = gw.link
        d = (await c.put("/prefer", json={"device": "samsung NOTE-9"})).json()
        assert d["prefer"] == "samsung NOTE-9"
        assert next(r for r in d["devices"] if r["device"] == "samsung NOTE-9")["preferred"] is True
    # 지금 폰(fake-phone)은 끊긴다. 우선 폰이 없으니 다시 붙는다(먼저 붙은 폰을 지킨다)
    for _ in range(100):
        if gw.link is not None and gw.link is not first and gw.link.device == "fake-phone":
            break
        await asyncio.sleep(0.05)
    assert gw.link is not first and gw.link.device == "fake-phone"
    # 우선 폰이 붙으면 자리를 넘겨받는다
    task = asyncio.create_task(other.run_once())
    for _ in range(100):
        if gw.link is not None and gw.link.device == "samsung NOTE-9":
            break
        await asyncio.sleep(0.05)
    assert gw.link.device == "samsung NOTE-9"
    task.cancel()
    # 재시작 뒤에도 남는다
    again = Gateway(HUB, DEV, [], state_dir=tmp_path / "gw")
    assert again.prefer == "samsung NOTE-9" and "fake-phone" in again.known


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
    a.android.home_after_use = True  # 알림 경로를 시험한다(기본값은 앱을 그대로 둔다)
    a.SEND_SETTLE_S = 0
    a.SEND_CHECK_S = 0.05
    a.SEND_CONFIRM_S = 0.3
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
    a.sel.pop("send_u2", None)  # 화면을 읽어 전송을 확인하는 경로(빠른 보내기는 누른 뒤 화면을 보지 않는다)
    res = await a.ask("빠른 답", "t1", timeout_s=120)  # 대기 끝 근처 규칙(10초)에 걸리지 않게 길게
    assert res.ok and res.raw == "echo: 빠른 답"
    assert res.latency_ms < 15_000


def _chat_adapter(bot: str, base: str, tmp_path) -> PhoneChatAdapter:
    d = __import__(f"adapters.{bot}", fromlist=["x"]).__path__[0]
    cfg = AdapterConfig.load(d + "/config.yaml")
    cfg = cfg.model_copy(update={"min_interval_s": 0})
    cfg.timeouts.quiet_s = 0.5
    a = PhoneChatAdapter(cfg, tmp_path / f"state-{bot}", selectors_path=d + "/selectors.yaml")
    a.device = PhoneDevice(base_url=base, token=HUB, holder=bot)
    a.device.IDLE_RELEASE_S = 0.3
    a.poll_s = 0.05
    a.android.app_start_wait_s = 1
    a.android.home_after_use = True  # 알림 경로를 시험한다(기본값은 앱을 그대로 둔다)
    a.SEND_SETTLE_S = 0
    a.SEND_CHECK_S = 0.05
    return a


@pytest.mark.parametrize("bot", ["pickle", "muse", "cue"])
async def test_chat_app_over_phone(stack, tmp_path, monkeypatch, bot):
    base, _, phone = stack
    monkeypatch.setenv("ANDROID_DEVICE_FACTORY", "")
    a = _chat_adapter(bot, base, tmp_path)
    try:
        res = await a.ask("오늘 날씨", "t1", timeout_s=60)
        assert res.ok, res
        assert res.raw == "echo: 오늘 날씨"
        assert a._signaled
        # 버튼 → [tap:]
        res = await a.ask("[chat:buttons] 식당", "t1", timeout_s=60)
        assert res.ok and [b["label"] for b in res.structured["buttons"]] == ["예약", "다른 곳"], res
        aid = res.structured["buttons"][0]["action_id"]
        res2 = await a.ask(f"[tap:{aid}]", "t1", timeout_s=60)
        assert res2.ok and "'예약' 선택하셨습니다." in res2.raw, res2
        # 다른 봇과 화면을 나눠 쓰고 끝나면 홈으로 나온다
        assert phone._fg() is None
        # Cue: 입력줄 마이크(누르면 음성 모드)는 한 번도 누르지 않았다
        assert all(app.voice_entered == 0 for app in phone.chats.values())
    finally:
        await a.device.aclose()


async def test_send_button_learned_per_device(stack, tmp_path, monkeypatch):
    """기본 셀렉터가 안 맞는 앱 버전: 일반 규칙(글 넣은 뒤 나타난 버튼)으로 보내고, 기기별 층에 배운다."""
    base, _, phone = stack
    a = _chat_adapter("pickle", base, tmp_path)
    a.sel["send"] = [{"desc": "새 버전 이름"}]  # 앱이 버튼 이름을 바꿨다고 치자
    a.sel.pop("send_u2", None)
    try:
        res = await a.ask("하나", "t1", timeout_s=60)
        assert res.ok and res.raw == "echo: 하나", res
        key = app_key("com.pickle.client", "1.0-fake(1)", device_id("fake-phone", 34))
        assert a.learned.get(key, "send") == {"desc": "보내기"}
        # 다음 전송은 배운 셀렉터로
        res = await a.ask("둘", "t1", timeout_s=60)
        assert res.ok and res.raw == "echo: 둘", res
        # 같은 허브의 다른 어댑터 인스턴스도 파일에서 읽는다(재시작)
        assert LearnedSelectors(a.state_dir / "learned_selectors.json").get(key, "send") == {"desc": "보내기"}
    finally:
        await a.device.aclose()


def test_learned_selectors_version_and_drop(tmp_path):
    path = tmp_path / "learned.json"
    s = LearnedSelectors(path)
    old, new = app_key("a.b", "1.0"), app_key("a.b", "1.1")
    s.put(old, "send", {"desc": "보내기"})
    s.put(app_key("c.d", "2"), "send", {"rid": "x"})
    assert LearnedSelectors(path).get(old, "send") == {"desc": "보내기"}
    # 앱이 업데이트되면 그 앱의 옛 버전 기록은 버린다(다른 앱은 그대로)
    s.put(new, "send", {"desc": "전송"})
    s2 = LearnedSelectors(path)
    assert s2.get(old, "send") is None and s2.get(new, "send") == {"desc": "전송"}
    assert s2.get(app_key("c.d", "2"), "send") == {"rid": "x"}
    s2.drop(new, "send")
    assert LearnedSelectors(path).get(new, "send") is None


def test_learned_selectors_are_per_device(tmp_path):
    """같은 앱 같은 버전이라도 기기(모델·안드로이드 버전)가 다르면 따로 배운다. 한 기기의 업데이트가 다른 기기 기록을 지우지 않는다."""
    path = tmp_path / "learned.json"
    s = LearnedSelectors(path)
    s10 = app_key("a.b", "1.0", device_id("samsung SM-N960N", 29))
    s15 = app_key("a.b", "1.0", device_id("samsung SM-S938N", 35))
    s.put(s10, "input", {"method": "focus_paste"})
    s.put(s15, "input", {"method": "set_text"})
    s.put(app_key("a.b", "1.1", device_id("samsung SM-S938N", 35)), "input", {"method": "set_text"})
    s2 = LearnedSelectors(path)
    assert s2.get(s10, "input") == {"method": "focus_paste"}  # 다른 기기의 업데이트에 지워지지 않았다
    assert s2.get(s15, "input") is None  # 같은 기기의 옛 버전은 버렸다


async def test_input_method_calibrated_per_device(stack, tmp_path, monkeypatch):
    """환경 맞추기: set_text 가 안 먹는 기기(안드로이드 10 노트9 의 뮤즈)는 포커스+붙여넣기를 찾아 저장하고 다음부터 그것만 쓴다."""
    base, gw, phone = stack
    app = phone.chats["com.facebook.aura"]
    app.set_text_ignored = True
    a = _chat_adapter("muse", base, tmp_path)
    try:
        res = await a.ask("하나", "t1", timeout_s=60)
        assert res.ok and res.raw == "echo: 하나", res
        key = app_key("com.facebook.aura", "1.0-fake(1)", device_id("fake-phone", 34))
        assert a.learned.get(key, "input") == {"method": "focus_paste"}
        # 다음 전송은 저장된 방법만 쓴다(set_text 를 쓰면 붙여넣기까지 막히는 앱)
        app.poisoned = False
        res = await a.ask("둘", "t1", timeout_s=60)
        assert res.ok and res.raw == "echo: 둘", res
        assert app.poisoned is False
        assert app.voice_entered == 0
    finally:
        await a.device.aclose()


async def test_no_screenshot_phone_uses_tree_rule_or_never_guesses(stack, tmp_path, monkeypatch):
    """스크린샷이 없는 안드로이드 10: Cue 는 트리 규칙(입력줄이 하나 늘면 맨 오른쪽이 ↑)으로 보낸다.
    규칙이 없는 앱이면 이름 없는 버튼을 추측해 누르지 않는다(마이크 안전)."""
    base, gw, phone = stack
    gw.link.sdk = 29
    cue = phone.chats["ai.manus.agents"]
    a = _chat_adapter("cue", base, tmp_path)
    try:
        res = await a.ask("하나", "t1", timeout_s=30)
        assert res.ok and res.raw == "echo: 하나", res
        assert cue.voice_entered == 0
    finally:
        await a.device.aclose()
    b = _chat_adapter("cue", base, tmp_path / "norule")
    b.sel.pop("send_tree_rule")
    try:
        res = await b.ask("둘", "t2", timeout_s=30)
        assert not res.ok and res.error == "unavailable", res
        assert cue.voice_entered == 0
    finally:
        await b.device.aclose()


async def test_three_bots_share_one_phone(stack, tmp_path, monkeypatch, vooy_phone):
    """세 봇이 동시에 물어도 화면 임대로 차례로 쓰고, 기다리는 동안은 화면을 놓아 서로 막지 않는다."""
    base, gw, phone = stack
    v, _, _ = vooy_phone
    p, m = _chat_adapter("pickle", base, tmp_path), _chat_adapter("muse", base, tmp_path)
    try:
        rs = await asyncio.gather(
            v.ask("하나", "t", timeout_s=60), p.ask("둘", "t", timeout_s=60), m.ask("셋", "t", timeout_s=60)
        )
        assert [r.raw for r in rs] == ["echo: 하나", "echo: 둘", "echo: 셋"], rs
    finally:
        await p.device.aclose()
        await m.device.aclose()


async def test_apk_distribution(tmp_path):
    """워커 앱 배포: 최신 정보·다운로드는 기기 토큰, 브라우저 설치는 한 번만 쓰는 링크(허브 토큰으로 발급)."""
    import json

    dist = tmp_path / "dist"
    dist.mkdir()
    port = _free_port()
    gw = Gateway(HUB, DEV, [], apk_dir=dist)
    server = uvicorn.Server(uvicorn.Config(gw.app(), host="127.0.0.1", port=port, log_level="warning", ws="websockets-sansio"))
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.02)
    base = f"http://127.0.0.1:{port}"
    try:
        async with httpx.AsyncClient(base_url=base, timeout=10) as c:
            dev = {"Authorization": f"Bearer {DEV}"}
            # 아직 배포본 없음
            assert (await c.get("/phone/apk/latest.json", headers=dev)).status_code == 404
            (dist / "roundtable-worker.apk").write_bytes(b"APKDATA")
            (dist / "latest.json").write_text(json.dumps({"versionCode": 7, "versionName": "x", "sha256": "s"}), encoding="utf-8")
            # 기기 토큰 없으면 막힌다. 허브 토큰도 안 된다
            assert (await c.get("/phone/apk/latest.json")).status_code == 401
            assert (await c.get("/phone/apk/download", headers={"X-Hub-Token": HUB})).status_code == 401
            assert (await c.get("/phone/apk/latest.json", headers=dev)).json()["versionCode"] == 7
            r = await c.get("/phone/apk/download", headers=dev)
            assert r.status_code == 200 and r.content == b"APKDATA"
            assert r.headers["content-type"] == "application/vnd.android.package-archive"
            # 일회용 링크: 허브 토큰으로만 발급, 한 번 쓰면 끝
            assert (await c.post("/apk/link")).status_code == 401
            path = (await c.post("/apk/link", headers={"X-Hub-Token": HUB})).json()["path"]
            assert (await c.get(path)).content == b"APKDATA"
            assert (await c.get(path)).status_code == 404
            assert (await c.get("/phone/apk/once/guess")).status_code == 404
            # 만료
            path2 = (await c.post("/apk/link", headers={"X-Hub-Token": HUB})).json()["path"]
            gw._once = {k: 0.0 for k in gw._once}
            assert (await c.get(path2)).status_code == 404
    finally:
        server.should_exit = True
        await task


def test_is_own_only_accepts_timestamp_prefix():
    """내 말풍선에 붙은 시각은 인정하고, 보낸 글로 끝나는 답("echo: …")은 내 글로 보지 않는다."""
    assert is_own("오늘 날씨", "오늘 날씨")
    assert is_own("어제 7:20 오후 오늘 날씨", "오늘 날씨")
    assert is_own("오늘 날씨 오후 3:05", "오늘 날씨")
    assert not is_own("echo: 오늘 날씨", "오늘 날씨")
    assert not is_own("네, 오늘 날씨", "오늘 날씨")


async def test_wakes_screen_without_secure_lock_and_turns_it_off_after(vooy_phone):
    """보안 잠금이 없는 휴대폰: 화면이 꺼져 잠금화면이어도 가용이고, 화면을 쓸 때 깨운 뒤 다 쓰면 다시 끈다."""
    a, gw, phone = vooy_phone
    gw.screen_off_after_s = 0.3
    phone.secure = False
    phone.screen, phone.keyguard = False, True
    assert await a.device.is_booted()  # 꺼져 있어도 깨울 수 있으니 가용
    res = await a.ask("오늘 날씨", "t1", timeout_s=30)
    assert res.ok and res.raw == "echo: 오늘 날씨", res
    assert "wake" in phone.handled
    for _ in range(40):
        if phone.screen_offs and not phone.screen:  # 보낸 뒤 한 번 꺼졌다가 읽을 때 다시 켜질 수 있다
            break
        await asyncio.sleep(0.1)
    assert phone.screen_offs >= 1 and not phone.screen  # 우리가 켠 화면은 다 쓴 뒤 끈다


async def test_screen_on_by_user_is_not_turned_off(vooy_phone):
    """사람이 켜 둔 화면(우리가 깨우지 않음)은 다 써도 끄지 않는다."""
    a, gw, phone = vooy_phone
    gw.screen_off_after_s = 0.2
    phone.secure = False
    res = await a.ask("오늘 날씨", "t1", timeout_s=30)
    assert res.ok, res
    await asyncio.sleep(0.6)
    assert phone.screen_offs == 0 and phone.screen


async def test_secure_lock_is_never_woken(vooy_phone):
    """보안 잠금(PIN 등)이 있으면 깨우지 않고 사용할 수 없다고 본다."""
    a, _, phone = vooy_phone
    phone.secure = True
    phone.screen, phone.keyguard = False, True
    assert not await a.device.is_booted()
    res = await a.ask("안녕", "t1", timeout_s=20)
    assert not res.ok and res.error == "unavailable"
    assert phone.screen is False


def _bot_adapter(bot: str, tmp_path) -> PhoneChatAdapter:
    d = __import__(f"adapters.{bot}", fromlist=["x"]).__path__[0]
    return PhoneChatAdapter(AdapterConfig.load(d + "/config.yaml"), tmp_path / f"state-{bot}", selectors_path=d + "/selectors.yaml")


def test_pickle_header_overlay_is_not_reply(tmp_path):
    """내 말풍선이 화면 위로 밀려 잘리면 목록 위에 겹친 머리 부분(프로필 이름·초대)이 답 앞에 섞였다(2026-11-12 실기기)."""
    from adapters.android_base import parse_hierarchy

    def tv(b: str, t: str) -> str:
        return f'<node class="android.widget.TextView" package="com.pickle.client" text="{t}" content-desc="" clickable="false" bounds="{b}"/>'

    xml = (
        '<hierarchy><node class="android.view.View" package="com.pickle.client" text="" content-desc="" clickable="false" bounds="[0,0][1080,2200]">'
        + tv("[98,0][936,85]", "내 질문")  # 위로 잘린 내 말풍선
        + tv("[98,101][936,351]", "봇 답 첫 줄")
        + tv("[98,367][936,617]", "봇 답 둘째 줄")
        + '<node class="android.widget.EditText" package="com.pickle.client" text="" content-desc="" clickable="true" bounds="[163,1813][797,1939]"/>'
        + '<node class="android.view.View" package="com.pickle.client" text="" content-desc="친구 초대" clickable="true" bounds="[892,82][1038,192]">'
        + tv("[929,113][1001,161]", "초대") + "</node>"
        + '<node class="android.view.View" package="com.pickle.client" text="" content-desc="봇이름 프로필" clickable="true" bounds="[469,0][611,292]">'
        + '<node class="android.view.View" package="com.pickle.client" text="" content-desc="" clickable="false" bounds="[469,200][611,292]">'
        + tv("[504,223][576,271]", "봇이름") + "</node></node>"
        + "</node></hierarchy>"
    )
    a = _bot_adapter("pickle", tmp_path)
    a._sent_norm = "내 질문"
    got = [n.text for n in a._new_texts(parse_hierarchy(xml))]
    assert got == ["봇 답 첫 줄", "봇 답 둘째 줄"]


def test_muse_processing_bubble_is_progress(tmp_path):
    """뮤즈가 답 대신 먼저 띄우는 "처리 중"을 답으로 받았다(2026-11-12 실기기)."""
    from adapters.base import RawMessage

    a = _bot_adapter("muse", tmp_path)
    assert a._is_progress(RawMessage(id="1", text="처리 중"))
    assert a._is_progress(RawMessage(id="2", text="처리 중..."))
    assert not a._is_progress(RawMessage(id="3", text="요청을 처리 중에 생긴 문제를 정리하면 이렇다"))


def _pickle_row_xml(with_input: bool = True) -> str:
    def node(cls: str, b: str, desc: str = "", click: str = "true") -> str:
        return f'<node class="{cls}" package="com.pickle.client" text="" content-desc="{desc}" clickable="{click}" bounds="{b}"/>'

    return (
        '<hierarchy><node class="android.view.View" package="com.pickle.client" text="" content-desc="" clickable="false" bounds="[0,0][1440,3120]">'
        + (node("android.widget.EditText", "[60,2663][1180,2843]") if with_input else "")
        + node("android.view.View", "[1200,2663][1380,2843]")  # 입력줄 오른쪽 버튼
        + node("android.view.View", "[1000,2862][1353,3064]")  # 입력창 바로 아래 하단 탭
        + "</node></hierarchy>"
    )


def test_bottom_tab_below_input_is_not_send_candidate(tmp_path):
    """2026-11-16 실기기: 입력창 바로 아래 하단 탭을 '나타난 전송 버튼'으로 눌러 계정 화면을 답으로 읽었다."""
    from adapters.android_base import Bounds, parse_hierarchy

    a = _bot_adapter("pickle", tmp_path)
    a._pre_field = Bounds(60, 2663, 1180, 2843)
    row = a._row_buttons(parse_hierarchy(_pickle_row_xml()))
    assert [n.bounds.as_tuple() for n in row] == [(1200, 2663, 1380, 2843)]


async def test_guessed_send_that_leaves_chat_is_failure(tmp_path, monkeypatch):
    """추측으로 고른 버튼을 누른 뒤 입력창도 내 말풍선도 없으면 성공이 아니라 실패(바뀐 화면을 답으로 읽지 않는다)."""
    from adapters.android_base import parse_hierarchy
    from adapters.base import BackendDown

    a = _bot_adapter("pickle", tmp_path)
    a.SEND_SETTLE_S = a.SEND_CHECK_S = a.SEND_FIRST_CHECK_S = 0.01
    a.SEND_CONFIRM_S = 0.1
    chat = parse_hierarchy(_pickle_row_xml())
    away = parse_hierarchy(_pickle_row_xml(with_input=False))
    screens = [chat]
    pressed = []

    async def tree():
        return screens[-1]

    async def find_send(root, for_press):
        n = next(n for n in root.walk() if n.bounds.as_tuple() == (1000, 2862, 1353, 3064))
        return n, "appeared"

    async def press(node, source):
        pressed.append(source)
        screens.append(away)  # 누르자 다른 화면으로 갔다

    monkeypatch.setattr(a, "tree", tree)
    monkeypatch.setattr(a, "_find_send", find_send)
    monkeypatch.setattr(a, "_press", press)
    with pytest.raises(BackendDown, match="채팅 화면이 아님"):
        await a._tap_send("안녕")
    assert pressed == ["appeared"]
    assert a._confirm_root is None
    assert a.learned.get(a._app_key, "send") is None


def test_cue_button_row_below_input_inside_input_box(tmp_path):
    """2026-11-17 울트라25 Cue: 버튼 줄이 글 칸 아래(같은 입력 상자 틀 안)에 있다. 글 칸 높이 안에 버튼이 없으면
    틀 안의 버튼 줄을 본다. 틀 밖(아래 하단 탭)은 여전히 후보가 아니다."""
    from adapters.android_base import Bounds, parse_hierarchy

    def node(cls: str, b: str, click: str = "true", inner: str = "") -> str:
        return f'<node class="{cls}" package="ai.manus.agents" text="" content-desc="" clickable="{click}" bounds="{b}">{inner}</node>'

    xml = (
        "<hierarchy>"
        + node(
            "android.view.View",
            "[0,0][1440,3120]",
            "false",
            node(
                "android.view.View",
                "[0,1378][1440,1730]",
                "true",
                node("android.view.View", "[75,1404][1346,1554]", "true", node("android.widget.EditText", "[75,1404][1346,1554]"))
                + node("android.widget.ImageView", "[75,1565][210,1700]")
                + node("android.widget.ImageView", "[1065,1565][1200,1700]")
                + node("android.widget.ImageView", "[1230,1565][1365,1700]"),
            )
            + node("android.view.View", "[1000,1745][1353,1900]"),  # 틀 밖 아래(탭 같은 것)
        )
        + "</hierarchy>"
    )
    a = _bot_adapter("cue", tmp_path)
    a._pre_field = Bounds(75, 1404, 1346, 1554)
    row = a._row_buttons(parse_hierarchy(xml))
    assert sorted(n.bounds.as_tuple() for n in row) == [(1065, 1565, 1200, 1700), (1230, 1565, 1365, 1700)]


def test_cue_button_row_below_multiline_input(tmp_path):
    """같은 날 울트라25 Cue: 글이 여러 줄이 되면 글 칸(1314~1554)만 감싼 좁은 틀이 생기고, 글 칸이 바깥 틀 위로
    넘친다. 가장 가까운 틀이 아니라 버튼 후보가 든 가장 가까운 틀에서 버튼 줄(1573~1708)을 찾는다."""
    from adapters.android_base import Bounds, parse_hierarchy

    def node(cls: str, b: str, click: str = "true", inner: str = "") -> str:
        return f'<node class="{cls}" package="ai.manus.agents" text="" content-desc="" clickable="{click}" bounds="{b}">{inner}</node>'

    xml = (
        "<hierarchy>"
        + node(
            "android.view.View",
            "[0,0][1440,3120]",
            "false",
            node(
                "android.view.View",
                "[0,1378][1440,1730]",  # 바깥 틀은 한 줄일 때 크기 그대로(글 칸이 위로 넘친다)
                "false",
                node(
                    "android.view.View",
                    "[60,1300][1360,1560]",  # 글 칸만 감싼 좁은 틀(버튼 없음)
                    "false",
                    node("android.widget.EditText", "[75,1314][1346,1554]"),
                )
                + node("android.widget.ImageView", "[75,1573][210,1708]")
                + node("android.widget.ImageView", "[1238,1573][1373,1708]"),
            )
            + node("android.view.View", "[1000,1745][1353,1900]"),  # 틀 밖 아래(탭 같은 것)
        )
        + "</hierarchy>"
    )
    a = _bot_adapter("cue", tmp_path)
    a._pre_field = Bounds(188, 1565, 1223, 1715)
    row = a._row_buttons(parse_hierarchy(xml))
    assert [n.bounds.as_tuple() for n in row] == [(1238, 1573, 1373, 1708)]


def test_tablet_popup_only_own_app_window(tmp_path):
    """2026-11-18 갤럭시 탭 S9 울트라: 여러 앱을 팝업으로 함께 띄우면 트리에 다른 앱 창이 섞인다.
    휴대폰에서는 이 앱 창(과 시스템 창)만 보고, 워커로 보내는 셀렉터도 이 앱으로 한정한다. 피클의 좌표 대비책은
    화면 끝이 아니라 팝업 창 오른쪽 끝을 기준으로 한다."""
    from adapters.android_base import Bounds, parse_hierarchy
    from adapters.phonechat import find_first

    def win(pkg: str, b: str, inner: str) -> str:
        return f'<node class="android.widget.FrameLayout" package="{pkg}" text="" content-desc="" clickable="false" bounds="{b}">{inner}</node>'

    def edit(pkg: str, b: str) -> str:
        return f'<node class="android.widget.EditText" package="{pkg}" text="" content-desc="" clickable="true" bounds="{b}"/>'

    xml = (
        "<hierarchy>"
        + win("com.pickle.client", "[842,300][1658,1600]", edit("com.pickle.client", "[911,1421][1520,1493]"))
        + win("com.vooy.mobile", "[1437,300][2264,1620]", edit("com.vooy.mobile", "[1502,1433][2138,1484]"))
        + win("android", "[900,800][1800,1000]", "")
        + "</hierarchy>"
    )
    a = _bot_adapter("vooy", tmp_path)
    a.device = type("Phone", (), {"wait_notification": None})()
    root = a.own_windows(parse_hierarchy(xml))
    assert [w.package for w in root.children] == ["com.vooy.mobile", "android"]
    assert find_first(root, {"cls": "EditText"}).bounds == Bounds(1502, 1433, 2138, 1484)
    assert a.app_window(root) == Bounds(1437, 300, 2264, 1620)
    assert a.scoped({"className": "android.widget.EditText"}) == {
        "className": "android.widget.EditText", "packageName": "com.vooy.mobile"}
    # 이 앱 창이 없으면(앱이 앞에 없음) 그대로
    other = parse_hierarchy("<hierarchy>" + win("com.pickle.client", "[0,0][10,10]", "") + "</hierarchy>")
    assert a.own_windows(other) is other
    # 에뮬레이터(휴대폰 아님)는 셀렉터를 바꾸지 않는다
    a.device = object()
    assert a.scoped({"className": "x"}) == {"className": "x"}


async def test_tap_input_clicks_node_not_stale_coordinates(tmp_path):
    """2026-11-20 노트9: 앱을 다시 열면 키보드가 늦게 올라와 입력줄이 위로 밀린다. 밀리기 전 좌표를 누르면
    자판을 누르므로, 휴대폰에서는 입력창을 이 앱으로 한정한 셀렉터로 접근성 클릭한다. 실패하면 좌표로."""
    from adapters.android_base import parse_hierarchy

    xml = ('<hierarchy><node class="android.widget.EditText" package="com.vooy.mobile" text="" '
           'content-desc="메시지 입력" clickable="true" bounds="[155,1819][794,1911]"/></hierarchy>')
    field = parse_hierarchy(xml).children[0]
    calls: list = []

    class Phone:
        wait_notification = None
        fail = False

        async def click_selector(self, sel):
            calls.append(("node", sel))
            if self.fail:
                raise RuntimeError("누를 노드를 찾지 못함")
            return "action"

        async def click(self, x, y):
            calls.append(("xy", x, y))

    a = _bot_adapter("vooy", tmp_path)
    a.device = Phone()
    await a.tap_input(field, {"descriptionMatches": "^메시지 입력$"})
    assert calls == [("node", {"descriptionMatches": "^메시지 입력$", "packageName": "com.vooy.mobile"})]
    calls.clear()
    a.device.fail = True
    await a.tap_input(field, {"descriptionMatches": "^메시지 입력$"})
    assert [c[0] for c in calls] == ["node", "xy"] and calls[1][1:] == field.bounds.center


async def test_cue_waits_for_row_to_grow_before_guessing(tmp_path, monkeypatch):
    """2026-11-18 탭 팝업 Cue: 글을 넣은 직후 트리는 아직 옛 마이크 하나뿐이다(화면에는 마이크가 밀리고 ↑ 가 생김).
    그 자리 그림이 바뀌었다고 옛 노드를 ↑ 로 고르지 않고, 버튼 줄이 늘 때까지 기다려 새 맨 오른쪽(↑)을 고른다."""
    from adapters.android_base import Bounds, parse_hierarchy

    def screen(buttons: list[str]) -> str:
        def node(cls: str, b: str) -> str:
            return f'<node class="{cls}" package="ai.manus.agents" text="" content-desc="" clickable="true" bounds="{b}"/>'

        return (
            '<hierarchy><node class="android.view.View" package="ai.manus.agents" text="" content-desc="" clickable="false" bounds="[286,295][1174,1626]">'
            + node("android.widget.EditText", "[361,1506][1021,1566]")
            + "".join(node("android.widget.ImageView", b) for b in buttons)
            + "</node></hierarchy>"
        )

    a = _bot_adapter("cue", tmp_path)
    a.device = type("Phone", (), {"wait_notification": None})()
    a.ROW_SETTLE_S = 2.0
    a._pre_field = Bounds(385, 1506, 1063, 1566)
    a._pre_bounds = [(1069, 1509, 1123, 1563)]
    a._pre_icons = [b"mic"]
    stale = parse_hierarchy(screen(["[1069,1509][1123,1563]"]))
    grown = parse_hierarchy(screen(["[1027,1509][1081,1563]", "[1093,1509][1147,1563]"]))
    reads = []

    async def tree():
        reads.append(1)
        return grown

    async def icons(nodes):
        # 옛 자리는 그림이 반쯤 바뀌어 "새 그림"으로 보인다. 늘어난 줄에서는 마이크가 그대로, ↑ 만 새 그림
        return [(n, b"mic" if n.bounds.left == 1027 else b"new") for n in nodes]

    monkeypatch.setattr(a, "tree", tree)
    monkeypatch.setattr(a, "_icons", icons)
    monkeypatch.setattr("adapters.phonechat.same_icon", lambda x, y: x == y)
    n = await a._appeared(stale)
    assert reads and n.bounds.as_tuple() == (1093, 1509, 1147, 1563)


def test_other_chat_on_screen_is_not_reply(tmp_path):
    """2026-11-18 탭 뮤즈: 답을 읽으러 앱을 열었더니 다른 대화방이 떠 있었고, 보낸 글이 없으니 그 방 글 전부를
    '새 글'로 보고 답으로 넘겼다. 보낸 글을 한 번도 못 봤으면 읽지 않는다. 한 번 본 뒤에는(긴 답에 밀려 사라져도)
    전처럼 읽는다."""
    from adapters.android_base import parse_hierarchy

    def screen(texts: list[str]) -> object:
        def tv(i: int, t: str) -> str:
            return (f'<node class="android.widget.TextView" package="com.facebook.aura" text="{t}" content-desc="" '
                    f'clickable="false" bounds="[100,{200 + i * 100}][900,{280 + i * 100}]"/>')

        return parse_hierarchy(
            '<hierarchy><node class="android.view.View" package="com.facebook.aura" text="" content-desc="" '
            'clickable="false" bounds="[0,0][1000,2000]">' + "".join(tv(i, t) for i, t in enumerate(texts)) + "</node></hierarchy>"
        )

    a = _bot_adapter("muse", tmp_path)
    before = screen(["지난 질문", "지난 답"])
    a._baseline = {a._sig(n) for n in a._texts(before)}
    a._sent_norm = "자기소개 해 주세요"
    a._chat_seen = False
    # 다른 대화방: 보낸 글도, 이 방의 글도 없다
    assert a._new_texts(screen(["다른 방의 내 말", "다른 방의 답"])) == []
    # 이 방: 보낸 글 아래가 답
    got = a._new_texts(screen(["지난 질문", "지난 답", "자기소개 해 주세요", "안녕하세요, 저는 뮤즈예요"]))
    assert [n.text for n in got] == ["안녕하세요, 저는 뮤즈예요"]
    # 한 번 본 뒤로는 보낸 글이 밀려 올라가 안 보여도 새 글을 읽는다
    got = a._new_texts(screen(["안녕하세요, 저는 뮤즈예요", "이어지는 긴 답"]))
    assert [n.text for n in got] == ["안녕하세요, 저는 뮤즈예요", "이어지는 긴 답"]


def test_chat_room_name_from_env(tmp_path, monkeypatch):
    """Cue 대화방 이름은 계정마다 달라 레포에 넣지 않고 CUE_CHAT_ROOM 으로 준다. 없으면 그 항목은 건너뛴다."""
    a = _bot_adapter("cue", tmp_path)
    monkeypatch.delenv("CUE_CHAT_ROOM", raising=False)
    assert all("name_env" not in t for t in a._chat_tabs()) and len(a._chat_tabs()) == 1
    monkeypatch.setenv("CUE_CHAT_ROOM", "my.agent")
    import re

    sel = a._chat_tabs()[-1]
    assert sel["clickable"] is True
    assert re.search(sel["desc_re"], "my.agent\n설명") and re.search(sel["desc_re"], "my.agent")
    assert not re.search(sel["desc_re"], "myXagent") and not re.search(sel["desc_re"], "my.agent2")


async def test_phone_does_not_resend_blindly_when_question_was_sent(stack, tmp_path, monkeypatch):
    """알림이 없어도 확인 시점에 보낸 질문이 화면에 있으면 다시 보내지 않고 답을 기다린다(절반 시점 무조건 재전송 없음)."""
    base, _, phone = stack
    monkeypatch.setenv("ANDROID_DEVICE_FACTORY", "")
    a = _chat_adapter("pickle", base, tmp_path)
    app = phone.chats["com.pickle.client"]
    app.notify = None  # 알림을 안 보내는 앱
    app.latency = 8.0  # 생각이 길다: 예전 규칙이면 절반(약 6초) 시점에 다시 보낸다
    a.android.expect_reply_s, a.android.verify_margin_s = 0.5, 0
    try:
        res = await a.ask("긴 생각", "t1", timeout_s=12)
        assert res.ok and res.raw == "echo: 긴 생각", res
        assert [m.text for m in app.messages if m.role == "own"] == ["긴 생각"]
        assert not any(r.get("retried") for r in a.logs)
    finally:
        await a.device.aclose()


async def test_phone_resends_only_when_question_is_missing(stack, tmp_path, monkeypatch):
    """빠른 보내기: 전송이 확인 안 돼도 다시 누르지 않고 떠난다. 확인 시점에 질문이 없고 화면이 그대로면 한 번 다시 보낸다."""
    base, _, phone = stack
    monkeypatch.setenv("ANDROID_DEVICE_FACTORY", "")
    a = _chat_adapter("pickle", base, tmp_path)
    app = phone.chats["com.pickle.client"]
    app.notify = None
    app.drop_sends = 1  # 첫 전송은 눌러도 안 간다
    a.android.expect_reply_s, a.android.verify_margin_s = 0.5, 0
    try:
        res = await a.ask("안 간 질문", "t1", timeout_s=30)
        assert res.ok and res.raw == "echo: 안 간 질문", res
        assert [m.text for m in app.messages if m.role == "own"] == ["안 간 질문"]
        assert any(r.get("retried") for r in a.logs)
    finally:
        await a.device.aclose()


def test_verify_time_learns_from_reply_notifications(tmp_path):
    a = _bot_adapter("pickle", tmp_path)
    a.android.expect_reply_s, a.android.verify_margin_s, a.android.notify_fallback_s = 30, 10, 60
    assert a.verify_after_s() == 40
    for s in (12, 20, 14):
        a._note_reply_time(s)
    assert a.verify_after_s() == 24  # 중간값 14 + 10
    # 다시 만들어도 기록이 남는다
    b = _bot_adapter("pickle", tmp_path)
    assert list(b._reply_times) == [12, 20, 14]
    a.android.notify_fallback_s = 20
    assert a.verify_after_s() == 20  # 상한


def test_reply_time_learning_shrinks_when_answer_was_already_done(tmp_path):
    """알림 없는 앱: 처음 열었을 때 답이 이미 끝나 있었으면 0.75배로 적어 다음엔 일찍 열어 본다."""

    class Phone:
        wait_notification = None

    a = _bot_adapter("pickle", tmp_path)
    a.device = Phone()
    a._sent_at = 100.0
    a._first_read_at = 130.0
    a._last_change_at = 130.5  # 처음 본 뒤 바뀌지 않았다 = 이미 끝나 있었다
    a._answered()
    a._first_read_at = 110.0
    a._last_change_at = 125.0  # 읽는 동안 답이 이어졌다 = 실제로 끝난 시각
    a._answered()
    assert list(a._reply_times) == [22.9, 25.0]  # 30.5 × 0.75, 25


async def test_lease_send_goes_before_read_and_reader_yields():
    """보내기가 읽기보다 먼저 화면을 받는다. 읽는 중인 봇은 보내려는 봇이 있으면 화면을 넘긴다."""
    from adapters.phone.gateway import Leases

    ls = Leases()
    reader = await ls.acquire("pickle", 1, 30, "read")
    assert reader is not None
    assert not await ls.yield_to_send(reader.id)  # 기다리는 보내기가 없으면 계속 읽는다
    assert not await ls.yield_to_send(reader.id, any_waiting=True)  # 아무도 안 기다린다

    order: list[str] = []

    async def take(holder: str, priority: str) -> None:
        got = await ls.acquire(holder, 5, 30, priority)
        order.append(holder)
        await asyncio.sleep(0.05)
        await ls.release(got.id)

    late_read = asyncio.create_task(take("vooy-read", "read"))
    await asyncio.sleep(0.05)
    send = asyncio.create_task(take("cue-send", "send"))
    await asyncio.sleep(0.05)
    assert await ls.yield_to_send(reader.id)  # 보내기가 기다린다 → 넘긴다
    await asyncio.gather(late_read, send)
    assert order == ["cue-send", "vooy-read"]  # 먼저 와서 기다린 읽기보다 보내기가 먼저


async def test_lease_reader_without_reply_yields_to_other_reader():
    """아직 답이 없는(생각 중인) 봇은 읽으려고 기다리는 다른 봇에게 화면을 넘긴다. 답이 들어오는 중이면 넘기지 않는다."""
    from adapters.phone.gateway import Leases

    ls = Leases()
    a = await ls.acquire("vooy", 1, 30, "read")
    other = asyncio.create_task(ls.acquire("cue", 5, 30, "read"))
    await asyncio.sleep(0.05)
    assert not await ls.yield_to_send(a.id)  # 답이 들어오는 중: 보내기 대기만 본다
    assert await ls.yield_to_send(a.id, any_waiting=True)  # 생각 중: 넘긴다
    got = await other
    assert got is not None and ls.current.holder == "cue"


async def test_default_stays_in_app_without_home(stack, tmp_path, monkeypatch):
    """기본값: 보내기·읽기 뒤 홈 버튼을 누르지 않는다(홈 직후 5초 동안 다른 앱 띄우기가 늦어진다). 알림이 없어도
    확인 시점에 화면을 읽어 답을 받는다."""
    base, _, phone = stack
    monkeypatch.setenv("ANDROID_DEVICE_FACTORY", "")
    a = _chat_adapter("pickle", base, tmp_path)
    a.android.home_after_use = False
    a.android.expect_reply_s, a.android.verify_margin_s = 0.5, 0
    try:
        homes = phone.handled.count("global")
        res = await a.ask("홈 없이", "t1", timeout_s=30)
        assert res.ok and res.raw == "echo: 홈 없이", res
        assert phone.handled.count("global") == homes  # 홈(global) 명령 없음
        assert phone._fg_pkg() == "com.pickle.client"
    finally:
        await a.device.aclose()
