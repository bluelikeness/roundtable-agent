"""Vooy 어댑터 (docs/03-adapters.md 7장). 에뮬레이터 안 Vooy 앱(com.vooy.mobile)을 uiautomator2 로 조작한다.

화면 구조(React Native testID 가 resource-id 로 나온다):
  ScrollView[desc=Conversation]
    ... ViewGroup[clickable, desc=<본문>] > ViewGroup[rid=other-message-bubble] > TextView(본문), TextView(시각)
    ... ViewGroup[clickable, desc=<본문>, <시각>] > ViewGroup[rid=own-message-bubble] > TextView(본문+U+FFFC)
  ViewGroup[rid=composer-input-pill] > EditText[desc=Message], Button[desc=Send message]
  View[rid=android-gnb-home]  하단 탭의 에이전트 대화방

응답 수집: 보낸 직후의 내 말풍선(본문 일치, 가장 아래)을 기준점으로 그 아래에 생긴 봇 말풍선을 모은다.
기준점이 화면에 없으면(긴 응답으로 밀려남, 버튼 누르기처럼 내 말풍선이 없는 경우) 보내기 직전에 보이던
봇 말풍선 목록에 없는 것을 새 응답으로 본다.

스레드: 앱의 에이전트 대화방은 하나라 봇 쪽 대화는 모든 thread_id 가 공유한다(03 7장, 계약 테스트 공유 동작).
앱에 새 대화(초기화) 기능이 없어 reset 은 어댑터 쪽 상태만 지운다(P2 확인, 07 미결 보고).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import re
import time
from pathlib import Path
from typing import Any

from adapters.android_base import AndroidAdapter, Bounds, UiNode, clean_text
from adapters.base import (
    AdapterConfig,
    AdapterError,
    BackendDown,
    Button,
    Confirmation,
    Image,
    Link,
    LoginFlow,
    LoginRequired,
    RateLimited,
    RawMessage,
    Structured,
    Thread,
    VncLogin,
)

log = logging.getLogger("adapters.vooy")

URL_RE = re.compile(r"https?://[^\s\]\)<>\"']+")
SELECTORS = Path(__file__).with_name("selectors.yaml")


def _norm(s: str) -> str:
    """말풍선 본문 비교용: 개체 문자·공백 차이를 지운다."""
    return re.sub(r"\s+", " ", clean_text(s)).strip()


def action_id_for(label: str) -> str:
    return "btn_" + hashlib.sha1(label.encode("utf-8")).hexdigest()[:8]


class ButtonNotFound(AdapterError):
    code = "internal"


class VooyAdapter(AndroidAdapter):
    def __init__(self, config: AdapterConfig, state_dir: str | Path, token: str = "", selectors_path: str | Path = SELECTORS):
        super().__init__(config, state_dir, token, selectors_path)
        extra = config.model_extra or {}
        self._limit_re = [re.compile(p, re.IGNORECASE) for p in extra.get("limit_patterns") or []]
        s = self.sel
        self._ts_re = re.compile(s.get("timestamp_re") or r"^$")
        self._exclude_btn_re = re.compile(s["exclude_button_desc_re"]) if s.get("exclude_button_desc_re") else None
        self._confirm_re = re.compile(s["confirm_button_re"]) if s.get("confirm_button_re") else None
        self._typing_tab_re = re.compile(s["typing_tab_desc_re"]) if s.get("typing_tab_desc_re") else None
        self._daily_path = self.state_dir / "daily.json"
        # 현재 왕복(큐가 직렬이라 하나뿐): 기준 말풍선 목록·보낸 본문·대상 스레드
        self._baseline: set[tuple[str, str]] = set()
        self._sent_norm: str | None = None
        self._thread: Thread | None = None
        self._tree_cache: tuple[float, UiNode] | None = None

    # --- 화면 읽기 ---------------------------------------------------------

    async def _tree(self, max_age_s: float = 0.0) -> UiNode:
        now = time.monotonic()
        if max_age_s and self._tree_cache and now - self._tree_cache[0] <= max_age_s:
            return self._tree_cache[1]
        t = await self.tree()
        self._tree_cache = (time.monotonic(), t)
        return t

    def _conversation(self, root: UiNode) -> UiNode:
        return root.find(**self.sel["conversation"]) or root

    def _bubble_text(self, bubble: UiNode) -> tuple[str, str]:
        """(본문, 시각). 시각 TextView 와 개체 문자를 뺀다."""
        body, stamp = [], ""
        for n in bubble.walk():
            if not n.cls.endswith("TextView") or not n.text:
                continue
            if self._ts_re.match(n.text):
                stamp = n.text.strip()
                continue
            body.append(clean_text(n.text))
        return "\n".join(b for b in body if b), stamp

    def _bubbles(self, root: UiNode, rid: str) -> list[UiNode]:
        return sorted(self._conversation(root).find_all(rid=rid), key=lambda n: n.bounds.top)

    def _sigs(self, root: UiNode) -> set[tuple[str, str]]:
        return {self._bubble_text(b) for b in self._bubbles(root, self.sel["bot_bubble"])}

    def _is_login_screen(self, root: UiNode) -> bool:
        markers = set(self.sel.get("login_markers") or [])
        return any(n.text.strip() in markers or n.desc.strip() in markers for n in root.walk())

    # --- 훅: 준비 ----------------------------------------------------------

    async def _ensure_ready(self) -> None:
        await self.ensure_device()
        if await self.ensure_foreground():
            await self.wait_for(self.android.app_start_wait_s, **self.sel["input"])
        for attempt in range(3):
            root = await self._tree()
            if self._is_login_screen(root):
                raise LoginRequired("Vooy 로그인 화면")
            if root.find(**self.sel["input"]) and root.find(**self.sel["send"]):
                return
            home = root.find(**self.sel["home_tab"])
            if home is not None:
                await self.tap(home)
            elif attempt == 0:
                await self.device.press("back")  # 설정·방 정보 같은 하위 화면
            if await self.wait_for(3, **self.sel["input"]) is not None:
                continue
        root = await self._tree()
        if self._is_login_screen(root):
            raise LoginRequired("Vooy 로그인 화면")
        raise BackendDown("Vooy 대화방 입력창을 찾지 못함(셀렉터 확인 필요)")

    async def _probe(self) -> None:
        await self._ensure_ready()

    # --- 훅: 전송 ----------------------------------------------------------

    def _check_daily(self) -> None:
        limit = self.android.daily_limit
        if not limit:
            return
        today = dt.date.today().isoformat()
        data = self._read_daily()
        if data.get("date") == today and int(data.get("count", 0)) >= limit:
            raise RateLimited(f"하루 전송 상한 {limit}건 도달")

    def _read_daily(self) -> dict[str, Any]:
        try:
            return json.loads(self._daily_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _count_daily(self) -> None:
        today = dt.date.today().isoformat()
        data = self._read_daily()
        count = int(data.get("count", 0)) + 1 if data.get("date") == today else 1
        self._daily_path.write_text(json.dumps({"date": today, "count": count}), encoding="utf-8")

    async def _send(self, thread: Thread, text: str, tap: str | None) -> None:
        self._check_daily()
        self._thread = thread
        root = await self._tree()
        self._baseline = self._sigs(root)
        self._sent_norm = None

        if tap is not None:
            label = (thread.data.get("buttons") or {}).get(tap)
            node = self._find_button(root, label) if label else None
            if node is None:
                raise ButtonNotFound(f"action_id {tap} 의 버튼({label!r})이 화면에 없음")
            await self.tap(node)

        body = text.strip()
        if body:
            field = root.find(**self.sel["input"])
            if field is None:
                raise BackendDown("입력창 없음")
            await self.tap(field)
            # 키보드가 올라오면 입력창이 위로 움직인다: 다시 찾는다(P0 확인)
            if await self.wait_for(3, **self.sel["input"]) is None:
                raise BackendDown("입력창이 사라짐")
            await self.input_text(self.sel["input_u2"], body)
            send = (await self._tree()).find(**self.sel["send"])
            if send is None:
                raise BackendDown("전송 버튼 없음")
            await self.tap(send)
            self._sent_norm = _norm(body)
            self._count_daily()
        self._tree_cache = None

    def _find_button(self, root: UiNode, label: str) -> UiNode | None:
        """직전 응답 영역의 버튼. 같은 라벨이 여럿이면 가장 아래(최신)."""
        conv = self._conversation(root)
        hits = [n for n in conv.walk() if n.clickable and (n.desc == label or label in n.texts())]
        hits = [n for n in hits if not n.find(rid=self.sel["bot_bubble"]) and not n.find(rid=self.sel["own_bubble"])]
        return max(hits, key=lambda n: n.bounds.top) if hits else None

    # --- 훅: 수집 ----------------------------------------------------------

    async def _collect(self, thread: Thread, deadline: float) -> list[RawMessage]:
        root = await self._tree()
        bots = self._bubbles(root, self.sel["bot_bubble"])
        new: list[UiNode]
        anchor = None
        if self._sent_norm:
            owns = [o for o in self._bubbles(root, self.sel["own_bubble"]) if self._own_matches(o)]
            anchor = owns[-1] if owns else None
        if anchor is not None:
            new = [b for b in bots if b.bounds.top >= anchor.bounds.bottom]
        else:
            new = [b for b in bots if self._bubble_text(b) not in self._baseline]

        msgs: list[RawMessage] = []
        seen: dict[str, int] = {}
        composer = root.find(**self.sel["composer"])
        floor = composer.bounds.top if composer else 10**9
        tops = sorted(
            n.bounds.top for rid in (self.sel["bot_bubble"], self.sel["own_bubble"]) for n in self._bubbles(root, rid)
        )
        for b in new:
            body, stamp = self._bubble_text(b)
            if not body:
                continue
            next_top = next((t for t in tops if t > b.bounds.top), floor)
            key = f"{body}|{stamp}"
            seen[key] = seen.get(key, 0) + 1
            mid = "vooy-" + hashlib.sha1(f"{key}|{seen[key]}".encode()).hexdigest()[:12]
            msgs.append(RawMessage(id=mid, text=body, meta=self._region(root, b, next_top)))
        for m in msgs:
            if any(r.search(m.text) for r in self._limit_re):
                raise RateLimited(f"Vooy 사용 제한 안내: {m.text[:60]}")
        return msgs

    def _own_matches(self, own: UiNode) -> bool:
        body, _ = self._bubble_text(own)
        got = _norm(body)
        want = self._sent_norm or ""
        # 긴 메시지는 말풍선에서 잘릴 수 있어 앞부분으로 비교한다
        return bool(got) and (got == want or (len(got) >= 20 and want.startswith(got[:20])))

    def _region(self, root: UiNode, bubble: UiNode, next_top: int) -> dict[str, Any]:
        """봇 말풍선 하나에 딸린 버튼·이미지(말풍선 위쪽 ~ 다음 말풍선 위쪽, 대화 영역 안)."""
        conv = self._conversation(root)
        top = bubble.bounds.top
        buttons: list[str] = []
        images: list[tuple[int, int, int, int]] = []
        image_classes = tuple(self.sel.get("image_classes") or [])
        for n in conv.walk():
            b = n.bounds
            if b.empty or b.top < top or b.top >= next_top:
                continue
            if image_classes and n.cls.endswith(image_classes):
                images.append(b.as_tuple())
                continue
            if not n.clickable:
                continue
            # 말풍선을 감싼 컨테이너(desc=본문)는 버튼이 아니다
            if n.find(rid=self.sel["bot_bubble"]) or n.find(rid=self.sel["own_bubble"]):
                continue
            label = n.desc.strip() or " ".join(t.strip() for t in n.texts() if t.strip())
            if not label or (self._exclude_btn_re and self._exclude_btn_re.search(label)):
                continue
            if label not in buttons:
                buttons.append(label)
        return {"buttons": buttons, "images": images, "bounds": bubble.bounds.as_tuple()}

    async def _is_typing(self, thread: Thread) -> bool:
        root = await self._tree(max_age_s=0.5)
        for rid in self.sel.get("streaming_rids") or []:
            if root.find(rid=rid) is not None:
                return True
        if self._typing_tab_re is not None:
            tab = root.find(**self.sel["home_tab"])
            if tab is not None and self._typing_tab_re.search(tab.desc):
                return True
        return False

    # --- 훅: 추출 ----------------------------------------------------------

    async def _extract(self, msgs: list[RawMessage]) -> tuple[str, Structured]:
        parts: list[str] = []
        buttons: list[Button] = []
        confirm: Confirmation | None = None
        images: list[Image] = []
        labels: dict[str, str] = {}
        for m in msgs:
            line = m.text
            for label in m.meta.get("buttons", []):
                line += f" [버튼: {label}]"
                aid = action_id_for(label)
                labels[aid] = label
                if self._confirm_re is not None and self._confirm_re.search(label) and confirm is None:
                    confirm = Confirmation(summary=m.text.strip()[:300], action_id=aid)
                else:
                    buttons.append(Button(label=label, action_id=aid))
            parts.append(line)
            for box in m.meta.get("images", []):
                url = await self.crop_screenshot(Bounds(*box), alt=None)
                if url:
                    images.append(Image(url=url, alt=m.text[:40] or None))
        raw = "\n\n".join(parts)
        s = Structured()
        if buttons:
            s.buttons = buttons
        if confirm is not None:
            s.confirmation_required = confirm
        links = []
        for u in URL_RE.findall("\n".join(m.text for m in msgs)):
            if all(link.url != u for link in links):
                links.append(Link(title=u, url=u))
        if links:
            s.links = links
        if images:
            s.images = images
        if self._thread is not None:
            # [tap:] 때 action_id → 라벨을 찾는다. 베이스가 스레드를 저장한다
            self._thread.data["buttons"] = labels
        return raw, s

    # --- 훅: 로그인·리셋 ---------------------------------------------------

    async def _login_flow(self) -> LoginFlow:
        async def complete() -> None:
            # 로그인 상태는 AVD 디스크(userdata)에 남는다. 스냅샷은 쓰지 않는다(UNSUPPORTED_VK_APP, 03 7장)
            await self.device.login_completed()
            self._tree_cache = None

        return VncLogin(
            hint="noVNC 화면에서 Vooy 앱에 Google 또는 이메일로 로그인한 뒤 '완료'를 누르세요.",
            vnc_url=self.android.vnc_url,
            complete=complete,
        )

    async def _on_reset(self, thread: Thread | None) -> None:
        # 앱에 새 대화 기능이 없다(P2 확인). 봇 쪽 대화방은 그대로 두고 어댑터 상태만 지운다.
        # 수집은 보낸 메시지 이후만 보므로 기능 문제는 없다. Vooy 는 이전 대화를 기억한다(description 에 명시).
        self._baseline = set()
        self._sent_norm = None
