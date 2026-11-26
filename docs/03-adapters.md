# Roundtable-Agent 어댑터 인터페이스 명세

> 작성일 2026-11-08

## 1. 목적과 범위

이 문서는 Claude Code가 보는 어댑터의 MCP 툴 계약, 허브 API가 쓰는 관리 엔드포인트, 어댑터 구현자가 따를 베이스 클래스와 봇별 구현 요점을 확정한다. 이 문서대로 만들면 어댑터는 서로를 몰라도 되고, 봇 추가는 어댑터 하나 추가로 끝난다.

범위 안: `ask`·`status` 툴, `AskResult`·`structured` 스키마, 생명주기와 타임아웃, 관리 엔드포인트, Vooy·뮤즈·피클·Mock 네 어댑터의 설계.

범위 밖: Claude Code의 시스템 프롬프트와 봇 선택 규칙(오케스트레이터 설계 문서), 허브 UI(UI 설계 문서), 배포 세부(아키텍처 설계서 7장).

언어·런타임: Python 3.12, 공식 `mcp` SDK, streamable HTTP 전송. 어댑터 하나 = 컨테이너 하나 = 프로세스 하나.

## 2. MCP 툴 계약

어댑터마다 MCP 서버 이름은 봇 이름(`vooy`, `muse`, `pickle`, `mock`)이고, Claude Code에는 `mcp__<bot>__ask`, `mcp__<bot>__status`로 보인다. 툴은 이 둘뿐이다.

**`ask` — 봇에게 묻고 답을 받는다**

```json
{
  "name": "ask",
  "description": "<봇 설명: 잘하는 일, 못하는 일, 평균 응답 시간, 한국어 가능 여부>",
  "inputSchema": {
    "type": "object",
    "required": ["text", "thread_id"],
    "properties": {
      "text":       {"type": "string", "maxLength": 4000},
      "thread_id":  {"type": "string", "description": "허브 세션 ID. 같은 ID면 같은 봇 대화를 잇는다"},
      "timeout_s":  {"type": "integer", "default": 120, "minimum": 10, "maximum": 240},
      "reset":      {"type": "boolean", "default": false, "description": "true면 봇 대화를 새로 시작한 뒤 보낸다"},
      "attachments": {"type": "array", "maxItems": 5,
                      "items": {"type": "string", "pattern": "^f_[A-Za-z0-9_-]{16,64}$"},
                      "description": "허브 파일 ID. 사용자 글의 [첨부 파일] 목록이나 다른 봇 답의 structured.files[].id"}
    }
  }
}
```

출력(`AskResult`)은 항상 성공 응답 형태로 돌려준다. 오류도 필드다.

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `bot` | string | 봇 이름 |
| `ok` | bool | 봇 응답을 받았으면 true. 타임아웃·장애면 false |
| `raw` | string | 봇 원문. 여러 메시지면 `\n\n`으로 이어 붙임. ok=false면 빈 문자열 |
| `structured` | object | 3장 스키마. 없으면 `{}` |
| `needs_input` | bool | 봇이 되물었다고 판단되면 true (물음표로 끝나거나 선택지 버튼이 있을 때) |
| `error` | string \| null | `timeout` · `unavailable` · `login_required` · `rate_limited` · `internal` · `unsupported`(받지 못하는 첨부) |
| `latency_ms` | int | 전송부터 응답 확정까지 |
| `message_ids` | string\[\] | 어댑터 내부 메시지 식별자. 디버깅용 |

**`status` — 호출 전 가용성 확인**

입력 없음. 출력:

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `available` | bool | 지금 `ask`가 성공할 가능성이 있는지 |
| `reason` | string \| null | 불가 사유. `login_required` · `disabled` · `backend_down` · `cooldown` |
| `last_ok_at` | string \| null | ISO 8601 |
| `queue_depth` | int | 대기 중 요청 수 |
| `avg_latency_ms` | int | 최근 10건 평균 |

**규칙**

- `text`는 그대로 봇에 전달한다. 어댑터는 내용을 고치지 않는다. 봇 이름·맥락을 붙이는 건 Claude Code 몫이다.
- `thread_id`당 봇 대화 하나. 어댑터가 처음 보는 `thread_id`면 새 봇 대화를 만든다.
- 같은 어댑터에 동시 `ask`가 오면 큐에서 순서대로 처리한다. Claude Code가 병렬로 불러도 어댑터 안에서는 직렬이다.
- MCP 레벨 예외를 던지지 않는다. 던지면 Claude Code 실행이 통째로 실패한다.
- 두 툴 모두 `readOnlyHint: true` 힌트를 단다. 없으면 Claude Code 가 여러 봇의 `ask` 를 한 번에 하나씩 실행한다(2026-11-26).

**첨부** (2026-11-26 추가, 승인된 계약 확장)

- 파일은 허브 파일 보관함(`hub/app/files.py`)에 두고 ID(`f_…`)로만 주고받는다. 사용자가 웹에서 붙인 파일은 허브가 러너에 넘기는 글 끝에 `[첨부 파일]` 목록(ID·이름·종류·크기)으로 붙는다.
- `ask` 의 `attachments` 에 ID 를 넣으면 어댑터가 허브 `GET /internal/files/<id>`(X-Hub-Token)로 받아 `_attach` 훅에 넘기고, 이어서 `text` 를 보낸다. 재시도 때는 글만 다시 보낸다.
- 첨부를 받는 봇은 `accepts_files`(MIME 앞부분, 예: `image/`. `*` 는 모든 종류)를 채운다. 피클·뮤즈는 `*` 이고, 봇 앱의 공유하기가 그 종류를 받지 않으면 `unsupported`. 비어 있는 봇에 첨부를 넣거나 받지 않는 종류면 `ok=false, error=unsupported`. `ask` 툴 설명 끝에 받는 종류(또는 받지 않음)를 베이스가 붙인다.
- 봇이 만든 파일은 어댑터가 허브 `POST /internal/files`(X-Hub-Token, X-Bot, X-File-Name)로 올리고 `structured.files[]` 로 돌려준다. Claude 는 그 ID 를 다음 봇의 `attachments` 로 넘겨 릴레이한다.
- 2026-11-27 첨부를 받는 어댑터: Mock, 휴대폰 봇 피클·뮤즈(config.yaml `android.accepts_files`). Vooy·Cue 는 아직 받지 않는다.
- 휴대폰 봇 넣기: 어댑터가 파일을 폰 게이트웨이에 잠깐 맡기고(`POST /files`), 워커가 기기 토큰으로 받아(`/phone/files/<code>`) 공유하기(ACTION_SEND)로 봇 앱에 넘긴 뒤 글을 보낸다. 공유 뒤 중간 화면은 selectors.yaml `share_steps` 로 넘긴다.
- 휴대폰 봇 가져오기(`android.collect_files`): 질문하는 동안 그 앱이 휴대폰에 저장한 파일(사진·영상·소리, 내려받기·문서 폴더. MediaStore 소유 앱 또는 내려받기 관리자)을 워커가 게이트웨이로 올리고(`POST /phone/files`) 어댑터가 받아 허브에 올린다. 봇 앱은 결과를 채팅에만 보여 주므로(2026-11-27 노트9: 피클·뮤즈 모두), 답을 읽은 뒤 어댑터가 내 말풍선 아래의 큰 그림을 눌러 크게 보기를 열고 앱의 저장 버튼(selectors.yaml `save_media`: 피클 "사진 저장", 뮤즈 "다운로드")을 누른 다음 저장된 파일을 가져온다.

## 3. structured 응답 스키마

봇 응답에서 텍스트 외의 요소를 잃지 않기 위한 공통 스키마다. 어댑터가 뽑을 수 있는 것만 채우고, 못 뽑는 필드는 생략한다. UI는 이 스키마로 봇 말풍선 안에 버튼·카드를 그린다.

```json
{
  "buttons": [{"label": "예약 확정", "action_id": "btn_3", "kind": "callback|url|reply"}],
  "links":   [{"title": "네이버 예약", "url": "https://..."}],
  "cards":   [{"title": "OO식당", "subtitle": "강남 · 한식", "body": "18:30 2명 가능", "url": "https://...", "image_url": "https://..."}],
  "images":  [{"url": "https://...", "alt": "메뉴 사진"}],
  "files":   [{"id": "f_…", "name": "clip.mp4", "mime": "video/mp4", "size": 1048576}],
  "choices": ["18:00", "18:30", "19:00"],
  "confirmation_required": {"summary": "KTX 서울→부산 11/12 07:00 결제 58,800원", "action_id": "btn_pay"}
}
```

| 필드 | 출처 | 비고 |
| --- | --- | --- |
| `buttons` | 앱 화면의 버튼 노드, 웹 응답의 버튼 요소 | `action_id`는 어댑터가 만든 식별자. 후속 `ask`의 `text`에 `[tap:btn_3]`을 넣으면 어댑터가 그 버튼을 누른다 |
| `links` | 메시지 안 URL | 본문에서 추출 |
| `cards` | 앱의 리스트 아이템, 웹 응답의 카드 요소 | 식당·상품·항공권 같은 선택지 |
| `images` | 이미지 첨부 | URL은 어댑터가 자기 스토리지에 복사한 로컬 경로. 외부 URL 그대로 노출 안 함 |
| `files` | 봇이 만든 파일(영상·문서 등) | 허브 파일 보관함에 올린 뒤의 ID. 다른 봇의 `attachments` 로 그대로 넘긴다. `images`(말풍선 캡처)와 달리 원본 파일 |
| `choices` | 되물음의 선택지 (버튼이 아닌 텍스트 나열) | `needs_input=true`와 함께 |
| `confirmation_required` | 결제·예약 확정 단계 | UI가 눈에 띄게 표시. Claude Code는 이 단계를 절대 자동으로 넘기지 않고 사용자에게 묻는다 |

**버튼 누르기**: 별도 툴을 두지 않는다. `ask`의 `text`가 `[tap:<action_id>]`로 시작하면 어댑터가 해당 버튼을 누르고 나머지 텍스트가 있으면 이어서 보낸다. `action_id`는 그 `thread_id`의 직전 응답에서만 유효하다. 툴 하나로 유지하면 Claude Code의 허용 툴 목록이 늘지 않는다.

**원문 보존 원칙**: `raw`에는 버튼 라벨도 `[버튼: 예약 확정]` 형태로 섞어 넣는다. `structured`를 못 쓰는 상황에서도 Claude가 원문만 보고 상황을 파악할 수 있게 한다.

## 4. ask 생명주기

> 그림: ask 생명주기 · 5상태 (원본은 claude.ai 문서에만 있음)

| 상태 | 하는 일 | 시간 제한 |
| --- | --- | --- |
| queued | 어댑터 큐에서 차례 대기. `status.queue_depth`에 반영 | `timeout_s`에 포함. 큐에서만 60초 넘으면 `error=rate_limited`로 실패(`status.reason`은 `cooldown`) |
| sending | `_send` 훅 실행. 로그인 상태 확인 포함 | 15초 |
| waiting | `_collect` 훅. 봇 메시지가 멈출 때까지 수집 | `timeout_s` 잔여분 (재시도 포함) |
| done | `AskResult(ok=true)` 반환. `needs_input` 판정 | — |
| failed | `AskResult(ok=false, error=...)` 반환 | — |

**응답 확정 규칙** (`_collect` 공통, 베이스가 적용): 첫 메시지가 온 뒤 `quiet_s`(기본 2.5초) 동안 새 메시지가 없으면 응답이 끝난 것으로 본다. 봇이 "찾는 중…" 같은 진행 메시지를 보내면 봇별 `progress_patterns`에 맞춰 무시하고 계속 기다린다. 타이핑 표시(웹 스피너, 앱의 응답 중 표시)가 있으면 그게 꺼지는 시점을 우선한다.

**재시도**: waiting 1회차가 응답 없이 끝나면 같은 `text`를 한 번 더 보낸다. 1회차는 waiting 진입 시점 잔여 시간의 절반까지만 기다리고, 재시도는 나머지 안에서 끝낸다. `ask` 한 번은 큐 대기·재시도를 포함해 절대 `timeout_s`를 넘지 않는다. 단, `[tap:]`으로 시작하는 버튼 누르기와 `confirmation_required` 단계의 확정 메시지는 재시도하지 않는다. 중복 결제·예약을 막기 위해서다.

**재시도 (휴대폰 경로)**: 휴대폰 워커로 앱을 다루는 어댑터는 절반 시점에 무조건 다시 보내지 않는다. 생각이 긴 질문에 같은 질문이 두 번 가기 때문이다. 보낸 뒤 "평소 응답 시간(최근 답 10개가 끝난 시각의 중간값, 없으면 `expect_reply_s`) + `verify_margin_s`"(상한 `notify_fallback_s`)까지 답 알림이 없으면 앱을 열어 본다. 보낸 질문이 화면에 있거나 화면이 보내기 전과 달라졌으면 그대로 답을 읽고, 질문이 없고 화면도 그대로일 때만 한 번 다시 보낸다(`SendLost`). 전송 버튼을 누른 뒤 확인은 짧게(약 1초) 하고, 확인이 안 돼도 다시 누르지 않는다. 느린 휴대폰은 화면 정보가 늦게 바뀌어 다시 누르면 중복 전송이 된다. `timeout_s` 상한과 `[tap:]`·확정 메시지 예외는 같다.

**needs\_input 판정**: `choices`나 `buttons`가 있거나, 원문 마지막 문장이 물음표로 끝나거나, 봇별 `question_patterns`에 맞으면 true. 오판은 허용한다. Claude Code가 원문을 읽고 최종 판단한다.

## 5. 관리 HTTP 엔드포인트

MCP와 같은 프로세스가 별도 포트(기본 9100)로 연다. 허브 API 전용이며 Claude Code는 모른다. 인증은 compose secret으로 주입한 공유 토큰을 `X-Hub-Token` 헤더로 보낸다. MCP 포트(기본 9000)도 같은 토큰을 요구해 내부망 안 다른 컨테이너의 무단 호출을 막는다.

| 메서드·경로 | 요청 | 응답 | 동작 |
| --- | --- | --- | --- |
| `GET /health` | — | `status` 툴과 같은 JSON + `version`, `uptime_s` | 허브가 30초마다 폴링. 봇 백엔드에 실제 핑은 하지 않고 마지막 결과를 돌려준다 |
| `POST /probe` | — | `health`와 같음 | 봇 백엔드에 실제로 가벼운 확인(페이지 로드, 앱 포그라운드)을 한 뒤 응답. 허브의 "상태 새로고침" 버튼 |
| `POST /login/start` | `{}` | `{"flow": "code" \| "vnc", "hint": "...", "vnc_url": "..."}` | 로그인 흐름 시작. 현재 세 봇(Vooy·뮤즈·피클) 모두 noVNC URL 반환. 코드 입력형(`code`)은 인증코드를 받는 봇을 위해 남겨 둔다 |
| `POST /login/submit` | `{"code": "12345"}` 또는 `{"password": "..."}` | `{"ok": true}` 또는 `{"ok": false, "next": "password"}` | 코드 입력형 로그인의 다음 단계 |
| `POST /login/complete` | `{}` | `{"ok": bool}` | VNC 수동 로그인이 끝났다고 운영자가 알림. 어댑터가 로그인 상태를 저장(쿠키·스냅샷)하고 `available` 갱신 |
| `POST /reset` | `{"thread_id": "..."}` 또는 `{}` | `{"ok": true}` | 특정 봇 대화 초기화. 빈 바디면 전체 |
| `POST /enable`, `POST /disable` | `{}` | `{"enabled": bool}` | 운영자 토글. disable이면 `status.reason=disabled` |
| `GET /threads` | — | `[{"thread_id", "last_at", "message_count"}]` | 디버깅·정리용 |
| `GET /logs?since=` | — | 최근 `ask` 기록 | 허브의 봇 상태 패널 상세 |
| `GET /images/<id>` | — | 이미지 바이너리 (`Content-Type` 원본 유지) | `structured.images[]`·`cards[].image_url`의 로컬 파일. 허브가 `GET /api/bots/<bot>/images/<id>`로 프록시한다(UI 설계 3장) |

**상태 보고 의무**: 어댑터가 `login_required`·`backend_down`을 감지하면 기다리지 않고 허브의 `POST /internal/bot-status` 웹훅을 호출한다. 폴링 간격보다 빨리 UI에 반영하기 위해서다. 웹훅 실패는 무시한다(다음 폴링이 잡는다).

## 6. 공통 베이스 클래스와 설정 파일

베이스 클래스가 큐·타임아웃·재시도·`AskResult` 조립·MCP 서버·관리 HTTP를 전부 맡는다. 봇별 구현은 훅 다섯 개만 채운다.

```python
# adapters/base.py
class BotAdapter(ABC):
    name: str                       # "vooy"
    description: str                # ask 툴 description
    quiet_s: float = 2.5            # 응답 확정 침묵 시간
    progress_patterns: list[str] = []   # 무시할 진행 메시지 정규식
    question_patterns: list[str] = []   # needs_input 힌트 정규식
    min_interval_s: float = 1.0     # 연속 전송 최소 간격
    accepts_files: tuple[str, ...] = ()  # 받는 첨부 MIME 앞부분. 비면 첨부는 error=unsupported

    # --- 봇별 구현 훅 ---
    async def _ensure_ready(self) -> None: ...            # 로그인 상태 확인, 아니면 LoginRequired 발생
    async def _send(self, thread: Thread, text: str, tap: str | None) -> None: ...
    async def _collect(self, thread: Thread, deadline: float) -> list[RawMessage]: ...
    async def _extract(self, msgs: list[RawMessage]) -> tuple[str, Structured]: ...
    async def _login_flow(self) -> LoginFlow: ...        # CodeLogin | VncLogin
    async def _attach(self, thread: Thread, files: list[Attachment]) -> None: ...  # 선택: 첨부를 봇 대화에 넣기

    # --- 베이스가 제공 ---
    async def ask(self, text, thread_id, timeout_s=120, reset=False, attachments=None) -> AskResult
    async def publish_file(self, data: bytes | Path, name: str, mime: str) -> FileRef  # 봇이 만든 파일 → 허브
    async def status(self) -> Status
    async def reset(self, thread_id: str | None) -> None
    def serve(self) -> None            # MCP(9000) + 관리 HTTP(9100) 동시 기동
```

`Thread`는 어댑터가 `thread_id`에 매핑해 보관하는 봇 쪽 대화 핸들이다(Vooy는 마지막 수집 말풍선의 해시·시각, 뮤즈는 브라우저 탭·대화 URL, 피클은 앱의 대화 화면 식별자). 디스크에 JSON으로 저장해 재시작 후에도 이어진다.

**`_collect` 계약**: `deadline`까지 새 메시지를 모아 반환한다. 베이스가 `quiet_s` 침묵 판정과 `progress_patterns` 필터를 적용하므로 훅은 "지금까지 도착한 메시지"만 성실히 반환하면 된다.

**설정 파일** (`adapters/<bot>/config.yaml`, 비밀값은 env로):

```yaml
name: vooy
description: |
  한국 생활 서비스(예약·주문·검색·일정)에 강한 AI 에이전트. 앱으로 응답하며
  보통 10~30초 걸린다. 한국어 전용. 결제·예약 확정 전에 반드시 확인을 요구한다.
timeouts: {default_s: 120, send_s: 15, quiet_s: 3}
queue: {max_depth: 5, max_wait_s: 60}
progress_patterns: ["^찾는 중", "^잠시만", "^확인하고 있"]
question_patterns: ["몇 명", "언제", "어디", "\\?$"]
health: {probe_interval_s: 300}
hub_webhook: http://hub-api:8000/internal/bot-status
# hub_files: http://hub-api:8000/internal/files   # 비면 hub_webhook 주소에서 만든다(env HUB_FILES_URL)
```

`description`은 그대로 MCP 툴 description이 된다. 봇의 강점·약점·응답 시간·언어·확정 정책을 적어야 Claude Code가 봇을 고를 수 있다.

**디렉터리**

```
adapters/
  base.py            # BotAdapter, AskResult, Structured, Thread, serve()
  android_base.py    # 접근성 트리·한글 입력·휴대폰 경로 흐름 (Vooy·피클·뮤즈 공통)
  phone/             # 폰 게이트웨이(gateway.py), PhoneDevice(device.py), 휴대폰 명령 규약(protocol.py)
  phonechat.py       # 범용 채팅 앱 어댑터(피클·뮤즈). 앱별 차이는 <bot>/selectors.yaml·config.yaml
  mcp_server.py      # ask/status 툴 등록, streamable HTTP
  admin_api.py       # 관리 HTTP
  vooy/  {adapter.py, config.yaml, Dockerfile}
  muse/  {adapter.py, config.yaml, Dockerfile}
  pickle/{adapter.py, config.yaml, Dockerfile}
  mock/  {adapter.py, config.yaml, Dockerfile}
```

## 7. Vooy 어댑터

> **ADR-9(2026-11-11)**: 운영 기기는 에뮬레이터가 아니라 사용자 휴대폰이다. 어댑터는 `ANDROID_DEVICE_FACTORY=adapters.phone.device:PhoneDevice`로 폰 게이트웨이를 거쳐 휴대폰을 조종하고, 흐름은 "보내기 → 홈으로 나가기 → 답 알림 대기 → 앱을 열어 읽기 → 홈"이다(docs/proposals/phone-worker.md 6장). 프로브는 앱을 열지 않고 휴대폰 연결·잠금만 본다. 로그인은 사람이 휴대폰에서 직접 하고 `/login/start`는 안내만 돌려준다. 휴대폰 Vooy는 한국어 UI라 입력창·전송 버튼 라벨이 "메시지 입력"·"메시지 전송"이다(`selectors.yaml`이 둘 다 받는다). 아래 에뮬레이터 항목은 개발용 경로로 남긴다.

안드로이드 에뮬레이터 안의 Vooy 앱(`com.vooy.mobile`)을 조작한다. 처음 설계는 텔레그램 유저 세션(Telethon)이었으나, P0에서 앱 경로가 GPU 없는 에뮬레이터에서도 한글 송수신까지 동작함을 확인해 앱 경로로 바꿨다(요구사항 7장 ①). 전용 텔레그램 계정·`api_id` 발급·유저 세션 자동화의 계정 정지 위험이 없어진다. 에뮬레이터 기동·접근성 트리 읽기·한글 입력은 피클과 공통이므로 `adapters/android_base.py`로 묶어 두 어댑터가 함께 쓴다. 가장 먼저 만드는 실봇 어댑터이고, 피클 어댑터의 기준 구현이 된다.

| 항목 | 설계 |
| --- | --- |
| 에뮬레이터 | 어댑터 전용 AVD 1개(피클과 공유하지 않음). API 34 Google Play x86\_64, `-gpu swiftshader_indirect`(GPU 불필요). Vooy는 React Native + Skia라 GPU를 끈(`hw.gpu.enabled=no`) AVD에서는 흰 화면만 나온다 |
| 자동화 | Python `uiautomator2`(기기에 APK 설치 없음). 앱이 React Native testID를 `resource-id`로 노출하므로 셀렉터는 ID 위주 |
| 로그인 흐름 | `VncLogin`: noVNC로 에뮬레이터 화면 노출 → 운영자가 앱에서 Google 또는 이메일로 로그인 → `/login/complete`. 로그인 상태는 AVD 디스크(userdata)에 남는다 |
| 스냅샷 | 쓰지 않는다. Vooy가 Vulkan을 쓰는 앱이라 한 번 띄운 세션은 스냅샷 저장이 거부된다(`UNSUPPORTED_VK_APP`). 재시작은 콜드 부팅(P0 실측 약 20초) + 디스크의 로그인 유지로 처리 |
| 대상 화면 | 하단 탭 `android-gnb-home`의 에이전트 대화방. `_ensure_ready`에서 앱 포그라운드 + 입력창(`content-desc="Message"`) 존재까지 맞춘다 |
| `_send` | 입력창 탭 → 키보드가 올라와 입력창이 이동하므로 다시 찾은 뒤 `set_text(text)`(한글·특수문자 가능, `adb shell input text`는 불가) → `content-desc="Send message"` 탭. `tap`은 직전 응답 영역의 버튼 노드를 `content-desc`/텍스트로 찾아 클릭. `min_interval_s=3` |
| `_collect` | 1초 간격으로 접근성 트리를 읽어 `other-message-bubble` 노드를 화면 y 좌표순으로 정렬(트리 순서는 최신이 먼저라 역순)하고, `Thread`의 마지막 확인 말풍선 이후에 생긴 것을 수집. 스트리밍 표시(`agent-message-segments` 등)가 사라진 뒤 `quiet_s`(3)를 센다 |
| `_extract` | 말풍선 하위 `TextView` 텍스트가 원문(끝의 시각 텍스트·`U+FFFC` 개체 문자는 제거), 응답 영역의 `clickable` 노드는 `buttons[]`, 리스트 아이템은 `cards[]`, URL 텍스트는 `links[]`, 이미지는 스크린샷을 노드 bounds로 크롭해 `images[]`. 예약·결제 확인 화면은 `confirmation_required` |
| 스레드 | 앱의 에이전트 대화방은 하나라 봇 쪽 대화는 공유된다. `thread_id` 전환 시 `reset=true`면 새 대화 기능(확인 필요, 미결)을 쓰고, 없으면 그대로 이어서 보낸다. `Thread`에는 마지막으로 수집한 말풍선의 텍스트 해시·시각만 보관 |
| 레이트 리밋 | 앱에는 명시적 신호가 없다. 메시지 간격 3초와 하루 전송 상한(설정, 기본 300건)을 어댑터가 강제하고, 앱이 제한 안내를 띄우면 `progress_patterns`와 별도의 `limit_patterns`로 잡아 `error=rate_limited` |
| 프로브 | 에뮬레이터 `boot_completed` + 앱 포그라운드 + 입력창 존재. 로그인 화면(`Continue with Google` 등)이면 `login_required`. 에뮬레이터가 응답 없으면 재부팅 후 재확인, 그래도 안 되면 `backend_down` |

**리스크와 대응**

- 계정이 운영자 개인 Vooy 계정이면 허브 질문이 개인 알림과 같은 대화방에 섞인다: 전용 계정(전용 Google 계정 로그인)을 권장한다. 개인 계정으로 쓰는 동안 `_collect`는 송신 이후에 생긴 말풍선만 보므로 기능 문제는 없다.
- 앱 업데이트로 testID·화면 구조 변경: 셀렉터를 `selectors.yaml`로 분리하고 에뮬레이터의 자동 업데이트를 끈다. 프로브가 핵심 셀렉터 존재를 검사해 깨지면 `backend_down`.
- 응답을 여러 말풍선으로 나눠 보낼 때: `quiet_s`로 묶는다. P0에서는 한 말풍선으로 왔다.
- Vooy 쪽 대화가 허브 세션별로 분리되지 않는 점: 1차에서는 허용. Claude Code 시스템 프롬프트에 "Vooy는 직전 대화를 기억할 수 있으니 필요한 맥락을 메시지에 포함하라"를 둔다.

## 8. 뮤즈 어댑터

> **ADR-9(2026-11-11)**: muse.ai 웹·Playwright·VPN 대신 휴대폰의 Meta AI 앱(`com.facebook.aura`)을 7장과 같은 휴대폰 경로로 조종한다. 실측: 입력창 `hatch-message-input`, 전송 `hatch-send-button`, 작업 중 알림 채널 `aura_run_in_background`이 사라지고 `aura_conversations` 답 알림이 오면 응답 끝. 아래 웹 설계는 이력으로 남긴다. 구현: `adapters/phonechat.py`(범용 채팅 앱 어댑터) + `adapters/muse/`(config·selectors).

muse.ai 웹을 Playwright로 조작한다. 모바일 UI 자동화보다 안정적이고 서버에 에뮬레이터가 필요 없다. 지역 제약은 컨테이너 egress를 VPN으로 돌려 푼다.

| 항목 | 설계 |
| --- | --- |
| 라이브러리 | Playwright(Chromium), `launch_persistent_context(user_data_dir=...)`로 프로필 디렉터리에 쿠키 유지 |
| 로그인 흐름 | `VncLogin`: 헤드풀 Chromium + Xvfb + noVNC. `/login/start`가 noVNC URL 반환 → 운영자가 Meta 계정으로 수동 로그인 → `/login/complete` → 어댑터가 `storage_state`를 백업 |
| 네트워크 | VPN 사이드카(WireGuard 또는 상용 VPN의 CLI 컨테이너)의 네트워크 네임스페이스를 공유. 어댑터 컨테이너 자체는 외부 라우트 없음. 로컬 노출은 9000·9100뿐 |
| 스레드 | `thread_id`마다 muse.ai의 대화 하나. 새 `thread_id`면 "새 대화" 버튼 클릭 후 URL을 `Thread`에 저장. 재방문은 그 URL로 `page.goto` |
| `_send` | 입력창 셀렉터에 `fill` 후 Enter. `tap`은 응답 영역의 버튼 텍스트로 클릭. 셀렉터는 `selectors.yaml`로 분리해 UI 변경 시 코드 수정 없이 대응 |
| `_collect` | 마지막 어시스턴트 메시지 노드의 `innerText`를 500ms 간격으로 비교. 변화가 멈추고 스트리밍 표시(스피너·커서)가 사라지면 확정. `quiet_s`는 1.5 |
| `_extract` | 메시지 노드의 텍스트, `a[href]`는 `links[]`, 버튼류 요소는 `buttons[]`(`action_id`=텍스트 해시), 이미지는 다운로드해 `images[]`. 뮤즈가 작업 실행 확인을 요구하는 UI(결제·전송 승인)는 `confirmation_required`로 매핑 |
| 프로브 | `page.goto(muse.ai)` 후 로그인 상태 셀렉터 존재 확인. 로그인 페이지로 리다이렉트되면 `login_required` |
| 동시성 | 브라우저 컨텍스트 하나, 탭 하나. 큐 직렬 처리가 전제 |

**리스크와 대응**

- DOM 변경으로 셀렉터가 깨짐: `selectors.yaml` + 프로브가 핵심 셀렉터 존재를 검사해 깨지면 `backend_down`으로 보고. 수정은 YAML만.
- 봇 감지·캡차: 헤드풀 Chromium, 실제 프로필, 타이핑 지연(`type` with delay) 사용. 캡차가 뜨면 `login_required`로 보고하고 noVNC로 운영자가 푼다.
- 지역 차단이 계정 레벨이라 VPN으로도 안 풀리는 경우: 어댑터를 Meta Model API 기반 `muse-api` 어댑터로 교체. 인터페이스는 같으므로 Claude Code·허브는 영향 없음. 이 대체 어댑터는 "에이전트 실행 없이 답만 하는 뮤즈"임을 `description`에 명시한다.

## 9. 피클 어댑터

> **ADR-9(2026-11-11)**: 에뮬레이터·전용 AVD·스냅샷 대신 휴대폰의 피클 앱을 7장과 같은 휴대폰 경로로 조종한다. 실측: 휴대폰에서는 입력창에 접근성 `SET_TEXT`가 바로 되고(에뮬레이터의 `send_keys` 우회 불필요) 전송 라벨은 "보내기", 답은 말풍선마다 알림 하나(`MessagingStyle`, `pickle.seq`). 아래 에뮬레이터 설계는 이력으로 남긴다. 구현: `adapters/phonechat.py` + `adapters/pickle/`. 에뮬레이터 1.1.16은 입력 중 전송 버튼을 트리에 내놓지 않아 `send_fallback: keyboard_right`(입력줄 오른쪽 끝 탭)를 둔다.

안드로이드 에뮬레이터 안의 피클 앱을 `android_base.py`(Vooy와 공통)로 조작한다. VM 2에서 돌고, 허브와는 WireGuard 사설망으로만 연결된다.

| 항목 | 설계 |
| --- | --- |
| 에뮬레이터 | 어댑터 전용 AVD(Vooy와 공유하지 않음 — 화면을 동시에 쓸 수 없고, Vooy를 띄운 AVD는 스냅샷이 막히므로). Android SDK 에뮬레이터, x86\_64 시스템 이미지(API 34, Google Play), KVM 가속, `-gpu swiftshader_indirect`. 해상도 1080×2400, 한국어 로케일 |
| 자동화 | `android_base.py`(Python `uiautomator2`, Vooy와 공통). 피클은 Compose UI라 `resource-id`가 없어 접근성 트리의 `TextView.text`와 x 좌표(봇 왼쪽·사용자 오른쪽)로 말풍선을 구분한다. OCR(Tesseract)은 접근성 라벨이 없는 뷰에만 보조로 |
| 로그인 흐름 | `VncLogin`: 에뮬레이터 화면을 noVNC로 노출. 운영자가 앱에서 로그인·권한 승인 → `/login/complete` → 어댑터가 `adb emu avd snapshot save logged_in` |
| 스냅샷 | 로그인 상태 스냅샷을 볼륨에 보관. 재시작·온디맨드 기동 시 `-snapshot logged_in`으로 복원 |
| 스레드 | 피클 앱의 대화 목록에서 `thread_id`에 대응하는 대화를 찾아 진입. 새 `thread_id`면 새 대화 생성 후 제목을 `thread_id`로 바꿔 식별(앱이 제목 편집을 지원하는지 확인 필요, 미결) |
| `_send` | 입력창을 탭해 포커스를 준 뒤 `d.send_keys(text)`(클립보드 붙여넣기)로 입력하고 전송 버튼 탭. 피클 입력창은 포커스를 받으면 접근성 트리에서 사라져 `set_text`가 안 된다(P0 확인). 클립보드에 마지막 입력이 남으므로 전송 후 비운다. `tap`은 직전 응답 영역의 버튼 element를 `accessibility id` 또는 텍스트로 찾아 클릭 |
| `_collect` | 1초 간격으로 접근성 트리 diff. 새 어시스턴트 말풍선 노드가 추가되거나 마지막 말풍선 텍스트가 바뀌면 수집. 피클은 실행 진행을 카드로 보여주므로 `progress_patterns`에 진행 카드 텍스트 등록. `quiet_s`는 4 |
| `_extract` | 말풍선 텍스트, 버튼 노드(`clickable=true`)는 `buttons[]`, 리스트 아이템은 `cards[]`. 이미지는 `screenshot` 후 element bounds로 크롭해 `images[]`. 결제·예약 승인 시트는 `confirmation_required` |
| 프로브 | 앱이 포그라운드인지 + 대화 목록 화면 element 존재. 로그인 화면이면 `login_required`. 에뮬레이터 응답 없으면 재부팅 후 스냅샷 복원, 그래도 안 되면 `backend_down` |
| 기동 모드 | 설정 `mode: always_on \| on_demand`. on\_demand는 `ask` 진입 시 에뮬레이터 부팅(스냅샷 복원 30\~60초, `send_s`에 포함되지 않게 별도 `boot_s=90`), 유휴 10분 후 종료 |

**리스크와 대응**

- 앱 업데이트로 element 구조 변경: 셀렉터를 `selectors.yaml`로 분리. 에뮬레이터의 자동 업데이트를 끄고 운영자가 수동으로 올린 뒤 셀렉터를 갱신한다.
- 에뮬레이터 탐지(루팅·에뮬레이터 체크): Google Play 시스템 이미지 + 기본 설정으로 시도. 막히면 실기기(안드로이드 폰 + USB) 워커로 전환. 어댑터 코드는 `ANDROID_SERIAL`만 바뀐다.
- 피클의 클라우드 브라우저가 느린 작업(전화 걸기 등): `timeout_s` 상한 240까지 Claude Code가 지정 가능. 그 이상은 봇이 알아서 끝내고 사용자가 앱에서 확인하는 것으로 둔다.

## 10. Mock 어댑터와 봇 추가 절차

**Mock 어댑터**는 베이스 클래스 위에 외부 호출 없이 동작한다. 허브·러너·UI를 실제 봇 없이 끝까지 검증하는 용도이며, 이름을 바꿔 여러 개 띄울 수 있다(`mock-a`, `mock-b`).

```yaml
name: mock-a
description: 테스트용 가짜 봇. 식당 추천과 날씨에 답한다.
mock:
  latency_s: {min: 2, max: 8}
  fail_rate: 0.1            # ok=false, error=timeout
  needs_input_rate: 0.2     # 되물음 + choices
  unavailable_after: null   # N번째 호출부터 login_required (장애 시나리오)
  replies:                  # 키워드 → 템플릿. 없으면 echo
    식당: "강남 OO식당 18:30 2명 가능합니다. [버튼: 예약] [버튼: 다른 곳]"
    날씨: "내일 서울 맑음, 최고 14도"
```

`[버튼: X]` 표기는 `buttons[]`로도 파싱돼 `[tap:]` 경로까지 테스트된다.

**봇 추가 절차** (새 봇 = 어댑터 하나)

1. `adapters/<bot>/` 디렉터리 생성, `BotAdapter` 상속해 훅 5개 구현. 로그인 방식에 따라 `CodeLogin` 또는 `VncLogin` 선택.
2. `config.yaml`에 `description`·타임아웃·패턴 작성. description은 Claude Code가 읽는 유일한 봇 설명이다.
3. `Dockerfile` 작성, `docker-compose.yml`에 서비스 추가(내부망, 볼륨, secret).
4. 허브 `bots.yaml`에 `{name, mcp_url, admin_url}` 한 줄 추가. 허브가 재시작 없이 다시 읽는다.
5. 러너는 `bots.yaml`과 헬스체크 결과로 `mcp.json`을 자동 생성하므로 수정 없음. 오케스트레이터 시스템 프롬프트도 봇 목록을 하드코딩하지 않으므로 수정 없음.
6. Mock과 같은 계약 테스트(11장)를 통과시킨다.

허브 API·러너·UI 코드는 손대지 않는다. 손대야 한다면 설계 위반이다.

## 11. 테스트 기준과 미결 사항

**계약 테스트** (모든 어댑터 공통, `tests/contract/`)

| 테스트 | 기대 |
| --- | --- |
| `ask` 정상 | `ok=true`, `raw` 비어 있지 않음, `latency_ms > 0`, `AskResult` 스키마 검증 통과 |
| `ask` 타임아웃 | `timeout_s=10`으로 느린 응답 유도 → 재시도 1회 후 `ok=false, error=timeout`, MCP 예외 없음 |
| `ask` 로그인 만료 | 세션 제거 후 호출 → `error=login_required`, `status.reason=login_required`, 허브 웹훅 호출됨 |
| 동시 `ask` 3건 | 직렬 처리, `queue_depth` 최대 2, 순서 보존 |
| `thread_id` 분리 | 두 스레드에 교차 전송 → 각 스레드 응답이 섞이지 않음(Mock·뮤즈·피클), Vooy는 문서화된 공유 동작(대화방 하나) 확인 |
| `[tap:]` | 직전 응답의 `action_id`로 버튼 클릭 성공, 존재하지 않는 `action_id`는 `error=internal` + 설명 |
| `status` | 프로브 전후 값 변화, `avg_latency_ms` 갱신 |
| 관리 HTTP 인증 | 토큰 없는 요청 401 |
| 재시작 복원 | 컨테이너 재시작 후 `status.available=true`, 기존 `thread_id`로 이어서 `ask` 성공 |

**실봇 스모크 테스트** (수동, 어댑터 착수 완료 기준)

- Vooy: 콜드 부팅 후 "내일 점심 강남 한식 2명 예약 가능한 곳" → 응답 수신, 버튼 파싱, `[tap:]`으로 선택지 하나 누르기까지.
- 뮤즈: 로그인 후 간단한 질문 1건 + 새 대화 생성 + 재방문.
- 피클: 스냅샷 복원 후 대화 1건, 에뮬레이터 재부팅 후 다시 1건.

**미결 사항**

- [x] Vooy 앱에 새 대화(대화 초기화) 기능이 있는지 — P2 확인: 방 정보·vooy 설정 화면에 없음(1.4.4). `reset`은 어댑터 상태만 지우고 대화방은 그대로 잇는다(`adapters/vooy/README.ko.md`)
- [ ] Vooy 전용 계정을 둘지(개인 계정이면 개인 알림과 대화방 공유)
- [ ] 피클 앱이 대화 제목 편집을 지원하는지. 안 되면 대화 생성 순서로 식별
- [ ] 뮤즈 VPN 경유 로그인이 계정 지역 체크를 통과하는지 (요구사항 미결과 동일)
- [ ] 피클 `on_demand` 모드의 부팅 지연을 Claude Code 타임아웃 안에 어떻게 넣을지 — `status`가 `booting`을 돌려주고 Claude가 기다리게 할지, 허브가 미리 깨울지
- [x] `images[]` 로컬 경로를 UI가 어떻게 받을지 — 5장 `GET /images/<id>` + 허브 프록시로 결정
