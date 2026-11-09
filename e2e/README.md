# UI E2E (P1-7)

Playwright 시나리오 5개. Mock 어댑터, 실제 허브·DB·웹·러너(`claude -p`)로 돈다(docs/05-ui.md 8장).

```bash
make e2e                       # 스택 기동 → 5개 실행 → 스택 내림
make e2e ARGS="-g T8"          # 한 시나리오만
make e2e E2E_KEEP=1            # 끝나도 스택 유지(다시 돌릴 때 빠르다). 내릴 때: docker compose -p multibot-e2e down
```

필요한 것: 러너 Claude Code 로그인(`make runner-login`, 운영 볼륨 `multibot_runner-claude`를 빌려 쓴다), Node 20+와 pnpm, Playwright Chromium(`cd e2e && pnpm exec playwright install chromium`). Git Bash에서 `MSYS_NO_PATHCONV=1`을 붙이면 make 안의 pnpm 경로가 깨지니 붙이지 않는다.

## 시나리오

| 테스트 | 오케스트레이터 케이스 | 허브 bots.yaml | 화면에서 확인하는 것 |
| --- | --- | --- | --- |
| T2 | 단일 봇 | `bots/default.yaml` | 실행 중 헤더, 대기 말풍선 → 같은 자리에서 원문으로 교체, 정리, 버튼 없음, 세션 제목, 새로고침 후 복원 |
| T3 | 팬아웃 3봇 | `bots/default.yaml` | 헤더 "n/3 응답", 봇 3개 원문, 정리 안 비교표(`table`) |
| T7 | 되물음 사용자 전달 | `bots/budget.yaml` | "봇이 추가 정보를 묻고 있습니다" 태그, 정리에 예산 질문, 재호출 없음 |
| T8→T9 | 확정 요청 → 진행 | `bots/confirm.yaml` | 확인 박스(버튼 없음), 자동 확정 없음, 정리의 `[진행]` 클릭 → "진행해" → `[tap:]` 확정 완료, 지난 버튼 사라짐 |
| T10 | 봇 불가 | `bots/b-down.yaml` | 패널 "로그인 필요"와 재로그인 버튼, mock-b 말풍선 없음, 재로그인 시트 열고 Esc로 닫기 |

## 설계 메모

- **`make test`에 넣지 않은 이유**: 실제 `claude -p`를 돌린다. 러너 로그인과 구독 사용량이 필요하고, LLM 응답이 매번 달라 결정적이지 않다. `make eval`과 같은 이유로 별도 타깃으로 두고, 프롬프트·UI를 고친 뒤와 게이트 확인 때 돌린다.
- **별도 compose 프로젝트 `multibot-e2e`**: 운영 스택(`multibot`)과 평가 스택(`multibot-eval`)을 건드리지 않는다. DB는 tmpfs이고 웹만 `127.0.0.1:8090`으로 연다.
- **시나리오 전환**: 봇 이름(툴 이름 `mcp__mock-a__*`)은 그대로 두고, 허브 `bots.yaml`(`e2e/out/bots/bots.yaml`)의 `mcp_url`만 변형 Mock 컨테이너로 바꾼다. 허브는 실행마다, 헬스체크(E2E에서는 2초)마다 이 파일을 다시 읽는다. 허브 코드는 고치지 않았다. Mock 설정은 평가와 같은 `runner/eval/mocks/*.yaml`이다(무작위 실패·되물음 없음).
- **로그인**: `global-setup.ts`가 E2E DB에 테스트 운영자를 `hub-admin create-operator --password-stdin`으로 만든다. 무작위 비밀번호와 TOTP 시크릿은 프로세스 메모리에만 있다. 로그인 화면에 입력해 로그인한 뒤 쿠키를 `out/state.json`에 둔다. 쿠키는 이 스택에서만 유효하고 커밋하지 않는다.
- **판정**: 정리 문장은 LLM마다 달라서 사실 키워드만 본다. 형식(줄 수·첫 줄·"진행할까요?")은 `make eval`이 판정한다. 재시도(`retries`)는 0이다.
- 산출물(`out/`: 임시 비밀값, 쿠키, HTML 리포트, 실패 trace)은 커밋하지 않는다.
