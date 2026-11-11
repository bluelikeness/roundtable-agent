"""가짜 휴대폰 워커. 게이트웨이에 실제 WebSocket 으로 붙어 워커 앱처럼 명령을 수행한다.

화면은 FakeVooyDevice(가짜 Vooy 앱)를 그대로 쓴다. 봇 답이 도착했을 때 Vooy 가 전경이 아니면
실제 휴대폰처럼 알림 이벤트(notif.posted)를 올린다. 실제 기기·계정·대화 내용은 쓰지 않는다.

  python -m adapters.testing.fake_phone      env: PHONE_GATEWAY_WS, PHONE_DEVICE_TOKEN, FAKE_PHONE_LOCKED(파일 경로)

FAKE_PHONE_LOCKED 파일이 있으면 화면이 잠긴 것으로 답한다(잠금 시나리오).
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
from pathlib import Path
from typing import Any

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from adapters.phone.protocol import PING_S
from adapters.testing.fake_vooy import PKG as VOOY_PKG
from adapters.testing.fake_vooy import FakeVooyDevice, Msg

log = logging.getLogger("adapters.testing.fake_phone")
LAUNCHER = "com.google.android.apps.nexuslauncher"


class FakePhone:
    def __init__(self, url: str, token: str, device: FakeVooyDevice | None = None, locked_file: str | None = None):
        self.url = url
        self.token = token
        self.vooy = device or FakeVooyDevice()
        self.vooy.on_bot_message = self._on_bot_message
        self.locked_file = Path(locked_file) if locked_file else None
        self.watch: set[str] = set()
        self.notif_id = 0
        self._ws = None
        self.handled: list[str] = []  # 받은 명령 이름(테스트에서 확인)
        self.connected = asyncio.Event()

    # --- 상태 -------------------------------------------------------------

    @property
    def locked(self) -> bool:
        return self.locked_file is not None and self.locked_file.exists()

    def _on_bot_message(self, m: Msg) -> None:
        if self.vooy.foreground or VOOY_PKG not in self.watch or self._ws is None:
            return  # 실제 앱도 대화방이 열려 있으면 알림을 띄우지 않는다
        self.notif_id += 1
        data = {
            "pkg": VOOY_PKG,
            "key": f"0|{VOOY_PKG}|0",
            "id": 0,
            "channel": "human-room-messages-private",
            "template": "BigTextStyle",
            "title": "비서",
            "text": m.text[:100],
            "messages": [],
            "when_ms": 0,
            "summary": False,
        }
        asyncio.get_running_loop().create_task(self._send({"type": "event", "event": "notif.posted", "data": data}))

    async def _send(self, obj: dict[str, Any]) -> None:
        if self._ws is not None:
            try:
                await self._ws.send(json.dumps(obj, ensure_ascii=False))
            except ConnectionClosed:
                pass

    # --- 명령 -------------------------------------------------------------

    async def handle(self, cmd: str, args: dict[str, Any]) -> dict[str, Any]:
        v = self.vooy
        if cmd == "state":
            return {
                "screen_on": True,
                "locked": self.locked,
                "foreground": VOOY_PKG if v.foreground else LAUNCHER,
                "battery": 100,
                "version": "fake",
            }
        if cmd == "watch":
            self.watch = set(args.get("pkgs") or [])
            return {}
        if self.locked:
            raise RuntimeError("화면이 잠겨 있음")
        if cmd == "launch":
            await v.app_start(str(args["pkg"]))
            return {"foreground": VOOY_PKG if v.foreground else LAUNCHER}
        if cmd == "tree":
            return {"xml": await v.dump()}
        if cmd == "tap":
            await v.click(int(args["x"]), int(args["y"]))
            return {}
        if cmd == "set_text":
            await v.set_text(dict(args["selector"]), str(args["text"]))
            return {}
        if cmd == "paste":
            await v.send_keys(str(args["text"]))
            return {}
        if cmd == "global":
            await v.press(str(args["action"]))
            return {}
        if cmd == "screenshot":
            return {"png_b64": base64.b64encode(await v.screenshot_png()).decode()}
        raise RuntimeError(f"모르는 명령 {cmd}")

    # --- 연결 -------------------------------------------------------------

    async def run_once(self) -> None:
        async with connect(self.url, additional_headers={"Authorization": f"Bearer {self.token}"}) as ws:
            self._ws = ws
            await ws.send(json.dumps({"type": "hello", "device": "fake-phone", "version": "fake"}))
            self.connected.set()
            pinger = asyncio.get_running_loop().create_task(self._ping())
            try:
                async for raw in ws:
                    msg = json.loads(raw)
                    if msg.get("type") != "req":
                        continue
                    self.handled.append(msg["cmd"])
                    try:
                        result = await self.handle(msg["cmd"], dict(msg.get("args") or {}))
                        await self._send({"type": "res", "id": msg["id"], "ok": True, "result": result})
                    except Exception as e:  # noqa: BLE001 - 워커는 실패를 결과로 돌려준다
                        await self._send({"type": "res", "id": msg["id"], "ok": False, "error": str(e)})
            finally:
                pinger.cancel()
                self._ws = None
                self.connected.clear()

    async def _ping(self) -> None:
        while True:
            await asyncio.sleep(PING_S)
            await self._send({"type": "ping"})

    async def run_forever(self) -> None:
        delay = 0.5
        while True:
            try:
                await self.run_once()
                delay = 0.5
            except (OSError, ConnectionClosed) as e:
                log.info("게이트웨이 연결 실패/끊김: %s", e)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 10)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    phone = FakePhone(
        os.environ.get("PHONE_GATEWAY_WS", "ws://phone-gateway:9200/phone/ws"),
        os.environ["PHONE_DEVICE_TOKEN"],
        locked_file=os.environ.get("FAKE_PHONE_LOCKED"),
    )
    asyncio.run(phone.run_forever())


if __name__ == "__main__":
    main()
