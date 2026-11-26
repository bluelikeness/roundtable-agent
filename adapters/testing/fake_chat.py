"""계약 테스트용 가짜 채팅 앱(피클·뮤즈·Cue 흉내). 가짜 휴대폰(fake_phone.py) 안에서 돈다.

실제 앱의 접근성 트리를 단순하게 흉내 낸다: 봇 말풍선은 왼쪽 TextView, 내 말풍선은 오른쪽 TextView,
버튼은 Button, 아래에 입력창(EditText)과 전송 버튼. 앱별 차이(입력창·전송 버튼 이름, 알림 채널)는
PROFILES 에 둔다. 실제 기기·계정·대화 내용은 쓰지 않는다.

Cue(ai.manus.agents) 흉내(2026-11-11 실기기 26.18.14 구조): 앱을 열면 입력줄이 "길게 눌러 말하기, 탭하여
입력" 버튼일 수 있다(탭하면 글 입력 모드). 입력창은 탭해 포커스를 줘야 넣은 글이 반영된다. 입력줄 오른쪽
버튼은 이름 없는 ImageView 다. 글이 없으면 오른쪽 끝이 마이크(누르면 음성 모드 → voice_entered, 시험 실패),
글이 있으면 마이크가 왼쪽으로 밀리고 그 자리(같은 bounds·클래스)에 전송 버튼이 생긴다. 트리로는 구분되지
않고 그림(스크린샷)으로만 구분된다. 말풍선 글은 content-desc 에 있다(Flutter).

text 지시어로 결과를 고정한다.
  [chat:slow]     응답하지 않는다
  [chat:buttons]  버튼 두 개("예약", "다른 곳")가 달린 응답
  [chat:confirm]  결제 확인 버튼("결제하기")이 달린 응답
  [chat:progress] 진행 메시지를 먼저 보내고 실제 응답
  [chat:file]     답에 그림 하나를 띄운다(첫 첨부가 있으면 mock-<이름>, 없으면 fake.png). 실제 앱처럼 저장하지 않는다.
                  그림을 누르면 크게 보기가 뜨고, 저장 버튼(PROFILES save_desc)을 눌러야 가짜 휴대폰 저장소로 간다
  그 밖           "echo: <본문>"
공유하기로 받은 첨부가 있으면 답 끝에 "받은 첨부: <이름들>"을 붙인다.
로그인 상태는 <dir>/session 파일 유무. 지우면 로그인 화면이 나온다.
"""

from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from xml.sax.saxutils import quoteattr

W, H = 1080, 2400
TOP, INPUT_TOP = 250, 2040
DIRECTIVE_RE = re.compile(r"\[chat:(\w+)\]\s*")

PROFILES = {
    "com.pickle.client": {
        "input": {"rid": "", "desc": ""},
        "send": {"cls": "android.view.View", "rid": "", "desc": "보내기", "only_when_text": True},
        "save_desc": "사진 저장",
        "channel": "messages",
        "progress_channel": None,
        "per_message_notif": True,
        # 실기기: 봇이 읽으면 내 말풍선 아래에 "방금 읽음" 이 뜬다(답으로 오인하면 안 됨)
        "receipt": "방금 읽음",
    },
    "com.facebook.aura": {
        "input": {"rid": "hatch-message-input", "desc": ""},
        "send": {"cls": "android.widget.Button", "rid": "hatch-send-button", "desc": "메시지 보내기", "only_when_text": False},
        "save_desc": "다운로드",
        "channel": "aura_conversations",
        "progress_channel": "aura_run_in_background",
        "per_message_notif": False,
    },
    "ai.manus.agents": {
        "input": {"rid": "", "desc": ""},
        "send": {"cls": "android.widget.ImageView", "rid": "", "desc": "", "only_when_text": True},
        "mic": {"cls": "android.widget.ImageView", "rid": "", "desc": ""},
        "mic_shifts": True,
        "needs_focus": True,
        "composer_gate": "길게 눌러 말하기, 탭하여 입력",
        "bubble_attr": "desc",
        # 실기기: 내 말풍선 desc 가 "시각 + 보낸 글" 한 덩어리다
        "own_prefix": "7:20 오후\n",
        "channel": "messages",
        "progress_channel": None,
        "per_message_notif": False,
    },
}


@dataclass
class Msg:
    role: str  # own | bot
    text: str
    buttons: list[str] = field(default_factory=list)
    media: tuple[str, str, bytes] | None = None  # 답 안의 그림(이름, 종류, 내용)


class FakeChatApp:
    def __init__(self, pkg: str, state_dir: str | Path, latency: float | None = None):
        self.pkg = pkg
        self.p = PROFILES[pkg]
        self.dir = Path(state_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        if not (self.dir / ".initialized").exists():
            (self.dir / "session").write_text("logged-in", encoding="utf-8")
            (self.dir / ".initialized").write_text("1", encoding="utf-8")
        self.latency = latency if latency is not None else float(os.environ.get("FAKE_CHAT_LATENCY", "1.0"))
        self.messages: list[Msg] = [Msg("bot", "안녕하세요! 무엇을 도와드릴까요?")]
        self.foreground = False
        self.input = ""
        self.typing = 0
        self.text_mode = not self.p.get("composer_gate")
        self.focused = False
        self._icons: list[tuple[tuple[int, int, int, int], str]] = []
        self.voice_entered = 0  # 마이크를 누른 횟수(음성 모드). 어댑터가 절대 누르면 안 된다
        self.drop_sends = 0  # 이 횟수만큼 전송 버튼을 눌러도 보내지지 않는다(글은 입력창에 남음)
        self._targets: list[tuple[tuple[int, int, int, int], Callable[[], None]]] = []
        # 가짜 휴대폰이 꽂는다: (채널, posted?) 알림을 올린다
        self.notify: Callable[[str, str, bool], None] | None = None
        # 공유하기로 받은 첨부(이름, 종류, 내용). 다음 전송에 붙는다
        self.attached: list[tuple[str, str, bytes]] = []
        # 가짜 휴대폰이 꽂는다: (이름, 종류, 내용) 을 휴대폰 저장소에 남긴다
        self.save: Callable[[str, str, bytes], None] | None = None
        self.viewer: tuple[str, str, bytes] | None = None  # 크게 보기로 연 그림

    @property
    def logged_in(self) -> bool:
        return (self.dir / "session").is_file()

    # --- 기기 명령 --------------------------------------------------------

    async def app_start(self, package: str) -> None:
        self.foreground = True
        if self.p.get("composer_gate"):
            self.text_mode = False  # 다시 열면 입력줄은 "탭하여 입력" 버튼부터
        self.focused = False
        self.poisoned = False

    async def press(self, key: str) -> None:
        if key == "back" and self.viewer is not None:
            self.viewer = None
        elif key == "home":
            self.foreground = False
            self.viewer = None

    # 안드로이드 10 노트9 의 뮤즈 흉내: set_text 는 성공을 돌려주지만 글이 들어가지 않고, 그 뒤로는 앱을 다시
    # 열 때까지 붙여넣기도 안 먹는다. 포커스를 준 뒤 붙여넣기만 된다
    set_text_ignored = False

    async def set_text(self, selector: dict[str, str], text: str) -> None:
        if self.set_text_ignored:
            self.poisoned = True
            return
        rid = self.p["input"]["rid"]
        ok = (rid and selector.get("resourceId") == rid) or selector.get("className") == "android.widget.EditText"
        if self.p.get("needs_focus") and not self.focused:
            return  # Flutter: 포커스 없이 넣은 글은 반영되지 않는다(오류도 없다)
        if not ok or not self.text_mode:
            raise RuntimeError(f"입력창을 찾지 못함: {selector}")
        self.input = text

    async def send_keys(self, text: str) -> None:
        if self.set_text_ignored and not self.focused:
            raise RuntimeError("붙여넣기 실패(포커스된 입력창 없음)")
        if self.poisoned:
            return
        self.input = text

    async def click(self, x: int, y: int) -> None:
        self._render()
        for (l, t, r, b), action in reversed(self._targets):
            if l <= x <= r and t <= y <= b:
                action()
                return

    async def screenshot_png(self) -> bytes:
        """아이콘 종류마다 다른 밝기로 칠한 화면. 그림 비교 규칙을 시험한다."""
        import io

        from PIL import Image, ImageDraw

        self._render()
        img = Image.new("L", (W, H), 30)
        draw = ImageDraw.Draw(img)
        shade = {"mic": 220, "send": 90, "other": 150}
        for b, kind in self._icons:
            draw.rectangle(b, fill=shade.get(kind, 150))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    async def dump(self) -> str:
        return self._render()

    # --- 봇 흉내 ----------------------------------------------------------

    def _emit(self, channel: str, key: str, posted: bool) -> None:
        if self.notify is not None:
            self.notify(channel, key, posted)

    def _send(self) -> None:
        if self.drop_sends > 0 and self.input.strip():
            self.drop_sends -= 1
            return
        text = self.input.strip()
        self.input = ""
        if not text:
            return
        self.messages.append(Msg("own", text))
        directives = set(DIRECTIVE_RE.findall(text))
        body = DIRECTIVE_RE.sub("", text).strip()
        if "slow" in directives:
            return
        if "buttons" in directives:
            reply = [Msg("bot", "강남 OO식당 18:30 2명 가능합니다.", ["예약", "다른 곳"])]
        elif "confirm" in directives:
            reply = [Msg("bot", "KTX 서울→부산 11/12 07:00 결제 58,800원. 진행할까요?", ["결제하기"])]
        else:
            reply = [Msg("bot", f"echo: {body}")]
        files, self.attached = self.attached, []
        if files:
            reply[-1] = Msg("bot", reply[-1].text + " 받은 첨부: " + ", ".join(n for n, _, _ in files), reply[-1].buttons)
        if "file" in directives:
            media = (f"mock-{files[0][0]}", files[0][1], files[0][2]) if files else ("fake.png", "image/png", b"fake")
            reply.append(Msg("bot", "", media=media))
        if "progress" in directives:
            reply.insert(0, Msg("bot", "찾아볼게요…"))
        self._later(reply)

    def _tap_mic(self) -> None:
        self.voice_entered += 1

    def _enter_text_mode(self) -> None:
        self.text_mode = True

    def _focus(self) -> None:
        self.focused = True

    def _open_viewer(self, media: tuple[str, str, bytes]) -> None:
        self.viewer = media

    def _save_viewer(self) -> None:
        if self.viewer is not None and self.save is not None:
            self.save(*self.viewer)

    def _tap_button(self, label: str) -> None:
        self._later([Msg("bot", "결제가 완료되었습니다." if label == "결제하기" else f"'{label}' 선택하셨습니다.")])

    def _later(self, replies: list[Msg]) -> None:
        self.typing += 1
        prog = self.p["progress_channel"]

        async def deliver() -> None:
            try:
                if prog:
                    self._emit(prog, "progress", True)
                for i, m in enumerate(replies):
                    await asyncio.sleep(self.latency if i == 0 else self.latency / 3)
                    self.messages.append(m)
                    if self.p["per_message_notif"]:
                        self._emit(self.p["channel"], f"m{len(self.messages)}", True)
                if prog:
                    self._emit(prog, "progress", False)
                if not self.p["per_message_notif"]:
                    self._emit(self.p["channel"], "conv", True)
            finally:
                self.typing -= 1

        asyncio.get_running_loop().create_task(deliver())

    # --- 접근성 트리 ------------------------------------------------------

    def _render(self) -> str:
        parts: list[str] = []
        self._targets = []
        self._icons = []

        def node(cls: str, b: tuple[int, int, int, int], close: bool = True, **a) -> None:
            attrs = {
                "class": cls,
                "package": self.pkg,
                "resource-id": a.get("rid", ""),
                "content-desc": a.get("desc", ""),
                "text": a.get("text", ""),
                "clickable": "true" if a.get("clickable") else "false",
                "bounds": "[{},{}][{},{}]".format(*b),
            }
            parts.append("<node " + " ".join(f"{k}={quoteattr(str(v))}" for k, v in attrs.items()) + (" />" if close else ">"))

        parts.append("<?xml version='1.0' encoding='UTF-8' standalone='yes' ?><hierarchy rotation=\"0\">")
        if not self.foreground:
            node("android.widget.FrameLayout", (0, 0, W, H), desc="Home")
        elif self.viewer is not None:
            # 크게 보기: 뒤로·저장 버튼만
            node("android.widget.FrameLayout", (0, 0, W, H), close=False)
            node("android.view.View", (37, 74, 163, 200), desc="뒤로", clickable=True)
            if self.p.get("save_desc"):
                sb = (917, 74, 1043, 200)
                node("android.view.View", sb, desc=self.p["save_desc"], clickable=True)
                self._targets.append((sb, self._save_viewer))
            node("android.widget.ImageView", (0, 600, W, 1800))
            parts.append("</node>")
        elif not self.logged_in:
            node("android.widget.FrameLayout", (0, 0, W, H), close=False)
            node("android.widget.TextView", (100, 1500, 980, 1600), text="로그인", clickable=True)
            parts.append("</node>")
        else:
            node("android.widget.FrameLayout", (0, 0, W, H), close=False)
            # 아래에서 위로 화면에 들어가는 만큼만
            rows: list[tuple[Msg, int, int]] = []
            y = INPUT_TOP - 20
            for m in reversed(self.messages):
                h = 420 if m.media else 100 + 40 * (len(m.text) // 25) + (110 if m.buttons else 0)
                if y - h < TOP:
                    break
                y -= h
                rows.append((m, y, h))
            for m, top, h in reversed(rows):
                if m.media is not None:
                    mb = (32, top, 432, top + 400)
                    node("android.view.View", mb, clickable=True)
                    self._targets.append((mb, lambda media=m.media: self._open_viewer(media)))
                    continue
                th = h - (110 if m.buttons else 0) - 20
                body = {"desc": m.text} if self.p.get("bubble_attr") == "desc" else {"text": m.text}
                cls = "android.view.View" if self.p.get("bubble_attr") == "desc" else "android.widget.TextView"
                if m.role == "own":
                    if self.p.get("own_prefix"):
                        body = {k: self.p["own_prefix"] + v for k, v in body.items()}
                    node(cls, (437, top, 1014, top + th - 30), **body)
                    if self.p.get("receipt"):
                        node("android.widget.TextView", (800, top + th - 28, 1014, top + th), text=self.p["receipt"])
                else:
                    node(cls, (66, top, 900, top + th), **body)
                    for i, label in enumerate(m.buttons):
                        bb = (66 + i * 330, top + th + 10, 366 + i * 330, top + th + 100)
                        node("android.widget.Button", bb, text=label, clickable=True)
                        self._targets.append((bb, lambda label=label: self._tap_button(label)))
            ip = self.p["input"]
            sb = (913, INPUT_TOP + 16, 1039, INPUT_TOP + 142)
            if not self.text_mode:
                gb = (40, INPUT_TOP + 16, 1040, INPUT_TOP + 142)
                node("android.view.View", gb, desc=self.p["composer_gate"], clickable=True)
                self._targets.append((gb, self._enter_text_mode))
            else:
                sp = self.p["send"]
                shifted = bool(self.input) and self.p.get("mic_shifts")
                fb = (163, INPUT_TOP + 16, 767 if shifted else 797, INPUT_TOP + 142)
                node("android.widget.EditText", fb, rid=ip["rid"], desc=ip["desc"], text=self.input, clickable=True)
                self._targets.append((fb, self._focus))
                if self.p.get("mic") and (shifted or not self.input):
                    mp = self.p["mic"]
                    mb = (777, INPUT_TOP + 16, 903, INPUT_TOP + 142) if shifted else sb
                    node(mp["cls"], mb, rid=mp["rid"], desc=mp["desc"], clickable=True)
                    self._targets.append((mb, self._tap_mic))
                    self._icons.append((mb, "mic"))
                if self.input or not sp["only_when_text"]:
                    node(sp["cls"], sb, rid=sp["rid"], desc=sp["desc"], clickable=True)
                    self._targets.append((sb, self._send))
                    self._icons.append((sb, "send"))
            parts.append("</node>")
        parts.append("</hierarchy>")
        return "".join(parts)
