"""가짜 휴대폰 워커. 게이트웨이에 실제 WebSocket 으로 붙어 워커 앱처럼 명령을 수행한다.

앱 넷을 흉내 낸다: Vooy(FakeVooyDevice), 피클·뮤즈·Cue(FakeChatApp). 전경 앱은 하나이고 launch 로 바뀐다.
봇 답이 도착했을 때 그 앱이 전경이 아니면 실제 휴대폰처럼 알림 이벤트(notif.posted)를 올린다.
실제 기기·계정·대화 내용은 쓰지 않는다.

  python -m adapters.testing.fake_phone      env: PHONE_GATEWAY_WS, PHONE_DEVICE_TOKEN, FAKE_PHONE_LOCKED(파일 경로),
                                                  FAKE_PHONE_DIR(앱 상태 폴더, 기본 /data)

FAKE_PHONE_LOCKED 파일이 있으면 화면이 잠긴 것으로 답한다(잠금 시나리오).
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from adapters.phone.protocol import PING_S
from adapters.testing.fake_chat import FakeChatApp
from adapters.testing.fake_vooy import PKG as VOOY_PKG
from adapters.testing.fake_vooy import FakeVooyDevice, Msg

log = logging.getLogger("adapters.testing.fake_phone")
LAUNCHER = "com.google.android.apps.nexuslauncher"


class FakePhone:
    device_name = "fake-phone"  # hello 의 기기 이름(다른 휴대폰 흉내는 인스턴스에서 바꾼다)
    device_id: str | None = None  # hello 의 기기 ID(ANDROID_ID). None 이면 옛 워커처럼 보내지 않는다
    sdk = 34  # 안드로이드 API 레벨(29 = 안드로이드 10: 스크린샷 없음)
    secure = True  # 보안 잠금(PIN 등). 거짓이면 wake 가 화면을 켜고 잠금화면을 넘긴다

    def __init__(
        self,
        url: str,
        token: str,
        device: FakeVooyDevice | None = None,
        locked_file: str | None = None,
        chat_dir: str | Path | None = None,
    ):
        self.url = url
        self.token = token
        self.vooy = device or FakeVooyDevice()
        self.vooy.on_bot_message = self._on_bot_message
        base = Path(chat_dir or os.environ.get("FAKE_PHONE_DIR", "/data"))
        self.chats = {pkg: FakeChatApp(pkg, base / f"fake-{pkg.split('.')[-1]}") for pkg in ("com.pickle.client", "com.facebook.aura", "ai.manus.agents")}
        for app in self.chats.values():
            app.notify = lambda ch, key, posted, app=app: self._chat_notif(app, ch, key, posted)
            app.save = lambda name, mime, data, app=app: self._save_media(app.pkg, name, mime, data)
        # 휴대폰 저장소 흉내(MediaStore): id → {name, mime, data, date_ms, owner}
        self.media: dict[int, dict[str, Any]] = {}
        self.media_access = True  # 사진·영상 읽기 권한
        self.locked_file = Path(locked_file) if locked_file else None
        self.watch: set[str] = set()
        self.notif_id = 0
        self._ws = None
        self.handled: list[str] = []  # 받은 명령 이름(테스트에서 확인)
        self.screen = True  # 화면 켜짐
        self.keyguard = False  # 잠금화면(보안 잠금 없이 화면이 꺼졌다 켜진 경우)
        self.screen_offs = 0  # lock_screen 으로 끈 횟수
        self.connected = asyncio.Event()

    # --- 상태 -------------------------------------------------------------

    @property
    def locked(self) -> bool:
        return self.keyguard or (self.locked_file is not None and self.locked_file.exists())

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

    def _chat_notif(self, app: FakeChatApp, channel: str, key: str, posted: bool) -> None:
        if self._ws is None or app.pkg not in self.watch:
            return
        if posted and app.foreground and channel != app.p["progress_channel"]:
            return  # 대화방이 열려 있으면 답 알림이 뜨지 않는다(작업 중 알림은 뜬다)
        data = {"pkg": app.pkg, "key": f"0|{app.pkg}|{key}", "id": 0, "channel": channel, "template": "MessagingStyle",
                "title_len": 2, "text_len": 10, "messages": 1, "when_ms": 0, "summary": False}
        event = "notif.posted" if posted else "notif.removed"
        asyncio.get_running_loop().create_task(self._send({"type": "event", "event": event, "data": data}))

    def _fg(self):
        """전경 앱(가짜 기기 객체) 또는 None(홈 화면)."""
        if self.vooy.foreground:
            return self.vooy
        return next((a for a in self.chats.values() if a.foreground), None)

    def _fg_pkg(self) -> str:
        app = self._fg()
        if app is None:
            return LAUNCHER
        return VOOY_PKG if app is self.vooy else app.pkg

    def _save_media(self, pkg: str, name: str, mime: str, data: bytes) -> None:
        mid = len(self.media) + 1
        self.media[mid] = {"name": name, "mime": mime, "data": data, "date_ms": int(time.time() * 1000), "owner": pkg}

    def _files_url(self, code: str | None = None) -> str:
        """ws://host/phone/ws → http://host/phone/files[/code] (워커 Media.filesUrl 과 같다)."""
        base = self.url.replace("wss://", "https://", 1).replace("ws://", "http://", 1).split("/phone/ws")[0]
        return f"{base}/phone/files" + (f"/{code}" if code else "")

    async def _share(self, args: dict[str, Any]) -> dict[str, Any]:
        pkg = str(args["pkg"])
        app = self.chats.get(pkg)
        if app is None:
            raise RuntimeError(f"이 앱은 공유하기로 이런 파일을 받지 않음: {pkg}")
        got = []
        async with httpx.AsyncClient(timeout=30, headers={"Authorization": f"Bearer {self.token}"}) as c:
            for f in args["files"]:
                r = await c.get(self._files_url(str(f["code"])))
                if r.status_code != 200:
                    raise RuntimeError(f"파일 받기 HTTP {r.status_code}")
                got.append((str(f.get("name") or "file"), str(f.get("mime") or "application/octet-stream"), r.content))
        for a in (self.vooy, *self.chats.values()):
            a.foreground = False
        await app.app_start(pkg)
        app.attached = got
        return {"shared": len(got), "foreground": self._fg_pkg()}

    def _media_since(self, args: dict[str, Any]) -> dict[str, Any]:
        if not self.media_access:
            return {"items": [], "access": False}
        since, pkg = int(args["since_ms"]), args.get("pkg")
        items = [
            {"id": mid, "name": m["name"], "mime": m["mime"], "size": len(m["data"]), "date_ms": m["date_ms"], "owner": m["owner"]}
            for mid, m in sorted(self.media.items(), reverse=True)
            if m["date_ms"] >= since and (not pkg or m["owner"] == pkg)
        ]
        return {"items": items, "access": True}

    async def _media_upload(self, args: dict[str, Any]) -> dict[str, Any]:
        m = self.media.get(int(args["id"]))
        if m is None:
            raise RuntimeError("파일 없음")
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(
                self._files_url(), content=m["data"],
                headers={"Authorization": f"Bearer {self.token}", "Content-Type": m["mime"], "X-File-Name": quote(m["name"], safe="")},
            )
        if r.status_code != 200:
            raise RuntimeError(f"올리기 HTTP {r.status_code}")
        return r.json()

    async def _send(self, obj: dict[str, Any]) -> None:
        if self._ws is not None:
            try:
                await self._ws.send(json.dumps(obj, ensure_ascii=False))
            except ConnectionClosed:
                pass

    # --- 명령 -------------------------------------------------------------

    async def handle(self, cmd: str, args: dict[str, Any]) -> dict[str, Any]:
        if cmd == "state":
            return {
                "screen_on": self.screen,
                "locked": self.locked,
                "secure": self.secure,
                "foreground": self._fg_pkg(),
                "battery": 100,
                "version": "fake",
                "width": 1080,
                "height": 2400,
                "density": 2.625,
                "ime_top": None,
                "sdk": self.sdk,
            }
        if cmd == "app_info":
            if str(args["pkg"]) not in {VOOY_PKG, *self.chats}:
                raise RuntimeError(f"설치되지 않은 앱: {args['pkg']}")
            return {"version_name": "1.0-fake", "version_code": 1}
        if cmd == "watch":
            self.watch = set(args.get("pkgs") or [])
            return {}
        if cmd == "media_since":
            return self._media_since(args)
        if cmd == "media_upload":
            return await self._media_upload(args)
        if cmd == "wake":
            woke = False
            if not self.secure and (not self.screen or self.locked):
                woke = True
                self.screen, self.keyguard = True, False
                if self.locked_file is not None and self.locked_file.exists():
                    self.locked_file.unlink()
            return {"screen_on": self.screen, "locked": self.locked, "secure": self.secure, "woke": woke}
        if cmd == "global" and args.get("action") == "lock_screen":
            self.screen, self.keyguard = False, True
            self.screen_offs += 1
            return {}
        if self.locked or not self.screen:
            raise RuntimeError("화면이 잠겨 있음")
        if cmd == "launch":
            pkg = str(args["pkg"])
            apps = {VOOY_PKG: self.vooy, **self.chats}
            if pkg not in apps:
                raise RuntimeError(f"설치되지 않은 앱: {pkg}")
            for a in apps.values():
                a.foreground = False
            await apps[pkg].app_start(pkg)
            return {"foreground": self._fg_pkg()}
        if cmd == "share":
            return await self._share(args)
        if cmd == "global":
            for a in (self.vooy, *self.chats.values()):
                await a.press(str(args["action"]))
            return {}
        v = self._fg()
        if cmd == "tree":
            if v is None:
                return {"xml": '<?xml version="1.0"?><hierarchy rotation="0"><node class="android.widget.FrameLayout" '
                        f'package="{LAUNCHER}" bounds="[0,0][1080,2400]" text="" content-desc="Home" resource-id="" clickable="false"/></hierarchy>'}
            return {"xml": await v.dump()}
        if v is None:
            raise RuntimeError("전경 앱 없음(홈 화면)")
        if cmd == "tap":
            await v.click(int(args["x"]), int(args["y"]))
            return {}
        if cmd == "click_at":
            await v.click(int(args["x"]), int(args["y"]))
            return {"method": "gesture"}
        if cmd == "click":
            from adapters.android_base import parse_hierarchy

            sel = dict(args["selector"])
            root = parse_hierarchy(await v.dump())
            import re as _re

            for n in root.walk():
                if "resourceId" in sel and n.rid != sel["resourceId"]:
                    continue
                if "description" in sel and n.desc != sel["description"]:
                    continue
                if "descriptionMatches" in sel and not _re.fullmatch(sel["descriptionMatches"], n.desc):
                    continue
                if "className" in sel and n.cls != sel["className"]:
                    continue
                x, y = n.bounds.center
                await v.click(x, y)
                return {"method": "action"}
            raise RuntimeError("누를 노드를 찾지 못함")
        if cmd == "set_text":
            await v.set_text(dict(args["selector"]), str(args["text"]))
            return {}
        if cmd == "paste":
            await v.send_keys(str(args["text"]))
            return {}
        if cmd == "screenshot":
            return {"png_b64": base64.b64encode(await v.screenshot_png()).decode()}
        raise RuntimeError(f"모르는 명령 {cmd}")

    # --- 연결 -------------------------------------------------------------

    async def run_once(self) -> None:
        async with connect(self.url, additional_headers={"Authorization": f"Bearer {self.token}"}) as ws:
            self._ws = ws
            hello = {"type": "hello", "device": self.device_name, "version": "fake", "sdk": self.sdk, "android": str(self.sdk - 20)}
            if self.device_id:
                hello["device_id"] = self.device_id
            await ws.send(json.dumps(hello))
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
