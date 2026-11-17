# Roundtable-Agent 워커 (휴대폰 앱)

[English](README.md)

사용자 휴대폰에서 Vooy·피클·뮤즈 앱을 접근성 서비스로 조종하는 안드로이드 앱이다(docs/proposals/phone-worker.md, ADR-9).
허브의 폰 게이트웨이에 바깥으로 WebSocket 연결(`wss://<허브 주소>/phone/ws`)하고, 게이트웨이가 보내는 범용 명령
(`adapters/phone/protocol.py`의 COMMANDS)을 수행한다. 봇별 판단은 서버 어댑터가 한다. 앱이 바뀌어도 서버의 `selectors.yaml`만 고친다.

## 연결 구조

```mermaid
sequenceDiagram
  participant A as 어댑터 (서버)
  participant G as phone-gateway
  participant W as 워커 (휴대폰)
  participant APP as 비서 앱
  W->>G: WSS /phone/ws + 기기 토큰, hello {device, sdk}
  loop 15초마다
    W-->>G: 하트비트
  end
  A->>G: rpc (launch / tree / set_text / tap ...)
  G->>W: 명령
  W->>APP: 접근성 동작
  W-->>G: 결과 (트리 XML, ok)
  APP-->>W: 답 알림
  W-->>G: 알림 이벤트 (길이·채널만)
  G-->>A: 결과 / 이벤트
```

### 자동 업데이트

```mermaid
flowchart LR
  REL["phone/release.sh"] -->|"빌드 + sha256"| DIST["phone/dist/<br/>APK + latest.json"]
  REL -->|"일회용 링크 + QR"| QR["처음 설치"]
  DIST --> GW["phone-gateway<br/>/phone/apk/*"]
  W["워커"] -->|"연결 때, 6시간마다,<br/>또는 update_check"| GW
  GW -->|"더 높은 versionCode"| W
  W -->|"sha256 확인 →<br/>PackageInstaller"| W
```

## 빌드

Gradle 없이 Android SDK build-tools와 Android Studio JDK로 빌드한다.

```bash
PHONE_WS_URL=wss://hub.example/phone/ws PHONE_DEVICE_TOKEN=<.env 의 값> phone/build.sh <출력 폴더>
```

두 env는 앱의 기본 설정값이 된다(앱 화면에서 바꿀 수 있다). 토큰이 APK 안에 들어가므로 빌드한 APK는 레포에 넣지 않고 본인 휴대폰에만 설치한다.

## 배포와 자동 업데이트

```bash
phone/release.sh   # .env 의 PUBLIC_HOST(각자의 도메인)로 https://<PUBLIC_HOST> 를 쓴다
```

다른 주소로 배포할 때만 `PHONE_PUBLIC_URL=https://<주소> phone/release.sh`처럼 덮어쓴다.

- 빌드(버전 번호는 2026-01-01부터 초 단위) → `phone/dist/`에 게시. `phone-gateway`가 이 폴더를 읽어 워커 자동 업데이트에 쓴다.
- 30분 동안 한 번만 쓰는 설치 링크와 QR(`phone/dist/qr.png`)을 만든다. APK 안에 기기 토큰이 있어 공개 링크는 두지 않는다.
- 연결된 휴대폰에 `update_check`를 보낸다. 워커는 연결될 때와 6시간마다 스스로도 확인한다.
- 워커는 새 버전을 기기 토큰으로 내려받아 SHA-256을 확인하고 PackageInstaller로 설치한다. 안드로이드 14+는
  `UPDATE_PACKAGES_WITHOUT_USER_ACTION` 권한 덕에 확인 창 없이 설치되고, 설치 뒤 접근성 서비스가 다시 켜져 저절로 다시 연결한다
  (에뮬레이터 API 34에서 확인). 서버 주소가 바뀐 배포는 이전 주소로 닿지 않으므로 QR로 다시 설치한다.

## 설치와 권한 (처음 한 번)

1. APK 설치(출처를 알 수 없는 앱 허용).
2. 앱 → "앱 정보 열기" → ⋮ → "제한된 설정 허용"(안드로이드 13+에서 직접 설치한 앱은 이걸 해야 다음 두 권한을 켤 수 있다).
3. "알림 접근 설정 열기" → Roundtable-Agent 워커 알림 켜기.
4. "접근성 설정 열기" → 설치된 앱 → Roundtable-Agent 워커 켜기. 켜지면 바로 게이트웨이에 연결한다(앱 화면을 닫아도 된다).

## 동작

- 연결: 끊기면 1→30초 지수 백오프로 다시 붙는다. 하트비트 15초.
- 명령: `state`(화면·잠금·전경 앱·키보드 위치), `watch`, `launch`(런처 인텐트), `tree`(앱 창의 접근성 트리를 uiautomator dump 형식 XML로), `tap`, `set_text`, `paste`(붙여넣은 뒤 클립보드 비움), `global`(home·back), `screenshot`(안드로이드 11+).
- 알림: `watch`로 받은 앱(Vooy·피클·뮤즈)의 알림만 이벤트로 올린다. 제목·본문은 보내지 않고 길이·채널만 보낸다.
- 화면 기록에는 명령 이름·상태만 남기고 메시지 내용은 남기지 않는다.

개인 휴대폰 기준이다. 화면이 꺼졌거나 잠겨 있으면 어댑터가 `backend_down`으로 보고하고 조종하지 않는다.
