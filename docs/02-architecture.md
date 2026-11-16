# 멀티봇 허브 아키텍처 설계서

> 작성일 2026-11-08

## 1. 한눈에

허브는 4계층이다. 웹 UI와 허브 API가 대화와 상태를 관리하고, 러너가 사용자 메시지마다 Claude Code를 헤드리스로 띄우며, Claude Code가 MCP 툴로 노출된 봇 어댑터를 골라 부르고, 어댑터가 각 봇의 실제 채널(사용자 휴대폰의 앱)을 조작한다. 휴대폰은 워커 앱이 허브의 폰 게이트웨이에 바깥으로 연결해 조종받는다(ADR-9, docs/proposals/phone-worker.md).

> 그림: 전체 아키텍처 · 4계층, 어댑터 3개 (원본은 claude.ai 문서에만 있음)

허브 API는 Claude Code를 거치지 않고 어댑터 헬스체크와 재로그인을 직접 호출한다(점선). 봇 응답은 Claude Code가 받아 정리한 뒤 러너의 stream-json을 통해 UI로 흘러간다.

**설계 원칙**

- Claude Code는 교체 가능한 호출 계층이다. 러너 인터페이스(`run(session, message) → event stream`) 뒤에 두어, 지인 공유로 전환할 때 API 키 구현으로 바꿀 수 있게 한다.
- 어댑터는 서로 모르고 허브도 모른다. MCP 툴 계약만 지키면 되고, 봇 추가는 어댑터 프로세스 하나와 `mcp-config` 한 줄이다.
- 봇 원문은 어댑터가 반환한 그대로 보존하고, 정리는 Claude Code만 한다.
- 자격증명은 어댑터 프로세스 안에만 있다. Claude Code와 허브 API는 토큰·쿠키를 보지 못한다.

## 2. 컴포넌트

| 컴포넌트 | 책임 | 하지 않는 것 | 기술 |
| --- | --- | --- | --- |
| React 웹 UI | 채팅 화면, 봇별 말풍선, 봇 상태 패널, 세션 목록 | 봇·Claude 직접 호출 | React + Vite, WebSocket 클라이언트 |
| 허브 API | 로그인, 세션 CRUD, 메시지 수신 → 러너 호출, 이벤트를 WebSocket으로 중계, 어댑터 헬스체크·재로그인 트리거 | 오케스트레이션 판단, 봇 응답 가공 | FastAPI, PostgreSQL |
| Claude Code 러너 | `claude -p` 프로세스 생성, 시스템 프롬프트·MCP 설정·허용 툴 주입, stream-json 파싱 → 허브 이벤트로 변환, 세션 ID 보관 | 봇 호출 | Python subprocess, 러너 인터페이스 뒤에 구현 |
| claude -p 프로세스 | 메시지 해석, 봇 선택, 팬아웃·릴레이 수행, 원문 정리, 되물음 처리 | 자격증명 접근, 파일·셸 실행 | Claude Code 헤드리스, 내 구독 |
| MCP 어댑터 (Vooy·피클·뮤즈) | 휴대폰의 봇 앱 조작(셀렉터·말풍선 수집·응답 확정·`[tap:]`), 답 알림 대기 | 다른 봇 인지, 휴대폰 직접 연결 | Python MCP SDK + `PhoneDevice`(게이트웨이 경유). 뮤즈는 Meta AI 앱(`com.facebook.aura`) |
| 폰 게이트웨이 | 휴대폰 워커의 WebSocket 수신(`/phone/ws`), 어댑터에 명령 중계, 화면 임대(봇 셋이 화면 하나를 차례로), 알림 이벤트 롱폴링 | 봇별 판단 | Python(Starlette), `adapters/phone/` |
| 휴대폰 워커 앱 | 접근성 서비스로 앱 열기·노드 찾기·입력·탭·화면 트리, 알림 리스너, 게이트웨이에 바깥으로 연결·재연결 | 봇을 모름(범용 명령만) | 안드로이드 앱(Java), 사이드로드 |
| MCP 어댑터 (Mock) | 지연·실패·되물음을 설정값으로 흉내 | 외부 호출 | Python MCP SDK |
| 자격증명 저장소 | 봇 앱 로그인 상태는 휴대폰에 있다. 서버에는 기기 토큰·허브 토큰만 | 허브 API·Claude Code에 노출 | compose secret |

어댑터는 같은 코드베이스의 공통 베이스 클래스를 상속하되, 프로세스는 각각 따로 뜬다. Claude Code는 어댑터를 `mcp__vooy__ask`, `mcp__muse__ask` 같은 툴 이름으로만 본다.

## 3. 메시지 흐름

메시지 한 건은 Claude Code 실행 한 번이다. 팬아웃·릴레이·토론의 차이는 그 실행 안에서 Claude Code가 툴을 어떤 순서로 부르느냐뿐이고, 허브와 러너 쪽 코드는 세 경우 모두 같다.

> 그림: 팬아웃 시퀀스 · 10단계 (원본은 claude.ai 문서에만 있음)

툴 호출(4·5)과 툴 결과(7·8)는 stream-json에 `tool_use`·`tool_result` 이벤트로 실시간으로 나오므로, 러너는 최종 텍스트를 기다리지 않고 각 원문을 도착 즉시 UI로 밀어준다. Claude의 정리 말풍선은 마지막 텍스트 블록이다.

**릴레이와 토론은 4–8만 다르다**

1. 릴레이: `mcp__vooy__ask` → 결과 받음 → 그 결과를 포함한 질문으로 `mcp__muse__ask` → 결과 → 정리. 툴 호출이 순차로 바뀔 뿐 이벤트 형태는 같다.
2. 토론: 릴레이를 라운드 상한(기본 3)까지 반복. 종료 판단은 시스템 프롬프트의 규칙(라운드 상한·시간 어림)과 전체 타임아웃으로 제한한다(턴 상한은 두지 않음, 04 참고).
3. 봇이 되물으면: 어댑터가 `needs_input: true`로 반환 → Claude Code가 세션 맥락으로 답할 수 있으면 다시 `ask`, 없으면 사용자에게 되묻고 실행 종료. 다음 메시지가 `--resume`으로 이어받는다.
4. 봇 장애: 어댑터가 `error: unavailable`을 반환 → Claude Code는 그 봇을 빼고 정리하며 이유를 밝힌다. 허브는 동시에 헬스체크 상태를 갱신한다.

## 4. Claude Code 호출 설계

러너는 메시지마다 아래 형태로 프로세스를 띄운다. 허브 세션 하나가 Claude Code 세션 하나에 1:1로 대응하고, 두 번째 메시지부터 `--resume`으로 이어받는다.

```bash
claude -p "<사용자 메시지>" \
  --resume <claude_session_id>              # 첫 메시지는 생략, 응답의 session_id 저장
  --output-format stream-json --verbose \
  --append-system-prompt-file ./orchestrator.md \
  --mcp-config ./mcp.json --strict-mcp-config \
  --allowedTools "mcp__vooy__ask,mcp__vooy__status,mcp__muse__ask,mcp__muse__status,..." \  # 가용 봇별로 러너가 생성
  --disallowedTools "Bash,Read,Write,Edit,WebSearch,WebFetch" \
  --model sonnet
```

| 항목 | 결정 | 이유 |
| --- | --- | --- |
| 세션 매핑 | 허브 세션 1 : Claude Code 세션 1, `--resume` | 봇 원문·되물음 맥락을 Claude가 직접 기억. 허브가 맥락을 재조립할 필요 없음 |
| 세션 수명 | 허브 세션 삭제 시 함께 폐기. 메시지 간 24시간 넘으면 새 세션 + 요약 주입 | Claude Code 세션 파일이 무한히 커지는 것 방지 |
| 시스템 프롬프트 | `--append-system-prompt-file`로 오케스트레이터 규칙 주입 (별도 문서) | Claude Code 기본 프롬프트는 코딩용이라 역할 재정의 필요 |
| 툴 제한 | `--strict-mcp-config` + `--allowedTools` 화이트리스트(`mcp.json`에 넣은 봇마다 `mcp__<bot>__ask`·`mcp__<bot>__status` 두 개, 러너가 `mcp.json`과 함께 생성), 파일·셸·웹 툴은 명시적 차단 | 오케스트레이터가 서버 파일을 건드리면 안 됨. 권한 프롬프트가 헤드리스에서 멈추는 것 방지 |
| 턴 상한 | 두지 않음(2026-11-16 개정, 처음엔 `--max-turns 12`). `RUNNER_MAX_TURNS`>0 이면 다시 켜진다 | 12는 정당한 게임·토론(4봇×4바퀴)을 정리 없이 끊었다. 긴 맥락은 Claude Code 자동 압축, 폭주는 재시도 금지·어댑터 cooldown·600초 타임아웃이 막는다 |
| 모델 | 기본 `sonnet`, 토론·복잡 비교만 `opus` 선택 가능 | 오케스트레이션은 판단 위주라 Sonnet으로 충분. 비용은 구독 한도 |
| 작업 디렉터리 | 빈 전용 디렉터리, `CLAUDE.md` 없음 | 프로젝트 지시가 섞이지 않게 |
| 타임아웃 | 프로세스 전체 600초. 초과 시 SIGTERM 후 UI에 부분 결과 + 실패 표시 | `ask` 한 번은 재시도 포함 `timeout_s`(기본 120) 안에 끝난다. 릴레이 2단·팬아웃은 여유 있게, 토론 6회는 봇 평균 응답 60초 기준으로 들어가는 값 |
| 동시 실행 | 허브 세션당 1개. 같은 세션에 메시지가 겹치면 큐잉 | `--resume` 세션 파일 동시 쓰기 방지 |

**stream-json 이벤트 → 허브 이벤트 변환**

| Claude Code 이벤트 | 허브 이벤트 | UI 표시 |
| --- | --- | --- |
| `system` (init, session\_id) | `run.started` {claude\_session\_id} | 헤더 "실행 중". session\_id 저장 |
| `assistant` 안 `tool_use` (`mcp__vooy__ask`) | `bot.pending` {bot: vooy} | "Vooy 응답 중…" 말풍선 자리 |
| `assistant` 안 `tool_use` (`mcp__<bot>__status`) | 없음 | 표시하지 않음 (`tool_call`에만 기록) |
| `user` 안 `tool_result` (`ask`) | `bot.reply` {bot, raw, structured} | 봇 말풍선 (원문) |
| `assistant` 안 `text` (중간) | `claude.note` | Claude 말풍선 ("Vooy 답을 뮤즈에 넘깁니다") |
| `result` (최종) | `claude.final` + `usage`, 이어서 `run.finished` {status: done} | Claude 정리 말풍선, 세션 통계 갱신 |
| `result` subtype error / 프로세스 비정상 종료 | `run.finished` {status: failed} | 오류 띠, 재시도 버튼 |
| 러너 전체 타임아웃 | `run.finished` {status: timeout} | 부분 결과 + 실패 띠 |

이벤트 type 목록과 봉투의 기준은 docs/05-ui.md 6장이다. 이 표와 다르면 05가 맞다.

**교체 가능성**: 러너는 `Runner` 인터페이스(`start(session, text) → AsyncIterator[HubEvent]`)로 추상화한다. 지인 공유로 전환하면 `ClaudeCodeRunner`를 `AnthropicApiRunner`(API 키 + 직접 tool-use 루프)로 바꾸고, 어댑터·허브·UI는 그대로 둔다.

## 5. MCP 어댑터 계층

어댑터는 봇 하나당 MCP 서버 하나다. Claude Code에는 HTTP 전송(streamable HTTP)으로 붙고, 허브 API에는 같은 프로세스가 여는 로컬 HTTP 포트로 헬스체크·관리 명령을 받는다.

**모든 어댑터가 노출하는 MCP 툴**

| 툴 | 입력 | 출력 | 비고 |
| --- | --- | --- | --- |
| `ask` | `text`, `thread_id`(허브 세션), `timeout_s`(기본 120), `reset` | `bot`, `ok`, `raw`(봇 원문), `structured`(버튼·링크·카드 목록), `needs_input`(bool), `error`, `latency_ms`, `message_ids` | 유일한 호출 경로. 큐에 들어가 순서대로 실행 |
| `status` | 없음 | `available`, `reason`, `last_ok_at`, `queue_depth`, `avg_latency_ms` | Claude가 호출 전 가용성 확인용 |

입출력 스키마의 기준은 docs/03-adapters.md 2·3장이다. 이 절은 요약이다.

툴 설명문(description)에는 봇의 특성을 적는다. 예: Vooy는 "한국 서비스 예약·주문·검색에 강함, 응답에 10~30초". Claude Code는 이 설명을 보고 봇을 고른다.

**허브 API가 쓰는 관리 엔드포인트 (HTTP, 로컬 전용)**

| 엔드포인트 | 용도 |
| --- | --- |
| `GET /health`, `POST /probe` | 가용성 + 마지막 응답 시각. 허브가 30초마다 `/health` 폴링, `/probe`는 실제 확인 |
| `POST /login/start`, `/login/submit`, `/login/complete` | 재로그인. 세 봇 모두 운영자가 noVNC로 수동 로그인 후 상태 저장(Vooy·피클은 에뮬레이터 화면, 뮤즈는 브라우저 화면) |
| `POST /reset` | 봇 대화 스레드 초기화 |
| `POST /enable`, `/disable` | 운영자 활성/비활성 토글 |
| `GET /threads`, `GET /logs`, `GET /images/<id>` | 디버깅, 봇 상태 패널 로그, 이미지 프록시 원본 |

전체 목록과 요청·응답 형식은 docs/03-adapters.md 5장이 기준이다.

**공통 베이스 클래스**

```python
class BotAdapter(ABC):
    name: str
    description: str          # MCP 툴 description에 들어감
    queue: asyncio.Queue      # 어댑터당 하나, 직렬 처리

    async def ask(self, text, thread_id, timeout_s=120, reset=False) -> AskResult
    async def status(self) -> Status
    async def reset(self, thread_id) -> None
```

`ask`는 큐에 넣고 결과를 기다리는 래퍼이고, 봇별 구현은 훅 다섯 개(`_ensure_ready`, `_send`, `_collect`, `_extract`, `_login_flow`)다. 시그니처와 계약은 docs/03-adapters.md 6장이 기준이다.

**봇별 구현 요점**

| 어댑터 | `_send` | `_collect` | 세션 보존 | 특이사항 |
| --- | --- | --- | --- | --- |
| Vooy | uiautomator2로 에뮬레이터 안 Vooy 앱 입력창에 `set_text`·전송 | 접근성 트리에서 `other-message-bubble` 새 노드 대기(testID가 `resource-id`로 노출) | AVD 디스크(스냅샷 불가) | 별도 워커, 전용 AVD, GPU 없이 `swiftshader`. 메시지 간격 최소 3초 |
| 뮤즈 | Playwright로 muse.ai 입력창에 타이핑·전송 | 응답 DOM 노드가 멈출 때까지 MutationObserver 폴링 | 브라우저 프로필 디렉터리 (쿠키) | 컨테이너 egress를 VPN 터널로. 지역 제약 시 Meta Model API 대체 어댑터로 교체 |
| 피클 | uiautomator2로 에뮬레이터 안 피클 앱 입력창 조작(한글은 `send_keys`) | 접근성 트리에서 새 말풍선 노드 등장 대기, OCR은 최후 수단 | 에뮬레이터 스냅샷 | 별도 워커. 상시 vs 온디맨드는 미결 |
| Mock | 설정된 지연 후 템플릿 응답 | 즉시 | 없음 | `fail_rate`, `needs_input_rate`로 장애·되물음 시뮬레이션 |

**실패 처리**: `_collect` 타임아웃 → `timeout_s` 안에서 1회 재시도 → 실패 시 `AskResult(error="timeout")` 반환. 로그인 만료 감지 → `available=false` 전환 + 허브에 알림. Claude Code에는 항상 정상 응답 형태로 돌려주고 오류는 필드로 표현한다. MCP 레벨 예외는 Claude Code 실행 자체를 멈출 수 있기 때문이다.

## 6. 세션·상태·데이터 모델

허브 세션이 기준 단위다. 세션 하나에 Claude Code 세션 ID 하나, 어댑터별 봇 스레드 ID가 매달린다. 봇 스레드는 어댑터가 관리하고, 허브는 ID만 안다.

**테이블 (PostgreSQL)**

| 테이블 | 주요 컬럼 | 비고 |
| --- | --- | --- |
| `session` | id, title, claude\_session\_id, created\_at, last\_message\_at, archived | claude\_session\_id는 첫 실행 후 채움 |
| `message` | id, session\_id, role(user·claude·bot·system), bot\_name, raw, structured(jsonb), seq, created\_at | 봇 원문과 Claude 정리 모두 message 행. seq로 표시 순서 고정 |
| `run` | id, session\_id, user\_message\_id, status(running·done·failed·timeout), started\_at, ended\_at, usage(jsonb), cost\_usd, error | Claude Code 실행 1회 = run 1행 |
| `tool_call` | id, run\_id, bot\_name, input, output(jsonb), latency\_ms, error, started\_at | 어댑터 호출 로그. 90일 보존 |
| `bot` | name, enabled, available, reason, last\_ok\_at, updated\_at | 헬스체크 결과 캐시 |
| `bot_thread` | session\_id, bot\_name, thread\_ref | 어댑터가 세션별 대화 스레드를 구분하는 키 |

**상태 머신 — run**

`queued → running → done | failed | timeout`. 같은 세션에 run이 running이면 새 메시지는 queued로 대기. UI는 queued 상태를 "이전 요청 처리 중"으로 표시한다.

**상태 머신 — bot**

`enabled` (운영자 토글)와 `available` (헬스체크 결과)은 독립이다. Claude Code에 노출되는 툴 목록은 둘 다 참인 봇만 포함하도록, 러너가 실행 직전에 `mcp.json`을 생성한다. 비활성 봇은 Claude가 아예 볼 수 없으므로 "없는 봇을 부르는" 실수가 안 생긴다.

**자격증명 보관**

| 자격증명 | 위치 | 형태 |
| --- | --- | --- |
| Claude Code 로그인 | 러너 컨테이너의 `~/.claude` 볼륨 | Claude Code가 관리. 러너 외 접근 불가 |
| Vooy 로그인 | Vooy 워커의 AVD 디스크(userdata) | AVD 디렉터리, 볼륨 암호화 |
| 뮤즈 쿠키 | 뮤즈 어댑터 볼륨 | Playwright 프로필 디렉터리 |
| 피클 로그인 | 피클 워커의 에뮬레이터 스냅샷 | AVD 스냅샷 파일 |
| 허브 로그인 | PostgreSQL `operator` 테이블 | bcrypt 해시 + TOTP 시크릿 |

DB에는 자격증명을 넣지 않는다. 어댑터 볼륨은 어댑터 컨테이너만 마운트한다.

**보존 정책**: `message`·`session`은 영구, `tool_call`·`run`은 90일 후 집계값만 남기고 삭제. 봇 원문에 개인정보가 섞일 수 있으니 `message.raw`는 운영자 로그인 뒤에만 조회된다.

## 7. 배포 구성

> **ADR-9로 바뀜(2026-11-11)**: 에뮬레이터 VM(VM 2)·WireGuard·noVNC·뮤즈 VPN 사이드카는 쓰지 않는다. 앱 서버(VM 1) 한 대에 docker compose로 전부 올리고 `phone-gateway` 컨테이너를 더한다. Caddy는 443에서 웹·허브 API와 함께 `/phone/ws`를 게이트웨이로 넘긴다. 봇 앱은 사용자 휴대폰(전용 공기계 권장)에서 돌고, 휴대폰 워커가 일반 인터넷으로 바깥 연결한다. 아래 VM 2·WireGuard·noVNC 서술은 이력으로 남긴다. 상세: docs/proposals/phone-worker.md 4·7장.

(이력) 앱 서버 한 대에 docker compose로 전부 올리고, 안드로이드 에뮬레이터(Vooy·피클 각 1개)만 KVM이 되는 별도 VM에 둔다. 일반 클라우드 VM은 중첩 가상화가 안 되어 에뮬레이터가 돌지 않거나 매우 느리므로, 베어메탈이나 nested-virt 지원 인스턴스를 골라야 한다.

> 그림: 배포 구성 · VM 2대, 컨테이너 8개 (원본은 claude.ai 문서에만 있음)

| 서버 | 사양 목표 | 이유 |
| --- | --- | --- |
| VM 1 | 2 vCPU, 4GB RAM, 40GB | Playwright 브라우저 1개 + Claude Code 프로세스 + DB. 피클 제외 시 여유 |
| VM 2 | 8 vCPU, 12GB RAM, KVM 지원, GPU 불필요 | 에뮬레이터 2개(Vooy·피클, 각 RAM 4GB) 상시 구동 기준. `swiftshader`라 CPU를 더 쓴다. 온디맨드로 가면 더 작은 사양 가능 |

**네트워크**

- 공개 포트는 caddy의 443 하나. 어댑터·DB·러너는 compose 내부망에만 있다.
- VM 1 ↔ VM 2는 WireGuard로 묶고, Vooy·피클 어댑터의 MCP 포트는 그 사설망 IP로만 열린다.
- 뮤즈 어댑터는 VPN 사이드카 컨테이너의 네트워크 네임스페이스를 공유해 egress만 해외로 나간다. 다른 컨테이너는 영향 없음.
- Claude Code의 MCP 설정은 stdio가 아니라 HTTP 전송을 쓴다. 어댑터가 별도 컨테이너·별도 VM에 있기 때문이다.

**운영**

- 로그인이 필요한 컨테이너(runner, adapter-vooy, adapter-muse, adapter-pickle)는 재시작해도 볼륨의 로그인 상태가 유지된다. 이미지 교체만으로 재로그인이 필요하지 않게 한다.
- 수동 로그인이 필요할 때(뮤즈 쿠키 만료, Vooy·피클 재인증)는 허브의 재로그인 버튼이 해당 어댑터의 noVNC 화면을 열어준다(내부망, 허브 로그인 뒤에만).
- Claude Code 버전은 러너 이미지에 고정하고, 업데이트는 미리 테스트 후 이미지 재빌드로만.

## 8. 기술 스택

| 영역 | 선택 | 근거 | 대안을 버린 이유 |
| --- | --- | --- | --- |
| 백엔드 언어 | Python 3.12 | Playwright·uiautomator2·MCP SDK가 전부 1급 지원 | Spring Boot: Playwright Java는 되지만 MCP 서버 SDK와 안드로이드 자동화 생태계가 얇음. 허브 코어만 나중에 Java로 옮기는 건 가능 |
| 허브 API | FastAPI + uvicorn | async 기본, WebSocket 내장, Pydantic으로 이벤트 스키마 공유 | Django: 동기 중심, 과함 |
| 러너 | asyncio subprocess | stream-json을 줄 단위로 비동기 파싱 | Claude Agent SDK: API 키 전제라 구독 경로와 안 맞음 |
| MCP 서버 | 공식 Python MCP SDK (`mcp` 패키지), streamable HTTP 전송 | 별도 컨테이너·VM에서 돌리려면 HTTP 필요 | stdio: 러너와 같은 프로세스 공간이어야 해서 격리 불가 |
| DB | PostgreSQL 16 + SQLAlchemy async + Alembic | jsonb로 봇 structured 응답 저장, 익숙한 스택 | SQLite: 단일 사용자라 충분하지만 jsonb 질의·백업 편의에서 밀림 |
| 프론트 | React 18 + Vite + TypeScript, Tailwind | 진행 중인 React 작업과 동일 스택 | Next.js: SSR 불필요 |
| 실시간 | WebSocket (허브 → UI), 이벤트 스키마는 Pydantic → TS 타입 생성 | 봇별 진행 상태 푸시에 필수 | SSE: 단방향이라 큐잉 취소 등 역방향 명령에 불편 |
| 브라우저 자동화 | Playwright (Chromium, persistent context) | 프로필 디렉터리로 로그인 유지, 안정적 셀렉터 | Selenium: 느리고 프로필 관리 번거로움 |
| 모바일 자동화 | Python `uiautomator2`, Android 에뮬레이터 (x86\_64, API 34, `swiftshader`) | 접근성 트리로 텍스트 추출, `set_text`·`send_keys`로 한글 입력(P0 확인), Appium 서버 불필요 | Appium: 서버 프로세스가 하나 더 늘고 같은 UiAutomator2를 감싼 것뿐. ADB만: 한글 입력 불가, 텍스트 추출에 OCR 필요. Vooy 텔레그램 경로(Telethon): 전용 계정·계정 정지 위험, ADR-8 |
| 리버스 프록시 | Caddy | 자동 TLS, 설정 짧음 | nginx: certbot 별도 |
| 배포 | docker compose, VM 2대 | 단일 사용자 규모에 맞는 최소 구성 | k8s: 과함 |
| 비밀 관리 | 암호화 볼륨 + compose secrets | 사용자 1명, 외부 KMS 불필요 | Vault: 과함 |

개발·테스트는 Mock 어댑터와 `claude -p` 실제 호출로 로컬에서 끝까지 돌릴 수 있게 한다. 실제 어댑터는 각각 독립 레포 디렉터리라 하나씩 붙인다.

## 9. 설계 결정 기록과 미결 사항

| ADR | 결정 | 배경 | 결과·대가 |
| --- | --- | --- | --- |
| ADR-1 | Claude 호출은 Claude Code 헤드리스, 러너 인터페이스로 격리 | 내 구독을 쓰면서 약관 안에 있는 유일한 경로 | 지인 공유 시 러너만 교체. Claude Code 버전 고정 필요 |
| ADR-2 | 허브 세션 : Claude Code 세션 = 1:1, `--resume` | 봇 원문·되물음 맥락을 Claude가 직접 들고 있어야 릴레이가 자연스러움 | 세션 파일 비대화 → 24시간 룰로 분할 |
| ADR-3 | 어댑터는 MCP 서버, 전송은 HTTP | 컨테이너·VM 분리와 Claude Code 네이티브 툴 호출을 동시에 만족 | stdio 대비 인증 레이어 필요(내부망 + 토큰 헤더) |
| ADR-4 | 어댑터 오류는 예외가 아니라 결과 필드 | MCP 예외는 Claude Code 실행 전체를 멈춤 | Claude가 오류 필드를 읽도록 시스템 프롬프트에 명시 |
| ADR-5 | 허브 API는 Claude Code를 우회해 어댑터 헬스체크 | 봇 상태 패널은 Claude 실행 없이도 갱신돼야 함 | 어댑터에 MCP 외 HTTP 관리 엔드포인트 추가 |
| ADR-6 | 비활성·불가용 봇은 `mcp.json`에서 제외 | Claude가 없는 봇을 부르는 경우를 원천 차단 | 실행마다 mcp.json 생성 |
| ADR-7 | (ADR-9로 대체) 에뮬레이터는 별도 VM | 일반 클라우드 VM에서 nested-virt 불가 | VM 2대, WireGuard 관리 |
| ADR-8 | Vooy는 텔레그램이 아니라 앱 경로, 봇마다 전용 AVD | P0에서 앱 경로가 GPU 없이(`swiftshader`) 한글 송수신까지 동작. 텔레그램은 전용 계정·유저 세션 자동화 정지 위험 | VM 2 사양 증가, 응답 감지가 이벤트가 아닌 폴링, 앱 업데이트에 취약. 에뮬레이터 하나를 공유하면 동시 질문이 직렬화되고 스냅샷이 막혀 AVD를 나눈다 |
| ADR-9 | 봇 앱은 사용자 휴대폰의 워커 앱이 접근성 서비스로 조종하고, 워커는 허브의 폰 게이트웨이에 아웃바운드 WebSocket으로 붙는다. 어댑터는 게이트웨이에 있고 MCP 계약은 그대로 | 2026-11-11 실기기 측정: ask 1건이 에뮬레이터 약 2분 → 휴대폰 약 30초, 세 앱 모두 접근성 입력·전송·답 알림 동작, 에뮬레이터 VM·VPN 불필요(docs/proposals/phone-worker.md 2장) | 휴대폰이 켜져 있고 잠금이 풀려 있어야 함(전용 공기계 권장), 사이드로드 APK와 접근성 권한 관리, 허브 공개 주소 필요, 화면 임대로 봇 간 화면 사용 직렬화 |

**미결 사항**

- [ ] 에뮬레이터 상시 구동 vs 온디맨드(스냅샷 복원 30\~60초 지연 허용?) — 피클 어댑터 착수 전 결정
- [ ] 뮤즈 지역 제약 해소 방법(해외 계정 + VPN) 확보 여부 — 안 되면 Meta Model API 대체 어댑터로 설계 변경
- [ ] Claude Code 헤드리스 개인 자동화 범위, Claude Code 약관 페이지에서 직접 확인 (요구사항 미결과 동일)
- [ ] `--append-system-prompt-file` 등 플래그가 고정할 Claude Code 버전에서 그대로 유효한지 설치 후 `claude --help`로 검증
- [ ] 봇 structured 응답(버튼·카드) 스키마 — 어댑터 명세 문서에서 확정
- [ ] 24시간 세션 분할 시 요약 주입 방식 — 오케스트레이터 설계 문서에서 확정

**다음 문서**: 어댑터 인터페이스 명세(툴 스키마·structured 스키마·봇별 로그인 흐름), 오케스트레이터 설계(시스템 프롬프트 초안·툴 선택 규칙·종료 조건).
