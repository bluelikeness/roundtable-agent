"""Mock 어댑터 (03 10장). 외부 호출 없이 베이스 위에서 가짜 봇을 흉내 낸다.

설정의 mock 블록대로 지연·실패·되물음을 무작위로 만든다. 계약 테스트처럼 결과를 고정해야 할 때는
text 에 지시어를 넣는다(봇이 자기 메시지를 읽고 행동을 바꾸는 것으로 본다).

  [mock:ok]       무작위 실패·되물음을 끈다
  [mock:slow]     응답하지 않는다(타임아웃 유도)
  [mock:ask]      되물음 + choices 로 답한다
  [mock:confirm]  결제 확인 단계(confirmation_required)로 답한다
  [mock:image]    이미지 첨부 하나를 붙여 답한다
  [mock:fast]     지연을 0.2초로 줄인다

로그인 상태는 state_dir/session.json 파일이 있느냐로 흉내 낸다. 지우면 login_required.

평가(runner/eval)는 지시어를 Claude 가 넣게 할 수 없으므로 설정으로 고정 시나리오를 만든다.
  ask_back   스레드마다 처음 times 번(없으면 매번) 이 질문으로 되묻는다
  confirm    되물음이 끝난 뒤의 일반 응답을 이 확정 요청으로 바꾼다. [tap:<action_id>] 면 확정 완료로 답한다
"""

from __future__ import annotations

import asyncio
import base64
import json
import random
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from adapters.base import (
    AdapterConfig,
    BotAdapter,
    BotTimeout,
    Button,
    CodeLogin,
    Confirmation,
    Image,
    Link,
    LoginFlow,
    LoginRequired,
    RawMessage,
    Structured,
    Thread,
    utcnow_iso,
)

BUTTON_RE = re.compile(r"\[버튼:\s*([^\]]+?)\s*\]")
URL_RE = re.compile(r"https?://[^\s\]\)]+")
DIRECTIVE_RE = re.compile(r"\[mock:(\w+)\]\s*")

# 1x1 PNG (images[] 경로 테스트용)
PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


class LatencyRange(BaseModel):
    min: float = 2
    max: float = 8


class AskBack(BaseModel):
    text: str = "몇 시로 할까요?"
    choices: list[str] = Field(default_factory=list)
    # 스레드당 되묻는 횟수. None 이면 매번
    times: int | None = None


class ConfirmStep(BaseModel):
    text: str
    summary: str
    action_id: str = "btn_confirm"
    done_text: str = "확정되었습니다."


class MockConfig(BaseModel):
    latency_s: LatencyRange = Field(default_factory=LatencyRange)
    fail_rate: float = 0.0
    needs_input_rate: float = 0.0
    unavailable_after: int | None = None
    replies: dict[str, str] = Field(default_factory=dict)
    ask_back: AskBack | None = None
    confirm: ConfirmStep | None = None


class MockAdapter(BotAdapter):
    def __init__(self, config: AdapterConfig, state_dir: str | Path, token: str = "", seed: int | None = None):
        super().__init__(config, state_dir, token)
        self.mock = MockConfig.model_validate((config.model_extra or {}).get("mock") or {})
        self._session_path = self.state_dir / "session.json"
        self._rng = random.Random(seed)
        self._sent = 0
        # thread_id → 마지막 전송 이후 도착한 메시지 / 실패 여부 / 응답 예약 태스크
        self._inbox: dict[str, list[RawMessage]] = {}
        self._failed: dict[str, bool] = {}
        self._pending: dict[str, asyncio.Task] = {}

    # --- 로그인 흉내 --------------------------------------------------------

    def _write_session(self) -> None:
        self._session_path.write_text(json.dumps({"logged_in_at": utcnow_iso()}), encoding="utf-8")

    async def _on_start(self) -> None:
        # 처음 뜰 때만 로그인된 상태로 시작한다. 이후에는 session.json 유무를 그대로 따른다
        marker = self.state_dir / ".initialized"
        if not marker.exists():
            self._write_session()
            marker.write_text(utcnow_iso(), encoding="utf-8")

    async def _ensure_ready(self) -> None:
        n = self.mock.unavailable_after
        if n is not None and self._sent + 1 >= n:
            raise LoginRequired(f"unavailable_after={n}")
        if not self._session_path.is_file():
            raise LoginRequired("session.json 없음")

    async def _login_flow(self) -> LoginFlow:
        async def submit(payload: dict[str, Any]) -> dict[str, Any]:
            if payload.get("code") or payload.get("password"):
                self._write_session()
                return {"ok": True}
            return {"ok": False, "next": "code"}

        async def complete() -> None:
            self._write_session()

        return CodeLogin(hint="Mock 로그인: 아무 코드나 넣으면 성공합니다", submit=submit, complete=complete)

    # --- 송수신 -------------------------------------------------------------

    async def _send(self, thread: Thread, text: str, tap: str | None) -> None:
        self._sent += 1
        tid = thread.thread_id
        old = self._pending.pop(tid, None)
        if old:
            old.cancel()
        self._inbox[tid] = []
        self._failed[tid] = False

        directives = set(DIRECTIVE_RE.findall(text))
        body = DIRECTIVE_RE.sub("", text).strip()
        deterministic = "ok" in directives or bool(directives - {"fast"})
        if "slow" in directives:
            return  # 응답하지 않는다

        latency = 0.2 if "fast" in directives else self._rng.uniform(self.mock.latency_s.min, self.mock.latency_s.max)
        fail = not deterministic and tap is None and self._rng.random() < self.mock.fail_rate
        ask_back = "ask" in directives or (
            not deterministic and tap is None and self._rng.random() < self.mock.needs_input_rate
        )
        meta: dict[str, Any] = {}
        scripted = self.mock.ask_back
        if not directives and tap is None and scripted is not None:
            asked = int(thread.data.get("ask_backs", 0))
            if scripted.times is None or asked < scripted.times:
                thread.data["ask_backs"] = asked + 1
                ask_back = True
        confirm = self.mock.confirm
        if tap is not None:
            if confirm is not None and tap == confirm.action_id:
                reply = confirm.done_text
            else:
                label = thread.data.get("buttons", {}).get(tap, tap)
                reply = f"'{label}' 선택하셨습니다." + (f" {body}" if body else "")
        elif "confirm" in directives:
            reply = "KTX 서울→부산 11/12 07:00 결제 58,800원. 진행할까요?"
            meta["confirmation"] = {"summary": "KTX 서울→부산 11/12 07:00 결제 58,800원", "action_id": "btn_pay"}
        elif ask_back:
            if scripted is not None and not directives:
                reply = scripted.text
                if scripted.choices:
                    meta["choices"] = list(scripted.choices)
            else:
                reply = "몇 시로 할까요?"
                meta["choices"] = ["18:00", "18:30", "19:00"]
        elif confirm is not None and not directives:
            reply = confirm.text
            meta["confirmation"] = {"summary": confirm.summary, "action_id": confirm.action_id}
        else:
            reply = next((tpl for kw, tpl in self.mock.replies.items() if kw in body), None) or f"echo: {body}"
        if "image" in directives:
            meta["images"] = [{"url": self.store_image(PIXEL_PNG, "image/png"), "alt": "mock 이미지"}]

        # 버튼 action_id 는 스레드별로 기억해 두어야 [tap:] 때 라벨을 찾는다
        buttons = [{"label": lb, "action_id": f"btn_{i + 1}"} for i, lb in enumerate(BUTTON_RE.findall(reply))]
        meta["buttons"] = buttons
        thread.data["buttons"] = {b["action_id"]: b["label"] for b in buttons}
        seq = int(thread.data.get("seq", 0)) + 1
        thread.data["seq"] = seq
        msg = RawMessage(id=f"{tid}#{seq}", text=reply, meta=meta)

        async def deliver() -> None:
            await asyncio.sleep(latency)
            if fail:
                self._failed[tid] = True
            else:
                self._inbox.setdefault(tid, []).append(msg)

        self._pending[tid] = asyncio.get_running_loop().create_task(deliver())

    async def _collect(self, thread: Thread, deadline: float) -> list[RawMessage]:
        if self._failed.get(thread.thread_id):
            raise BotTimeout("mock fail_rate")
        return list(self._inbox.get(thread.thread_id, []))

    async def _extract(self, msgs: list[RawMessage]) -> tuple[str, Structured]:
        raw = "\n\n".join(m.text for m in msgs)
        s = Structured()
        buttons = [Button(**b) for m in msgs for b in m.meta.get("buttons", [])]
        if buttons:
            s.buttons = buttons
        links = [Link(title=u, url=u) for u in URL_RE.findall(raw)]
        if links:
            s.links = links
        images = [Image(**i) for m in msgs for i in m.meta.get("images", [])]
        if images:
            s.images = images
        for m in msgs:
            if m.meta.get("choices"):
                s.choices = list(m.meta["choices"])
            if m.meta.get("confirmation"):
                s.confirmation_required = Confirmation(**m.meta["confirmation"])
        return raw, s

    async def _on_reset(self, thread: Thread | None) -> None:
        tids = [thread.thread_id] if thread else list(self._inbox)
        for tid in tids:
            task = self._pending.pop(tid, None)
            if task:
                task.cancel()
            self._inbox.pop(tid, None)
            self._failed.pop(tid, None)
