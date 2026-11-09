# Vooy 어댑터

에뮬레이터 안 Vooy 앱(`com.vooy.mobile`)을 `uiautomator2`로 조작한다. 설계는 `docs/03-adapters.md` 7장.
공통 기기 코드는 `adapters/android_base.py`(피클과 공유), 셀렉터는 `selectors.yaml`(앱 업데이트 때 이것만 고친다).

## 구성

| 항목 | 값 |
| --- | --- |
| 전용 AVD | `vooy` (API 34 Google Play x86_64, 1080×2400, `-gpu swiftshader_indirect`, 스냅샷 안 씀). `scripts/android/avd.py`로 만든다 |
| 앱 설치 | 이미 설치된 기기에서 APK를 옮겨 설치(`avd.py sideload`). Play 스토어 로그인이 필요 없고 자동 업데이트를 피한다 |
| 로그인 | `VncLogin`. 운영자가 화면(noVNC, 로컬은 에뮬레이터 창)에서 앱에 로그인 → `/login/complete`. 상태는 AVD 디스크에만 남는다 |
| 기기 연결 | 컨테이너 밖 adb 서버(`ANDROID_ADB_SERVER_HOST/PORT`)를 거쳐 `ANDROID_SERIAL` 기기에 붙는다 |
| compose | `docker compose --profile vooy up -d vooy`. 로컬 포트 MCP 9005, 관리 9105 |
| 계약 테스트 | `make test-adapters`가 같은 어댑터 코드를 가짜 기기(`adapters/testing/fake_vooy.py`)에 붙인 `vooy-fake`로 돌린다. 실봇은 아래 "실봇 계약 실행" |

env(레포 루트 `.env`, 모두 선택): `VOOY_ANDROID_SERIAL`(기본 `emulator-5554`), `VOOY_ADB_SERVER_HOST`(기본 `host.docker.internal`),
`VOOY_ADB_SERVER_PORT`(기본 5037), `VOOY_VNC_URL`.

### 로컬(Windows) adb 연결

Windows의 adb 서버는 기본으로 `127.0.0.1:5037`에만 열려 컨테이너에서 닿지 않는다. 두 방법 중 하나:

1. 어댑터를 호스트에서 직접 띄운다(컨테이너 없이). 로컬 스모크는 이쪽이 간단하다.
   ```bash
   # 레포 루트에서
   PYTHONPATH=. HUB_ADAPTER_TOKEN=<.env 값> ANDROID_SERIAL=emulator-5560 ADAPTER_STATE_DIR=.vooy-state MCP_PORT=9005 ADMIN_PORT=9105 uv run --project adapters --group android python -m adapters.vooy
   ```
2. adb 서버를 모든 인터페이스로 다시 띄운다(`adb kill-server` 후 `adb -a nodaemon server start`). 같은 PC의 다른 adb 사용자도 끊기고
   LAN에 adb가 열리므로 방화벽으로 막을 때만 쓴다.

VM 2(Linux)에서는 에뮬레이터 호스트의 adb 서버 주소를 `VOOY_ADB_SERVER_HOST`로 준다(P2 VM 2 프로비저닝에서 확정).

## 전용 AVD 준비 (사람이 1회)

```bash
python scripts/android/avd.py create vooy
python scripts/android/avd.py start vooy --port 5560
# 부팅 후(약 20초~), 이미 Vooy 가 설치된 기기에서 옮겨 설치
python scripts/android/avd.py sideload com.vooy.mobile --src emulator-5556 --dst emulator-5560
```
에뮬레이터 창에서 Vooy를 열고 로그인한다(전용 계정 권장, 아래 미결 참고). 로그인 정보·화면을 저장소·로그에 남기지 않는다.

## 실봇 계약 실행 (사람이, 필요할 때)

실봇에 메시지가 실제로 간다(1회 20건 안팎). 로그인 만료·확정 단계·버튼·이미지 케이스는 자동으로 건너뛴다(`LiveVooyDriver`).
`test_restart_restores_threads`는 compose 서비스 `vooy`를 재시작하므로 어댑터를 compose로 띄웠을 때만 돌린다.

```bash
docker compose -f docker-compose.yml -f docker-compose.override.yml -f adapters/tests/contract/compose.yml --profile vooy up -d --build --wait webhook-sink vooy
cd adapters && CONTRACT_TARGETS=vooy uv run pytest -q tests/contract
```

## 스모크 체크리스트 (사람이 함, 03 11장 + 로드맵 P2 게이트)

준비: 전용 AVD `vooy`에 Vooy 로그인 완료, 어댑터 기동(compose 또는 호스트 직접), `.env`의 토큰. 아래 `$T`는 `X-Hub-Token` 값.

1. [ ] **콜드 부팅**: 에뮬레이터를 끄고 `avd.py start vooy`로 다시 켠다. 로그인이 유지되는지 앱 화면에서 확인.
2. [ ] **프로브**: `curl -s -X POST -H "X-Hub-Token: $T" http://127.0.0.1:9105/probe` → `available: true`. 앱이 꺼져 있었다면 어댑터가 띄우고 vooy 탭 대화방으로 간다.
3. [ ] **질문**: 허브 웹(또는 MCP 클라이언트)에서 Vooy에게 "내일 점심 강남 한식 2명 예약 가능한 곳" → 30초 안에 응답 수신, `raw`에 본문, 시각·`U+FFFC` 없음.
4. [ ] **버튼 파싱**: 응답에 버튼·선택지가 있으면 `structured.buttons`와 `raw`의 `[버튼: X]`가 일치하는지. 화면의 다른 요소(이모지 반응, 상단 아이콘)가 버튼으로 잡히지 않는지.
5. [ ] **`[tap:]`**: 버튼 하나를 `[tap:<action_id>]`로 누름 → 앱 화면에서 실제로 눌리고 다음 응답 수신. 결제·예약 확정 버튼은 누르지 않는다.
6. [ ] **카드·이미지**: 식당 목록이 카드·이미지로 오면 무엇이 잡히는지 기록(현재 카드 추출은 미구현, 이미지는 화면 크롭). 화면 구조를 `selectors.yaml`에 반영할지 판단.
7. [ ] **응답 중 표시**: 응답을 기다리는 동안 하단 vooy 탭의 `content-desc`(평소 "vooy tab, Done")와 스트리밍 노드(`agent-message-segments`)가 어떻게 바뀌는지 `uiautomator2` dump로 확인 → `selectors.yaml`의 `streaming_rids`·`typing_tab_desc_re` 갱신. 지금은 침묵 3초로만 확정한다.
8. [ ] **여러 말풍선**: 응답이 두 말풍선 이상으로 오면 하나의 `raw`로 묶이는지.
9. [ ] **로그인 만료**: (선택, 전용 계정일 때만) 앱에서 로그아웃 → `ask` 결과 `error=login_required`, 허브 봇 패널 갱신 → 다시 로그인 → `POST /login/complete` → `available: true`.
10. [ ] **재시작 복원**: 어댑터를 재시작 → 같은 `thread_id`로 이어서 `ask` 성공.
11. [ ] **허브 등록**: 허브 `hub/bots.yaml`에 `- {name: vooy, mcp_url: http://vooy:9000/mcp, admin_url: http://vooy:9100, aliases: [부이]}` 한 줄 추가 후 허브 봇 패널에 뜨는지(이 PR은 hub/를 건드리지 않았다).

## 알려진 동작과 미결

- **새 대화 기능 없음**: P2에서 방 정보(Room info)·vooy 설정 화면을 확인했으나 대화 초기화·새 대화 메뉴가 없다. `reset`은 어댑터 상태만 지우고 봇 쪽 대화방은 그대로 잇는다. Vooy는 메모리(장기 기억)가 있어 이전 맥락을 기억한다.
- **대화방 공유**: 모든 `thread_id`가 앱의 대화방 하나를 쓴다. 수집은 내가 보낸 말풍선 아래의 봇 말풍선만 보므로 응답이 섞이지 않는다. 단 봇이 그사이 먼저 보내는 알림성 메시지가 응답으로 잡힐 수 있다.
- **전용 계정**: 개인 계정이면 개인 알림 메시지가 같은 대화방에 섞이고, 그게 응답으로 잡힐 수 있다. 전용 계정(전용 Google 계정 로그인)을 권장한다 — 운영자 결정 필요.
- **레이트 리밋**: 메시지 간격 3초, 하루 300건(`android.daily_limit`), 제한 안내 문구(`limit_patterns`)면 `rate_limited`.
