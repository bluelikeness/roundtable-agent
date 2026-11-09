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
import importlib
import io
import logging
import os
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Protocol

import yaml
from pydantic import BaseModel, Field

from adapters.base import AdapterConfig, BackendDown, BotAdapter

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

    # --- 기기 상태 ---------------------------------------------------------

    async def tree(self) -> UiNode:
        try:
            return parse_hierarchy(await self.device.dump())
        except ET.ParseError as e:
            raise BackendDown(f"접근성 트리를 읽지 못함: {e}") from e

    async def ensure_device(self) -> None:
        """에뮬레이터가 부팅돼 있는지. 아니면 복구(재부팅)를 뒤에서 걸고 BackendDown."""
        if self._recovering is not None and not self._recovering.done():
            raise BackendDown("에뮬레이터 복구 중")
        if await self.device.is_booted():
            return
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

    async def input_text(self, u2_selector: dict[str, str], text: str) -> None:
        """한글 입력. set_text 가 기본, 피클처럼 포커스 후 노드가 사라지는 앱은 send_keys."""
        if self.android.input_method == "send_keys":
            await self.device.send_keys(text)
            await self.device.clear_clipboard()  # 클립보드에 마지막 입력이 남지 않게
        else:
            await self.device.set_text(u2_selector, text)

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
