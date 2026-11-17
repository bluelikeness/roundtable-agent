"""봇 상태 감시와 어댑터 관리 HTTP 클라이언트 (docs/03-adapters.md 5장, docs/05-ui.md 4장).

- 30초마다 각 어댑터 GET /health 를 폴링해 bot 테이블을 갱신하고, 바뀌면 bot.status 를 모든 연결에 보낸다
- 어댑터 웹훅(POST /internal/bot-status)은 폴링을 기다리지 않고 같은 경로로 반영한다
- 허브 REST 의 봇 관리(로그인·초기화·이미지 등)는 이 클라이언트로 어댑터를 부른다. 어댑터 토큰은 허브만 갖는다

enabled 는 허브 bot 테이블이 진실이다(어댑터의 enabled 는 메모리라 재시작하면 풀린다).
허브에서 끈 봇은 어댑터 상태와 무관하게 reason=disabled 로 보낸다.
"""

import asyncio
import contextlib
import logging
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bots import BotConfig, load_bots
from app.config import Settings
from app.events import BotStatus, BotStatusPayload
from app.models import Bot

log = logging.getLogger("hub.bots")

# 관리 HTTP 기본 상한. probe·login 은 봇 백엔드를 실제로 건드려 오래 걸릴 수 있다
ADMIN_TIMEOUT = 60.0
HEALTH_TIMEOUT = 5.0


class AdminError(Exception):
    """허브 REST 가 그대로 HTTP 오류로 바꾼다."""

    def __init__(self, status: int, code: str):
        self.status = status
        self.code = code


def _now() -> datetime:
    return datetime.now(UTC)


def _parse_ts(v: Any) -> datetime | None:
    if not isinstance(v, str) or not v:
        return None
    try:
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


class BotMonitor:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        settings: Settings,
        broadcast,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._sm = sessionmaker
        self.settings = settings
        # broadcast(ev: BotStatus) — RunManager.broadcast_global
        self._broadcast = broadcast
        self._client = httpx.AsyncClient(
            transport=transport,
            timeout=ADMIN_TIMEOUT,
            headers={"X-Hub-Token": settings.read_adapter_token()},
        )
        self.statuses: dict[str, BotStatusPayload] = {}
        self._task: asyncio.Task | None = None
        self._switch: asyncio.Task | None = None

    # --- 기동·종료 ---

    async def start(self) -> None:
        """DB 에 남은 마지막 상태로 시작하고, 간격이 0보다 크면 폴링을 돌린다."""
        names = {b.name for b in self.bots()}
        async with self._sm() as db:
            for row in (await db.execute(select(Bot))).scalars():
                if row.name in names:
                    self.statuses[row.name] = self._payload(row, None)
        if self.settings.health_interval_s > 0:
            self._task = asyncio.create_task(self._loop(), name="bot-monitor")

    async def aclose(self) -> None:
        if self._switch is not None:
            self._switch.cancel()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
        await self._client.aclose()

    async def _loop(self) -> None:
        while True:
            try:
                await self.poll_once()
            except Exception:
                log.exception("헬스체크 폴링 오류")
            await asyncio.sleep(self.settings.health_interval_s)

    async def poll_once(self) -> None:
        bots = [b for b in self.bots() if b.admin_url]
        await asyncio.gather(*(self.refresh(b.name) for b in bots), return_exceptions=True)

    # --- 봇 목록 ---

    def bots(self) -> list[BotConfig]:
        return load_bots(self.settings.bots_file)

    def bot(self, name: str) -> BotConfig:
        for b in self.bots():
            if b.name == name:
                if not b.admin_url:
                    raise AdminError(404, "bot_has_no_admin")
                return b
        raise AdminError(404, "bot_not_found")

    def snapshot(self) -> list[BotStatus]:
        order = [b.name for b in self.bots()]
        return [self._event(self.statuses[n]) for n in order if n in self.statuses]

    # --- 관리 HTTP ---

    async def call(
        self, name: str, method: str, path: str, json: Any = None, timeout: float | None = None
    ) -> httpx.Response:
        b = self.bot(name)
        url = b.admin_url.rstrip("/") + path  # type: ignore[union-attr]
        try:
            r = await self._client.request(method, url, json=json, timeout=timeout or ADMIN_TIMEOUT)
        except httpx.HTTPError as e:
            log.info("어댑터 %s %s %s 실패: %s", name, method, path, type(e).__name__)
            raise AdminError(502, "adapter_unreachable") from None
        if r.status_code in (401, 403):
            # 토큰 불일치. 브라우저 쪽 인증 문제로 오해하지 않게 502 로 바꾼다
            raise AdminError(502, "adapter_auth_failed")
        return r

    async def call_json(self, name: str, method: str, path: str, json: Any = None, timeout: float | None = None) -> Any:
        r = await self.call(name, method, path, json=json, timeout=timeout)
        if r.status_code >= 400:
            raise AdminError(502, "adapter_error")
        try:
            return r.json()
        except ValueError:
            raise AdminError(502, "adapter_bad_response") from None

    # --- 휴대폰(폰 게이트웨이) ---

    async def phone(self, method: str, path: str, json: Any = None) -> Any:
        """폰 게이트웨이 내부 API. 주소가 없으면 404, 닿지 않으면 502."""
        base = self.settings.phone_gateway_url.rstrip("/")
        if not base:
            raise AdminError(404, "phone_gateway_off")
        try:
            r = await self._client.request(method, base + path, json=json)
        except httpx.HTTPError as e:
            log.info("폰 게이트웨이 %s %s 실패: %s", method, path, type(e).__name__)
            raise AdminError(502, "phone_gateway_unreachable") from None
        if r.status_code == 400:
            raise AdminError(422, "bad_device")
        if r.status_code >= 300:
            raise AdminError(502, "phone_gateway_error")
        return r.json()

    def after_phone_switch(self, prefer: str) -> None:
        """우선 휴대폰을 바꾼 뒤: 새 폰이 붙기를 기다렸다가 봇 상태를 다시 확인한다.
        휴대폰이 바뀌는 동안 실패한 봇이 backend_down 에 머물지 않게 한다."""
        if self._switch is not None and not self._switch.done():
            self._switch.cancel()
        self._switch = asyncio.create_task(self._wait_phone_then_probe(prefer), name="phone-switch")

    async def _wait_phone_then_probe(self, prefer: str) -> None:
        loop = asyncio.get_running_loop()
        end = loop.time() + self.settings.phone_switch_wait_s
        while loop.time() < end:
            await asyncio.sleep(2)
            try:
                st = await self.phone("GET", "/state")
            except AdminError:
                continue
            dev = st.get("device") or ""
            if st.get("connected") and (not prefer or prefer in dev):
                break
        else:
            log.warning("우선 휴대폰 %s 가 %s초 안에 붙지 않음", prefer, self.settings.phone_switch_wait_s)
        bots = [b.name for b in self.bots() if b.admin_url]
        await asyncio.gather(*(self.refresh(n, probe=True) for n in bots), return_exceptions=True)
        # 잠긴 폰은 사람이 풀 때까지 실패한다. 풀리면 곧 쓸 수 있게 한동안 실패한 봇만 다시 확인한다
        end = loop.time() + self.settings.phone_switch_recheck_s
        while loop.time() < end:
            down = [n for n in bots if (st := self.statuses.get(n)) is not None and not st.available and st.reason == "backend_down"]
            if not down:
                return
            await asyncio.sleep(self.settings.phone_switch_recheck_every_s)
            await asyncio.gather(*(self.refresh(n, probe=True) for n in down), return_exceptions=True)

    # --- 상태 ---

    async def refresh(self, name: str, probe: bool = False) -> BotStatusPayload:
        """어댑터 health(또는 probe)를 읽어 반영한다. 어댑터가 응답하지 않으면 backend_down."""
        try:
            if probe:
                data = await self.call_json(name, "POST", "/probe")
            else:
                data = await self.call_json(name, "GET", "/health", timeout=HEALTH_TIMEOUT)
            if not isinstance(data, dict):
                raise AdminError(502, "adapter_bad_response")
        except AdminError as e:
            if e.code in ("bot_not_found", "bot_has_no_admin"):
                raise
            return await self.apply(name, available=False, reason="backend_down")
        return await self.apply(
            name,
            available=bool(data.get("available")),
            reason=data.get("reason") if isinstance(data.get("reason"), str) else None,
            last_ok_at=_parse_ts(data.get("last_ok_at")),
            queue_depth=data.get("queue_depth") if isinstance(data.get("queue_depth"), int) else 0,
            avg_latency_ms=data.get("avg_latency_ms") if isinstance(data.get("avg_latency_ms"), int) else None,
        )

    async def apply(
        self,
        name: str,
        *,
        available: bool,
        reason: str | None,
        last_ok_at: datetime | None = None,
        queue_depth: int | None = None,
        avg_latency_ms: int | None = None,
        enabled: bool | None = None,
    ) -> BotStatusPayload:
        """bot 테이블을 갱신하고, 화면에 보일 값이 바뀌었으면 bot.status 를 보낸다."""
        prev = self.statuses.get(name)
        async with self._sm() as db:
            row = await db.get(Bot, name)
            if row is None:
                row = Bot(name=name, enabled=True, available=False)
                db.add(row)
            if enabled is not None:
                row.enabled = enabled
            if not row.enabled:
                available, reason = False, "disabled"
            row.available = available
            row.reason = reason
            if last_ok_at is not None:
                row.last_ok_at = last_ok_at
            row.updated_at = _now()
            await db.commit()
            await db.refresh(row)
            extra = {
                "queue_depth": queue_depth if queue_depth is not None else (prev.queue_depth if prev else 0),
                "avg_latency_ms": avg_latency_ms if avg_latency_ms is not None else (prev.avg_latency_ms if prev else None),
            }
            payload = self._payload(row, extra)
        self.statuses[name] = payload
        if payload != prev:
            self._broadcast(self._event(payload))
        return payload

    async def set_enabled(self, name: str, enabled: bool) -> BotStatusPayload:
        self.bot(name)
        try:
            await self.call(name, "POST", "/enable" if enabled else "/disable", json={})
        except AdminError as e:
            # 어댑터가 꺼져 있어도 허브 쪽 토글은 남긴다(허브가 진실)
            log.info("어댑터 %s 토글 실패(허브만 반영): %s", name, e.code)
        await self.apply(name, available=False, reason=None, enabled=enabled)
        return await self.refresh(name)

    @staticmethod
    def _payload(row: Bot, extra: dict[str, Any] | None) -> BotStatusPayload:
        extra = extra or {}
        reason = row.reason
        return BotStatusPayload(
            bot=row.name,
            available=row.available,
            reason=reason,
            last_ok_at=row.last_ok_at,
            queue_depth=extra.get("queue_depth") or 0,
            avg_latency_ms=extra.get("avg_latency_ms"),
            enabled=row.enabled and reason != "disabled",
        )

    @staticmethod
    def _event(p: BotStatusPayload) -> BotStatus:
        return BotStatus(type="bot.status", session_id=None, seq=None, ts=_now(), payload=p)
