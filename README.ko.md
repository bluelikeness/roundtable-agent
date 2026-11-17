# 멀티봇 허브

[English](README.md)

여러 소비자용 AI 비서 앱을 웹 화면 하나에서 **Claude**가 지휘하게 하는 개인용 허브입니다.
메시지를 쓰면 Claude가 어느 비서에게 물을지 정하고, 비서들 사이에 답을 넘기며(릴레이·토론·회의) 정리까지 해 줍니다.
비서들은 공개 API가 없는 앱이라, 허브는 안드로이드 휴대폰의 접근성 서비스 워커 앱으로 이 앱들을 조종합니다.

> 사용자 1명, 직접 운영(self-hosted)용입니다. Claude는 **내 구독**으로 로그인한 Claude Code를 헤드리스(`claude -p`)로 띄워 씁니다.
> 다른 사람과 허브를 같이 쓰면 그 사람들의 요청이 내 구독으로 처리되는데, 이는 Claude Code 약관에 어긋납니다.
> 공유하려면 먼저 러너를 API 키 방식으로 바꾸세요. 접근성 자동화로 다른 회사 앱을 조종하는 것은 비공식 방법이니
> 각 앱의 약관을 확인하세요.

## 동작 방식

```
 브라우저 ──HTTPS──> web (nginx) ──/api, /ws──> hub-api (FastAPI, Postgres)
                                                  │  메시지 하나 = 실행 하나
                                                  ▼
                                               runner ── claude -p ── MCP 툴: <봇>__ask / status
                                                                         │
                                       adapters (봇마다 MCP 서버 하나) ──┘
                                                  │ 범용 화면 명령
                                                  ▼
 안드로이드 휴대폰: 워커 앱 ──WSS /phone/ws──> phone-gateway
 (접근성 서비스가 비서 앱을 조종하고, 알림 리스너가 답을 잡는다)
```

- **hub/** – 로그인(비밀번호 + TOTP), 세션(방), 실행 대기열, WebSocket 이벤트, 봇 상태 패널, 바깥 입구 API.
- **runner/** – 메시지마다 오케스트레이터 지침(`runner/orchestrator.md`)으로 Claude Code를 띄우고, 툴 호출과 답을
  이벤트로 흘려보냅니다. 한 실행은 600초에서 끊깁니다.
- **adapters/** – 비서마다 MCP 서버 하나(`ask`, `status`). 오류는 MCP 예외가 아니라 결과 필드로 돌려줍니다.
  앱별 화면 셀렉터는 `adapters/<봇>/selectors.yaml`에 있습니다. 개발·테스트용 가짜 봇(`mock-a/b/c`)도 들어 있습니다.
- **phone/** – 안드로이드 워커 앱(Java, Gradle 없음)과 빌드·배포 스크립트.
- **web/** – React + TypeScript 화면.
- **edge/** – (선택) 내 도메인에 자동 HTTPS를 붙여 주는 Caddy 입구.
- **docs/** – 요구사항, 아키텍처, 어댑터 계약, 오케스트레이터, UI, 로드맵.

## 준비물

- Docker(Compose v2)
- GNU Make(선택. 명령은 `docker compose`·`uv`·`pnpm`을 감싼 것뿐입니다)
- 개발·테스트: Python 3.12 + [uv](https://docs.astral.sh/uv/), Node.js + pnpm 10
- Claude Code를 쓸 수 있는 Claude 구독
- 실제 비서를 쓰려면: 비서 앱이 설치·로그인된 안드로이드 휴대폰(안드로이드 10 이상, 스크린샷을 쓰려면 11 이상 권장)
- 휴대폰·바깥에서 접속하려면: **각자의 도메인**(아래 참고)

## 빠르게 시작하기 (가짜 봇, 이 PC에서만)

```bash
cp .env.example .env            # change-me 를 모두 무작위 값으로 바꾼다
make up                         # postgres, hub-api, runner, web, phone-gateway, 가짜 봇
                                # (실제 봇 어댑터도 뜨지만 휴대폰이 붙기 전까지는 사용 불가로 표시)
make admin ARGS="create-operator --username me"   # 인증 앱에 등록할 TOTP 시크릿이 한 번 표시된다
make runner-login               # 러너 컨테이너 안에서 Claude Code 로그인(처음 한 번)
```

http://127.0.0.1:8080 에 들어가 로그인하고, 방을 만든 뒤 "mock-a랑 mock-b한테 내일 날씨 물어봐"를 보내 보세요.
이 PC의 포트는 127.0.0.1에만 열립니다(`docker-compose.override.yml`).

## 각자의 도메인 쓰기

바깥으로 열리는 주소는 모두 **`.env`의 `PUBLIC_HOST` 값 하나**로 정해집니다. 저장소에는 도메인을 넣지 않으므로
쓰는 사람마다 자기 도메인을 씁니다.

1. 도메인(또는 DDNS 이름)이 집 공인 IP를 가리키게 합니다. 어느 등록업체나 DDNS 서비스든 됩니다.
2. 공유기에서 바깥 TCP **443**을 허브가 도는 PC로 넘깁니다(포트 포워딩).
3. `.env`에 넣습니다.
   ```
   PUBLIC_HOST=hub.내도메인.example
   COMPOSE_PROFILES=public
   HUB_COOKIE_SECURE=true
   ```
4. `docker compose up -d` — `edge`(Caddy)가 `PUBLIC_HOST`로 Let's Encrypt 인증서를 받아 web으로 넘겨 줍니다.

터널이나 다른 리버스 프록시를 쓰고 싶다면 `web` 컨테이너(80번)로 향하게 하고, `PUBLIC_HOST`는 그대로 적어 두세요.
휴대폰 배포 스크립트가 이 값으로 접속 주소를 정합니다.

## 휴대폰 연결

```bash
phone/release.sh
```

`PUBLIC_HOST`와 기기 토큰으로 워커 APK를 빌드해 자동 업데이트용으로 게시하고, **30분 동안 한 번만** 쓰는 다운로드
링크와 QR(`phone/dist/qr.png`)을 만듭니다. APK 안에 기기 토큰이 들어 있으니 커밋하거나 링크를 공유하지 마세요.
휴대폰에서 설치한 뒤 "제한된 설정 허용"을 하고, 알림 접근과 접근성 서비스를 켭니다(자세한 순서는
[phone/README.md](phone/README.md)). 휴대폰은 한 번에 한 대만 붙습니다. 워커가 화면을 스스로 깨우게 하려면 보안 잠금이
없는 휴대폰을 쓰세요. 보안 잠금은 절대 풀지 않습니다.

그다음 허브의 봇 패널에서 실제 봇(`vooy`, `pickle`, `muse`, `cue`)을 켭니다. 봇 추가는 `hub/bots.yaml` 한 줄과 어댑터
폴더 하나면 되고, `hub/`·`runner/`·`web/`은 고치지 않습니다.

## 바깥 입구 (선택)

HTTP 요청을 보낼 수 있는 다른 비서(예: 피클)가 아무 때나 방에 의견을 넣을 수 있습니다.

```
POST https://<PUBLIC_HOST>/api/inbox
Authorization: Bearer <HUB_INBOX_TOKEN>
{"room": "방 제목", "text": "보낼 내용", "from": "피클"}
```

- 방은 제목으로 찾습니다(공백·대소문자 무시 일치 → 유일한 부분 일치). 없으면 404와 방 제목 목록을 돌려줍니다.
- 받은 글은 사용자 메시지로 들어가 평소처럼 실행됩니다. 요청은 최대 15초 기다려, 끝났으면
  `200 {"status": "done", "answer": ...}`, 아직이면 `202`와 `result_url`을 돌려줍니다. 나중에 같은 토큰으로
  `GET <result_url>` 하면 답을 받습니다.
- `HUB_INBOX_TOKEN`을 넣어야 열립니다. 분당 10건, 4000자까지 받습니다.

## 개발

```bash
make test        # hub, runner, adapters(단위 + 계약), web
make eval        # 실제 claude -p 로 가짜 봇 상대 오케스트레이터 평가(runner-login 필요)
make types       # hub/app/events.py → web/src/types/events.ts 재생성
```

바꾸면 안 되는 계약: 어댑터 MCP `ask`/`status` 스키마(docs/03), WebSocket 이벤트 봉투와 type 목록(docs/05 6장),
러너 인터페이스. 이 저장소의 작업 규칙은 [CLAUDE.md](CLAUDE.md)에 있습니다.

## 보안 메모

- `.env`, 빌드한 APK, `phone/dist/` 아래 파일은 커밋하지 않습니다. 비밀값(어댑터 토큰, 기기 토큰, 입구 토큰, 쿠키 키)은
  설치마다 새로 만듭니다.
- 내부 서비스(어댑터, 러너, 폰 게이트웨이 내부 API)는 Docker 내부망에서만 닿습니다. 바깥에 열리는 것은 web 컨테이너뿐이고,
  `public` 프로필을 켜면 `edge`를 거칩니다.
- 비서의 답은 믿을 수 없는 글로 Claude 맥락에 들어갑니다. Claude는 봇의 `ask`·`status` 툴만 쓸 수 있고(셸·파일·웹 도구는
  막혀 있음), 오케스트레이터 지침은 결제·예약을 다음 메시지에서 사용자가 허락하기 전에는 확정하지 않습니다.

## 라이선스

아직 정하지 않았습니다. 모든 권리는 작성자에게 있습니다.
