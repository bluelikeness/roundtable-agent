"""폰 게이트웨이 (docs/proposals/phone-worker.md 3~6장).

휴대폰 워커가 바깥으로 붙는 WebSocket(/phone/ws)과, 어댑터가 쓰는 내부 HTTP API 를 한 프로세스에서 연다.

  /phone/ws                     휴대폰 워커. Authorization: Bearer <기기 토큰>. 공개 경로는 이것 하나(Caddy)
  GET  /state?device=           연결 여부·기기 이름·마지막 수신 경과
  POST /rpc                     {cmd, args, timeout_s, lease, device} → 휴대폰 명령 결과
  POST /lease                   {holder, wait_s, ttl_s, priority, device} → {lease}. 화면 임대(봇 여럿이 폰 한 대의 화면을
                                나눠 쓴다). priority: send(기본) | read. 보내려고 기다리는 봇이 있으면 read 는 빈 화면을 받지 못한다
  POST /lease/{id}/renew, DELETE /lease/{id}
  POST /lease/{id}/yield        {to: send|any} 보내려고(any: 누구든) 기다리는 봇이 있으면 임대를 돌려준다 → {released}
  GET  /events?after=&pkg=&wait_s=&device=   알림 이벤트 롱폴링
  POST /apk/link                한 번만 쓰는 APK 다운로드 경로를 만든다(QR 설치용, 30분)
  GET  /devices                 붙은 적 있는 휴대폰 목록과 기본 휴대폰(허브 봇 상태 패널 "사용할 폰")
  PUT  /prefer                  {device} 기본 휴대폰을 바꾼다. "" 면 먼저 붙은 폰

여러 휴대폰(2026-11-25): 휴대폰마다 연결·화면 임대·알림이 따로다. 휴대폰 이름은 "모델 · 기기 ID 앞 6자"
(예: "samsung SM-X910 · 3f9a1c")라 같은 모델 두 대도 가른다(기기 ID 를 안 보내는 옛 워커는 모델 이름만).
device 는 휴대폰 이름의 일부(예: "SM-X910", "3f9a1c") 또는 기기 ID 전체. 어댑터는 PHONE_DEVICE 로 자기 폰을 정한다. device 가 없으면 기본 휴대폰(우선 폰이 붙어 있으면 그 폰,
아니면 먼저 붙은 폰)을 쓴다. 폰이 다르면 화면도 달라 서로 기다리지 않는다.

  워커 앱 배포(공개 경로, Caddy·nginx 가 /phone/ 아래만 넘긴다)
  GET  /phone/apk/latest.json   최신 버전 정보. 기기 토큰(Bearer)
  GET  /phone/apk/download      최신 APK. 기기 토큰(Bearer). 워커 자동 업데이트가 쓴다
  GET  /phone/apk/once/{code}   한 번만 쓰는 다운로드(브라우저 설치용). APK 안에 기기 토큰이 있어 공개 링크를 두지 않는다

내부 API 는 어댑터와 같은 X-Hub-Token 을 요구한다. 기기 토큰과 허브 토큰은 다르다.
같은 휴대폰(같은 기기 이름)의 새 연결은 이전 연결을 끊고 대신한다(재연결 중 끊긴 소켓이 남는 경우).
그래서 기기 이름이 똑같은 두 폰(같은 모델 두 대)은 함께 붙지 못한다.
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
from starlette.responses import FileResponse, JSONResponse, Response
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
    sdk: int | None = None  # 안드로이드 API 레벨(hello 에 없으면 None: 옛 워커)
    android: str | None = None
    device_id: str | None = None  # 기기 고유 값(ANDROID_ID). 같은 모델 두 대를 가른다. 옛 워커는 None
    last_seen: float = field(default_factory=time.monotonic)
    pending: dict[int, asyncio.Future] = field(default_factory=dict)
    next_id: int = 1
    closed: bool = False

    @property
    def label(self) -> str:
        """휴대폰 이름: 모델 + 기기 ID 앞 6자. 기기 ID 가 없으면 모델만."""
        return f"{self.device} · {self.device_id[:6]}" if self.device_id else self.device

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
    """휴대폰 한 대의 화면 임대. 만료(ttl)가 있어 어댑터가 죽어도 화면이 영영 잠기지 않는다."""

    PRIORITIES = ("send", "read")

    def __init__(self) -> None:
        self.current: Lease | None = None
        self._cond = asyncio.Condition()
        # 기다리는 수. 보내기(send)가 읽기(read)보다 먼저다: 봇 넷에 차례로 보내는 동안 먼저 보낸 봇이 답을 읽느라
        # 화면을 잡으면 뒤 봇의 보내기가 그만큼 밀린다(2026-11-21 노트9: 넷째 봇이 48초 늦게 보냄)
        self.waiting = {p: 0 for p in self.PRIORITIES}

    def _expire(self) -> None:
        if self.current is not None and time.monotonic() >= self.current.expires_at:
            log.warning("화면 임대 만료: %s", self.current.holder)
            self.current = None

    async def acquire(self, holder: str, wait_s: float, ttl_s: float, priority: str = "send") -> Lease | None:
        if priority not in self.PRIORITIES:
            priority = "send"
        end = time.monotonic() + wait_s
        async with self._cond:
            self.waiting[priority] += 1
            try:
                while True:
                    self._expire()
                    if self.current is None and (priority == "send" or self.waiting["send"] == 0):
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
            finally:
                self.waiting[priority] -= 1
                self._cond.notify_all()  # 보내기 대기가 끝나면 읽기가 받을 수 있다

    def valid(self, lease_id: str | None) -> bool:
        self._expire()
        return self.current is not None and lease_id is not None and hmac.compare_digest(self.current.id, lease_id)

    def renew(self, lease_id: str, ttl_s: float) -> bool:
        if not self.valid(lease_id):
            return False
        self.current.expires_at = time.monotonic() + ttl_s
        return True

    async def yield_to_send(self, lease_id: str, any_waiting: bool = False) -> bool:
        """보내려고 기다리는 봇이 있으면 임대를 돌려준다(읽는 중인 봇이 한 번 볼 때마다 부른다).

        any_waiting: 읽기를 기다리는 봇에게도 넘긴다(아직 답이 없는 봇이 화면을 쥐고 기다리지 않게)."""
        if self.waiting["send"] == 0 and not (any_waiting and self.waiting["read"] > 0):
            return False
        return await self.release(lease_id)

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

    async def add(self, event: str, data: dict[str, Any], device: str | None = None) -> None:
        """device: 이벤트를 보낸 휴대폰의 기기 이름. 같은 앱이 여러 폰에 있을 때 어느 폰의 알림인지 가른다."""
        async with self._cond:
            self.seq += 1
            self.items.append({"seq": self.seq, "event": event, "at": time.time(), "device": device, "data": data})
            self._cond.notify_all()

    def since(self, after: int, pkg: str | None, device: str | None = None) -> list[dict[str, Any]]:
        return [
            e
            for e in self.items
            if e["seq"] > after
            and (pkg is None or e["data"].get("pkg") == pkg)
            and (device is None or device in (e.get("device") or ""))
        ]

    async def wait(self, after: int, pkg: str | None, wait_s: float, device: str | None = None) -> list[dict[str, Any]]:
        end = time.monotonic() + wait_s
        async with self._cond:
            while True:
                got = self.since(after, pkg, device)
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


@dataclass
class Phone:
    """휴대폰 한 대: 연결(끊겼다 다시 붙어도 같은 객체)·화면 임대·화면 끄기 상태."""

    name: str  # PhoneLink.label
    link: PhoneLink | None = None
    leases: Leases = field(default_factory=Leases)
    connected_at: float = 0.0
    woke_by_us: bool = False
    off_task: asyncio.Task | None = None

    @property
    def online(self) -> bool:
        return self.link is not None and not self.link.closed

    def matches(self, query: str) -> bool:
        """query: 휴대폰 이름의 일부 또는 기기 ID 전체."""
        return query in self.name or (self.link is not None and self.link.device_id == query)


class Gateway:
    APK_NAME = "roundtable-worker.apk"
    ONCE_TTL_S = 30 * 60
    ONCE_GRACE_S = 120  # 첫 다운로드 뒤 다시 받을 수 있는 시간(다운로드 관리자의 재요청용)

    def __init__(
        self,
        hub_token: str,
        device_token: str,
        watch_pkgs: list[str],
        apk_dir: str | Path | None = None,
        screen_off_after_s: float = 0,
        state_dir: str | Path | None = None,
    ):
        if not device_token:
            raise ValueError("기기 토큰 없이 기동하지 않습니다(PHONE_DEVICE_TOKEN)")
        self.apk_dir = Path(apk_dir) if apk_dir else None
        self._once: dict[str, float] = {}  # 한 번만 쓰는 다운로드 코드 → 만료 시각
        self.hub_token = hub_token
        self.device_token = device_token.encode()
        self.watch_pkgs = watch_pkgs
        self.phones: dict[str, Phone] = {}  # 휴대폰 이름(PhoneLink.label) → 휴대폰
        self.events = EventLog()
        self._reaper: asyncio.Task | None = None
        # 화면 다시 끄기: wake 로 우리가 켠 화면만, 그 폰의 화면 임대가 모두 끝나고 이만큼 지나면 끈다(0 이면 끄지 않음)
        self.screen_off_after_s = screen_off_after_s
        # 우선(기본) 휴대폰·붙은 적 있는 휴대폰. state_dir 이 있으면 파일에 남겨 재시작 뒤에도 쓴다.
        # 화면에서 고른 적이 없으면(None) PHONE_PREFER_DEVICE 를 쓴다
        self._state_file = Path(state_dir) / "phones.json" if state_dir else None
        self._prefer: str | None = None
        self.known: dict[str, dict[str, Any]] = {}
        self._load_state()

    # --- 휴대폰 고르기 -----------------------------------------------------

    def online(self) -> list[Phone]:
        """붙어 있는 휴대폰(먼저 붙은 순)."""
        return sorted((p for p in self.phones.values() if p.online), key=lambda p: p.connected_at)

    def resolve(self, device: str | None = None) -> Phone | None:
        """device(기기 이름 일부)에 맞는 붙어 있는 휴대폰. 없으면 기본 휴대폰: 우선 폰이 붙어 있으면 그 폰, 아니면 먼저 붙은 폰."""
        phones = self.online()
        if device:
            return next((p for p in phones if p.matches(device)), None)
        prefer = self.prefer
        if prefer:
            hit = next((p for p in phones if p.matches(prefer)), None)
            if hit is not None:
                return hit
        return phones[0] if phones else None

    def _by_lease(self, lease_id: str | None) -> Phone | None:
        if not lease_id:
            return None
        return next((p for p in self.phones.values() if p.leases.valid(lease_id)), None)

    @property
    def link(self) -> PhoneLink | None:
        """기본 휴대폰의 연결(한 대일 때와 같은 뜻)."""
        p = self.resolve()
        return p.link if p is not None else None

    @property
    def leases(self) -> Leases:
        """기본 휴대폰의 화면 임대. 붙은 폰이 없으면 빈 임대."""
        p = self.resolve()
        return p.leases if p is not None else Leases()

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
        sdk = hello.get("sdk")
        link = PhoneLink(
            ws,
            str(hello.get("device") or "phone"),
            str(hello.get("version") or "?"),
            sdk=int(sdk) if isinstance(sdk, int) else None,
            android=str(hello["android"]) if hello.get("android") else None,
            device_id=str(hello["device_id"])[:64] if hello.get("device_id") else None,
        )
        self._remember(link)
        phone = self.phones.setdefault(link.label, Phone(link.label))
        old = phone.link
        phone.link = link
        phone.connected_at = time.monotonic()
        if old is not None:
            # 같은 휴대폰의 재연결: 끊긴 소켓이 남아 있던 경우. 이전 연결을 끊고 대신한다
            old.fail_all("새 연결로 교체됨")
            try:
                await old.ws.close(code=4409)
            except Exception:  # noqa: BLE001 - 이미 끊긴 소켓
                pass
        others = [p.name for p in self.online() if p is not phone]
        log.info(
            "휴대폰 연결: %s (안드로이드 %s, 워커 %s)%s",
            link.label,
            link.android or "?",
            link.version,
            f" · 함께 붙은 폰: {', '.join(others)}" if others else "",
        )
        if hello.get("last_error"):
            log.warning("휴대폰이 알린 직전 끊김 이유: %s", str(hello["last_error"])[:300])
        if hello.get("crash"):
            log.error("휴대폰 워커 앱 충돌 기록:%s%s", chr(10), str(hello["crash"])[:4000])
        await self.events.add("state.changed", {"connected": True, "device": link.label}, link.label)
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
                    await self.events.add(str(msg.get("event")), dict(msg.get("data") or {}), link.label)
                elif kind == "ping":
                    await ws.send_text('{"type":"pong"}')
        except (WebSocketDisconnect, ValueError, RuntimeError):
            pass
        finally:
            link.fail_all("휴대폰 연결 끊김")
            if phone.link is link:
                phone.link = None
                log.warning("휴대폰 연결 끊김: %s", link.label)
                await self.events.add("state.changed", {"connected": False, "device": link.label}, link.label)

    async def _watch(self, link: PhoneLink) -> None:
        try:
            await link.call("watch", {"pkgs": self.watch_pkgs}, 10)
        except Exception as e:  # noqa: BLE001 - 연결 직후 끊기면 다음 연결에서 다시 한다
            log.warning("watch 실패: %s", e)

    async def _reap(self) -> None:
        """하트비트가 끊긴 연결을 정리한다(이동통신망에서는 소켓이 조용히 죽는다)."""
        while True:
            await asyncio.sleep(5)
            for phone in list(self.phones.values()):
                link = phone.link
                if link is not None and not link.closed and time.monotonic() - link.last_seen > DEAD_AFTER_S:
                    log.warning("휴대폰 %s 하트비트 없음 %ss → 연결 정리", link.label, DEAD_AFTER_S)
                    link.fail_all("하트비트 없음")
                    try:
                        await link.ws.close(code=4408)
                    except Exception:  # noqa: BLE001
                        pass

    # --- 우선(기본) 휴대폰 -------------------------------------------------

    @property
    def prefer(self) -> str:
        return read_prefer_device() if self._prefer is None else self._prefer

    def _load_state(self) -> None:
        if self._state_file is None or not self._state_file.is_file():
            return
        try:
            data = json.loads(self._state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            log.warning("휴대폰 상태 파일을 읽지 못함(무시): %s", e)
            return
        if isinstance(data.get("prefer"), str):
            self._prefer = data["prefer"]
        if isinstance(data.get("devices"), dict):
            self.known = {str(k): dict(v) for k, v in data["devices"].items() if isinstance(v, dict)}

    def _save_state(self) -> None:
        if self._state_file is None:
            return
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._state_file.with_suffix(".tmp")
            body = {"prefer": self._prefer, "devices": self.known}
            tmp.write_text(json.dumps(body, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(self._state_file)
        except OSError as e:
            log.warning("휴대폰 상태 파일을 쓰지 못함: %s", e)

    def _remember(self, link: PhoneLink) -> None:
        """붙은 휴대폰을 목록에 남긴다. 기기 ID 를 보내는 워커로 바뀌면 모델 이름만 있던 옛 줄을 지운다."""
        if link.device_id:
            self.known.pop(link.device, None)
        self.known[link.label] = {
            "android": link.android,
            "sdk": link.sdk,
            "version": link.version,
            "last_seen_at": int(time.time()),
        }
        self._save_state()

    async def devices(self, request: Request) -> JSONResponse:
        default = self.resolve()
        online = {p.name for p in self.online()}
        prefer = self.prefer
        rows = [
            {"device": name, **info, "connected": name in online, "preferred": bool(prefer) and prefer in name}
            for name, info in sorted(self.known.items(), key=lambda kv: -int(kv[1].get("last_seen_at") or 0))
        ]
        return JSONResponse({"prefer": prefer, "current": default.name if default else None, "devices": rows})

    async def set_prefer(self, request: Request) -> JSONResponse:
        """기본 휴대폰(PHONE_DEVICE 를 안 정한 어댑터가 쓰는 폰)을 바꾼다. 붙어 있는 다른 폰은 끊지 않는다."""
        try:
            body = await request.json()
        except ValueError:
            body = None
        device = body.get("device") if isinstance(body, dict) else None
        if not isinstance(device, str) or len(device) > 120:
            return JSONResponse({"error": "device 는 문자열"}, status_code=400)
        device = device.strip()
        self._prefer = device
        self._save_state()
        log.info("기본 휴대폰: %s", device or "(없음: 먼저 붙은 폰)")
        return await self.devices(request)

    # --- 내부 API ---------------------------------------------------------

    async def state(self, request: Request) -> JSONResponse:
        phone = self.resolve(request.query_params.get("device") or None)
        link = phone.link if phone is not None else None
        cur = phone.leases.current if phone is not None else None
        holder = cur.holder if cur is not None and phone.leases.valid(cur.id) else None
        return JSONResponse(
            {
                "connected": link is not None and not link.closed,
                "device": link.device if link else None,  # 모델(기기별 셀렉터 층의 열쇠)
                "name": link.label if link else None,  # 휴대폰 이름(같은 모델 두 대도 다름)
                "version": link.version if link else None,
                "sdk": link.sdk if link else None,
                "android": link.android if link else None,
                "last_seen_s": round(time.monotonic() - link.last_seen, 1) if link else None,
                "lease_holder": holder,
                "event_seq": self.events.seq,
                "phones": [p.name for p in self.online()],
            }
        )

    async def rpc(self, request: Request) -> JSONResponse:
        body = await request.json()
        cmd = str(body.get("cmd") or "")
        if cmd not in COMMANDS:
            return JSONResponse({"error": f"모르는 명령 {cmd!r}"}, status_code=400)
        lease = body.get("lease")
        # 임대를 들고 오면 그 임대의 폰에 보낸다(기본 폰이 바뀌어도 화면을 잡은 폰에서 이어 간다)
        phone = self._by_lease(lease)
        if cmd in SCREEN_COMMANDS:
            if phone is None:
                return JSONResponse({"error": "화면 임대가 없거나 만료됨"}, status_code=409)
            phone.leases.renew(lease, float(body.get("ttl_s") or 30))
        if phone is None:
            phone = self.resolve(body.get("device") or None)
        link = phone.link if phone is not None else None
        if link is None or link.closed:
            return JSONResponse({"error": _not_connected(body.get("device"))}, status_code=503)
        try:
            result = await link.call(cmd, dict(body.get("args") or {}), float(body.get("timeout_s") or 15))
        except PhoneGone as e:
            return JSONResponse({"error": str(e)}, status_code=503)
        except TimeoutError:
            return JSONResponse({"error": f"휴대폰 응답 시간 초과({cmd})"}, status_code=504)
        except CommandFailed as e:
            return JSONResponse({"error": str(e)}, status_code=422)
        if cmd in SCREEN_COMMANDS:
            phone.leases.renew(lease, float(body.get("ttl_s") or 30))
        if cmd == "wake" and result.get("woke"):
            phone.woke_by_us = True
        return JSONResponse({"result": result})

    async def lease_acquire(self, request: Request) -> JSONResponse:
        body = await request.json()
        device = body.get("device") or None
        phone = self.resolve(device)
        if phone is None:
            return JSONResponse({"error": _not_connected(device)}, status_code=503)
        holder = str(body.get("holder") or "?")
        got = await phone.leases.acquire(
            holder, float(body.get("wait_s") or 0), float(body.get("ttl_s") or 30), str(body.get("priority") or "send")
        )
        if got is not None and phone.off_task is not None:
            phone.off_task.cancel()  # 다시 쓴다. 끄지 않는다
            phone.off_task = None
        if got is None:
            cur = phone.leases.current
            return JSONResponse({"error": "화면 사용 중", "holder": cur.holder if cur else None}, status_code=409)
        return JSONResponse({"lease": got.id, "device": phone.name})

    async def lease_renew(self, request: Request) -> JSONResponse:
        body = await request.json() if await request.body() else {}
        lease = request.path_params["lease"]
        phone = self._by_lease(lease)
        ok = phone is not None and phone.leases.renew(lease, float(body.get("ttl_s") or 30))
        return JSONResponse({"ok": ok}, status_code=200 if ok else 409)

    async def lease_yield(self, request: Request) -> JSONResponse:
        body = await request.json() if await request.body() else {}
        lease = request.path_params["lease"]
        phone = self._by_lease(lease)
        released = phone is not None and await phone.leases.yield_to_send(lease, body.get("to") == "any")
        return JSONResponse({"released": released})

    async def lease_release(self, request: Request) -> JSONResponse:
        lease = request.path_params["lease"]
        phone = self._by_lease(lease)
        ok = phone is not None and await phone.leases.release(lease)
        if ok:
            self._schedule_screen_off(phone)
        return JSONResponse({"ok": ok})

    def _schedule_screen_off(self, phone: Phone) -> None:
        if self.screen_off_after_s <= 0 or not phone.woke_by_us:
            return
        if phone.off_task is not None:
            phone.off_task.cancel()

        async def later() -> None:
            await asyncio.sleep(self.screen_off_after_s)
            link = phone.link
            if phone.leases.current is not None or link is None or link.closed or not phone.woke_by_us:
                return
            try:
                await link.call("global", {"action": "lock_screen"}, 10)
                phone.woke_by_us = False
                log.info("%s 화면 끔(다 쓴 뒤 %ss)", phone.name, self.screen_off_after_s)
            except Exception as e:  # noqa: BLE001 - 끄지 못해도 화면은 시간이 지나면 저절로 꺼진다
                log.info("화면 끄기 실패: %s", e)

        phone.off_task = asyncio.get_running_loop().create_task(later())

    async def events_poll(self, request: Request) -> JSONResponse:
        """device 가 없으면 기본 휴대폰의 이벤트만(같은 앱이 다른 폰에도 있을 때 그 폰 알림을 답으로 읽지 않게)."""
        q = request.query_params
        after = int(q.get("after") or 0)
        wait_s = min(float(q.get("wait_s") or 0), 30.0)
        device = q.get("device") or None
        phone = self.resolve(device)
        # 고른 폰의 이름으로 거른다(기기 ID 전체로 골라도). 그 폰이 지금 없으면 준 값 그대로
        target = phone.name if phone is not None else device
        got = await self.events.wait(after, q.get("pkg") or None, wait_s, target)
        return JSONResponse({"events": got, "last": self.events.seq})

    # --- 워커 앱 배포 -----------------------------------------------------

    def _device_ok(self, request: Request) -> bool:
        auth = request.headers.get("authorization", "")
        got = auth[7:].encode() if auth.lower().startswith("bearer ") else b""
        return hmac.compare_digest(got, self.device_token)

    def _latest(self) -> dict[str, Any] | None:
        if self.apk_dir is None:
            return None
        meta, apk = self.apk_dir / "latest.json", self.apk_dir / self.APK_NAME
        if not meta.is_file() or not apk.is_file():
            return None
        try:
            return json.loads(meta.read_text(encoding="utf-8"))
        except ValueError:
            return None

    async def apk_latest(self, request: Request) -> Response:
        if not self._device_ok(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        latest = self._latest()
        if latest is None:
            return JSONResponse({"error": "배포된 APK 없음"}, status_code=404)
        return JSONResponse(latest)

    async def apk_download(self, request: Request) -> Response:
        if not self._device_ok(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return self._apk_file()

    async def apk_once(self, request: Request) -> Response:
        code = request.path_params["code"]
        now = time.monotonic()
        self._once = {k: v for k, v in self._once.items() if v > now}
        exp = self._once.get(code)
        if exp is None:
            return JSONResponse({"error": "만료됐거나 이미 쓴 링크"}, status_code=404)
        # 안드로이드 브라우저는 첫 응답을 보고 다운로드 관리자가 같은 주소를 한 번 더 부른다.
        # 그래서 첫 사용 뒤 잠깐(ONCE_GRACE_S)만 더 살려 두고 사라지게 한다
        self._once[code] = min(exp, now + self.ONCE_GRACE_S)
        return self._apk_file()

    def _apk_file(self) -> Response:
        if self._latest() is None:
            return JSONResponse({"error": "배포된 APK 없음"}, status_code=404)
        return FileResponse(
            self.apk_dir / self.APK_NAME,
            media_type="application/vnd.android.package-archive",
            filename=self.APK_NAME,
            headers={"Cache-Control": "no-store"},
        )

    async def apk_link(self, request: Request) -> JSONResponse:
        if self._latest() is None:
            return JSONResponse({"error": "배포된 APK 없음"}, status_code=404)
        code = secrets.token_urlsafe(24)
        self._once[code] = time.monotonic() + self.ONCE_TTL_S
        return JSONResponse({"path": f"/phone/apk/once/{code}", "expires_in_s": self.ONCE_TTL_S, "latest": self._latest()})

    # --- 조립 -------------------------------------------------------------

    def app(self):
        @contextlib.asynccontextmanager
        async def lifespan(_app):
            self._reaper = asyncio.get_running_loop().create_task(self._reap())
            try:
                yield
            finally:
                self._reaper.cancel()

        public = Starlette(
            routes=[
                Route("/phone/apk/latest.json", self.apk_latest),
                Route("/phone/apk/download", self.apk_download),
                Route("/phone/apk/once/{code}", self.apk_once),
            ]
        )
        inner = Starlette(
            routes=[
                WebSocketRoute("/phone/ws", self.phone_ws),
                Route("/state", self.state),
                Route("/apk/link", self.apk_link, methods=["POST"]),
                Route("/rpc", self.rpc, methods=["POST"]),
                Route("/lease", self.lease_acquire, methods=["POST"]),
                Route("/lease/{lease}/renew", self.lease_renew, methods=["POST"]),
                Route("/lease/{lease}/yield", self.lease_yield, methods=["POST"]),
                Route("/lease/{lease}", self.lease_release, methods=["DELETE"]),
                Route("/events", self.events_poll),
                Route("/devices", self.devices),
                Route("/prefer", self.set_prefer, methods=["PUT"]),
            ],
            lifespan=lifespan,
        )
        # HubTokenGuard 는 http 만 검사한다. 휴대폰 WebSocket 은 위에서 기기 토큰으로 따로 검사한다.
        # /phone/apk/ 는 휴대폰·브라우저가 쓰는 공개 경로라 허브 토큰 대신 기기 토큰·일회용 코드로 검사한다
        guarded = HubTokenGuard(inner, self.hub_token)

        async def dispatch(scope, receive, send):
            if scope["type"] == "http" and scope.get("path", "").startswith("/phone/apk/"):
                await public(scope, receive, send)
            else:
                await guarded(scope, receive, send)

        return dispatch


def _not_connected(device: str | None) -> str:
    return f"휴대폰 {device} 이(가) 연결돼 있지 않음" if device else "휴대폰이 연결돼 있지 않음"


def read_prefer_device() -> str:
    """우선(기본) 휴대폰. 휴대폰 이름의 일부(예: "SM-S938N") 또는 기기 ID. 비면 먼저 붙은 휴대폰."""
    return os.environ.get("PHONE_PREFER_DEVICE", "").strip()


def state_dir_from_env() -> str | None:
    """PHONE_STATE_DIR: 우선 휴대폰·휴대폰 목록을 남길 폴더(볼륨). 비면 메모리에만 둔다."""
    return os.environ.get("PHONE_STATE_DIR") or None


def read_device_token() -> str:
    path = Path(os.environ.get("PHONE_DEVICE_TOKEN_FILE", "/run/secrets/phone_device_token"))
    token = path.read_text(encoding="utf-8").strip() if path.is_file() else ""
    return token or os.environ.get("PHONE_DEVICE_TOKEN", "").strip()


def apk_dir_from_env() -> str | None:
    return os.environ.get("PHONE_APK_DIR") or None


def screen_off_after_from_env() -> float:
    """PHONE_SCREEN_OFF_AFTER_S: 우리가 켠 화면을 다 쓴 뒤 끄기까지 초. 0 이면 끄지 않는다(기본 30)."""
    try:
        return max(0.0, float(os.environ.get("PHONE_SCREEN_OFF_AFTER_S", "30")))
    except ValueError:
        return 30.0


def watch_pkgs_from_env() -> list[str]:
    return [p.strip() for p in os.environ.get("PHONE_WATCH_PKGS", DEFAULT_WATCH).split(",") if p.strip()]
