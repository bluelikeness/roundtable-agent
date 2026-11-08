"""어댑터 공통 베이스 (docs/03-adapters.md 2·3·4·6장).

베이스가 큐·타임아웃·재시도·AskResult 조립·MCP 서버·관리 HTTP 를 맡고,
봇별 구현은 훅 다섯 개(_ensure_ready, _send, _collect, _extract, _login_flow)만 채운다.

ask 는 어떤 경우에도 예외를 밖으로 던지지 않는다. 오류는 항상 AskResult.error 필드다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
import time
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal

import httpx
import yaml
from pydantic import BaseModel, ConfigDict, Field

log = logging.getLogger("adapters")

VERSION = "0.1.0"

TEXT_MAX = 4000
TIMEOUT_MIN, TIMEOUT_MAX, TIMEOUT_DEFAULT = 10, 240, 120

ErrorCode = Literal["timeout", "unavailable", "login_required", "rate_limited", "internal"]
StatusReason = Literal["login_required", "disabled", "backend_down", "cooldown"]

TAP_RE = re.compile(r"^\[tap:([^\]\s]+)\]\s*", re.DOTALL)


def utcnow_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# 계약 모델 (03 2·3장). 필드를 늘리지 않는다.
# ---------------------------------------------------------------------------


class Button(BaseModel):
    label: str
    action_id: str
    kind: Literal["callback", "url", "reply"] = "callback"


class Link(BaseModel):
    title: str
    url: str


class Card(BaseModel):
    title: str
    subtitle: str | None = None
    body: str | None = None
    url: str | None = None
    image_url: str | None = None


class Image(BaseModel):
    url: str
    alt: str | None = None


class Confirmation(BaseModel):
    summary: str
    action_id: str


class Structured(BaseModel):
    """봇 응답의 텍스트 외 요소. 못 뽑은 필드는 생략한다(직렬화 시 None 제외)."""

    buttons: list[Button] | None = None
    links: list[Link] | None = None
    cards: list[Card] | None = None
    images: list[Image] | None = None
    choices: list[str] | None = None
    confirmation_required: Confirmation | None = None

    def dump(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)


class AskResult(BaseModel):
    bot: str
    ok: bool
    raw: str = ""
    structured: dict[str, Any] = Field(default_factory=dict)
    needs_input: bool = False
    error: ErrorCode | None = None
    latency_ms: int = 0
    message_ids: list[str] = Field(default_factory=list)


class Status(BaseModel):
    available: bool
    reason: StatusReason | None = None
    last_ok_at: str | None = None
    queue_depth: int = 0
    avg_latency_ms: int = 0


# ---------------------------------------------------------------------------
# 어댑터 내부 모델
# ---------------------------------------------------------------------------


@dataclass
class RawMessage:
    """봇이 보낸 메시지 하나. meta 는 _extract 가 쓰는 봇별 부가 정보."""

    id: str
    text: str
    at: float = field(default_factory=time.time)
    meta: dict[str, Any] = field(default_factory=dict)


class Thread(BaseModel):
    """thread_id 에 매핑한 봇 쪽 대화 핸들. 디스크에 JSON 으로 저장해 재시작 후에도 잇는다."""

    model_config = ConfigDict(extra="ignore")

    thread_id: str
    created_at: str = Field(default_factory=utcnow_iso)
    last_at: str | None = None
    message_count: int = 0
    # 직전 응답의 버튼 action_id. [tap:] 은 이 목록 안에서만 유효하다(03 3장)
    last_action_ids: list[str] = Field(default_factory=list)
    # 직전 응답이 confirmation_required 였는지. 다음 메시지는 재시도하지 않는다(03 4장)
    pending_confirmation: bool = False
    # 봇별 대화 핸들(대화 URL, 마지막 말풍선 해시 등)
    data: dict[str, Any] = Field(default_factory=dict)


@dataclass
class CodeLogin:
    """인증코드 입력형 로그인. submit 은 {"ok": bool, "next"?: str} 를 돌려준다."""

    hint: str
    submit: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
    complete: Callable[[], Awaitable[None]] | None = None
    flow: str = "code"


@dataclass
class VncLogin:
    """noVNC 로 운영자가 직접 로그인. complete 에서 로그인 상태를 저장한다."""

    hint: str
    vnc_url: str
    complete: Callable[[], Awaitable[None]] | None = None
    flow: str = "vnc"


LoginFlow = CodeLogin | VncLogin


# 훅이 상태를 알리는 예외. ask 밖으로는 나가지 않고 AskResult.error 로 바뀐다.
class AdapterError(Exception):
    code: ErrorCode = "internal"


class LoginRequired(AdapterError):
    code = "login_required"


class BackendDown(AdapterError):
    code = "unavailable"


class RateLimited(AdapterError):
    code = "rate_limited"


class BotTimeout(AdapterError):
    code = "timeout"


# ---------------------------------------------------------------------------
# 설정 (adapters/<bot>/config.yaml, 비밀값은 env)
# ---------------------------------------------------------------------------


class TimeoutsConfig(BaseModel):
    default_s: int = TIMEOUT_DEFAULT
    send_s: float = 15
    quiet_s: float | None = None


class QueueConfig(BaseModel):
    max_depth: int = 5
    max_wait_s: float = 60
    cooldown_s: float = 30


class HealthConfig(BaseModel):
    probe_interval_s: float = 300


class AdapterConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str
    description: str
    timeouts: TimeoutsConfig = Field(default_factory=TimeoutsConfig)
    queue: QueueConfig = Field(default_factory=QueueConfig)
    progress_patterns: list[str] = Field(default_factory=list)
    question_patterns: list[str] = Field(default_factory=list)
    min_interval_s: float | None = None
    health: HealthConfig = Field(default_factory=HealthConfig)
    hub_webhook: str | None = None

    @classmethod
    def load(cls, path: str | Path) -> "AdapterConfig":
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        # 같은 이미지로 여러 개 띄울 때(mock-a, mock-b) 이름과 웹훅만 env 로 바꾼다
        if os.environ.get("ADAPTER_NAME"):
            data["name"] = os.environ["ADAPTER_NAME"]
        if os.environ.get("HUB_WEBHOOK_URL"):
            data["hub_webhook"] = os.environ["HUB_WEBHOOK_URL"]
        return cls.model_validate(data)


def read_token() -> str:
    """허브 공유 토큰. compose secret 파일을 우선하고, 없으면 env. 없으면 기동하지 않는다."""
    path = Path(os.environ.get("HUB_ADAPTER_TOKEN_FILE", "/run/secrets/hub_adapter_token"))
    token = path.read_text(encoding="utf-8").strip() if path.is_file() else ""
    token = token or os.environ.get("HUB_ADAPTER_TOKEN", "").strip()
    if not token:
        raise RuntimeError("HUB_ADAPTER_TOKEN(또는 secret 파일)이 없습니다. 토큰 없이 기동하지 않습니다")
    return token


# ---------------------------------------------------------------------------
# 베이스 클래스
# ---------------------------------------------------------------------------


class BotAdapter(ABC):
    name: str
    description: str
    quiet_s: float = 2.5
    progress_patterns: list[str] = []
    question_patterns: list[str] = []
    min_interval_s: float = 1.0
    poll_s: float = 0.25  # _collect 호출 간격

    def __init__(self, config: AdapterConfig, state_dir: str | Path, token: str = ""):
        self.config = config
        self.name = config.name
        self.description = config.description.strip()
        if config.timeouts.quiet_s is not None:
            self.quiet_s = config.timeouts.quiet_s
        if config.progress_patterns:
            self.progress_patterns = config.progress_patterns
        if config.question_patterns:
            self.question_patterns = config.question_patterns
        if config.min_interval_s is not None:
            self.min_interval_s = config.min_interval_s
        self.send_s = config.timeouts.send_s
        self.default_timeout_s = config.timeouts.default_s
        self._progress_re = [re.compile(p) for p in self.progress_patterns]
        self._question_re = [re.compile(p) for p in self.question_patterns]
        self.token = token

        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        (self.state_dir / "images").mkdir(exist_ok=True)
        self._threads_path = self.state_dir / "threads.json"
        self.threads: dict[str, Thread] = self._load_threads()

        self._lock = asyncio.Lock()
        self._waiting = 0
        self._last_send_mono = 0.0
        self._started_mono = time.monotonic()
        self.enabled = True
        self._login_required = False
        self._backend_down = False
        self._cooldown_until = 0.0
        self.last_ok_at: str | None = None
        self._latencies: deque[int] = deque(maxlen=10)
        self.logs: deque[dict[str, Any]] = deque(maxlen=500)
        self._login: LoginFlow | None = None
        self._bg: set[asyncio.Task] = set()

    # --- 봇별 구현 훅 -------------------------------------------------------

    @abstractmethod
    async def _ensure_ready(self) -> None:
        """로그인 상태 확인. 아니면 LoginRequired, 백엔드 장애면 BackendDown."""

    @abstractmethod
    async def _send(self, thread: Thread, text: str, tap: str | None) -> None:
        """봇에 전송. tap 이 있으면 그 버튼을 누르고 text 가 있으면 이어서 보낸다."""

    @abstractmethod
    async def _collect(self, thread: Thread, deadline: float) -> list[RawMessage]:
        """마지막 _send 이후 지금까지 도착한 봇 메시지를 모두 돌려준다(누적).

        베이스가 poll_s 간격으로 반복 호출하며 quiet_s 침묵 판정과 progress_patterns 필터를 적용한다.
        deadline(time.monotonic 기준)을 넘겨 블록하지 않는다.
        """

    @abstractmethod
    async def _extract(self, msgs: list[RawMessage]) -> tuple[str, Structured]:
        """원문과 structured 추출. 버튼 라벨은 raw 에 [버튼: X] 로 남긴다."""

    @abstractmethod
    async def _login_flow(self) -> LoginFlow:
        """CodeLogin 또는 VncLogin."""

    # --- 선택 훅 (기본 구현 있음) ------------------------------------------

    async def _is_typing(self, thread: Thread) -> bool:
        """봇이 응답 중 표시(스피너·타이핑)를 띄우고 있으면 True. 꺼질 때까지 확정을 미룬다."""
        return False

    async def _probe(self) -> None:
        """POST /probe 의 실제 확인. 기본은 _ensure_ready."""
        await self._ensure_ready()

    async def _on_reset(self, thread: Thread | None) -> None:
        """봇 쪽 대화 초기화. None 이면 전체."""

    async def _on_start(self) -> None:
        """서버 기동 직전 1회."""

    # --- ask ---------------------------------------------------------------

    async def ask(self, text: Any, thread_id: Any, timeout_s: Any = None, reset: Any = False) -> AskResult:
        t0 = time.monotonic()
        record: dict[str, Any] = {"at": utcnow_iso(), "thread_id": thread_id, "text": str(text)[:200]}
        try:
            res = await self._ask(text, thread_id, timeout_s, reset, t0, record)
        except Exception as e:  # 마지막 방어선: 어떤 예외도 MCP 로 새지 않는다
            log.exception("ask 내부 오류")
            record["detail"] = f"{type(e).__name__}: {e}"
            res = self._fail("internal", t0)
        record.update(ok=res.ok, error=res.error, latency_ms=res.latency_ms, message_ids=res.message_ids)
        self.logs.append(record)
        return res

    def _fail(self, code: ErrorCode, t0: float) -> AskResult:
        return AskResult(bot=self.name, ok=False, error=code, latency_ms=self._elapsed_ms(t0))

    @staticmethod
    def _elapsed_ms(t0: float) -> int:
        return max(1, int((time.monotonic() - t0) * 1000))

    async def _ask(self, text, thread_id, timeout_s, reset, t0: float, record: dict) -> AskResult:
        # 입력 검증: 스키마 위반도 예외가 아니라 error=internal
        if timeout_s is None:
            timeout_s = self.default_timeout_s
        problem = None
        if not isinstance(text, str) or not text.strip():
            problem = "text 는 비어 있지 않은 문자열이어야 합니다"
        elif len(text) > TEXT_MAX:
            problem = f"text 는 {TEXT_MAX}자 이하여야 합니다"
        elif not isinstance(thread_id, str) or not thread_id:
            problem = "thread_id 는 비어 있지 않은 문자열이어야 합니다"
        elif isinstance(timeout_s, bool) or not isinstance(timeout_s, int) or not TIMEOUT_MIN <= timeout_s <= TIMEOUT_MAX:
            problem = f"timeout_s 는 {TIMEOUT_MIN}~{TIMEOUT_MAX} 정수여야 합니다"
        elif not isinstance(reset, bool):
            problem = "reset 은 bool 이어야 합니다"
        if problem:
            record["detail"] = problem
            return self._fail("internal", t0)

        if not self.enabled:
            record["detail"] = "disabled"
            return self._fail("unavailable", t0)

        deadline = t0 + timeout_s
        if self._waiting >= self.config.queue.max_depth:
            record["detail"] = "queue full"
            return self._fail("rate_limited", t0)

        # queued: 큐 대기도 timeout_s 에 포함. 큐에서만 max_wait_s 를 넘으면 rate_limited + cooldown
        self._waiting += 1
        acquired = False
        try:
            wait_limit = min(self.config.queue.max_wait_s, deadline - time.monotonic())
            try:
                await asyncio.wait_for(self._lock.acquire(), timeout=max(0.0, wait_limit))
                acquired = True
            except TimeoutError:
                if wait_limit >= self.config.queue.max_wait_s:
                    self._cooldown_until = time.monotonic() + self.config.queue.cooldown_s
                    record["detail"] = "queue wait exceeded"
                    return self._fail("rate_limited", t0)
                record["detail"] = "timeout while queued"
                return self._fail("timeout", t0)
        finally:
            self._waiting -= 1

        try:
            # 훅(_extract·_is_typing·_on_reset 등)이 멈춰도 timeout_s 를 넘기지 않고 큐 락을 놓는다
            async with asyncio.timeout(max(0.0, deadline - time.monotonic())):
                return await self._run(text, thread_id, reset, t0, deadline, record)
        except TimeoutError:
            record.setdefault("detail", "ask 전체 시간 초과")
            return self._fail("timeout", t0)
        finally:
            if acquired:
                self._lock.release()

    async def _run(self, text: str, thread_id: str, reset: bool, t0: float, deadline: float, record: dict) -> AskResult:
        # 연속 전송 최소 간격
        gap = self._last_send_mono + self.min_interval_s - time.monotonic()
        if gap > 0:
            await asyncio.sleep(min(gap, max(0.0, deadline - time.monotonic())))

        if reset:
            await self.reset(thread_id)
        thread = self.threads.get(thread_id) or Thread(thread_id=thread_id)
        self.threads[thread_id] = thread

        tap = None
        body = text
        m = TAP_RE.match(text)
        if m:
            tap, body = m.group(1), text[m.end() :]
            if tap not in thread.last_action_ids:
                record["detail"] = f"unknown action_id: {tap} (직전 응답의 버튼이 아님)"
                return self._fail("internal", t0)
        no_retry = tap is not None or thread.pending_confirmation

        try:
            # sending: 로그인 확인 + 전송, send_s 제한
            send_limit = min(self.send_s, deadline - time.monotonic())
            try:
                async with asyncio.timeout(max(0.0, send_limit)):
                    await self._ensure_ready()
                    await self._send(thread, body, tap)
            except TimeoutError:
                raise BotTimeout("sending 단계 시간 초과") from None
            self._last_send_mono = time.monotonic()

            # waiting: 1회차는 잔여 시간의 절반까지. 무응답이면 같은 text 를 한 번 더 보낸다
            remaining = deadline - time.monotonic()
            first_until = deadline if no_retry else time.monotonic() + remaining / 2
            msgs, saw_any = await self._wait_reply(thread, first_until)
            if not msgs and saw_any:
                # 진행 메시지는 왔다: 재전송하지 않고 남은 시간 동안 계속 기다린다
                msgs, _ = await self._wait_reply(thread, deadline)
            elif not msgs and not no_retry:
                record["retried"] = True
                async with asyncio.timeout(max(0.0, min(self.send_s, deadline - time.monotonic()))):
                    await self._send(thread, body, tap)
                self._last_send_mono = time.monotonic()
                msgs, _ = await self._wait_reply(thread, deadline)
            if not msgs:
                raise BotTimeout("응답 없음")

            raw, structured = await self._extract(msgs)
        except AdapterError as e:
            record["detail"] = str(e) or e.code
            self._note_failure(e)
            return self._fail(e.code, t0)
        except TimeoutError:
            record["detail"] = "timeout"
            return self._fail("timeout", t0)

        sd = structured.dump()
        thread.last_action_ids = [b.action_id for b in structured.buttons or []]
        if structured.confirmation_required:
            thread.last_action_ids.append(structured.confirmation_required.action_id)
        thread.pending_confirmation = structured.confirmation_required is not None
        thread.message_count += 1 + len(msgs)
        thread.last_at = utcnow_iso()
        self._save_threads()

        latency = self._elapsed_ms(t0)
        self._latencies.append(latency)
        self.last_ok_at = utcnow_iso()
        self._login_required = self._backend_down = False
        return AskResult(
            bot=self.name,
            ok=True,
            raw=raw,
            structured=sd,
            needs_input=self._needs_input(raw, structured),
            latency_ms=latency,
            message_ids=[m.id for m in msgs],
        )

    def _is_progress(self, msg: RawMessage) -> bool:
        return any(r.search(msg.text) for r in self._progress_re)

    async def _wait_reply(self, thread: Thread, until: float) -> tuple[list[RawMessage], bool]:
        """응답 확정 규칙(03 4장). (확정 메시지, 진행 메시지 포함 무엇이든 왔는지)."""
        last_sig: tuple | None = None
        last_change = time.monotonic()
        real: list[RawMessage] = []
        saw_any = False
        while True:
            now = time.monotonic()
            msgs = await self._collect(thread, until)
            sig = tuple((m.id, m.text) for m in msgs)
            if sig != last_sig:
                last_sig, last_change = sig, time.monotonic()
            saw_any = saw_any or bool(msgs)
            real = [m for m in msgs if not self._is_progress(m)]
            now = time.monotonic()
            if real and now - last_change >= self.quiet_s and not await self._is_typing(thread):
                return real, saw_any
            if now >= until:
                # 시간이 다 됐으면 받은 만큼이라도 돌려준다
                return real, saw_any
            await asyncio.sleep(min(self.poll_s, max(0.0, until - now)))

    def _needs_input(self, raw: str, s: Structured) -> bool:
        if s.choices or s.buttons:
            return True
        last = raw.strip().splitlines()[-1].strip() if raw.strip() else ""
        if last.endswith("?") or last.endswith("？"):
            return True
        return any(r.search(raw) for r in self._question_re)

    # --- 상태 --------------------------------------------------------------

    def _note_failure(self, e: AdapterError) -> None:
        if isinstance(e, LoginRequired):
            self._login_required = True
            self._notify_hub("login_required")
        elif isinstance(e, BackendDown):
            self._backend_down = True
            self._notify_hub("backend_down")

    async def status(self) -> Status:
        reason: StatusReason | None = None
        if not self.enabled:
            reason = "disabled"
        elif self._login_required:
            reason = "login_required"
        elif self._backend_down:
            reason = "backend_down"
        elif time.monotonic() < self._cooldown_until:
            reason = "cooldown"
        avg = int(sum(self._latencies) / len(self._latencies)) if self._latencies else 0
        return Status(
            available=reason is None,
            reason=reason,
            last_ok_at=self.last_ok_at,
            queue_depth=self._waiting,
            avg_latency_ms=avg,
        )

    async def health(self) -> dict[str, Any]:
        out = (await self.status()).model_dump()
        out["version"] = VERSION
        out["uptime_s"] = int(time.monotonic() - self._started_mono)
        return out

    async def probe(self) -> dict[str, Any]:
        """봇 백엔드에 실제 확인을 한 뒤 health 를 돌려준다."""
        try:
            await asyncio.wait_for(self._probe(), timeout=self.send_s)
        except LoginRequired as e:
            self._note_failure(e)
        except Exception as e:  # noqa: BLE001 - 어떤 장애든 backend_down
            log.warning("probe 실패: %s", e)
            self._note_failure(BackendDown(str(e)))
        else:
            self._login_required = self._backend_down = False
            self.last_ok_at = utcnow_iso()
        return await self.health()

    def _notify_hub(self, reason: str) -> None:
        """허브 웹훅(POST /internal/bot-status). 기다리지 않고, 실패는 무시한다."""
        url = self.config.hub_webhook
        if not url:
            return

        async def send() -> None:
            payload = {"bot": self.name, "available": False, "reason": reason, "at": utcnow_iso()}
            try:
                async with httpx.AsyncClient(timeout=3) as c:
                    await c.post(url, json=payload, headers={"X-Hub-Token": self.token})
            except Exception as e:  # noqa: BLE001
                log.info("허브 웹훅 실패(무시): %s", e)

        self._spawn(send())

    def _spawn(self, coro) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)

    # --- 관리 동작 ---------------------------------------------------------

    async def reset(self, thread_id: str | None) -> None:
        if thread_id is None:
            await self._on_reset(None)
            self.threads.clear()
        elif thread_id in self.threads:
            await self._on_reset(self.threads.pop(thread_id))
        self._save_threads()

    async def login_start(self) -> dict[str, Any]:
        self._login = await self._login_flow()
        out: dict[str, Any] = {"flow": self._login.flow, "hint": self._login.hint}
        if isinstance(self._login, VncLogin):
            out["vnc_url"] = self._login.vnc_url
        return out

    async def login_submit(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(self._login, CodeLogin):
            return {"ok": False, "next": "start"}
        res = await self._login.submit(payload)
        if res.get("ok"):
            await self.probe()
        return res

    async def login_complete(self) -> dict[str, Any]:
        flow = self._login or await self._login_flow()
        if flow.complete is not None:
            await flow.complete()
        self._login = None
        st = await self.probe()
        return {"ok": bool(st["available"]) or st["reason"] == "disabled"}

    def list_threads(self) -> list[dict[str, Any]]:
        return [
            {"thread_id": t.thread_id, "last_at": t.last_at, "message_count": t.message_count}
            for t in self.threads.values()
        ]

    def list_logs(self, since: str | None) -> list[dict[str, Any]]:
        if not since:
            return list(self.logs)
        return [r for r in self.logs if r["at"] > since]

    # --- 이미지 스토리지 (structured.images[].url 의 로컬 파일) -------------

    def store_image(self, data: bytes, content_type: str) -> str:
        """이미지를 저장하고 structured 에 넣을 로컬 URL(/images/<id>)을 돌려준다."""
        image_id = secrets.token_hex(8)
        d = self.state_dir / "images"
        (d / image_id).write_bytes(data)
        (d / f"{image_id}.type").write_text(content_type, encoding="utf-8")
        return f"/images/{image_id}"

    def load_image(self, image_id: str) -> tuple[bytes, str] | None:
        if not re.fullmatch(r"[0-9a-f]{16}", image_id):
            return None
        d = self.state_dir / "images"
        p = d / image_id
        if not p.is_file():
            return None
        ctype = (d / f"{image_id}.type").read_text(encoding="utf-8").strip() or "application/octet-stream"
        return p.read_bytes(), ctype

    # --- 스레드 저장 -------------------------------------------------------

    def _load_threads(self) -> dict[str, Thread]:
        if not self._threads_path.is_file():
            return {}
        try:
            data = json.loads(self._threads_path.read_text(encoding="utf-8"))
            return {k: Thread.model_validate(v) for k, v in data.items()}
        except Exception:  # noqa: BLE001 - 깨진 파일이면 새로 시작
            log.exception("threads.json 을 읽지 못했습니다. 빈 상태로 시작합니다")
            return {}

    def _save_threads(self) -> None:
        tmp = self._threads_path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({k: t.model_dump() for k, t in self.threads.items()}, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp.replace(self._threads_path)

    # --- 기동 --------------------------------------------------------------

    async def _probe_loop(self) -> None:
        while True:
            await asyncio.sleep(self.config.health.probe_interval_s)
            await self.probe()

    async def run(self, host: str = "0.0.0.0", mcp_port: int = 9000, admin_port: int = 9100) -> None:
        import uvicorn

        from adapters.admin_api import build_admin_app
        from adapters.mcp_server import build_mcp_app

        await self._on_start()
        await self.probe()
        self._spawn(self._probe_loop())
        servers = [
            uvicorn.Server(uvicorn.Config(build_mcp_app(self), host=host, port=mcp_port, log_level="info")),
            uvicorn.Server(uvicorn.Config(build_admin_app(self), host=host, port=admin_port, log_level="info")),
        ]
        await asyncio.gather(*(s.serve() for s in servers))

    def serve(self) -> None:
        """MCP(9000) + 관리 HTTP(9100) 동시 기동."""
        logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
        asyncio.run(
            self.run(
                mcp_port=int(os.environ.get("MCP_PORT", "9000")),
                admin_port=int(os.environ.get("ADMIN_PORT", "9100")),
            )
        )
