"""범용 채팅 앱 어댑터 (피클·뮤즈, docs/03-adapters.md 8·9장 ADR-9 안내).

"입력창 + 전송 버튼 + 말풍선 목록" 모양의 앱을 휴대폰 경로(PhoneDevice)로 조종한다. 앱별 차이는
selectors.yaml 과 config.yaml 의 android 절(패키지·작업 중 알림 채널)뿐이다.

말풍선 구분은 위치(왼쪽·오른쪽)에 기대지 않는다. 보낸 글과 같은 글자인 노드(내 말풍선)를 기준점으로
그 아래에 새로 생긴 글 노드를 봇 답으로 본다. 기준점이 화면에 없으면(긴 답에 밀림) 보내기 전 화면에
없던 글 노드를 본다. 입력창 아래(하단 탭 등)는 대화 영역이 아니다.

selectors.yaml 키 (input·input_u2·send 는 셀렉터 하나 또는 후보 목록. 목록이면 앞에서부터 시도한다.
앱 버전·언어·계정별 실험으로 화면이 달라도 한 파일이 모두 받도록 후보를 덧붙이기만 한다)
  input         입력창(UiNode.find 셀렉터: rid·desc·desc_re·text·text_re·cls·clickable)
  input_u2      set_text 용(uiautomator2 키: resourceId·className·description·descriptionMatches…)
  send          전송 버튼(입력 뒤에만 보이는 앱이 있어 입력 후 화면에서 찾는다). 비워 둘 수 있다
  send_heuristic  기본 true. send 후보로 못 찾으면 "글을 넣은 뒤 입력줄 오른쪽에 새로 나타난 버튼"을 전송
                버튼으로 본다. 휴대폰에서는 버튼 그림으로 비교한다: 글을 넣기 전 입력줄 버튼들의 그림과 같은
                그림은 자리가 옮겨졌어도 같은 버튼이다(Cue: 글을 넣으면 마이크가 왼쪽으로 밀리고 그 자리에
                ↑ 가 생긴다. 트리만 보면 밀린 마이크가 새 버튼처럼 보인다). 그림을 못 얻으면 추측해 누르지
                않는다. 이렇게 찾아 전송에 성공하면 이름(rid·desc)이 있는 버튼은 기기별 층(learned.py)에 저장해
                다음부터 먼저 쓴다. 글을 넣기 전에도 있던 버튼(음성·마이크)은 절대 누르지 않는다
  focus_input   true 면 글을 넣기 전에 입력창을 탭한다(Flutter: 포커스 없이 넣은 글은 화면에 반영되지 않는다)
  chat_tabs     입력창이 없을 때 차례로 눌러 볼 대화 탭·버튼 셀렉터 목록(없으면 뒤로가기). 입력창을
                탭해야 글 입력 모드가 되는 앱(Cue)은 그 입력줄 버튼도 여기 둔다.
                {name_env: 환경변수} 항목은 그 값(대화방 이름)으로 시작하는 목록 항목을 누른다. 대화방 이름은
                계정마다 달라 저장소에 넣지 않고 .env 로 준다(값이 없으면 그 항목은 건너뛴다)
  exclude_text_re   봇 답에서 뺄 글(시각·"복사" 같은 UI 글자)
  exclude_button_re 버튼에서 뺄 라벨
  exclude_inside_desc_re  content-desc 가 이것과 맞는 노드(3단계 위 조상까지) 안의 글은 답이 아니다. 대화 목록 위에
                겹쳐 그려지는 머리 부분(피클: "… 프로필" 아래 이름, "친구 초대" 안의 "초대")은 내 말풍선이 화면
                위로 밀려 잘리면 "내 말 아래 새 글"로 잡힌다(2026-11-12 실기기)
  confirm_button_re 결제·예약 확정 버튼 → confirmation_required
  login_markers     로그인 화면에만 있는 글자
  text_from_desc    true 면 글을 content-desc 에서도 읽는다(Flutter 앱: Cue. 말풍선 글이 text 가 아니라 desc 에 있다)
  send_only_with_text  true 면 전송 버튼이 글이 있을 때만 보인다(누른 뒤 사라지면 전송됨)
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import io
import logging
import os
import re
from pathlib import Path
from typing import Any

from adapters.android_base import AndroidAdapter, Bounds, UiNode, clean_text
from adapters.base import (
    AdapterConfig,
    AdapterError,
    BackendDown,
    Button,
    Confirmation,
    Link,
    LoginFlow,
    LoginRequired,
    RawMessage,
    Structured,
    Thread,
    VncLogin,
)
from adapters.learned import LearnedSelectors, app_key, device_id

log = logging.getLogger(__name__)
URL_RE = re.compile(r"https?://[^\s\]\)<>\"']+")


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", clean_text(s)).strip()


def candidates(v: Any) -> list[dict[str, Any]]:
    """셀렉터 값(하나 또는 목록)을 후보 목록으로."""
    if not v:
        return []
    if isinstance(v, dict):
        return [v]
    return [c for c in v if isinstance(c, dict)]


def find_first(root: UiNode, v: Any) -> UiNode | None:
    for c in candidates(v):
        n = root.find(**c)
        if n is not None:
            return n
    return None


ICON = 24  # 버튼 그림 비교 크기(회색조 24x24)


def icon_of(img: Any, b: Bounds) -> bytes:
    return img.crop(b.as_tuple()).resize((ICON, ICON)).tobytes()


def same_icon(a: bytes, b: bytes) -> bool:
    """같은 그림인지(평균 밝기 차이). 마이크와 ↑ 처럼 다른 아이콘은 차이가 크다."""
    return len(a) == len(b) and sum(abs(x - y) for x, y in zip(a, b)) / max(1, len(a)) < 12


def learnable(n: UiNode) -> dict[str, Any] | None:
    """찾은 버튼을 다시 찾을 셀렉터. 이름(rid·desc)이 없으면 배우지 않는다(일반 규칙이 매번 찾는다)."""
    if n.rid:
        return {"rid": n.rid}
    if n.desc.strip():
        return {"desc": n.desc}
    return None


_STAMP_RE = re.compile(
    r"((어제|오늘|Yesterday|Today)\s*)?((오전|오후)\s*)?\d{1,2}:\d{2}(\s*([AaPp][Mm]|오전|오후))?"
)


def is_own(got: str, want: str) -> bool:
    """정규화한 노드 글이 내가 보낸 글(말풍선)인지. 말풍선에 시각이 붙어 한 덩어리인 앱이 있다
    (Cue: "어제 7:20 오후 + 줄바꿈 + 보낸 글"). 붙은 것이 시각일 때만 인정한다("echo: 보낸 글" 같은
    답을 내 글로 오인하지 않게). 긴 메시지는 말풍선에서 잘릴 수 있어 앞부분으로도 비교한다."""
    if not got or not want:
        return False
    if got == want:
        return True
    if got.endswith(" " + want) and _STAMP_RE.fullmatch(got[: -len(want) - 1]):
        return True
    if got.startswith(want + " ") and _STAMP_RE.fullmatch(got[len(want) + 1 :]):
        return True
    return len(got) >= 20 and want.startswith(got[:20])


def action_id_for(label: str) -> str:
    return "btn_" + hashlib.sha1(label.encode("utf-8")).hexdigest()[:8]


class ButtonNotFound(AdapterError):
    code = "internal"


class PhoneChatAdapter(AndroidAdapter):
    SEND_SETTLE_S = 0.4
    SEND_CHECK_S = 1.0
    SEND_TRIES = 3
    # 글을 넣은 뒤 입력줄 버튼이 늘기를 기다리는 시간(rightmost_when_row_grows 앱). 탭 Cue 는 트리가 4초 넘게 늦기도 했다
    ROW_SETTLE_S = 10.0
    SEND_FIRST_CHECK_S = 0.3
    SEND_CONFIRM_S = 4.0  # 한 번 누른 뒤 전송 확인을 기다리는 시간(느린 휴대폰은 트리 반영이 몇 초 늦다)
    FOCUS_SETTLE_S = 1.0
    INPUT_CHECK_S = 3.0  # 노트9: set_text 결과가 트리에 1.5초 넘게 늦게 보이기도 한다
    # 글 넣는 방법(환경 맞추기에서 앞에서부터 시험한다)
    #   set_text     접근성 ACTION_SET_TEXT. 대부분의 휴대폰
    #   focus_paste  입력창을 눌러 포커스를 준 뒤 붙여넣기. 안드로이드 10 노트9 의 뮤즈(RN)는 이것만 된다.
    #                set_text 를 한 번이라도 쓰면 앱을 다시 열 때까지 붙여넣기도 안 먹는다
    INPUT_METHODS = ("set_text", "focus_paste")
    SCREENSHOT_MIN_SDK = 30
    REOPEN_SETTLE_S = 2.5
    PASTE_TRIES = 3

    def __init__(self, config: AdapterConfig, state_dir: str | Path, token: str = "", selectors_path: str | Path | None = None):
        super().__init__(config, state_dir, token, selectors_path)
        s = self.sel
        self._exclude_text = re.compile(s["exclude_text_re"]) if s.get("exclude_text_re") else None
        self._exclude_btn = re.compile(s["exclude_button_re"]) if s.get("exclude_button_re") else None
        self._exclude_inside = re.compile(s["exclude_inside_desc_re"]) if s.get("exclude_inside_desc_re") else None
        self._confirm_re = re.compile(s["confirm_button_re"]) if s.get("confirm_button_re") else None
        self._baseline: set[str] = set()
        self._sent_norm: str | None = None
        self._chat_seen = False  # 보낸 뒤 내 말풍선을 한 번이라도 봤는가(본 뒤로는 긴 답에 밀려 사라져도 같은 방)
        self._thread: Thread | None = None
        self.learned = LearnedSelectors(self.state_dir / "learned_selectors.json")
        self._app_key = app_key(self.android.package, None)
        # 글을 넣기 전 입력줄 오른쪽 버튼들(일반 규칙: 이것과 다른 버튼이 전송 버튼)
        self._pre_row: set[str] = set()
        self._pre_field: Bounds | None = None
        self._pre_icons: list[bytes] | None = None
        self._pre_bounds: list[tuple[int, int, int, int]] = []
        self._confirm_root: UiNode | None = None
        self._sdk: int | None = None
        self._set_text_used = False

    # --- 화면 읽기 ---------------------------------------------------------

    def _app_nodes(self, root: UiNode) -> list[UiNode]:
        pkg = self.android.package
        return [n for n in root.walk() if n.package == pkg or not n.package]

    def _floor(self, root: UiNode) -> int:
        """대화 영역의 아래 끝(입력창 위쪽). 입력창이 없으면 화면 끝."""
        field = find_first(root, self.sel["input"])
        return field.bounds.top if field is not None else 10**9

    def _label(self, n: UiNode) -> str:
        """노드의 글. 보통은 text, text_from_desc 앱(Flutter)은 누를 수 없는 노드의 content-desc 도."""
        if n.text.strip():
            return n.text
        if self.sel.get("text_from_desc") and not n.clickable and not n.cls.endswith("Button"):
            return n.desc
        return ""

    def _texts(self, root: UiNode) -> list[UiNode]:
        """대화 영역의 글 노드(위→아래). 입력창 안 글자는 뺀다."""
        floor = self._floor(root)
        out = []
        for n in self._app_nodes(root):
            if not self._label(n).strip() or n.bounds.empty or n.bounds.top >= floor:
                continue
            if n.cls.endswith("EditText"):
                continue
            out.append(n)
        return sorted(out, key=lambda n: (n.bounds.top, n.bounds.left))

    def _sig(self, n: UiNode) -> str:
        return _norm(self._label(n))

    def _is_login_screen(self, root: UiNode) -> bool:
        markers = set(self.sel.get("login_markers") or [])
        return bool(markers) and any(n.text.strip() in markers or n.desc.strip() in markers for n in root.walk())

    # --- 준비 --------------------------------------------------------------

    def _chat_tabs(self) -> list[dict[str, Any]]:
        out = []
        for sel in self.sel.get("chat_tabs") or []:
            if isinstance(sel, dict) and "name_env" in sel:
                name = os.environ.get(str(sel["name_env"]), "").strip()
                if name:
                    out.append({"desc_re": "^" + re.escape(name) + r"(\n|$)", "clickable": True})
            else:
                out.append(sel)
        return out

    async def _ensure_ready(self) -> None:
        await self.ensure_device()
        if await self.ensure_foreground():
            await self._wait_ready(self.android.app_start_wait_s)
        tabs = self._chat_tabs()
        for attempt in range(len(tabs) + 2):
            root = await self.tree()
            if self._is_login_screen(root):
                raise LoginRequired(f"{self.name} 로그인 화면")
            if find_first(root, self.sel["input"]) is not None:
                return
            tab = next((t for t in (root.find(**sel) for sel in tabs) if t is not None), None)
            if tab is not None:
                await self.tap(tab)
            else:
                await self.device.press("back")
            if await self._wait_input(3) is not None:
                return
        raise BackendDown(f"{self.name} 대화 입력창을 찾지 못함(셀렉터 확인 필요)")

    async def _wait_input(self, timeout_s: float, or_tabs: bool = False) -> UiNode | None:
        """입력창이 보일 때까지. or_tabs 면 눌러 볼 대화 탭·입력줄 버튼이 보여도 멈춘다(앱이 뜬 것)."""
        loop = asyncio.get_running_loop()
        end = loop.time() + timeout_s
        tabs = self._chat_tabs() if or_tabs else []
        while True:
            root = await self.tree()
            node = find_first(root, self.sel["input"]) or find_first(root, tabs)
            if node is not None or loop.time() >= end:
                return node
            await asyncio.sleep(0.5)

    async def _wait_ready(self, timeout_s: float) -> None:
        await self._wait_input(timeout_s, or_tabs=True)

    async def _probe(self) -> None:
        if self.phone_mode:
            await self.ensure_device()  # 개인 폰: 상태 확인만으로 앱을 열지 않는다
            return
        await self._ensure_ready()

    # --- 전송 --------------------------------------------------------------

    async def _send(self, thread: Thread, text: str, tap: str | None) -> None:
        async with self.screen():
            await self._send_locked(thread, text, tap)

    async def _send_locked(self, thread: Thread, text: str, tap: str | None) -> None:
        self._thread = thread
        if self.phone_mode and await self.device.current_package() != self.android.package:
            await self._ensure_ready()  # 재전송: 첫 전송 뒤 홈으로 나가 있다
        await self.before_send()
        if hasattr(self.device, "app_version"):
            prof = await self.device.device_profile() if hasattr(self.device, "device_profile") else {}
            self._sdk = prof.get("sdk")
            dev = device_id(prof.get("device"), self._sdk) if prof.get("device") else None
            self._app_key = app_key(self.android.package, await self.device.app_version(self.android.package), dev)
        self._set_text_used = False
        self._confirm_root = None
        root = await self.tree()
        self._baseline = {self._sig(n) for n in self._texts(root)}
        self._sent_norm = None
        self._chat_seen = False

        if tap is not None:
            label = (thread.data.get("buttons") or {}).get(tap)
            node = self._find_button(root, label) if label else None
            if node is None:
                raise ButtonNotFound(f"action_id {tap} 의 버튼({label!r})이 화면에 없음")
            await self.tap(node)

        body = text.strip()
        if body:
            field = find_first(root, self.sel["input"])
            if field is None and self.phone_mode:
                # 앱은 떠 있지만 다른 화면(지난번 헛누름으로 간 계정 화면 등). 채팅 탭·뒤로가기로 돌아간다
                await self._ensure_ready()
                root = await self.tree()
                self._baseline = {self._sig(n) for n in self._texts(root)}
                field = find_first(root, self.sel["input"])
            if field is None:
                raise BackendDown("입력창 없음")
            if self.sel.get("focus_input"):
                # 키보드가 뜨며 입력줄이 위로 옮겨진다. 옮겨진 뒤를 기준으로 삼는다
                await self.tap_input(field, candidates(self.sel["input_u2"])[0])
                await asyncio.sleep(self.FOCUS_SETTLE_S)
                root = await self.tree()
                field = find_first(root, self.sel["input"]) or field
            if field.text.strip() and find_first(root, self.sel.get("send")) is None:
                # 지난번 실패로 남은 글. 그대로 두면 전송 버튼이 이미 떠 있어 "글을 넣은 뒤 새로 나타난 버튼"을
                # 찾을 수 없다 → 비운 뒤의 입력줄을 기준으로 삼는다(안내 문구를 text 로 내보내는 앱은 한 번 더 비울 뿐)
                await self._clear_input()
                await asyncio.sleep(self.FOCUS_SETTLE_S)
                root = await self.tree()
                field = find_first(root, self.sel["input"]) or field
            self._pre_field = field.bounds
            row = self._row_buttons(root)
            self._pre_row = {self._node_sig(n) for n in row}
            self._pre_bounds = [n.bounds.as_tuple() for n in row]
            icons = await self._icons(row)
            self._pre_icons = [ic for _, ic in icons] if icons is not None else None
            # 누르지 않고 바로 글을 넣는다. 피클처럼 포커스를 받으면 입력창이 트리에서 사라지는 앱이 있다
            # (워커가 실패하면 스스로 눌러 포커스를 준 뒤 다시 넣는다)
            await self._set_input(body)
            await self._tap_send(body)
            self._sent_norm = _norm(body)
        replied = False
        if self.phone_mode and self._sent_norm and self._confirm_root is not None:
            # 답은 알림으로 안다. 앱이 열려 있는 동안 온 답은 알림이 뜨지 않으므로 전송이 확인되면 바로 홈으로
            # 나간다. 화면을 더 읽지 않고, 전송을 확인한 그 화면에 답이 벌써 있는지만 본다
            replied = bool(self._new_texts(self._confirm_root))
        await self.after_send(already_replied=replied)

    async def _clear_input(self) -> None:
        self._set_text_used = True
        with contextlib.suppress(Exception):
            await self.input_text(candidates(self.sel["input_u2"])[0], "")

    async def _set_input(self, body: str) -> None:
        """글을 넣고 입력창에 실제로 들어갔는지 확인한다(환경 맞추기).

        휴대폰에서는 이 기기·이 앱 버전에서 통한 방법을 기기별 층에 "input" 으로 저장해 두고 먼저 쓴다.
        저장된 방법이 없거나 안 통하면 INPUT_METHODS 를 차례로 시험하고 통한 방법을 저장한다.
        """
        if not self.phone_mode or self.android.input_method == "send_keys":
            await self._input_with_set_text(body)
            return
        saved = (self.learned.get(self._app_key, "input") or {}).get("method")
        order = [saved] if saved in self.INPUT_METHODS else []
        order += [m for m in self.INPUT_METHODS if m not in order]
        for m in order:
            if m == "focus_paste" and self._set_text_used:
                # set_text 를 쓴 뒤에는 붙여넣기가 안 먹는 앱이 있어 다시 연다. 늦게 들어간 글은 임시 저장돼
                # 다시 열어도 남으므로(붙여넣기가 뒤에 덧붙는다) 먼저 비운다
                await self._clear_input()
                await asyncio.sleep(0.5)
                await self._reopen()
            if await self._try_input(m, body):
                if m != saved:
                    log.info("환경 맞추기: %s 글 넣는 방법 = %s", self._app_key, m)
                    self.learned.put(self._app_key, "input", {"method": m})
                return
            log.info("글 넣는 방법 %s 실패", m)
        if saved:
            self.learned.drop(self._app_key, "input")
        raise BackendDown("입력창에 글을 넣지 못함(글 넣는 방법을 모두 시험함)")

    async def _input_with_set_text(self, body: str) -> None:
        self._set_text_used = True
        cands = candidates(self.sel["input_u2"])
        for i, u2 in enumerate(cands):
            try:
                await self.input_text(u2, body)
                return
            except Exception:  # noqa: BLE001 - 다음 후보로. 마지막 후보의 실패는 그대로 올린다
                if i == len(cands) - 1:
                    raise

    async def _try_input(self, method: str, body: str) -> bool:
        if method == "set_text":
            try:
                await self._input_with_set_text(body)
            except Exception as e:  # noqa: BLE001 - 다음 방법으로
                log.info("set_text 실패: %s", e)
                return False
            return await self._input_has(body)
        # focus_paste. 앱을 막 연 직후(노트9 뮤즈)는 붙여넣기가 성공을 돌려줘도 글이 안 들어가는 때가 있다.
        # 입력창이 비어 있는 것을 확인한 뒤에만 다시 붙여넣는다(늦게 들어간 글에 덧붙지 않게)
        try:
            for attempt in range(self.PASTE_TRIES):
                field = find_first(await self.tree(), self.sel["input"])
                if field is None:
                    return False
                if field.text.strip():
                    log.info("붙여넣기 전 입력창에 글이 남아 있음(%d자)", len(field.text))
                    return False
                await self.tap_input(field, candidates(self.sel["input_u2"])[0])
                await asyncio.sleep(self.FOCUS_SETTLE_S + 0.5)  # 키보드가 뜨고 입력창이 포커스를 받을 때까지
                try:
                    await self.device.send_keys(body)
                except Exception as e:  # noqa: BLE001 - 포커스가 아직 안 잡혔다. 다시
                    log.info("붙여넣기 실패(%d): %s", attempt + 1, e)
                    continue
                if await self._input_has(body):
                    return True
                f = find_first(await self.tree(), self.sel["input"])
                log.info("붙여넣기 뒤 입력창(%d): 글자 %d", attempt + 1, len(f.text) if f else -1)
                if f is not None and f.text.strip():
                    return False  # 무언가 들어갔는데 보낸 글과 다르다. 덧붙이지 않는다
                await asyncio.sleep(1.0)
            return False
        finally:
            with contextlib.suppress(Exception):
                await self.device.clear_clipboard()

    async def _reopen(self) -> None:
        """앱을 다시 열고 대화 입력창까지 간다."""
        await self.device.press("home")
        await asyncio.sleep(1.0)
        await self.device.app_start(self.android.package)
        await self._wait_ready(self.android.app_start_wait_s)
        await self._ensure_ready()
        # 입력창이 트리에 보여도 앱(RN)이 아직 입력을 받을 준비가 안 됐을 수 있다(노트9: 바로 붙여넣으면 안 먹음)
        await asyncio.sleep(self.REOPEN_SETTLE_S)
        self._set_text_used = False

    async def _input_has(self, body: str) -> bool:
        """입력창에 보낸 글이 보이는지. 입력창이 트리에서 사라지는 앱(피클)은 확인할 수 없어 참으로 본다."""
        want = _norm(body)
        loop = asyncio.get_running_loop()
        end = loop.time() + self.INPUT_CHECK_S
        while True:
            field = find_first(await self.tree(), self.sel["input"])
            if field is None or _norm(field.text) == want:
                return True
            if loop.time() >= end:
                return False
            await asyncio.sleep(0.3)

    # --- 전송 버튼 찾기: 기기별 층 → 기본 층 후보 → 일반 규칙 → 좌표 대비책 ---

    @staticmethod
    def _node_sig(n: UiNode) -> str:
        return f"{n.cls}|{n.rid}|{n.desc}|{n.bounds.as_tuple()}"

    def _row_buttons(self, root: UiNode) -> list[UiNode]:
        """입력줄의 오른쪽 절반에 있는 누를 수 있는 노드.

        기준은 지금 화면의 입력창, 없으면(포커스를 받으면 사라지는 앱) 글 넣기 전 입력창.
        1) 버튼의 가운데가 입력창 높이 안. 가장자리로 재면 입력창 바로 아래 하단 탭이 끼어들어, 그 탭을 전송
           버튼으로 눌러 다른 화면을 답으로 읽었다(2026-11-16 피클: 입력창 2663~2843 아래 탭 2862~3064).
        2) 1이 비면 입력창을 감싼 입력 상자 틀 안의 버튼 줄. 버튼 줄이 글 칸 아래에 붙은 앱(2026-11-17 울트라25
           Cue: 틀 1378~1730 안에 글 칸 1404~1554, 버튼 줄 1565~1700). 틀은 글 칸 높이의 3배 이하만 본다.
           글이 여러 줄이 되면 글 칸만 감싼 좁은 틀이 생기고(같은 날 울트라25 Cue: 글 칸 1314~1554 로 커짐) 글 칸이
           바깥 틀 위로 넘치기도 해서, 가장 가까운 틀이 아니라 버튼 후보가 들어 있는 가장 가까운 틀을 쓴다.
        """
        now = find_first(root, self.sel["input"]) if self._pre_field is not None else None
        f = now.bounds if now is not None and not now.bounds.empty else self._pre_field
        if f is None:
            return []
        pad = max(20, (f.bottom - f.top) // 4)
        cx = f.center[0]
        nodes = [
            n
            for n in self._app_nodes(root)
            if n.clickable
            and not n.bounds.empty
            and not n.cls.endswith("EditText")
            and not n.bounds.contains(f)
            # 입력창을 감싼 틀은 버튼이 아니다. 긴 글로 입력창이 여러 줄이 되면 틀이 커져 위치가 바뀐다(노트9 Cue)
            and (n.bounds.right - n.bounds.left) * 2 <= (f.right - f.left)
            and n.bounds.center[0] > cx
        ]
        out = [n for n in nodes if f.top - pad <= n.bounds.center[1] <= f.bottom + pad]
        if out:
            return out
        return self._input_box_buttons(now, f, nodes) if now is not None else []

    @staticmethod
    def _input_box_buttons(field: UiNode, f: Bounds, nodes: list[UiNode]) -> list[UiNode]:
        """글 칸의 조상 틀 중 버튼 후보가 들어 있는 가장 가까운 틀(글 칸 높이의 3배 이하)의 후보. 없으면 []."""
        h = f.bottom - f.top
        p = field.parent
        while p is not None:
            b = p.bounds
            if not b.empty:
                if b.bottom - b.top > 3 * h:
                    return []
                inside = [n for n in nodes if b.contains(n.bounds)]
                if inside:
                    return inside
            p = p.parent
        return []

    async def _icons(self, nodes: list[UiNode]) -> list[tuple[UiNode, bytes]] | None:
        """휴대폰: 노드들의 버튼 그림. 스크린샷을 못 얻으면 None. 에뮬레이터는 쓰지 않는다(None)."""
        if not self.phone_mode:
            return None
        if not nodes:
            return []
        if self._sdk is not None and self._sdk < self.SCREENSHOT_MIN_SDK:
            return None  # 안드로이드 11 미만은 접근성 스크린샷이 없다(그림 비교 없이는 추측해 누르지 않는다)
        try:
            from PIL import Image  # android 의존성 그룹

            img = Image.open(io.BytesIO(await self.device.screenshot_png())).convert("L")
        except Exception as e:  # noqa: BLE001 - 스크린샷 권한·속도 제한. 그림 없이는 추측해 누르지 않는다
            log.info("입력줄 스크린샷 실패: %s", e)
            return None
        return [(n, icon_of(img, n.bounds)) for n in nodes]

    async def _appeared(self, root: UiNode) -> UiNode | None:
        """글을 넣기 전에는 없던 입력줄 버튼 중 가장 오른쪽.

        휴대폰은 그림으로 비교한다(자리를 옮긴 옛 버튼을 새 버튼으로 오인하지 않게). 그림이 없으면 None.
        """
        row = self._row_buttons(root)
        if self.phone_mode and self.sel.get("send_tree_rule") == "rightmost_when_row_grows" and self._pre_bounds:
            # 입력줄 버튼이 늘어야(마이크가 밀리고 ↑ 가 생김) 고른다. 트리는 화면보다 늦게 바뀐다
            # (2026-11-18 탭 팝업 Cue: 글을 넣고 1.5초 뒤에도 옛 마이크 하나뿐 → 그림이 바뀐 옛 자리를 ↑ 로 오인)
            loop = asyncio.get_running_loop()
            until = loop.time() + self.ROW_SETTLE_S
            while len(row) <= len(self._pre_bounds) and loop.time() < until:
                await asyncio.sleep(0.5)
                row = self._row_buttons(await self.tree())
        if self.phone_mode:
            now = await self._icons(row)
            if now is None or self._pre_icons is None:
                return self._grown_row_rightmost(row)
            new = [n for n, ic in now if not any(same_icon(ic, p) for p in self._pre_icons)]
        else:
            new = [n for n in row if self._node_sig(n) not in self._pre_row]
        return max(new, key=lambda n: (n.bounds.right, n.bounds.top)) if new else None

    def _grown_row_rightmost(self, row: list[UiNode]) -> UiNode | None:
        """그림이 없을 때(안드로이드 10 등)의 트리 규칙. selectors 의 send_tree_rule: rightmost_when_row_grows 인 앱만.

        Cue(2026-11-11·12, 두 기기·안드로이드 10/15 확인): 글을 넣으면 마이크가 왼쪽으로 밀리고 원래 자리에 ↑ 가
        생긴다. 그래서 "입력줄 버튼이 딱 하나 늘었고, 원래 맨 오른쪽 자리에 여전히 버튼이 있으면" 그 맨 오른쪽이
        ↑ 다. 조건이 하나라도 다르면 추측하지 않는다(마이크를 누르면 음성 모드)."""
        if self.sel.get("send_tree_rule") != "rightmost_when_row_grows":
            return None
        log.info(
            "트리 규칙: 글 넣기 전 %s / 뒤 %s",
            self._pre_bounds,
            [n.bounds.as_tuple() for n in row],
        )
        if not self._pre_bounds or len(row) != len(self._pre_bounds) + 1:
            return None
        right = max(row, key=lambda n: (n.bounds.right, n.bounds.top))
        old = max(self._pre_bounds, key=lambda b: (b[2], b[1]))
        # 같은 자리: 긴 글로 입력창이 여러 줄이 되면 버튼 줄이 몇 픽셀 옮겨진다(노트9: 938 → 933)
        cx, cy = right.bounds.center
        ox, oy = (old[0] + old[2]) // 2, (old[1] + old[3]) // 2
        tol = max(20, (old[2] - old[0]) // 3)
        if abs(cx - ox) > tol or abs(cy - oy) > tol:
            return None
        return right

    async def _find_send(self, root: UiNode, for_press: bool) -> tuple[UiNode, str] | None:
        learned = self.learned.get(self._app_key, "send")
        if learned:
            n = root.find(**learned)
            if n is not None:
                return n, "learned"
            if for_press:
                self.learned.drop(self._app_key, "send")
        n = find_first(root, self.sel.get("send"))
        if n is not None:
            return n, "base"
        if self.sel.get("send_heuristic", True):
            n = await self._appeared(root)
            if n is not None:
                return n, "appeared"
        return None

    async def _press(self, node: UiNode, source: str) -> None:
        log.info("전송 버튼 누름: %s %s %s", source, node.cls.rsplit(".", 1)[-1], node.bounds.as_tuple())
        if source == "base" and self.sel.get("send_u2") and hasattr(self.device, "click_selector"):
            # 접근성 클릭(RN 버튼). 후보를 차례로, 다 못 찾으면 찾은 노드 자리에 접근성 클릭
            # (2026-11-18 탭 뮤즈: 전송 버튼에 testID 가 없고 이름(메시지 보내기)만 있다)
            for sel in candidates(self.sel["send_u2"]):
                try:
                    await self.device.click_selector(self.scoped(sel))
                    return
                except Exception as e:  # noqa: BLE001 - 워커: 누를 노드를 찾지 못함
                    log.info("send_u2 %s 실패: %s", sel, e)
            if hasattr(self.device, "click_at"):
                x, y = node.bounds.center
                await self.device.click_at(x, y)
            else:
                await self.tap(node)
        elif source != "base" and hasattr(self.device, "click_at"):
            x, y = node.bounds.center
            await self.device.click_at(x, y)  # 그 자리의 버튼에 접근성 클릭(Flutter·RN 아이콘 버튼)
        else:
            await self.tap(node)

    def _only_with_text(self, used: tuple[UiNode, str] | None) -> bool:
        """전송 버튼이 글이 있을 때만 보이는지. 설정에 그렇게 적힌 앱이거나, 글을 넣은 뒤 나타난 버튼(정의상)."""
        return bool(self.sel.get("send_only_with_text")) or (used is not None and used[1] in ("appeared", "learned"))

    async def _tap_send(self, body: str) -> None:
        """전송 버튼을 누르고, 입력창이 비거나 내 말풍선이 보이면 전송된 것으로 본다."""
        await asyncio.sleep(self.SEND_SETTLE_S)
        want = _norm(body)

        def mine(r: UiNode) -> int:
            # 보낸 글과 같은 글 노드(입력창 제외) 수. 늘었으면 내 말풍선이 생긴 것 = 전송됨
            return sum(1 for n in self._app_nodes(r) if not n.cls.endswith("EditText") and is_own(_norm(self._label(n)), want))

        before = mine(await self.tree())
        used: tuple[UiNode, str] | None = None
        for attempt in range(self.SEND_TRIES):
            root = await self.tree()
            hit = await self._find_send(root, for_press=attempt == 0)
            if hit is not None:
                await self._press(*hit)
                used = hit
            elif attempt > 0 and self._only_with_text(used):
                # 한 번 누른 뒤 전송 버튼이 사라졌다 = 전송됨. 그 자리는 이제 음성 버튼이라
                # (뮤즈·Cue: 누르거나 길게 누르면 음성 모드) 절대 다시 누르지 않는다
                return self._learn(used)
            elif not await self._tap_send_fallback():
                f = find_first(root, self.sel["input"])
                log.warning(
                    "전송 버튼 못 찾음: 입력창 %s(글 %s자) 글 넣기 전 %s, 입력줄 후보 전 %s / 지금 %s, 그림 %s",
                    f.bounds.as_tuple() if f else None,
                    len(f.text) if f else None,
                    self._pre_field.as_tuple() if self._pre_field else None,
                    self._pre_bounds,
                    [n.bounds.as_tuple() for n in self._row_buttons(root)],
                    "없음" if self._pre_icons is None else len(self._pre_icons),
                )
                raise BackendDown("전송 버튼 없음(셀렉터 확인 필요)")
            # 느린 휴대폰(노트9)은 전송 결과가 트리에 몇 초 늦게 반영된다. 한 번만 보고 다시 누르면 이미 보낸
            # 뒤라 헛누름(또는 중복 전송)이 된다 → 잠시 여러 번 본다
            loop = asyncio.get_running_loop()
            until = loop.time() + max(self.SEND_CHECK_S, self.SEND_CONFIRM_S)
            first = True
            while True:
                # 첫 확인은 짧게(전송이 확인되는 대로 앱을 떠나야 답 알림이 뜬다)
                await asyncio.sleep(min(self.SEND_FIRST_CHECK_S, self.SEND_CHECK_S) if first else self.SEND_CHECK_S)
                first = False
                root = await self.tree()
                self._confirm_root = root
                if mine(root) > before:
                    return self._learn(used)  # 내 말풍선이 생겼다
                field = find_first(root, self.sel["input"])
                guessed = used is not None and used[1] in ("appeared", "learned")
                if guessed and field is None:
                    # 추측으로 고른 버튼을 누른 뒤 입력창도 내 말풍선도 없다 = 채팅 화면을 떠났을 수 있다(하단 탭).
                    # 여기서 성공으로 보면 바뀐 화면을 답으로 읽는다 → 확인 시간 동안 더 보고, 그래도면 실패
                    if loop.time() >= until:
                        self._confirm_root = None
                        raise BackendDown("전송 버튼으로 고른 곳을 누른 뒤 채팅 화면이 아님(전송 안 됨)")
                    continue
                if self._only_with_text(used) and await self._find_send(root, for_press=False) is None:
                    return self._learn(used)  # 전송 버튼이 사라졌다 = 입력창이 비었다(접근성 트리의 입력창 글자는 늦게 지워진다: 뮤즈)
                # 빈 입력창은 안내 문구(hint)를 text 로 내보내는 앱도 있어 '보낸 글과 다름'으로 본다
                if field is None or _norm(field.text) != want:
                    return self._learn(used)
                if loop.time() >= until:
                    break
        raise BackendDown(f"전송 버튼을 {self.SEND_TRIES}번 눌러도 전송되지 않음")

    def _learn(self, used: tuple[UiNode, str] | None) -> None:
        """일반 규칙으로 찾은 버튼으로 전송에 성공했으면 기기별 층에 저장한다."""
        if used is None or used[1] != "appeared":
            return
        sel = learnable(used[0])
        if sel is not None:
            self.learned.put(self._app_key, "send", sel)

    async def _tap_send_fallback(self) -> bool:
        """전송 버튼을 트리에 내놓지 않는 앱(에뮬레이터 피클 1.1.16).

        selectors 의 send_fallback: keyboard_right 이면 입력줄 오른쪽 끝(전송 버튼 자리)을 누른다.
        키보드가 떠 있으면 키보드 바로 위, 아니면 입력창과 같은 높이.
        """
        if self.sel.get("send_fallback") != "keyboard_right" or not self.phone_mode:
            return False
        st = await self.device.state()
        ime, width, dp = st.get("ime_top"), st.get("width"), float(st.get("density") or 0)
        if not width or not dp:
            return False
        root = await self.tree()
        win = self.app_window(root)
        popup = win is not None and (win.right - win.left) < width * 0.95
        if ime and not popup:
            y = int(ime - 35 * dp)  # 키보드가 떠 있으면 입력줄은 키보드 바로 위
        else:
            # 키보드 없이 글이 들어간 경우, 또는 태블릿 팝업 창(키보드와 떨어져 있다): 입력줄 그대로
            field = find_first(root, self.sel["input"])
            if field is None:
                return False
            y = field.bounds.center[1]
        # 전송 버튼 자리는 앱 창의 오른쪽 끝(팝업이면 화면 끝이 아니다)
        right = win.right if popup and win is not None else width
        x = int(right - 40 * dp)
        if hasattr(self.device, "click_at"):
            await self.device.click_at(x, y)  # 접근성 클릭(Flutter·RN 아이콘 버튼은 좌표 탭이 안 먹을 수 있다)
        else:
            await self.device.click(x, y)
        return True

    def _own_anchor(self, root: UiNode) -> UiNode | None:
        want = self._sent_norm or ""
        if not want:
            return None
        hits = []
        for n in self._texts(root):
            if is_own(self._sig(n), want):
                hits.append(n)
        return hits[-1] if hits else None

    def _find_button(self, root: UiNode, label: str) -> UiNode | None:
        floor = self._floor(root)
        hits = [
            n
            for n in self._app_nodes(root)
            if n.clickable and n.bounds.top < floor and (n.desc.strip() == label or label in [t.strip() for t in n.texts()] or n.text.strip() == label)
        ]
        return max(hits, key=lambda n: n.bounds.top) if hits else None

    # --- 수집 --------------------------------------------------------------

    def _new_texts(self, root: UiNode) -> list[UiNode]:
        texts = self._texts(root)
        anchor = self._own_anchor(root)
        if anchor is not None:
            self._chat_seen = True
            new = [n for n in texts if n.bounds.top >= anchor.bounds.bottom]
        elif self._baseline and self._sent_norm and not self._chat_seen:
            # 보낸 글이 한 번도 보이지 않았는데 화면이 바뀌었다 = 다른 대화방일 수 있다(2026-11-18 탭 뮤즈: 같은 계정을
            # 다른 기기에서 쓰면 앱이 가장 최근 대화방을 띄워, 그 방의 글 전부를 답으로 읽었다). 이 방의 글이 다시
            # 보일 때까지 아무것도 읽지 않는다(못 읽으면 timeout 이 낫다. 남의 대화를 답으로 넘기지 않는다)
            log.warning("보낸 글이 화면에 없음(다른 대화방일 수 있음): 답으로 읽지 않음")
            return []
        else:
            new = [n for n in texts if self._sig(n) not in self._baseline]
        want = self._sent_norm or ""
        out = []
        for n in new:
            sig = self._sig(n)
            if not sig or is_own(sig, want):
                continue
            if self._exclude_text is not None and self._exclude_text.search(sig):
                continue
            if self._is_button_text(n) or self._is_inside_excluded(n):
                continue
            out.append(n)
        return out

    def _is_inside_excluded(self, n: UiNode) -> bool:
        if self._exclude_inside is None:
            return False
        p = n.parent
        for _ in range(3):
            if p is None:
                break
            if p.desc and self._exclude_inside.search(p.desc.strip()):
                return True
            p = p.parent
        return False

    @staticmethod
    def _is_button_text(n: UiNode) -> bool:
        """Button 노드(또는 그 바로 아래) 안의 글자는 답이 아니라 버튼 라벨이다.

        말풍선 자체가 누를 수 있는 앱(Vooy)이 있어 '누를 수 있음'만으로는 버튼으로 보지 않는다.
        """
        p = n
        for _ in range(2):
            if p is None:
                break
            if p.clickable and p.cls.endswith("Button"):
                return True
            p = p.parent
        return False

    async def _collect(self, thread: Thread, deadline: float) -> list[RawMessage]:
        if not await self.reply_signal(deadline):
            return []
        async with self.screen():
            await self.open_for_read()
            root = await self.tree()
        if self.phone_mode and not any(n.package == self.android.package for n in root.walk()):
            return []  # 다른 앱 화면(다음 폴링에서 다시)
        msgs: list[RawMessage] = []
        seen: dict[str, int] = {}
        nodes = self._new_texts(root)
        floor = self._floor(root)
        for i, n in enumerate(nodes):
            body = clean_text(self._label(n))
            seen[body] = seen.get(body, 0) + 1
            mid = f"{self.name}-" + hashlib.sha1(f"{body}|{seen[body]}".encode()).hexdigest()[:12]
            next_top = nodes[i + 1].bounds.top if i + 1 < len(nodes) else floor
            msgs.append(RawMessage(id=mid, text=body, meta={"buttons": self._buttons(root, n.bounds.top, next_top)}))
        return msgs

    def _buttons(self, root: UiNode, top: int, bottom: int) -> list[str]:
        labels: list[str] = []
        for n in self._app_nodes(root):
            b = n.bounds
            if not n.clickable or b.empty or b.top < top or b.top >= bottom:
                continue
            if not n.cls.endswith("Button") and not self.sel.get("buttons_any_clickable"):
                continue  # 누를 수 있는 말풍선·목록을 버튼으로 오인하지 않는다(앱별로 켤 수 있음)
            label = n.desc.strip() or " ".join(t.strip() for t in n.texts() if t.strip()) or n.text.strip()
            if not label or len(label) > 40 or (self._exclude_btn is not None and self._exclude_btn.search(label)):
                continue
            if label not in labels:
                labels.append(label)
        return labels

    async def _is_typing(self, thread: Thread) -> bool:
        return await self.progress_active()

    # --- 추출 --------------------------------------------------------------

    async def _extract(self, msgs: list[RawMessage]) -> tuple[str, Structured]:
        parts: list[str] = []
        buttons: list[Button] = []
        confirm: Confirmation | None = None
        labels: dict[str, str] = {}
        for m in msgs:
            line = m.text
            for label in m.meta.get("buttons", []):
                line += f" [버튼: {label}]"
                aid = action_id_for(label)
                labels[aid] = label
                if self._confirm_re is not None and self._confirm_re.search(label) and confirm is None:
                    confirm = Confirmation(summary=m.text.strip()[:300], action_id=aid)
                elif all(b.action_id != aid for b in buttons):
                    buttons.append(Button(label=label, action_id=aid))
            parts.append(line)
        s = Structured()
        if buttons:
            s.buttons = buttons
        if confirm is not None:
            s.confirmation_required = confirm
        links: list[Link] = []
        for u in URL_RE.findall("\n".join(m.text for m in msgs)):
            if all(link.url != u for link in links):
                links.append(Link(title=u, url=u))
        if links:
            s.links = links
        if self._thread is not None:
            self._thread.data["buttons"] = labels
        await self.after_read()
        return "\n\n".join(parts), s

    # --- 로그인·리셋 -------------------------------------------------------

    async def _login_flow(self) -> LoginFlow:
        async def complete() -> None:
            await self.device.login_completed()

        # 로그인은 사람이 휴대폰에서 직접 한다(03 5장 flow 값은 code|vnc 뿐이라 vnc 에 URL 없이 안내만)
        return VncLogin(hint=f"휴대폰에서 {self.name} 앱에 직접 로그인한 뒤 '완료'를 누르세요.", vnc_url="", complete=complete)

    async def _on_reset(self, thread: Thread | None) -> None:
        # 앱의 대화방은 하나로 쓴다. 어댑터 상태만 지운다(Vooy 와 같음)
        self._baseline = set()
        self._sent_norm = None

