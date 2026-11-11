"""폰 게이트웨이 (docs/proposals/phone-worker.md 3~6장).

휴대폰 워커가 바깥으로 붙는 WebSocket(/phone/ws)과, 어댑터가 쓰는 내부 HTTP API 를 한 프로세스에서 연다.

  /phone/ws                     휴대폰 워커. Authorization: Bearer <기기 토큰>. 공개 경로는 이것 하나(Caddy)
  GET  /state                   연결 여부·기기 이름·마지막 수신 경과
  POST /rpc                     {cmd, args, timeout_s, lease} → 휴대폰 명령 결과
  POST /lease                   {holder, wait_s, ttl_s} → {lease}. 화면 임대(봇 셋이 화면 하나를 나눠 쓴다)
  POST /lease/{id}/renew, DELETE /lease/{id}
  GET  /events?after=&pkg=&wait_s=   알림 이벤트 롱폴링

내부 API 는 어댑터와 같은 X-Hub-Token 을 요구한다. 기기 토큰과 허브 토큰은 다르다.
휴대폰은 한 대만 붙는다. 새 연결이 오면 이전 연결을 끊는다(재연결 중 끊긴 소켓이 남는 경우).
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import logging
import os
import secrets
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from adapters.auth import HubTokenGuard
from adapters.phone.protocol import COMMANDS, DEAD_AFTER_S, SCREEN_COMMANDS

log = logging.getLogger("adapters.phone.gateway")

DEFAULT_WATCH = "com.vooy.mobile,com.pickle.client,com.facebook.aura"


class PhoneGone(Exception):
    """휴대폰이 연결돼 있지 않거나 명령 중에 끊김."""


# ---------------------------------------------------------------------------
# 휴대폰 연결
# ---------------------------------------------------------------------------


@dataclass
class PhoneLink:
    ws: WebSocket
    device: str
    version: str
    last_seen: float = field(default_factory=time.monotonic)
    pending: dict[int, asyncio.Future] = field(default_factory=dict)
    next_id: int = 1
    closed: bool = False

    async def call(self, cmd: str, args: dict[str, Any], timeout_s: float) -> dict[str, Any]:
        if self.closed:
            raise PhoneGone("휴대폰 연결이 끊겼습니다")
        rid = self.next_id
        self.next_id += 1
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self.pending[rid] = fut
        try:
            await self.ws.send_text(json.dumps({"type": "req", "id": rid, "cmd": cmd, "args": args}, ensure_ascii=False))
            return await asyncio.wait_for(fut, timeout_s)
        finally:
            self.pending.pop(rid, None)

    def fail_all(self, why: str) -> None:
        self.closed = True
        for fut in self.pending.values():
            if not fut.done():
                fut.set_exception(PhoneGone(why))
        self.pending.clear()


class CommandFailed(Exception):
    """휴대폰이 명령을 수행하지 못함(노드 없음 등). 어댑터 쪽 판단에 맡긴다."""


# ---------------------------------------------------------------------------
# 화면 임대
# ---------------------------------------------------------------------------


@dataclass
class Lease:
    id: str
    holder: str
    expires_at: float


class Leases:
    """화면 임대 하나. 만료(ttl)가 있어 어댑터가 죽어도 화면이 영영 잠기지 않는다."""

    def __init__(self) -> None:
        self.current: Lease | None = None
        self._cond = asyncio.Condition()

    def _expire(self) -> None:
        if self.current is not None and time.monotonic() >= self.current.expires_at:
            log.warning("화면 임대 만료: %s", self.current.holder)
            self.current = None

    async def acquire(self, holder: str, wait_s: float, ttl_s: float) -> Lease | None:
        end = time.monotonic() + wait_s
        async with self._cond:
            while True:
                self._expire()
                if self.current is None:
                    self.current = Lease(secrets.token_hex(8), holder, time.monotonic() + ttl_s)
                    return self.current
                left = end - time.monotonic()
                if left <= 0:
                    return None
                # 만료 시각에도 깨어나야 하므로 짧게 나눠 기다린다
                try:
                    await asyncio.wait_for(self._cond.wait(), timeout=min(left, 0.5))
                except TimeoutError:
                    pass

    def valid(self, lease_id: str | None) -> bool:
        self._expire()
        return self.current is not None and lease_id is not None and hmac.compare_digest(self.current.id, lease_id)

    def renew(self, lease_id: str, ttl_s: float) -> bool:
        if not self.valid(lease_id):
            return False
        self.current.expires_at = time.monotonic() + ttl_s
        return True

    async def release(self, lease_id: str) -> bool:
        async with self._cond:
            if not self.valid(lease_id):
                return False
            self.current = None
            self._cond.notify_all()
            return True


# ---------------------------------------------------------------------------
# 알림 이벤트
# ---------------------------------------------------------------------------


class EventLog:
    def __init__(self, maxlen: int = 1000) -> None:
        self.items: deque[dict[str, Any]] = deque(maxlen=maxlen)
        self.seq = 0
        self._cond = asyncio.Condition()

    async def add(self, event: str, data: dict[str, Any]) -> None:
        async with self._cond:
            self.seq += 1
            self.items.append({"seq": self.seq, "event": event, "at": time.time(), "data": data})
            self._cond.notify_all()

    def since(self, after: int, pkg: str | None) -> list[dict[str, Any]]:
        return [e for e in self.items if e["seq"] > after and (pkg is None or e["data"].get("pkg") == pkg)]

    async def wait(self, after: int, pkg: str | None, wait_s: float) -> list[dict[str, Any]]:
        end = time.monotonic() + wait_s
        async with self._cond:
            while True:
                got = self.since(after, pkg)
                left = end - time.monotonic()
                if got or left <= 0:
                    return got
                try:
                    await asyncio.wait_for(self._cond.wait(), timeout=left)
                except TimeoutError:
                    pass


# ---------------------------------------------------------------------------
# 앱
# ---------------------------------------------------------------------------


class Gateway:
    def __init__(self, hub_token: str, device_token: str, watch_pkgs: list[str]):
        if not device_token:
            raise ValueError("기기 토큰 없이 기동하지 않습니다(PHONE_DEVICE_TOKEN)")
        self.hub_token = hub_token
        self.device_token = device_token.encode()
        self.watch_pkgs = watch_pkgs
        self.link: PhoneLink | None = None
        self.leases = Leases()
        self.events = EventLog()
        self._reaper: asyncio.Task | None = None

    # --- 휴대폰 WebSocket -------------------------------------------------

    async def phone_ws(self, ws: WebSocket) -> None:
        auth = ws.headers.get("authorization", "")
        got = auth[7:].encode() if auth.lower().startswith("bearer ") else b""
        if not hmac.compare_digest(got, self.device_token):
            await ws.close(code=4401)
            return
        await ws.accept()
        try:
            hello = json.loads(await asyncio.wait_for(ws.receive_text(), timeout=10))
        except (TimeoutError, WebSocketDisconnect, ValueError):
            await ws.close(code=4400)
            return
        if hello.get("type") != "hello":
            await ws.close(code=4400)
            return
        link = PhoneLink(ws, str(hello.get("device") or "phone"), str(hello.get("version") or "?"))
        old, self.link = self.link, link
        if old is not None:
            old.fail_all("새 연결로 교체됨")
            try:
                await old.ws.close(code=4409)
            except Exception:  # noqa: BLE001 - 이미 끊긴 소켓
                pass
        log.info("휴대폰 연결: %s (워커 %s)", link.device, link.version)
        await self.events.add("state.changed", {"connected": True, "device": link.device})
        if self.watch_pkgs:
            asyncio.get_running_loop().create_task(self._watch(link))
        try:
            while True:
                msg = json.loads(await ws.receive_text())
                link.last_seen = time.monotonic()
                kind = msg.get("type")
                if kind == "res":
                    fut = link.pending.get(msg.get("id"))
                    if fut is not None and not fut.done():
                        if msg.get("ok"):
                            fut.set_result(msg.get("result") or {})
                        else:
                            fut.set_exception(CommandFailed(str(msg.get("error") or "실패")))
                elif kind == "event":
                    await self.events.add(str(msg.get("event")), dict(msg.get("data") or {}))
                elif kind == "ping":
                    await ws.send_text('{"type":"pong"}')
        except (WebSocketDisconnect, ValueError, RuntimeError):
            pass
        finally:
            link.fail_all("휴대폰 연결 끊김")
            if self.link is link:
                self.link = None
                log.warning("휴대폰 연결 끊김: %s", link.device)
                await self.events.add("state.changed", {"connected": False, "device": link.device})

    async def _watch(self, link: PhoneLink) -> None:
        try:
            await link.call("watch", {"pkgs": self.watch_pkgs}, 10)
        except Exception as e:  # noqa: BLE001 - 연결 직후 끊기면 다음 연결에서 다시 한다
            log.warning("watch 실패: %s", e)

    async def _reap(self) -> None:
        """하트비트가 끊긴 연결을 정리한다(이동통신망에서는 소켓이 조용히 죽는다)."""
        while True:
            await asyncio.sleep(5)
            link = self.link
            if link is not None and time.monotonic() - link.last_seen > DEAD_AFTER_S:
                log.warning("휴대폰 하트비트 없음 %ss → 연결 정리", DEAD_AFTER_S)
                link.fail_all("하트비트 없음")
                try:
                    await link.ws.close(code=4408)
                except Exception:  # noqa: BLE001
                    pass

    # --- 내부 API ---------------------------------------------------------

    async def state(self, request: Request) -> JSONResponse:
        link = self.link
        cur = self.leases.current
        holder = cur.holder if cur is not None and self.leases.valid(cur.id) else None
        return JSONResponse(
            {
                "connected": link is not None and not link.closed,
                "device": link.device if link else None,
                "version": link.version if link else None,
                "last_seen_s": round(time.monotonic() - link.last_seen, 1) if link else None,
                "lease_holder": holder,
                "event_seq": self.events.seq,
            }
        )

    async def rpc(self, request: Request) -> JSONResponse:
        body = await request.json()
        cmd = str(body.get("cmd") or "")
        if cmd not in COMMANDS:
            return JSONResponse({"error": f"모르는 명령 {cmd!r}"}, status_code=400)
        lease = body.get("lease")
        if cmd in SCREEN_COMMANDS:
            if not self.leases.valid(lease):
                return JSONResponse({"error": "화면 임대가 없거나 만료됨"}, status_code=409)
            self.leases.renew(lease, float(body.get("ttl_s") or 30))
        link = self.link
        if link is None or link.closed:
            return JSONResponse({"error": "휴대폰이 연결돼 있지 않음"}, status_code=503)
        try:
            result = await link.call(cmd, dict(body.get("args") or {}), float(body.get("timeout_s") or 15))
        except PhoneGone as e:
            return JSONResponse({"error": str(e)}, status_code=503)
        except TimeoutError:
            return JSONResponse({"error": f"휴대폰 응답 시간 초과({cmd})"}, status_code=504)
        except CommandFailed as e:
            return JSONResponse({"error": str(e)}, status_code=422)
        if cmd in SCREEN_COMMANDS:
            self.leases.renew(lease, float(body.get("ttl_s") or 30))
        return JSONResponse({"result": result})

    async def lease_acquire(self, request: Request) -> JSONResponse:
        body = await request.json()
        holder = str(body.get("holder") or "?")
        got = await self.leases.acquire(holder, float(body.get("wait_s") or 0), float(body.get("ttl_s") or 30))
        if got is None:
            cur = self.leases.current
            return JSONResponse({"error": "화면 사용 중", "holder": cur.holder if cur else None}, status_code=409)
        return JSONResponse({"lease": got.id})

    async def lease_renew(self, request: Request) -> JSONResponse:
        body = await request.json() if await request.body() else {}
        ok = self.leases.renew(request.path_params["lease"], float(body.get("ttl_s") or 30))
        return JSONResponse({"ok": ok}, status_code=200 if ok else 409)

    async def lease_release(self, request: Request) -> JSONResponse:
        ok = await self.leases.release(request.path_params["lease"])
        return JSONResponse({"ok": ok})

    async def events_poll(self, request: Request) -> JSONResponse:
        q = request.query_params
        after = int(q.get("after") or 0)
        wait_s = min(float(q.get("wait_s") or 0), 30.0)
        got = await self.events.wait(after, q.get("pkg") or None, wait_s)
        return JSONResponse({"events": got, "last": self.events.seq})

    # --- 조립 -------------------------------------------------------------

    def app(self):
        @contextlib.asynccontextmanager
        async def lifespan(_app):
            self._reaper = asyncio.get_running_loop().create_task(self._reap())
            try:
                yield
            finally:
                self._reaper.cancel()

        inner = Starlette(
            routes=[
                WebSocketRoute("/phone/ws", self.phone_ws),
                Route("/state", self.state),
                Route("/rpc", self.rpc, methods=["POST"]),
                Route("/lease", self.lease_acquire, methods=["POST"]),
                Route("/lease/{lease}/renew", self.lease_renew, methods=["POST"]),
                Route("/lease/{lease}", self.lease_release, methods=["DELETE"]),
                Route("/events", self.events_poll),
            ],
            lifespan=lifespan,
        )
        # HubTokenGuard 는 http 만 검사한다. 휴대폰 WebSocket 은 위에서 기기 토큰으로 따로 검사한다
        return HubTokenGuard(inner, self.hub_token)


def read_device_token() -> str:
    path = Path(os.environ.get("PHONE_DEVICE_TOKEN_FILE", "/run/secrets/phone_device_token"))
    token = path.read_text(encoding="utf-8").strip() if path.is_file() else ""
    return token or os.environ.get("PHONE_DEVICE_TOKEN", "").strip()


def watch_pkgs_from_env() -> list[str]:
    return [p.strip() for p in os.environ.get("PHONE_WATCH_PKGS", DEFAULT_WATCH).split(",") if p.strip()]
