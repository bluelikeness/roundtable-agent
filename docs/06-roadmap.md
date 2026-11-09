# 멀티봇 로드맵과 클로드 코드 핸드오프

> **현재 단계: P2** (단계가 바뀌면 사람이 이 줄만 고친다)

## 1. 로드맵

코어는 Mock으로 끝까지 만들고, 실제 봇은 추가 비용이 낮은 순서(Vooy → 피클 → 뮤즈)로 하나씩 붙인다. 각 단계는 게이트를 통과해야 다음으로 간다. 날짜는 두지 않는다 — 개인 프로젝트이고 P0의 확인 결과에 따라 P3·P4 순서가 바뀔 수 있다.

> 그림: 로드맵 · 6단계, 게이트 6개 (원본은 claude.ai 문서에만 있음)

P2 이후는 독립적이라 병렬로 가도 된다. 단, 클로드 코드 세션은 한 번에 한 단계만 맡긴다. 범위가 섞이면 어댑터 계약을 임의로 늘리는 일이 생긴다.

## 2. 단계별 범위·완료 기준·선행 조건

| 단계 | 범위 | 완료 기준 (게이트) | 선행 조건 | 클로드 코드 세션 수(예상) |
| --- | --- | --- | --- | --- |
| P0 사전 확인 | 코드 없음. 수동 확인 4건: ① Vooy 연결 경로(텔레그램 1:1 응답, 안 되면 앱 경로), ② 한국에서 muse.ai 로그인(VPN 포함), ③ 로컬 안드로이드 에뮬레이터에서 피클 로그인·대화 1건, ④ Claude Code 설치 후 `claude -p --output-format stream-json --mcp-config --strict-mcp-config --allowedTools --append-system-prompt-file --max-turns` 전부 유효 | 4건 결과를 요구사항 문서 7장 미결에 기록 | 없음 | 0 |
| P1 코어 | 허브 API, 러너, Mock 어댑터 3개, 웹 UI 전부. docker compose로 로컬 기동 | 오케스트레이터 설계 10장 T1\~T14 각 5회 중 4회 통과, UI E2E 5개 통과 | P0 ④ | 6\~10 |
| P2 Vooy | `adapters/android_base.py`(에뮬레이터·접근성 트리·한글 입력 공통), Vooy 어댑터(앱 경로, 03 7장), VM 2 프로비저닝, WireGuard, noVNC 로그인 시트, 이미지 프록시 | 계약 테스트 전부 통과, 콜드 부팅 후 스모크(예약 가능 식당 찾기 → `[tap:]`) 성공 | P0 ①, P1 | 3\~5 |
| P3 피클 | 피클 어댑터(P2의 `android_base.py`·VM 2·noVNC 재사용, 전용 AVD 추가) | 계약 테스트 통과, 에뮬레이터 재부팅 후 스냅샷 복원 → 대화 1건 성공 | P0 ③, P2 | 2\~3 |
| P4 뮤즈 | 뮤즈 어댑터 + VPN 사이드카. 지역 제약이 안 풀리면 `muse-api` 대체 어댑터 | 계약 테스트 통과, 스모크 1건. 대체 시 description에 "실행 없음" 명시 | P0 ②, P1 | 2\~4 |
| P5 운영 배포 | VM 1 배포, caddy TLS, TOTP, DB 백업, 로그 보존 잡, 모바일 점검 | 2주 일상 사용 중 수동 개입 없이 운영 | P1\~P4 중 최소 P2 | 2\~3 |

**P1 내부 순서** (한 세션이 한 줄을 맡는다)

1. 레포 스캐폴드 + compose + DB 스키마(Alembic) + 허브 API 로그인·세션·메시지 REST
2. 어댑터 베이스 클래스 + MCP 서버 + 관리 HTTP + Mock 어댑터 + 계약 테스트
3. 러너: `claude -p` spawn, stream-json 파싱, 이벤트 변환, `mcp.json` 생성, 세션 분할·요약 주입
4. 오케스트레이터 프롬프트 + T1\~T14 평가 스크립트 (Mock으로 실행)
5. 웹 UI: 스토어·WebSocket·말풍선·입력창
6. 웹 UI: 봇 상태 패널·로그인 시트·structured 렌더링·모바일
7. 통합 E2E + 프롬프트 튜닝

각 줄이 끝나면 `main`에 머지하고 다음 세션은 새로 시작한다. 세션 하나에 두 줄을 맡기지 않는다.

## 3. 레포 구조와 개발 환경

모노레포 하나. 서비스별 디렉터리에 Dockerfile을 두고 루트 compose가 묶는다.

```
multibot/
  CLAUDE.md                 # 4장
  docs/                     # 설계 문서 export(markdown), 클로드 코드가 읽는 사본
    01-requirements.md  02-architecture.md  03-adapters.md
    04-orchestrator.md  05-ui.md  06-roadmap.md
  docker-compose.yml        # hub-api, runner, postgres, web, adapter-mock-a/b/c (+ vooy, muse, pickle)
  docker-compose.vm2.yml    # adapter-pickle + emulator
  .env.example
  hub/                      # FastAPI
    app/{api, ws, models, services, runner_client}.py  alembic/  tests/
    bots.yaml               # 봇 등록: name, mcp_url, admin_url, aliases, color
  runner/                   # Claude Code 러너
    runner/{spawn, stream, events, mcp_config, summary}.py
    orchestrator.md         # 시스템 프롬프트 (오케스트레이터 설계 8장)
    eval/                   # T1~T14 스크립트 + 결과 리포트
    Dockerfile              # Claude Code 고정 버전 설치, ~/.claude 볼륨
  adapters/                 # 어댑터 명세 6장 디렉터리 그대로
    base.py mcp_server.py admin_api.py
    mock/ vooy/ muse/ pickle/
    tests/contract/
  web/                      # React + Vite + TS (UI 설계 8장 구조)
  scripts/                  # gen-types(Pydantic→TS), backup-db, hub-admin
```

**개발 환경**

- Python 3.12 + `uv`, Node 20 + `pnpm`. 루트 `Makefile`: `make up`(compose), `make test`(hub·adapters·web 전부), `make eval`(T1\~T14), `make types`.
- 로컬에서는 Mock 3개만 기동. 실제 어댑터는 compose profile(`--profile vooy` 등)로 선택 기동.
- Claude Code는 러너 컨테이너 안에서만 실행. 개발자 PC의 Claude Code(개발용)와 러너의 Claude Code(오케스트레이터)는 별개 설치·별개 로그인. 러너 컨테이너 첫 기동 시 `docker compose exec runner claude login`.
- `.env`에 비밀값. `docs/`는 설계 문서를 마크다운으로 export해 넣고, 설계가 바뀌면 다시 export한다. 클로드 코드는 claude.ai 문서를 못 읽으므로 이 사본이 기준이다.
- 브랜치: `main` 보호, 세션마다 `p1/03-runner` 식 브랜치 → PR → 테스트 통과 후 머지.

## 4. CLAUDE.md 초안

레포 루트에 둔다. 클로드 코드가 매 세션 읽는 유일한 상시 지시다. 짧게 유지하고 상세는 `docs/`로 보낸다.

```markdown
# 멀티봇 허브

여러 AI 비서 앱(Vooy·뮤즈·피클)을 한 웹 화면에서 Claude Code(헤드리스)가 오케스트레이션하는 개인용 허브.
사용자 1명. 설계 문서는 docs/ 에 있고, 이 문서가 설계와 충돌하면 설계가 맞다.

## 읽을 순서
작업 시작 전 docs/06-roadmap.md 의 "현재 단계"와 그 단계의 작업 지시문을 읽는다.
그다음 그 작업이 속한 설계 문서(02~05) 중 해당 장만 읽는다. 전부 읽지 않는다.

## 바꾸면 안 되는 것
- 어댑터 MCP 툴 계약 (docs/03-adapters.md 2·3장): ask/status 입출력 스키마. 필드 추가가 필요하면 멈추고 보고한다.
- 허브 WebSocket 이벤트 봉투와 type 목록 (docs/05-ui.md 6장).
- 러너 인터페이스 `Runner.start(session, text) -> AsyncIterator[HubEvent]`.
- 어댑터가 MCP 예외를 던지는 코드. 오류는 항상 AskResult 필드.
- orchestrator.md 의 "금지" 절.

## 규칙
- 봇 추가 시 hub/·runner/·web/ 를 고치지 않는다. 고쳐야 한다면 설계 위반이니 보고한다.
- 자격증명(앱 로그인 상태가 든 AVD, 쿠키, API 토큰)을 로그·DB·테스트 픽스처에 넣지 않는다.
- 실제 봇 어댑터 테스트는 계약 테스트(tests/contract)로만. 실봇 스모크는 사람이 한다.
- Claude Code 버전은 runner/Dockerfile 에 고정. 올리지 않는다.
- 한국어 주석·커밋 메시지. 코드 식별자는 영어.

## 명령
- make up / make test / make eval / make types
- 테스트 없이 머지하지 않는다. eval 은 P1 4번 작업 이후부터.

## 완료 보고 형식
작업 끝에: 바꾼 파일 목록, 통과한 테스트, 설계와 다르게 한 점(있으면), 다음 작업자가 알아야 할 것 3줄 이내.
```

`docs/06-roadmap.md`의 "현재 단계" 절은 사람이 단계가 바뀔 때마다 한 줄 고친다. 클로드 코드가 스스로 단계를 넘기지 않는다.

## 5. 단계별 클로드 코드 작업 지시문

세션을 열 때 아래 지시문을 그대로 붙인다. 각 지시문은 "읽을 것 · 만들 것 · 완료 기준 · 하지 말 것" 네 줄이다.

**P1-1 스캐폴드·DB·허브 REST**

> 읽을 것: docs/02-architecture.md 2·6·8장, docs/05-ui.md 6장(이벤트 봉투만). 만들 것: 레포 구조(06-roadmap 3장), docker-compose(hub-api·postgres·web 빈 껍데기), Alembic 초기 마이그레이션(session·message·run·tool\_call·bot·bot\_thread·operator), 허브 REST(로그인+TOTP, 세션 CRUD, 메시지 조회), Pydantic 이벤트 모델과 `make types`. 완료 기준: `make up` 후 로그인→세션 생성→메시지 조회가 curl로 되고 hub 단위 테스트 통과. 하지 말 것: 러너·WebSocket·어댑터 호출 구현.

**P1-2 어댑터 베이스 + Mock**

> 읽을 것: docs/03-adapters.md 전체. 만들 것: `adapters/base.py`(BotAdapter·AskResult·Structured·Thread), MCP 서버(streamable HTTP, ask·status), 관리 HTTP(5장 전부), Mock 어댑터(10장 설정 그대로), 계약 테스트(11장 표 전부). 완료 기준: Mock 3개를 compose로 띄우고 계약 테스트 전부 통과, `X-Hub-Token` 없는 요청 401. 하지 말 것: 실제 봇 어댑터, 허브 코드 수정.

**P1-3 러너**

> 읽을 것: docs/02-architecture.md 3·4장, docs/04-orchestrator.md 9장. 만들 것: `Runner` 인터페이스와 `ClaudeCodeRunner`(spawn, stream-json 파싱→HubEvent, 세션 ID 저장, `mcp.json` 생성, 타임아웃·SIGTERM, 세션 분할·요약 주입), 러너 Dockerfile(Claude Code 고정 버전), 허브→러너 호출과 WebSocket 중계. 완료 기준: Mock 상대로 `claude -p` 실제 실행해 tool\_use→bot.pending, tool\_result→bot.reply, result→claude.final 이벤트가 WebSocket으로 나온다. stream-json 샘플 고정 입력으로 파서 단위 테스트. 하지 말 것: 프롬프트 튜닝, UI.

**P1-4 오케스트레이터 프롬프트 + 평가**

> 읽을 것: docs/04-orchestrator.md 전체. 만들 것: `runner/orchestrator.md`(8장 초안 그대로 시작), 변수 주입, `runner/eval/`에 T1\~T14 스크립트(각 5회 실행, 툴 호출 패턴·정리 형식 자동 판정, 리포트). 완료 기준: 14개 중 12개 이상 4/5 통과. 미달 케이스는 리포트에 원인 기록. 하지 말 것: 어댑터 계약 변경, `--max-turns` 상향으로 통과시키기.

**P1-5 웹 UI 코어**

> 읽을 것: docs/05-ui.md 1·2·5·6·8장. 만들 것: Vite 프로젝트, 스토어·WebSocket 클라이언트(재연결·since\_seq), 세션 목록, 말풍선 7종, 입력창(@자동완성·/명령), 실행 헤더. 완료 기준: Mock + 러너로 T2·T3·T7 흐름이 화면에서 보이고 새로고침 후 복원됨. 스토어 리듀서 스냅샷 테스트. 하지 말 것: structured 렌더링, 봇 패널, 모바일.

**P1-6 웹 UI 나머지**

> 읽을 것: docs/05-ui.md 3·4·7장. 만들 것: structured 요소 6종, 확정 박스와 `[진행]` 버튼, 봇 상태 패널, 로그인 시트(CodeLogin·VncLogin 공통 껍데기), 이미지 프록시 허브 엔드포인트, 모바일 레이아웃, 다크 모드. 완료 기준: Mock 응답의 `[버튼: X]`가 칩으로 뜨고 클릭 시 `[tap:]` 전송, 768px 이하에서 드로어·바텀시트 동작. 하지 말 것: 실제 noVNC 연동.

**P1-7 통합**

> 읽을 것: docs/06-roadmap.md 2장 P1 게이트. 만들 것: Playwright E2E 5개, `make eval` 재실행, 발견된 결함 수정. 완료 기준: P1 게이트 전부. 하지 말 것: 새 기능.

**P2 Vooy** · **P3 피클** · **P4 뮤즈**

> 읽을 것: docs/03-adapters.md 해당 장 + 6·11장. 만들 것: 어댑터 디렉터리, config.yaml, Dockerfile, compose profile, 계약 테스트 통과. 완료 기준: 계약 테스트 전부 + 사람이 하는 스모크용 체크리스트 출력. 하지 말 것: 베이스 클래스·허브·UI 수정 (필요하면 멈추고 보고).

**P5 운영 배포**

> 읽을 것: docs/02-architecture.md 7장, docs/05-ui.md 7장. 만들 것: caddy 설정, 프로덕션 compose, DB 백업 스크립트와 cron, 로그 보존 잡, 배포 README. 완료 기준: 새 VM에 README대로 30분 안에 올라간다. 하지 말 것: 코드 기능 변경.

## 6. 설계 문서 목록과 미결 사항 통합

**문서 세트** (claude.ai 프로젝트 "멀티봇")

| # | 문서 | `docs/` 파일명 | 클로드 코드가 읽는 시점 |
| --- | --- | --- | --- |
| 1 | 멀티봇 허브 요구사항 정의서 | 01-requirements.md | 참고용, 지시문에서 직접 가리키지 않음 |
| 2 | 멀티봇 허브 아키텍처 설계서 | 02-architecture.md | P1-1, P1-3, P5 |
| 3 | 멀티봇 어댑터 인터페이스 명세 | 03-adapters.md | P1-2, P2\~P4 |
| 4 | 멀티봇 오케스트레이터 설계 | 04-orchestrator.md | P1-3(9장), P1-4 |
| 5 | 멀티봇 UI 설계 | 05-ui.md | P1-1(6장), P1-5, P1-6, P5 |
| 6 | 멀티봇 로드맵과 클로드 코드 핸드오프 | 06-roadmap.md | 매 세션 (CLAUDE.md가 가리킴) |

**미결 사항 통합** — 어느 단계 전에 결정해야 하는지 기준으로 모았다.

| 결정 전 단계 | 미결 | 출처 |
| --- | --- | --- |
| P0 | Vooy 연결 경로(→ 앱 경로로 결정), 앱의 새 대화 기능 유무 | 요구사항 7장, 어댑터 11장 |
| P0 | 한국에서 muse.ai 로그인 가능 여부, 안 되면 해외 계정 확보 방법 | 요구사항 7장, 아키텍처 9장 |
| P0 | 피클 Android가 에뮬레이터에서 로그인·동작하는지, 대화 제목 편집 지원 여부 | 요구사항 7장, 어댑터 11장 |
| P0 | Claude Code 약관의 헤드리스 개인 자동화 범위, 플래그 유효성 | 요구사항 7장, 아키텍처 9장 |
| P1-3 | 24시간 세션 분할 시 요약 주입 형식 확정 | 아키텍처 9장, 오케스트레이터 9장 |
| P1-4 | Claude Code 기본 프롬프트의 코딩 성향 잔존 여부, 병렬 툴 호출 여부, `status` 선호출 필요성 | 오케스트레이터 11장 |
| P1-4 | `[진행]` 버튼 감지: 정규식 vs 구조화 마커 | UI 9장 |
| P1-6 | 팬아웃 원문 정렬(도착순 vs 봇 순서), 봇 색 팔레트 | UI 9장 |
| ~~P1-6~~ | ~~어댑터 명세 5장에 `GET /images/<id>` 추가~~ — 반영 완료 | UI 9장 |
| P3 | 에뮬레이터 상시 vs 온디맨드, `booting` 상태 처리 | 아키텍처 9장, 어댑터 11장 |
| P3 | noVNC 프록시 주체(허브 API vs caddy) | UI 9장 |
| P4 | 뮤즈 VPN 경유 지역 체크 통과 여부 → 대체 어댑터 전환 결정 | 어댑터 11장 |
| P5 | 허브 대화 이력 보존 기간, 클라우드 서버 사양·비용 상한 | 요구사항 7장 |
| 미정 | 모델 선택(Sonnet/Opus) 자동 vs 토글, `auto_confirm_limit` 값 | 오케스트레이터 11장 |

**다음 할 일**: P0 사전 확인 4건을 직접 해본 뒤 결과를 요구사항 7장에 적는다. 그 결과로 P3·P4 순서와 뮤즈 대체 여부가 정해지면, `docs/`로 export해 레포를 만들고 P1-1 세션을 연다.
