"""Vooy 어댑터·android_base 단위 테스트. 가짜 기기(adapters.testing.fake_vooy)로 프로세스 안에서 돈다.

픽스처 XML 은 손으로 만든 것이다. 실제 기기의 덤프(대화 내용이 들어 있다)를 넣지 않는다.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PIL", reason="android 의존성 그룹 필요: uv run --group android pytest")

from adapters.android_base import Bounds, UiNode, parse_hierarchy  # noqa: E402
from adapters.base import AdapterConfig  # noqa: E402
from adapters.testing.fake_vooy import FakeVooyDevice  # noqa: E402
from adapters.vooy.adapter import VooyAdapter, action_id_for  # noqa: E402

XML = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node class="android.widget.ScrollView" content-desc="Conversation" bounds="[0,3][1080,2400]" clickable="false" text="" resource-id="">
    <node class="android.view.ViewGroup" content-desc="둘째 답, 9:01 AM" clickable="true" bounds="[42,900][953,1100]" text="" resource-id="">
      <node class="android.view.ViewGroup" resource-id="other-message-bubble" bounds="[42,900][953,1100]" text="" content-desc="" clickable="false">
        <node class="android.widget.TextView" text="둘째 답" bounds="[74,920][922,1000]" resource-id="" content-desc="" clickable="false"/>
        <node class="android.widget.TextView" text="9:01 AM" content-desc="9:01 AM" bounds="[816,1050][922,1090]" resource-id="" clickable="false"/>
      </node>
    </node>
    <node class="android.view.ViewGroup" content-desc="질문, 9:00 AM" clickable="true" bounds="[253,600][1038,700]" text="" resource-id="">
      <node class="android.view.ViewGroup" resource-id="own-message-bubble" bounds="[253,600][1038,700]" text="" content-desc="" clickable="false">
        <node class="android.widget.TextView" text="질문&#xfffc;" content-desc="질문" bounds="[285,620][1007,680]" resource-id="" clickable="false"/>
      </node>
    </node>
    <node class="android.widget.Button" content-desc="sun, 1 person" clickable="true" bounds="[882,700][1028,760]" text="" resource-id=""/>
    <node class="android.view.ViewGroup" content-desc="첫 답" clickable="true" bounds="[42,200][953,400]" text="" resource-id="">
      <node class="android.view.ViewGroup" resource-id="other-message-bubble" bounds="[42,200][953,400]" text="" content-desc="" clickable="false">
        <node class="android.widget.TextView" text="첫 답" bounds="[74,220][922,300]" resource-id="" content-desc="" clickable="false"/>
      </node>
    </node>
  </node>
</hierarchy>"""


def test_parse_and_find():
    root = parse_hierarchy(XML)
    bubbles = root.find_all(rid="other-message-bubble")
    assert len(bubbles) == 2
    assert bubbles[0].texts() == ["둘째 답", "9:01 AM"]
    assert root.find(desc="sun, 1 person").clickable
    assert Bounds.parse("[1,2][3,4]").center == (2, 3)
    assert Bounds.parse("garbage").empty
    assert isinstance(bubbles[0].ancestor(5), UiNode)


@pytest.fixture
async def vooy(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_VOOY_DIR", str(tmp_path / "fake"))
    monkeypatch.setenv("FAKE_VOOY_LATENCY", "0.1")
    monkeypatch.setenv("ANDROID_DEVICE_FACTORY", "adapters.testing.fake_vooy:FakeVooyDevice")
    cfg = AdapterConfig.load(__import__("adapters.vooy", fromlist=["x"]).__path__[0] + "/config.yaml")
    cfg = cfg.model_copy(update={"min_interval_s": 0})
    cfg.timeouts.quiet_s = 0.2
    a = VooyAdapter(cfg, tmp_path / "state")
    a.poll_s = 0.05
    a.android.app_start_wait_s = 1
    a.SEND_SETTLE_S = 0
    a.SEND_CHECK_S = 0.05
    a.SEND_CONFIRM_S = 0.3
    return a


def _drop_send_clicks(vooy, n):
    """전송 버튼 클릭을 앞에서 n 번 무시한다(RN 이 입력값을 아직 반영하지 않은 상태 흉내)."""
    dev = vooy.device
    real = dev.click
    left = {"n": n}

    async def click(x, y):
        if 925 <= x <= 1014 and y >= 2000 and left["n"] > 0:
            left["n"] -= 1
            dev.clicks.append((x, y))
            return
        await real(x, y)

    dev.click = click


async def test_send_retries_when_first_tap_ignored(vooy):
    await vooy.ask("준비", "t1")  # 앱 기동·기기 준비
    _drop_send_clicks(vooy, 1)
    r = await vooy.ask("안녕 Vooy", "t1")
    assert r.ok, r
    assert r.raw == "echo: 안녕 Vooy"  # 한 번만 보냈다(중복 없음)


async def test_send_gives_up_when_input_never_clears(vooy):
    await vooy.ask("준비", "t1")
    _drop_send_clicks(vooy, 99)
    r = await vooy.ask("안녕 Vooy", "t1")
    assert not r.ok and r.error == "unavailable", r  # BackendDown → unavailable


def test_bubble_text_and_order(vooy):
    root = parse_hierarchy(XML)
    bubbles = vooy._bubbles(root, "other-message-bubble")
    assert [vooy._bubble_text(b) for b in bubbles] == [("첫 답", ""), ("둘째 답", "9:01 AM")]


async def test_ask_echo_starts_app_and_collects_below_own_bubble(vooy):
    assert isinstance(vooy.device, FakeVooyDevice)
    r = await vooy.ask("안녕 Vooy", "t1")
    assert r.ok, r
    assert r.raw == "echo: 안녕 Vooy"
    assert r.structured == {} and r.needs_input is False
    assert vooy.device.foreground  # _ensure_ready 가 앱을 띄웠다
    # 이전 인사말(기준점 위)은 섞이지 않는다
    assert "무엇을" not in r.raw
    r2 = await vooy.ask("두 번째", "t1")
    assert r2.raw == "echo: 두 번째" and not set(r.message_ids) & set(r2.message_ids)


async def test_send_button_only_after_typing(vooy):
    """새 Vooy 는 입력창이 비면 전송 버튼이 없다(음성 버튼만). 대화방 준비로 보고 글을 넣은 뒤 전송한다."""
    vooy.device.send_only_with_text = True
    r = await vooy.ask("전송 버튼이 늦게 나오는 앱", "t1")
    assert r.ok, r
    assert r.raw == "echo: 전송 버튼이 늦게 나오는 앱"


async def test_buttons_and_tap(vooy):
    r = await vooy.ask("[vooy:buttons] 강남 식당", "t1")
    assert r.ok, r
    labels = [b["label"] for b in r.structured["buttons"]]
    assert labels == ["예약", "다른 곳"]
    assert "[버튼: 예약]" in r.raw and r.needs_input
    aid = r.structured["buttons"][0]["action_id"]
    assert aid == action_id_for("예약")
    t = await vooy.ask(f"[tap:{aid}]", "t1")
    assert t.ok, t
    assert t.raw == "'예약' 선택하셨습니다."


async def test_tap_button_gone_is_internal(vooy):
    r = await vooy.ask("[vooy:buttons] 강남 식당", "t1")
    aid = r.structured["buttons"][0]["action_id"]
    # 그사이 대화가 밀려 버튼이 화면에서 사라진 경우
    for i in range(20):
        vooy.device.messages.append(type(vooy.device.messages[0])("bot", f"다른 메시지 {i}" * 3, "1:00 PM"))
    vooy.threads["t1"].last_action_ids = [aid]
    t = await vooy.ask(f"[tap:{aid}]", "t1")
    assert t.ok is False and t.error == "internal"
    assert "화면에 없음" in vooy.logs[-1]["detail"]


async def test_confirmation(vooy):
    r = await vooy.ask("[vooy:confirm] 기차표", "t1")
    assert r.ok, r
    conf = r.structured["confirmation_required"]
    assert conf["action_id"] == action_id_for("결제하기")
    assert "58,800원" in conf["summary"]
    assert "buttons" not in r.structured
    assert vooy.threads["t1"].pending_confirmation


async def test_image_is_cropped(vooy):
    r = await vooy.ask("[vooy:image] 사진", "t1")
    assert r.ok, r
    url = r.structured["images"][0]["url"]
    data, ctype = vooy.load_image(url.rsplit("/", 1)[1])
    assert ctype == "image/png" and data.startswith(b"\x89PNG")


async def test_progress_is_ignored(vooy):
    r = await vooy.ask("[vooy:progress] 찾아줘", "t1")
    assert r.ok and r.raw == "echo: 찾아줘"


async def test_limit_message_is_rate_limited(vooy):
    r = await vooy.ask("[vooy:limit] 하나 더", "t1")
    assert r.ok is False and r.error == "rate_limited"


async def test_daily_limit(vooy):
    vooy.android.daily_limit = 1
    assert (await vooy.ask("하나", "t1")).ok
    r = await vooy.ask("둘", "t1")
    assert r.ok is False and r.error == "rate_limited"


async def test_login_required_and_complete(vooy):
    assert (await vooy.ask("확인", "t1")).ok
    (vooy.device.dir / "session").unlink()
    r = await vooy.ask("로그인 만료", "t1")
    assert r.ok is False and r.error == "login_required"
    assert (await vooy.status()).reason == "login_required"
    started = await vooy.login_start()
    assert started["flow"] == "vnc" and "vnc_url" in started
    assert await vooy.login_complete() == {"ok": True}
    assert (await vooy.status()).available


async def test_timeout_retries_once(vooy):
    r = await vooy.ask("[vooy:slow] 느리게", "t1", timeout_s=10)
    assert r.ok is False and r.error == "timeout"
    assert vooy.logs[-1].get("retried") is True
    own = [m for m in vooy.device.messages if m.role == "own"]
    assert [m.text for m in own] == ["[vooy:slow] 느리게"] * 2


async def test_backend_down_when_not_booted(vooy):
    async def no() -> bool:
        return False

    rebooted = []

    async def reboot() -> None:
        rebooted.append(1)

    vooy.android.boot_timeout_s = 0
    vooy.device.is_booted = no
    vooy.device.reboot = reboot
    h = await vooy.probe()
    assert h["available"] is False and h["reason"] == "backend_down"
    await vooy._recovering
    assert rebooted == [1]
