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
    "state": ("{}", "{screen_on, locked, secure, foreground, battery, version, sdk}  secure: 보안 잠금(PIN·패턴·지문) 있음"),
    "wake": ("{}", "{screen_on, locked, secure, woke}  보안 잠금이 없으면 화면을 켜고 잠금화면을 넘긴다. 있으면 아무것도 안 한다"),
    "watch": ("{pkgs: [str]}", "{}  이후 이 패키지들의 알림을 이벤트로 올린다"),
    "launch": ("{pkg}", "{foreground}  런처 인텐트로 연다(알림 PendingIntent 는 BAL 에 막힌다)"),
    "tree": ("{}", "{xml}  uiautomator dump 와 같은 형식(class·resource-id·content-desc·text·package·clickable·bounds)"),
    "tap": ("{x, y}", "{}"),
    "click": ("{selector}", "{method}  셀렉터(set_text 와 같은 키)로 찾은 노드에 접근성 클릭, 안 되면 좌표 탭"),
    "click_at": ("{x, y}", "{method}  그 위치를 덮는 가장 작은 누를 수 있는 노드에 접근성 클릭, 없으면 좌표 탭"),
    "set_text": ("{selector, text}", "{}  selector 키: description·descriptionMatches·text·textMatches·resourceId·className"),
    "paste": ("{text}", "{}  포커스된 입력창에 클립보드로 붙여넣고 클립보드를 비운다"),
    "global": ("{action: home|back|lock_screen}", "{}  lock_screen: 화면 끄기(안드로이드 9+)"),
    "screenshot": ("{}", "{png_b64}"),
    "update_check": ("{}", "{current, latest}  워커가 바로 새 버전을 확인한다(있으면 내려받아 설치)"),
    "app_info": ("{pkg}", "{version_name, version_code}  설치된 앱 버전(셀렉터 기기별 층의 열쇠). 없으면 오류"),
    "logs": ("{}", "{text}  워커 화면 기록(명령 이름·상태만, 메시지 내용 없음). 진단용"),
    # 첨부(2026-11-27). 파일은 게이트웨이 중계(/phone/files)로 오간다. 워커는 기기 토큰으로 받고 올린다
    "share": ("{pkg, files: [{code, name, mime}]}",
              "{shared}  파일을 받아 공유하기(ACTION_SEND·SEND_MULTIPLE)로 pkg 앱에 넘긴다. 공유 뒤 화면은 앱마다 다르다"),
    "media_since": ("{since_ms, pkg?, mimes?: [str]}",
                    ("{items: [{id, name, mime, size, date_ms, owner}], access}  since_ms 뒤 휴대폰에 저장된 사진·영상."
                     " pkg 가 있으면 그 앱이 저장한 것만(안드로이드 10+). access=false 면 저장소 읽기 권한 없음")),
    "media_upload": ("{id}", "{code, name, mime, size}  media_since 의 항목을 게이트웨이(/phone/files)에 올린다"),
}
SCREEN_COMMANDS = frozenset({"launch", "tree", "tap", "click", "click_at", "set_text", "paste", "global", "screenshot", "share"})

# 이벤트 종류
EVENTS = ("notif.posted", "notif.removed", "state.changed")
# notif.* 의 data: {pkg, key, id, channel, template, title, text, messages: [str], when_ms, summary: bool}
