"""PhoneDevice: 게이트웨이를 거쳐 휴대폰을 조종하는 기기 (android_base.Device 구현).

AndroidAdapter 는 기기가 에뮬레이터(U2Device)인지 휴대폰(PhoneDevice)인지 모른다. 휴대폰만의 것:
- 화면 임대: 화면을 쓰는 명령 전에 게이트웨이에서 임대를 받고, 명령이 IDLE_RELEASE_S 동안 없으면 돌려준다.
  봇 셋(어댑터 셋)이 휴대폰 한 대의 화면을 차례로 쓴다. 기다리는 동안(알림 대기)에는 화면을 잡지 않는다.
- 알림: events_cursor()·wait_notification() 으로 답 알림을 기다린다(AndroidAdapter.reply_signal).

쓰는 법: ANDROID_DEVICE_FACTORY=adapters.phone.device:PhoneDevice, PHONE_GATEWAY_URL=http://phone-gateway:9200
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
import os
from typing import Any

import httpx

from adapters.base import BackendDown, read_token

log = logging.getLogger("adapters.phone.device")


class PhoneCommandFailed(RuntimeError):
    """휴대폰이 명령을 수행하지 못함(노드 없음 등)."""


class PhoneDevice:
    LEASE_TTL_S = 30.0
    IDLE_RELEASE_S = 2.0

    def __init__(self, serial: str = "phone", *, base_url: str | None = None, token: str | None = None, holder: str | None = None):
        self.base_url = (base_url or os.environ.get("PHONE_GATEWAY_URL", "http://phone-gateway:9200")).rstrip("/")
        self.token = token if token is not None else read_token()
        self.holder = holder or os.environ.get("ADAPTER_NAME") or serial
        self.lease_wait_s = float(os.environ.get("PHONE_LEASE_WAIT_S", "60"))
        self._client: httpx.AsyncClient | None = None
        self._lease: str | None = None
        self._lease_lock = asyncio.Lock()
        self._busy = 0
        self._held = 0
        self._idle_task: asyncio.Task | None = None

    # --- HTTP -------------------------------------------------------------

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(base_url=self.base_url, headers={"X-Hub-Token": self.token}, timeout=90)
        return self._client

    async def _req(self, method: str, path: str, **kw: Any) -> httpx.Response:
        try:
            return await self._http().request(method, path, **kw)
        except httpx.HTTPError as e:
            raise BackendDown(f"폰 게이트웨이에 연결하지 못함: {e}") from e

    async def _rpc(self, cmd: str, args: dict[str, Any] | None = None, timeout_s: float = 15, lease: str | None = None) -> dict:
        r = await self._req(
            "POST", "/rpc", json={"cmd": cmd, "args": args or {}, "timeout_s": timeout_s, "lease": lease, "ttl_s": self.LEASE_TTL_S}
        )
        if r.status_code == 200:
            return r.json().get("result") or {}
        err = _error(r)
        if r.status_code in (503, 504):
            raise BackendDown(f"휴대폰: {err}")
        if r.status_code == 409:
            raise _LeaseLost(err)
        raise PhoneCommandFailed(f"{cmd}: {err}")

    # --- 화면 임대 ---------------------------------------------------------

    async def _ensure_lease(self) -> str:
        async with self._lease_lock:
            if self._lease is not None:
                return self._lease
            r = await self._req("POST", "/lease", json={"holder": self.holder, "wait_s": self.lease_wait_s, "ttl_s": self.LEASE_TTL_S})
            if r.status_code != 200:
                raise BackendDown(f"휴대폰 화면을 받지 못함: {_error(r)}")
            self._lease = r.json()["lease"]
            return self._lease

    async def release(self) -> None:
        """화면 임대를 바로 돌려준다(알림을 기다리기 전 등)."""
        if self._idle_task is not None:
            self._idle_task.cancel()
            self._idle_task = None
        async with self._lease_lock:
            lease, self._lease = self._lease, None
        if lease is not None:
            try:
                await self._req("DELETE", f"/lease/{lease}")
            except BackendDown:
                pass  # 게이트웨이가 죽었으면 임대도 의미 없다(만료된다)

    def _schedule_idle_release(self) -> None:
        if self._idle_task is not None:
            self._idle_task.cancel()

        async def later() -> None:
            await asyncio.sleep(self.IDLE_RELEASE_S)
            if self._busy == 0 and self._held == 0:
                self._idle_task = None
                await self.release()

        self._idle_task = asyncio.get_running_loop().create_task(later())

    @contextlib.asynccontextmanager
    async def hold(self):
        """여러 명령을 한 묶음으로 화면을 잡는다(앱 열기 → 읽기 사이에 다른 봇이 끼어들지 않게)."""
        self._held += 1
        try:
            await self._ensure_lease()
            yield
        finally:
            self._held -= 1
            if self._held == 0:
                self._schedule_idle_release()

    async def _screen(self, cmd: str, args: dict[str, Any] | None = None, timeout_s: float = 15) -> dict:
        self._busy += 1
        try:
            for attempt in (1, 2):
                lease = await self._ensure_lease()
                try:
                    return await self._rpc(cmd, args, timeout_s, lease)
                except _LeaseLost:
                    # 만료(너무 오래 쉼)·게이트웨이 재시작. 한 번만 다시 받는다
                    async with self._lease_lock:
                        if self._lease == lease:
                            self._lease = None
                    if attempt == 2:
                        raise BackendDown("휴대폰 화면 임대를 잃음") from None
            raise AssertionError("unreachable")
        finally:
            self._busy -= 1
            self._schedule_idle_release()

    # --- android_base.Device ----------------------------------------------

    async def state(self) -> dict[str, Any]:
        return await self._rpc("state", timeout_s=10)

    async def is_booted(self) -> bool:
        """휴대폰이 붙어 있고 화면이 켜져 있으며 잠금이 풀려 있으면 True(조종할 수 있는 상태)."""
        try:
            st = await self.state()
        except BackendDown as e:
            log.info("휴대폰 상태 확인 실패: %s", e)
            return False
        return bool(st.get("screen_on")) and not st.get("locked")

    async def dump(self) -> str:
        return str((await self._screen("tree", timeout_s=20)).get("xml") or "")

    async def current_package(self) -> str | None:
        return (await self.state()).get("foreground")

    async def app_start(self, package: str) -> None:
        await self._screen("launch", {"pkg": package}, timeout_s=20)

    async def click(self, x: int, y: int) -> None:
        await self._screen("tap", {"x": int(x), "y": int(y)})

    async def click_selector(self, selector: dict[str, str]) -> str:
        """셀렉터로 찾은 노드를 접근성 클릭. 사용한 방법(action|gesture)."""
        return str((await self._screen("click", {"selector": selector})).get("method"))

    async def click_at(self, x: int, y: int) -> str:
        """그 위치의 누를 수 있는 노드를 접근성 클릭(이름 없는 버튼). 사용한 방법(action|gesture)."""
        return str((await self._screen("click_at", {"x": int(x), "y": int(y)})).get("method"))

    async def app_version(self, package: str) -> str | None:
        """설치된 앱 버전(versionName). 옛 워커·앱 없음이면 None(셀렉터 기기별 층이 버전 없이 묶인다)."""
        try:
            r = await self._rpc("app_info", {"pkg": package}, timeout_s=10)
        except Exception as e:  # noqa: BLE001 - 버전은 있으면 좋은 정보다. 전송을 막지 않는다
            log.info("앱 버전 확인 실패(%s): %s", package, e)
            return None
        name = r.get("version_name")
        return f"{name}({r.get('version_code')})" if name else None

    async def set_text(self, selector: dict[str, str], text: str) -> None:
        await self._screen("set_text", {"selector": selector, "text": text})

    async def send_keys(self, text: str) -> None:
        await self._screen("paste", {"text": text})

    async def clear_clipboard(self) -> None:
        return None  # paste 가 붙여넣은 뒤 휴대폰에서 바로 비운다

    async def press(self, key: str) -> None:
        await self._screen("global", {"action": key})

    async def screenshot_png(self) -> bytes:
        return base64.b64decode((await self._screen("screenshot", timeout_s=20)).get("png_b64") or "")

    async def reboot(self) -> None:
        # 휴대폰은 원격으로 재부팅하지 않는다. 연결·잠금 상태가 돌아오길 기다릴 뿐이다
        log.warning("휴대폰은 재부팅하지 않음(연결·화면 상태를 확인하세요)")

    async def login_completed(self) -> None:
        return None  # 로그인은 사람이 휴대폰에서 직접 한다

    # --- 알림 -------------------------------------------------------------

    async def device_profile(self) -> dict[str, Any]:
        """붙어 있는 휴대폰의 {device, sdk, android}. 기기별 층의 열쇠와 기기 능력 판단(스크린샷 등)에 쓴다.

        sdk 를 모르면(옛 워커) None. 실패해도 예외 대신 빈 값을 돌려준다(전송을 막지 않는다).
        """
        try:
            r = await self._req("GET", "/state")
            st = r.json() if r.status_code == 200 else {}
        except Exception as e:  # noqa: BLE001
            log.info("휴대폰 정보 확인 실패: %s", e)
            st = {}
        return {"device": st.get("device"), "sdk": st.get("sdk"), "android": st.get("android")}

    async def events_cursor(self) -> int:
        r = await self._req("GET", "/state")
        if r.status_code != 200:
            raise BackendDown(f"폰 게이트웨이 상태: {_error(r)}")
        return int(r.json().get("event_seq") or 0)

    async def wait_events(self, pkg: str, after: int, wait_s: float) -> tuple[list[dict[str, Any]], int]:
        """after 이후 pkg 의 알림 이벤트(notif.posted·notif.removed, 요약 알림 제외)와 새 커서."""
        r = await self._req("GET", "/events", params={"after": after, "pkg": pkg, "wait_s": max(0.0, wait_s)})
        if r.status_code != 200:
            raise BackendDown(f"폰 게이트웨이 이벤트: {_error(r)}")
        evs = r.json().get("events") or []
        cursor = max([after, *[int(e["seq"]) for e in evs]])
        return [e for e in evs if not (e.get("data") or {}).get("summary")], cursor

    async def wait_notification(self, pkg: str, after: int, wait_s: float) -> tuple[list[dict[str, Any]], int]:
        """after 이후 pkg 의 notif.posted 이벤트(요약 알림 제외)와 새 커서."""
        evs, cursor = await self.wait_events(pkg, after, wait_s)
        return [e for e in evs if e.get("event") == "notif.posted"], cursor

    async def aclose(self) -> None:
        await self.release()
        if self._client is not None:
            await self._client.aclose()


class _LeaseLost(Exception):
    pass


def _error(r: httpx.Response) -> str:
    try:
        return str(r.json().get("error") or r.text)
    except ValueError:
        return r.text or str(r.status_code)
