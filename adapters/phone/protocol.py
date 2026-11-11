"""휴대폰 워커 ↔ 게이트웨이 메시지 규약 (docs/proposals/phone-worker.md 5장).

WebSocket 텍스트 프레임 하나에 JSON 하나. 휴대폰이 먼저 연결하고 hello 를 보낸다.

  휴대폰 → 게이트웨이
    {"type": "hello", "device": "<기기 이름>", "version": "<워커 버전>"}
    {"type": "res", "id": 7, "ok": true, "result": {...}}       명령 결과
    {"type": "res", "id": 7, "ok": false, "error": "<사유>"}
    {"type": "event", "event": "notif.posted", "data": {...}}    알림·상태 이벤트
    {"type": "ping"}                                             하트비트(PING_S 간격)
  게이트웨이 → 휴대폰
    {"type": "req", "id": 7, "cmd": "tree", "args": {...}}
    {"type": "pong"}

명령(cmd)과 인자·결과는 COMMANDS 표가 기준이다. 화면을 쓰는 명령(SCREEN_COMMANDS)은 게이트웨이의
화면 임대(lease)를 가진 어댑터만 보낼 수 있다(봇 셋이 휴대폰 한 대의 화면을 나눠 쓰므로).
"""

from __future__ import annotations

PING_S = 15  # 휴대폰 하트비트 간격
DEAD_AFTER_S = 45  # 이만큼 아무 프레임도 없으면 연결이 죽은 것으로 본다

# cmd → (인자, 결과) 설명. 워커 앱(Java)과 가짜 휴대폰이 이 표를 따른다
COMMANDS: dict[str, tuple[str, str]] = {
    "state": ("{}", "{screen_on, locked, foreground, battery, version}"),
    "watch": ("{pkgs: [str]}", "{}  이후 이 패키지들의 알림을 이벤트로 올린다"),
    "launch": ("{pkg}", "{foreground}  런처 인텐트로 연다(알림 PendingIntent 는 BAL 에 막힌다)"),
    "tree": ("{}", "{xml}  uiautomator dump 와 같은 형식(class·resource-id·content-desc·text·package·clickable·bounds)"),
    "tap": ("{x, y}", "{}"),
    "set_text": ("{selector, text}", "{}  selector 키: description·descriptionMatches·text·textMatches·resourceId·className"),
    "paste": ("{text}", "{}  포커스된 입력창에 클립보드로 붙여넣고 클립보드를 비운다"),
    "global": ("{action: home|back}", "{}"),
    "screenshot": ("{}", "{png_b64}"),
}
SCREEN_COMMANDS = frozenset({"launch", "tree", "tap", "set_text", "paste", "global", "screenshot"})

# 이벤트 종류
EVENTS = ("notif.posted", "notif.removed", "state.changed")
# notif.* 의 data: {pkg, key, id, channel, template, title, text, messages: [str], when_ms, summary: bool}
