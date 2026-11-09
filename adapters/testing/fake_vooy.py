"""계약 테스트용 가짜 Vooy 기기. 실제 앱의 접근성 트리 모양(testID·content-desc)을 흉내 낸다.

VooyAdapter 를 실봇 없이 계약 테스트(make test-adapters)에 올리기 위한 것이다. 어댑터 코드는 그대로이고
android_base.Device 자리에 이 클래스만 꽂는다(env ANDROID_DEVICE_FACTORY=adapters.testing.fake_vooy:FakeVooyDevice).
실제 기기·계정·대화 내용은 쓰지 않는다. 트리 구조는 P2 에서 본 Vooy 1.4.4 를 바탕으로 손으로 만든 것이다.

결과를 고정하려고 Mock 처럼 text 지시어를 쓴다(봇이 메시지를 읽고 행동을 바꾸는 것으로 본다).
  [vooy:slow]      응답하지 않는다
  [vooy:buttons]   버튼 두 개("예약", "다른 곳")가 달린 응답
  [vooy:confirm]   결제 확인 버튼("결제하기")이 달린 응답
  [vooy:image]     이미지 하나가 달린 응답
  [vooy:progress]  진행 메시지를 먼저 보내고 실제 응답
  [vooy:limit]     사용 제한 안내
  그 밖           "echo: <본문>"

로그인 상태는 <state>/session 파일 유무로 흉내 낸다. 지우면 로그인 화면이 나온다.
login_completed() 는 운영자가 noVNC 로 로그인을 끝낸 것을 흉내 내 session 을 다시 만든다.
"""

from __future__ import annotations

import asyncio
import io
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from xml.sax.saxutils import quoteattr

PKG = "com.vooy.mobile"
W, H = 1080, 2400
CONV_TOP, CONV_BOTTOM = 300, 2030
DIRECTIVE_RE = re.compile(r"\[vooy:(\w+)\]\s*")


@dataclass
class Msg:
    role: str  # "own" | "bot"
    text: str
    stamp: str
    buttons: list[str] = field(default_factory=list)
    image: bool = False


def _stamp() -> str:
    t = time.localtime()
    h = t.tm_hour % 12 or 12
    return f"{h}:{t.tm_min:02d} {'AM' if t.tm_hour < 12 else 'PM'}"


class _Xml:
    def __init__(self) -> None:
        self.parts: list[str] = []

    def open(self, cls: str, bounds: tuple[int, int, int, int], **a: str | bool) -> None:
        attrs = {
            "class": cls,
            "package": PKG,
            "resource-id": a.pop("rid", ""),
            "content-desc": a.pop("desc", ""),
            "text": a.pop("text", ""),
            "clickable": "true" if a.pop("clickable", False) else "false",
            "bounds": "[{},{}][{},{}]".format(*bounds),
        }
        self.parts.append("<node " + " ".join(f"{k}={quoteattr(str(v))}" for k, v in attrs.items()) + ">")

    def close(self) -> None:
        self.parts.append("</node>")

    def leaf(self, cls: str, bounds, **a) -> None:
        self.open(cls, bounds, **a)
        self.close()


class FakeVooyDevice:
    def __init__(self, serial: str = "fake") -> None:
        self.serial = serial
        self.dir = Path(os.environ.get("FAKE_VOOY_DIR", "/data/fake-vooy"))
        self.dir.mkdir(parents=True, exist_ok=True)
        self.latency = float(os.environ.get("FAKE_VOOY_LATENCY", "1.0"))
        if not (self.dir / ".initialized").exists():
            (self.dir / "session").write_text("logged-in", encoding="utf-8")
            (self.dir / ".initialized").write_text("1", encoding="utf-8")
        self.messages: list[Msg] = [Msg("bot", "안녕하세요! 무엇을 도와드릴까요?", _stamp())]
        self.foreground = False
        self.input = ""
        self.typing = 0  # 응답 대기 중인 건수(응답 중 표시)
        self._targets: list[tuple[tuple[int, int, int, int], Callable[[], None]]] = []
        self._image_boxes: list[tuple[int, int, int, int]] = []
        self.clicks: list[tuple[int, int]] = []

    # --- 상태 --------------------------------------------------------------

    @property
    def logged_in(self) -> bool:
        return (self.dir / "session").is_file()

    async def is_booted(self) -> bool:
        return True

    async def current_package(self) -> str | None:
        return PKG if self.foreground else "com.google.android.apps.nexuslauncher"

    async def app_start(self, package: str) -> None:
        if package == PKG:
            self.foreground = True

    async def press(self, key: str) -> None:
        return None

    async def reboot(self) -> None:
        self.foreground = False

    async def login_completed(self) -> None:
        (self.dir / "session").write_text("logged-in", encoding="utf-8")

    async def clear_clipboard(self) -> None:
        return None

    async def send_keys(self, text: str) -> None:
        self.input = text

    async def set_text(self, selector: dict[str, str], text: str) -> None:
        if selector.get("description") != "Message":
            raise RuntimeError(f"UiObjectNotFound: {selector}")
        self.input = text

    async def click(self, x: int, y: int) -> None:
        self.clicks.append((x, y))
        self._render()  # 화면 좌표를 최신으로
        for (l, t, r, b), action in reversed(self._targets):
            if l <= x <= r and t <= y <= b:
                action()
                return

    async def screenshot_png(self) -> bytes:
        from PIL import Image, ImageDraw

        self._render()
        img = Image.new("RGB", (W, H), "white")
        draw = ImageDraw.Draw(img)
        for box in self._image_boxes:
            draw.rectangle(box, fill=(200, 80, 40))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    # --- 봇 흉내 -----------------------------------------------------------

    def _send(self) -> None:
        text = self.input.strip()
        self.input = ""
        if not text:
            return
        self.messages.append(Msg("own", text, _stamp()))
        directives = set(DIRECTIVE_RE.findall(text))
        body = DIRECTIVE_RE.sub("", text).strip()
        if "slow" in directives:
            return
        if "buttons" in directives:
            reply = Msg("bot", "강남 OO식당 18:30 2명 가능합니다.", "", buttons=["예약", "다른 곳"])
        elif "confirm" in directives:
            reply = Msg("bot", "KTX 서울→부산 11/12 07:00 결제 58,800원. 진행할까요?", "", buttons=["결제하기"])
        elif "image" in directives:
            reply = Msg("bot", "메뉴 사진입니다.", "", image=True)
        elif "limit" in directives:
            reply = Msg("bot", "오늘 사용량을 모두 썼어요. 내일 다시 이용해 주세요.", "")
        else:
            reply = Msg("bot", f"echo: {body}", "")
        first = Msg("bot", "찾는 중이에요…", "") if "progress" in directives else None
        self._later(reply, first)

    def _tap_button(self, label: str) -> None:
        if label == "결제하기":
            reply = Msg("bot", "결제가 완료되었습니다.", "")
        else:
            reply = Msg("bot", f"'{label}' 선택하셨습니다.", "")
        self._later(reply, None)

    def _later(self, reply: Msg, first: Msg | None) -> None:
        self.typing += 1

        async def deliver() -> None:
            try:
                if first is not None:
                    await asyncio.sleep(self.latency / 2)
                    first.stamp = _stamp()
                    self.messages.append(first)
                await asyncio.sleep(self.latency)
                reply.stamp = _stamp()
                self.messages.append(reply)
            finally:
                self.typing -= 1

        asyncio.get_running_loop().create_task(deliver())

    # --- 접근성 트리 -------------------------------------------------------

    async def dump(self) -> str:
        return self._render()

    def _render(self) -> str:
        x = _Xml()
        self._targets = []
        self._image_boxes = []
        x.parts.append("<?xml version='1.0' encoding='UTF-8' standalone='yes' ?><hierarchy rotation=\"0\">")
        if not self.foreground:
            x.leaf("android.widget.FrameLayout", (0, 0, W, H), desc="Home")
        elif not self.logged_in:
            x.open("android.widget.FrameLayout", (0, 0, W, H))
            x.leaf("android.widget.TextView", (100, 1500, 980, 1600), text="Continue with Google", clickable=True)
            x.leaf("android.widget.TextView", (100, 1650, 980, 1750), text="Continue with email", clickable=True)
            x.close()
        else:
            self._render_chat(x)
        x.parts.append("</hierarchy>")
        return "".join(x.parts)

    def _render_chat(self, x: _Xml) -> None:
        # 아래에서 위로 쌓아 화면에 들어가는 만큼만 그린다(가상화된 목록 흉내)
        rows: list[tuple[Msg, int]] = []
        y = CONV_BOTTOM - 20
        if self.typing:
            y -= 80
        for m in reversed(self.messages):
            h = 120 + 30 * (len(m.text) // 30) + (90 if m.buttons else 0) + (320 if m.image else 0)
            if y - h < CONV_TOP:
                break
            y -= h
            rows.append((m, y))
        x.open("android.widget.FrameLayout", (0, 0, W, H))
        x.open("android.widget.ScrollView", (0, 0, W, H), desc="Conversation")
        # 실제 앱처럼 트리 순서는 최신이 먼저
        for m, top in rows:
            self._render_msg(x, m, top)
        if self.typing:
            x.leaf("android.view.ViewGroup", (42, CONV_BOTTOM - 90, 400, CONV_BOTTOM - 20), rid="agent-message-segments")
        x.close()
        x.open("android.view.ViewGroup", (42, 2040, 1038, 2183), rid="composer-input-pill")
        x.leaf("android.widget.EditText", (155, 2066, 794, 2156), desc="Message", text=self.input, clickable=True)
        x.leaf("android.widget.Button", (925, 2067, 1014, 2156), desc="Send message", clickable=True)
        self._targets.append(((925, 2067, 1014, 2156), self._send))
        self._targets.append(((155, 2066, 794, 2156), lambda: None))
        x.close()
        x.leaf("android.view.View", (456, 2214, 624, 2361), rid="android-gnb-home", desc="vooy tab, Done", clickable=True)
        self._targets.append(((456, 2214, 624, 2361), lambda: None))
        x.close()

    def _render_msg(self, x: _Xml, m: Msg, top: int) -> None:
        text_h = 60 + 30 * (len(m.text) // 30)
        if m.role == "own":
            box = (253, top, 1038, top + text_h + 40)
            x.open("android.view.ViewGroup", (42, top, 1038, box[3] + 10))
            x.leaf("android.view.ViewGroup", (42, top, 1038, box[3] + 10), rid="message-selection-content")
            x.open("android.view.ViewGroup", box, desc=f"{m.text}, {m.stamp}", clickable=True)
            x.open("android.view.ViewGroup", box, rid="own-message-bubble")
            x.leaf("android.widget.TextView", (285, top + 20, 1007, top + text_h), desc=m.text, text=m.text + "￼")
            x.leaf("android.widget.TextView", (901, top + text_h, 1007, top + text_h + 30), desc=m.stamp, text=m.stamp)
            x.close()
            x.close()
            x.close()
            return
        box = (42, top, 953, top + text_h + 40)
        bottom = box[3] + (90 if m.buttons else 0) + (320 if m.image else 0)
        x.open("android.view.ViewGroup", (42, top, 1038, bottom))
        x.leaf("android.view.ViewGroup", (42, top, 1038, bottom), rid="message-selection-content")
        x.open("android.view.ViewGroup", box, desc=m.text, clickable=True)
        x.open("android.view.ViewGroup", box, rid="other-message-bubble")
        x.leaf("android.widget.TextView", (74, top + 20, 922, top + text_h), text=m.text)
        x.leaf("android.widget.TextView", (816, top + text_h, 922, top + text_h + 30), desc=m.stamp, text=m.stamp)
        x.close()
        x.close()
        y = box[3]
        if m.image:
            ib = (74, y + 10, 474, y + 310)
            x.leaf("android.widget.ImageView", ib)
            self._image_boxes.append(ib)
            y += 320
        for i, label in enumerate(m.buttons):
            bb = (74 + i * 320, y + 10, 374 + i * 320, y + 80)
            x.open("android.widget.Button", bb, desc=label, clickable=True)
            x.leaf("android.widget.TextView", bb, text=label)
            x.close()
            self._targets.append((bb, lambda label=label: self._tap_button(label)))
        x.close()
