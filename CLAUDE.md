# Roundtable-Agent

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
