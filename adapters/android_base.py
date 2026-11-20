"""안드로이드 앱 경로 어댑터 공통 (docs/03-adapters.md 6·7·9장). Vooy·피클이 함께 쓴다.

여기 있는 것:
- 접근성 트리(uiautomator dump XML) 파싱과 노드 검색 (UiNode, parse_hierarchy)
- 기기 추상화 Device 와 실제 구현 U2Device(Python uiautomator2, 기기에 APK 설치 없음)
- AndroidAdapter: BotAdapter 위에 기기 연결·부팅 확인·복구(재부팅)·한글 입력·스크린샷 크롭을 얹는다

봇별 어댑터는 AndroidAdapter 를 상속해 훅 다섯 개만 채운다. 셀렉터는 selectors.yaml 로 분리한다
(앱 업데이트 때 코드 대신 YAML 만 고친다).

기기 연결은 adb 서버를 거친다. 어댑터 컨테이너 안에는 adb 서버를 두지 않고
ANDROID_ADB_SERVER_HOST/PORT 로 에뮬레이터 쪽 adb 서버에 붙는다(adbutils 가 읽는 env).
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import io
import json
import logging
import os
import re
import statistics
import time
import xml.etree.ElementTree as ET
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Protocol

import yaml
from pydantic import BaseModel, Field

from adapters.base import AdapterConfig, BackendDown, BotAdapter, SendLost

log = logging.getLogger("adapters.android")

BOUNDS_RE = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")
# 말풍선 텍스트에 섞여 나오는 개체 대체 문자(U+FFFC). 원문에서 지운다
OBJ_CHAR = "￼"


# ---------------------------------------------------------------------------
# 접근성 트리
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Bounds:
    left: int
    top: int
    right: int
    bottom: int

    @classmethod
    def parse(cls, s: str) -> "Bounds":
        m = BOUNDS_RE.fullmatch(s.strip()) if s else None
        if not m:
            return cls(0, 0, 0, 0)
        return cls(*(int(g) for g in m.groups()))

    @property
    def center(self) -> tuple[int, int]:
        return (self.left + self.right) // 2, (self.top + self.bottom) // 2

    @property
    def empty(self) -> bool:
        return self.right <= self.left or self.bottom <= self.top

    def contains(self, other: "Bounds") -> bool:
        return (
            self.left <= other.left and self.top <= other.top and self.right >= other.right and self.bottom >= other.bottom
        )

    def as_tuple(self) -> tuple[int, int, int, int]:
        return self.left, self.top, self.right, self.bottom


@dataclass(eq=False)
class UiNode:
    cls: str = ""
    rid: str = ""  # resource-id. React Native testID 가 그대로 나온다
    desc: str = ""  # content-desc
    text: str = ""
    package: str = ""
    clickable: bool = False
    bounds: Bounds = field(default_factory=lambda: Bounds(0, 0, 0, 0))
    children: list["UiNode"] = field(default_factory=list)
    parent: "UiNode | None" = None

    def walk(self) -> Iterator["UiNode"]:
        """전위 순회(자기 자신 포함)."""
        stack = [self]
        while stack:
            n = stack.pop()
            yield n
            stack.extend(reversed(n.children))

    def find_all(self, **sel: Any) -> list["UiNode"]:
        return [n for n in self.walk() if n.matches(**sel)]

    def find(self, **sel: Any) -> "UiNode | None":
        return next((n for n in self.walk() if n.matches(**sel)), None)

    def matches(
        self,
        rid: str | None = None,
        desc: str | None = None,
        text: str | None = None,
        desc_re: str | None = None,
        text_re: str | None = None,
        cls: str | None = None,
        clickable: bool | None = None,
    ) -> bool:
        if rid is not None and self.rid != rid:
            return False
        if desc is not None and self.desc != desc:
            return False
        if text is not None and self.text != text:
            return False
        if desc_re is not None and not re.search(desc_re, self.desc):
            return False
        if text_re is not None and not re.search(text_re, self.text):
            return False
        if cls is not None and not self.cls.endswith(cls):
            return False
        if clickable is not None and self.clickable != clickable:
            return False
        return True

    def texts(self) -> list[str]:
        """하위 TextView 들의 텍스트(트리 순서)."""
        return [n.text for n in self.walk() if n.text and n.cls.endswith("TextView")]

    def ancestor(self, levels: int) -> "UiNode":
        n = self
        for _ in range(levels):
            if n.parent is None:
                break
            n = n.parent
        return n


def parse_hierarchy(xml: str) -> UiNode:
    """uiautomator dump XML → UiNode 트리. 루트는 hierarchy 를 감싼 가상 노드."""
    root_el = ET.fromstring(xml)
    root = UiNode(cls="hierarchy")

    def conv(el: ET.Element, parent: UiNode) -> None:
        for c in el:
            if c.tag != "node":
                continue
            a = c.attrib
            n = UiNode(
                cls=a.get("class", ""),
                rid=a.get("resource-id", ""),
                desc=a.get("content-desc", ""),
                text=a.get("text", ""),
                package=a.get("package", ""),
                clickable=a.get("clickable") == "true",
                bounds=Bounds.parse(a.get("bounds", "")),
                parent=parent,
            )
            parent.children.append(n)
            conv(c, n)

    conv(root_el, root)
    return root


def clean_text(s: str) -> str:
    return s.replace(OBJ_CHAR, "").strip()


# ---------------------------------------------------------------------------
# 기기
# ---------------------------------------------------------------------------


class Device(Protocol):
    """어댑터가 쓰는 기기 조작. 실제는 U2Device, 계약 테스트는 가짜 기기(adapters/testing)."""

    async def is_booted(self) -> bool: ...
    async def dump(self) -> str: ...
    async def current_package(self) -> str | None: ...
    async def app_start(self, package: str) -> None: ...
    async def click(self, x: int, y: int) -> None: ...
    async def set_text(self, selector: dict[str, str], text: str) -> None: ...
    async def send_keys(self, text: str) -> None: ...
    async def clear_clipboard(self) -> None: ...
    async def press(self, key: str) -> None: ...
    async def screenshot_png(self) -> bytes: ...
    async def reboot(self) -> None: ...
    async def login_completed(self) -> None:
        """운영자가 noVNC 로 로그인을 끝냈다는 알림. 실제 기기는 할 일이 없다(로그인은 AVD 디스크에 남는다)."""


# uiautomator2 셀렉터 키. selectors.yaml 은 이 키 이름을 쓴다
U2_KEYS = {"description", "text", "resourceId", "className", "descriptionMatches", "textMatches"}


class U2Device:
    """Python uiautomator2 로 실제 에뮬레이터를 조작한다. 동기 API 라 스레드에서 돌린다."""

    def __init__(self, serial: str):
        self.serial = serial
        self._d = None

    def _conn(self):
        if self._d is None:
            import uiautomator2 as u2  # android 의존성 그룹. Mock 이미지에는 없다

            self._d = u2.connect(self.serial)
        return self._d

    async def _run(self, fn, *args):
        try:
            return await asyncio.to_thread(fn, *args)
        except Exception:
            # 연결이 끊긴 뒤에는 새로 붙는다(에뮬레이터 재부팅, adb 서버 재시작)
            self._d = None
            raise

    def _adb(self):
        import adbutils

        return adbutils.adb.device(self.serial)

    async def is_booted(self) -> bool:
        def f() -> bool:
            return self._adb().shell("getprop sys.boot_completed", timeout=5).strip() == "1"

        try:
            return await asyncio.to_thread(f)
        except Exception as e:  # noqa: BLE001 - 기기 없음·adb 서버 없음 모두 '안 뜸'
            log.info("boot 확인 실패: %s", e)
            return False

    async def dump(self) -> str:
        return await self._run(lambda: self._conn().dump_hierarchy(compressed=False))

    async def current_package(self) -> str | None:
        return await self._run(lambda: (self._conn().app_current() or {}).get("package"))

    async def app_start(self, package: str) -> None:
        await self._run(lambda: self._conn().app_start(package))

    async def click(self, x: int, y: int) -> None:
        await self._run(lambda: self._conn().click(x, y))

    async def set_text(self, selector: dict[str, str], text: str) -> None:
        bad = set(selector) - U2_KEYS
        if bad:
            raise ValueError(f"모르는 셀렉터 키: {bad}")
        await self._run(lambda: self._conn()(**selector).set_text(text))

    async def send_keys(self, text: str) -> None:
        await self._run(lambda: self._conn().send_keys(text))

    async def clear_clipboard(self) -> None:
        await self._run(lambda: self._conn().set_clipboard(""))

    async def press(self, key: str) -> None:
        await self._run(lambda: self._conn().press(key))

    async def screenshot_png(self) -> bytes:
        def f() -> bytes:
            img = self._conn().screenshot()  # PIL.Image
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()

        return await self._run(f)

    async def reboot(self) -> None:
        def f() -> None:
            self._adb().shell("reboot", timeout=10)

        self._d = None
        await asyncio.to_thread(f)

    async def login_completed(self) -> None:
        return None


def make_device(factory: str | None, serial: str) -> Device:
    """'모듈:클래스' 경로로 기기를 만든다. 비우면 U2Device. 계약 테스트는 env 로 가짜 기기를 꽂는다."""
    if not factory:
        return U2Device(serial)
    mod, _, attr = factory.partition(":")
    return getattr(importlib.import_module(mod), attr)(serial)


# ---------------------------------------------------------------------------
# 설정
# ---------------------------------------------------------------------------


class AndroidConfig(BaseModel):
    serial: str = "emulator-5554"
    package: str
    # 'set_text'(접근성 노드에 직접) | 'send_keys'(포커스 후 클립보드 붙여넣기, 피클)
    input_method: str = "set_text"
    boot_timeout_s: float = 120
    app_start_wait_s: float = 8
    device_factory: str | None = None
    # noVNC 로 에뮬레이터 화면을 여는 URL. VM 2 프로비저닝 때 정한다(env ANDROID_VNC_URL 이 우선)
    vnc_url: str = ""
    # 하루 전송 상한(03 7장 레이트 리밋). 0 이면 끈다
    daily_limit: int = 300
    # 휴대폰 경로(PhoneDevice): 보낸 뒤 "평소 응답 시간 + verify_margin_s" 동안 답 알림이 없으면 앱을 열어
    # 보낸 질문이 있는지 보고 화면을 직접 읽기 시작한다. 평소 응답 시간은 최근 답 10개가 끝난 시각의 중간값
    # (없으면 expect_reply_s). notify_fallback_s 는 그 확인 시점의 상한.
    # 2026-11-21 노트9 측정: 방금 앱을 쓴 뒤에는 뮤즈만 답 알림을 보냈다(Vooy·피클·Cue 는 150초 동안 없음).
    # 알림을 안 보내는 앱은 verify_margin_s 를 0 으로 둬 평소 응답 시간에 바로 읽는다
    notify_fallback_s: float = 60
    expect_reply_s: float = 30
    verify_margin_s: float = 10
    # 답 알림으로 읽기 시작했으면 앱이 답을 다 쓴 뒤다(뮤즈: 작업 중 알림이 사라진 뒤 답 알림 1번). 화면은 이만큼만
    # 조용하면 끝으로 본다(quiet_s 보다 길면 quiet_s)
    notified_quiet_s: float = 2.0
    # 보내기·읽기 뒤 홈으로 나갈지. 안드로이드는 홈 버튼 직후 5초 동안 다른 앱 띄우기를 늦춘다(앱 전환 보호.
    # 2026-11-21 노트9: 홈 뒤 띄우기 4~5초, 앱에서 앱으로는 0.7초). 그래서 기본은 앱을 그대로 두고 화면만 놓는다.
    # 그 앱이 앞에 있는 동안은 답 알림이 안 오지만, 다른 봇이 자기 앱을 띄우면 뒤로 가 알림이 온다. 전용 폰 기준
    home_after_use: bool = False
    # "작업 중" 알림 채널(뮤즈 aura_run_in_background). 답 신호로 치지 않고, 떠 있는 동안 응답 중으로 본다
    progress_channels: list[str] = []


def load_selectors(path: str | Path) -> dict[str, Any]:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}


# ---------------------------------------------------------------------------
# 베이스
# ---------------------------------------------------------------------------


class AndroidAdapter(BotAdapter):
    """에뮬레이터 안 앱을 조작하는 어댑터의 공통 부분."""

    poll_s = 1.0  # 접근성 트리는 1초 간격으로 읽는다(03 7·9장)

    def __init__(self, config: AdapterConfig, state_dir: str | Path, token: str = "", selectors_path: str | Path | None = None):
        super().__init__(config, state_dir, token)
        extra = config.model_extra or {}
        data = dict(extra.get("android") or {})
        if os.environ.get("ANDROID_SERIAL"):
            data["serial"] = os.environ["ANDROID_SERIAL"]
        if os.environ.get("ANDROID_DEVICE_FACTORY"):
            data["device_factory"] = os.environ["ANDROID_DEVICE_FACTORY"]
        if os.environ.get("ANDROID_VNC_URL"):
            data["vnc_url"] = os.environ["ANDROID_VNC_URL"]
        self.android = AndroidConfig.model_validate(data)
        self.sel: dict[str, Any] = load_selectors(selectors_path) if selectors_path else {}
        self.device: Device = make_device(self.android.device_factory, self.android.serial)
        self._recovering: asyncio.Task | None = None
        # 휴대폰 경로의 답 알림 대기 상태(큐가 직렬이라 왕복 하나분)
        self._notif_cursor = 0
        self._signaled = True
        self._fallback_at = 0.0
        self._verifying = False  # 알림 없이 확인 시점이 돼 화면을 처음 여는 중(보낸 질문이 있는지 본다)
        self._by_notification = False  # 이번 읽기가 답 알림으로 시작됐는가
        self._first_read_at = 0.0  # 보낸 뒤 처음 화면을 읽으러 간 시각
        self._sent_at = 0.0
        self._progress_keys: set[str] = set()
        self._reply_times_path = self.state_dir / "reply_times.json"
        self._reply_times: deque[float] = deque(self._load_reply_times(), maxlen=10)

    @property
    def blind_resend(self) -> bool:  # type: ignore[override]
        """휴대폰은 절반 시점에 무조건 다시 보내지 않는다(긴 생각에 같은 질문이 두 번 간다). 확인 시점에 보낸
        질문이 화면에 없을 때만(SendLost) 다시 보낸다. 에뮬레이터는 예전대로."""
        return not self.phone_mode

    def _load_reply_times(self) -> list[float]:
        try:
            data = json.loads(self._reply_times_path.read_text(encoding="utf-8"))
            return [float(x) for x in data][-10:]
        except (OSError, ValueError, TypeError):
            return []

    def _note_reply_time(self, seconds: float) -> None:
        self._reply_times.append(round(seconds, 1))
        with contextlib.suppress(OSError):
            self._reply_times_path.write_text(json.dumps(list(self._reply_times)), encoding="utf-8")

    def reply_quiet_s(self) -> float:
        if self.phone_mode and self._by_notification:
            return min(self.quiet_s, self.android.notified_quiet_s)
        return self.quiet_s

    def _answered(self) -> None:
        """휴대폰: 보낸 뒤 답이 끝나기까지 걸린 시간을 기록한다(확인 시점 학습).

        알림을 안 보내는 앱은 화면을 처음 연 뒤에야 답을 본다. 그때 답이 이미 끝나 있었으면(처음 본 뒤 바뀌지 않음)
        실제로는 더 일찍 끝났을 수 있으니 0.75배로 적어 다음엔 조금 일찍 열어 본다. 늦게 열면 학습이 줄지 않는다.
        """
        if not self.phone_mode or not self._sent_at:
            return
        took = self._last_change_at - self._sent_at
        if self._first_read_at and self._last_change_at <= self._first_read_at + 2.0:
            took *= 0.75
        self._note_reply_time(max(1.0, took))

    def verify_after_s(self) -> float:
        """보낸 뒤 알림 없이 앱을 열어 확인하기까지의 시간."""
        a = self.android
        expect = statistics.median(self._reply_times) if self._reply_times else a.expect_reply_s
        return min(a.notify_fallback_s, expect + a.verify_margin_s)

    @property
    def phone_mode(self) -> bool:
        """기기가 휴대폰 워커(PhoneDevice)인지. 알림으로 답을 기다리고, 기다리는 동안 화면을 놓는다."""
        return hasattr(self.device, "wait_notification")

    def screen(self, priority: str = "send"):
        """휴대폰: 화면을 쓰는 한 묶음(보내기, 읽기)을 다른 봇과 섞이지 않게 잡는다. 에뮬레이터는 아무 일 없음.

        읽기는 priority="read": 다른 봇이 보내려고 기다리면 그쪽이 먼저 화면을 받는다."""
        return self.device.hold(priority) if self.phone_mode else contextlib.nullcontext()

    async def yield_screen(self, has_reply: bool) -> None:
        """휴대폰: 한 번 읽은 뒤 화면을 넘길지. 보내려는 봇이 있으면 늘 넘긴다. 아직 답이 없으면(생각 중) 읽으려고
        기다리는 봇에게도 넘긴다. 답이 들어오는 중이면 끝까지 잡아 빨리 끝낸다."""
        if self.phone_mode and hasattr(self.device, "yield_screen"):
            await self.device.yield_screen(to_any=not has_reply)

    # --- 휴대폰 경로: 보내기 → 홈 → 알림 → 읽기 (docs/proposals/phone-worker.md 6장) ---

    async def before_send(self) -> None:
        """보내기 직전의 알림 커서. 보낸 직후 온 알림도 놓치지 않게 먼저 잡는다."""
        if self.phone_mode:
            self._send_started = time.monotonic()
            self._notif_cursor = await self.device.events_cursor()
            self._progress_keys = set()

    async def after_send(self, already_replied: bool = False) -> None:
        """보낸 뒤 화면을 놓는다(home_after_use 면 홈으로 나간 뒤). 앱이 전경인 동안은 답 알림이 오지 않는다.

        already_replied: 나가기 전에 이미 답이 화면에 있으면(빠른 봇) 알림을 기다리지 않고 바로 읽는다.
        """
        if not self.phone_mode:
            return
        self._verifying = False
        self._by_notification = False
        self._sent_at = time.monotonic()
        self._first_read_at = self._sent_at if already_replied else 0.0
        log.info("보냄: 화면을 잡고 %.1f초, %s", self._sent_at - getattr(self, "_send_started", self._sent_at),
                 "답이 이미 보임 → 바로 읽음" if already_replied else f"{self.verify_after_s():.0f}초 뒤 확인")
        if already_replied:
            self._signaled = True
            await self.device.release()
            return
        self._signaled = False
        self._fallback_at = self._sent_at + self.verify_after_s()
        if self.android.home_after_use:
            await self.device.press("home")
        await self.device.release()

    async def reply_signal(self, deadline: float) -> bool:
        """답을 읽으러 화면을 열어도 되는지. 에뮬레이터는 늘 True(화면을 계속 본다).

        휴대폰은 대상 앱의 답 알림이 오면 True. 알림이 확인 시점(verify_after_s)까지 없으면 화면을 직접 읽기
        시작하고, 그 첫 화면에서 보낸 질문이 있는지 본다(check_sent). 이번 대기의 끝이 가까워도 읽는다.
        "작업 중" 알림(뮤즈)이 떠 있으면 보내기는 된 것이라 확인 시점을 미룬다.
        """
        if not self.phone_mode or self._signaled:
            return True
        now = time.monotonic()
        wait = max(0.0, min(1.0, deadline - now))
        posted = self._absorb(*await self.device.wait_events(self.android.package, self._notif_cursor, wait))
        now = time.monotonic()
        if posted:
            log.info("답 알림 %d개 → 화면 읽기 (보낸 뒤 %.1f초)", len(posted), now - self._sent_at)
            self._signaled = True
            self._by_notification = True
            self._first_read_at = now
        elif deadline - now <= max(10.0, self.quiet_s * 3):
            log.info("답 알림 없음, 대기 끝이 가까움 → 화면을 직접 읽음")
            self._signaled = True
            self._first_read_at = now
        elif now >= self._fallback_at and not self._progress_keys:
            log.info("답 알림 없음 (보낸 뒤 %.1f초) → 앱을 열어 보낸 질문 확인", now - self._sent_at)
            self._signaled = True
            self._verifying = True
            self._first_read_at = now
        return self._signaled

    def check_sent(self, has_own: bool, changed: bool) -> None:
        """확인 시점에 처음 연 화면의 판정. 보낸 질문이 없고 화면도 보내기 전 그대로면 전송이 안 된 것(SendLost).

        화면이 바뀌었으면(긴 답에 질문이 밀려 올라갔을 수 있다) 다시 보내지 않는다. 같은 질문이 두 번 가는 것보다
        늦는 편이 낫다. 질문이 있으면 생각 중이니 화면을 계속 읽는다(알림을 안 보내는 앱이 있어 홈으로 나가지 않는다).
        """
        if not self._verifying:
            return
        self._verifying = False
        if has_own or changed:
            log.info("확인: 보낸 질문 %s, 화면 변화 %s → 계속 읽음", "있음" if has_own else "없음", "있음" if changed else "없음")
            return
        raise SendLost("보낸 질문이 대화에 없음(전송 안 됨)")

    def _absorb(self, events: list[dict[str, Any]], cursor: int) -> list[dict[str, Any]]:
        """알림 이벤트를 반영하고 답 신호가 되는 posted 이벤트만 돌려준다."""
        self._notif_cursor = cursor
        progress = set(self.android.progress_channels)
        answers = []
        for e in events:
            d = e.get("data") or {}
            key = str(d.get("key") or d.get("id"))
            if d.get("channel") in progress:
                if e.get("event") == "notif.posted":
                    self._progress_keys.add(key)
                else:
                    self._progress_keys.discard(key)
            elif e.get("event") == "notif.posted":
                answers.append(e)
        return answers

    async def progress_active(self) -> bool:
        """휴대폰: "작업 중" 알림이 떠 있는지(뮤즈). 이벤트를 기다리지 않고 바로 확인한다."""
        if not self.phone_mode or not self.android.progress_channels:
            return False
        self._absorb(*await self.device.wait_events(self.android.package, self._notif_cursor, 0))
        return bool(self._progress_keys)

    async def open_for_read(self) -> None:
        """휴대폰: 읽기 전에 앱을 전경으로(런처 인텐트. 알림 PendingIntent 는 BAL 에 막힌다)."""
        if self.phone_mode and await self.ensure_foreground():
            await asyncio.sleep(1.0)

    async def after_read(self) -> None:
        """휴대폰: 다 읽었으면 화면을 놓는다(home_after_use 면 홈으로 나간 뒤)."""
        if self.phone_mode:
            if self.android.home_after_use:
                await self.device.press("home")
            await self.device.release()

    # --- 기기 상태 ---------------------------------------------------------

    # 다른 앱 창을 걸러도 남겨 둘 시스템 창(권한·설치 확인 등. 봇 앱 흐름 위에 뜬다)
    SYSTEM_WINDOW_PKGS = ("android", "com.android.permissioncontroller", "com.google.android.permissioncontroller",
                          "com.android.packageinstaller", "com.google.android.packageinstaller")

    async def tree(self) -> UiNode:
        try:
            root = parse_hierarchy(await self.device.dump())
        except ET.ParseError as e:
            raise BackendDown(f"접근성 트리를 읽지 못함: {e}") from e
        return self.own_windows(root) if self.phone_mode else root

    def own_windows(self, root: UiNode) -> UiNode:
        """휴대폰: 이 앱의 창만 남긴다(맨 위 노드 하나가 창 하나).

        태블릿은 여러 앱을 팝업·분할 화면으로 함께 띄운다. 그러면 트리에 다른 앱의 입력창·버튼이 섞여
        "첫 EditText" 가 다른 앱 것이 된다(2026-11-18 탭 S9 울트라: Vooy 가 피클 입력창을 집음).
        이 앱 창이 하나도 없으면(앱이 앞에 없음) 그대로 둔다 — 앞 화면 판단은 원래대로.
        """
        pkg = self.android.package
        mine = [w for w in root.children if w.package == pkg]
        if not mine or len(mine) == len(root.children):
            return root
        keep = [w for w in root.children if w.package == pkg or w.package in self.SYSTEM_WINDOW_PKGS]
        out = UiNode(cls=root.cls)
        for w in keep:
            w.parent = out
            out.children.append(w)
        return out

    def app_window(self, root: UiNode) -> Bounds | None:
        """이 앱 창(여럿이면 가장 큰 것)의 화면 위치. 팝업이면 화면보다 작다."""
        wins = [w.bounds for w in root.children if w.package == self.android.package and not w.bounds.empty]
        return max(wins, key=lambda b: (b.right - b.left) * (b.bottom - b.top)) if wins else None

    def scoped(self, selector: dict[str, str]) -> dict[str, str]:
        """기기(워커·uiautomator2)로 보내는 셀렉터를 이 앱 창으로 한정한다(휴대폰만)."""
        return {**selector, "packageName": self.android.package} if self.phone_mode else selector

    async def ensure_device(self) -> None:
        """에뮬레이터가 부팅돼 있는지. 아니면 복구(재부팅)를 뒤에서 걸고 BackendDown."""
        if self._recovering is not None and not self._recovering.done():
            raise BackendDown("에뮬레이터 복구 중")
        if await self.device.is_booted():
            return
        if self.phone_mode:
            # 휴대폰은 재부팅하지 않는다. 연결이 돌아오거나 사람이 잠금을 풀 때까지 사용할 수 없음
            raise BackendDown("휴대폰이 연결돼 있지 않거나 화면이 꺼졌거나 잠겨 있음")
        self.start_recovery()
        raise BackendDown("에뮬레이터 응답 없음(boot_completed 아님)")

    def start_recovery(self) -> None:
        """재부팅 후 boot_completed 까지 기다리는 작업을 뒤에서 돌린다. 프로브 시간 제한(send_s)에 묶이지 않게."""
        if self._recovering is not None and not self._recovering.done():
            return

        async def recover() -> None:
            log.warning("에뮬레이터 복구: 재부팅")
            try:
                await self.device.reboot()
            except Exception as e:  # noqa: BLE001 - adb 가 기기를 못 찾으면 재부팅도 실패한다. 다음 프로브가 다시 본다
                log.warning("재부팅 명령 실패: %s", e)
                return
            end = time.monotonic() + self.android.boot_timeout_s
            while time.monotonic() < end:
                await asyncio.sleep(3)
                if await self.device.is_booted():
                    log.info("에뮬레이터 복구 완료")
                    return
            log.error("에뮬레이터가 %s초 안에 부팅되지 않음", self.android.boot_timeout_s)

        self._recovering = asyncio.get_running_loop().create_task(recover())
        self._bg.add(self._recovering)
        self._recovering.add_done_callback(self._bg.discard)

    async def ensure_foreground(self) -> bool:
        """앱이 포그라운드가 아니면 띄운다. 띄웠으면 True."""
        if await self.device.current_package() == self.android.package:
            return False
        await self.device.app_start(self.android.package)
        return True

    async def wait_for(self, timeout_s: float, **sel: Any) -> UiNode | None:
        end = time.monotonic() + timeout_s
        while True:
            node = (await self.tree()).find(**sel)
            if node is not None or time.monotonic() >= end:
                return node
            await asyncio.sleep(0.5)

    # --- 조작 --------------------------------------------------------------

    async def tap(self, node: UiNode) -> None:
        x, y = node.bounds.center
        await self.device.click(x, y)

    async def tap_input(self, node: UiNode, u2_selector: dict[str, str]) -> None:
        """입력창을 눌러 포커스를 준다. 휴대폰은 좌표 대신 노드를 접근성 클릭한다.

        앱을 다시 열면 지난번 포커스가 남아 키보드가 조금 늦게 올라오고 입력줄이 위로 밀린다. 밀리기 전에 읽은
        좌표를 누르면 키보드 자판을 누르게 된다(2026-11-20 노트9: Vooy·Cue 글 넣기가 매번 실패).
        """
        if self.phone_mode and hasattr(self.device, "click_selector"):
            try:
                await self.device.click_selector(self.scoped(u2_selector))
                return
            except Exception as e:  # noqa: BLE001 - 노드를 못 누르면 예전처럼 좌표로
                log.info("입력창 노드 클릭 실패, 좌표로 누름: %s", e)
        await self.tap(node)

    async def input_text(self, u2_selector: dict[str, str], text: str) -> None:
        """한글 입력. set_text 가 기본, 피클처럼 포커스 후 노드가 사라지는 앱은 send_keys."""
        if self.android.input_method == "send_keys":
            await self.device.send_keys(text)
            await self.device.clear_clipboard()  # 클립보드에 마지막 입력이 남지 않게
        else:
            await self.device.set_text(self.scoped(u2_selector), text)

    async def crop_screenshot(self, bounds: Bounds, alt: str | None = None) -> str | None:
        """화면을 노드 bounds 로 잘라 이미지 스토리지에 넣고 /images/<id> 를 돌려준다."""
        if bounds.empty:
            return None
        from PIL import Image  # android 의존성 그룹

        png = await self.device.screenshot_png()
        img = Image.open(io.BytesIO(png))
        crop = img.crop(bounds.as_tuple())
        buf = io.BytesIO()
        crop.save(buf, format="PNG")
        return self.store_image(buf.getvalue(), "image/png")
