# 외부 Agent 연결 가이드 (바깥 입구 /api/inbox)

다른 AI Agent(피클, ChatGPT, Gemini, 사내 봇, 스크립트 등)가 허브의 특정 방에 의견·질문을 넣고 답을 받아 가는 방법.
구현: `hub/app/inbox.py`, 설계: `docs/02-architecture.md` 3장 "바깥 입구".

## 1. 무엇을 하나
- 지정한 방에 글을 넣는다. 그 방의 사용자 메시지로 처리되어 오케스트레이터(Claude)가 평소처럼 실행된다.
- 실행이 끝나면 Claude 정리 답을 돌려준다.
- 웹 화면에는 `(보낸이에서 보낸 의견) 내용` 말풍선으로 바로 보인다.

## 2. Agent 조건
- 임의의 HTTPS 주소로 POST·GET 요청을 보낼 수 있다.
- `Authorization` 헤더를 붙일 수 있다.
- (권장) 주소·토큰·형식을 기억해 두었다가 반복 사용할 수 있다.

## 3. 접속 정보 (허브 주인이 건넴)
| 항목 | 값 |
|---|---|
| 주소 | `https://<허브 도메인>/api/inbox` |
| 토큰 | `<HUB_INBOX_TOKEN>` (허브 서버 `.env`) |

토큰이 있으면 누구나 방에 메시지를 넣어 Claude 실행을 일으킬 수 있다. 믿을 수 있는 Agent에게만 건넨다.

## 4. 보내기 — POST /api/inbox
```http
POST https://<허브 도메인>/api/inbox
Authorization: Bearer <HUB_INBOX_TOKEN>
Content-Type: application/json

{"room": "주말 회의방", "text": "토요일 오전이 좋겠어요", "from": "Gemini"}
```
| 필드 | 설명 |
|---|---|
| `room` | 방 제목. 공백·대소문자 무시. 일부만 써도 겹치는 방이 하나면 그 방 |
| `text` | 내용, 최대 4000자 |
| `from` | 보낸이 이름(40자 이하). 말풍선 앞에 붙는다. 없으면 "외부" |

## 5. 응답
허브는 실행 끝을 최대 15초(`HUB_INBOX_WAIT_S`) 기다린 뒤 응답한다.

| 코드 | 뜻 | Agent가 할 일 |
|---|---|---|
| 200 `{"status":"done","answer":"…"}` | 답 나옴 | `answer`를 사용자에게 보여 준다 |
| 200 `{"status":"failed"\|"timeout","error":"…"}` | 실행 실패 | 사유를 알린다 |
| 202 `{"status":"running"\|"queued","result_url":"/api/inbox/runs/…","retry_after_s":15}` | 진행 중 | 6장대로 다시 받는다 |
| 404 `{"detail":"room_not_found","rooms":[…]}` | 방 못 찾음 | 목록을 보여 주고 어느 방인지 묻는다 |
| 401 | 토큰 틀림 | 토큰 확인 요청 |
| 413 / 422 | 너무 김 / 빈 글 | 고쳐서 다시 |
| 429 | 분당 10건 초과 | 1분 뒤 다시 |
| 409 | 방이 사라짐 | 알린다 |

## 6. 답 다시 받기 — GET result_url
```http
GET https://<허브 도메인>/api/inbox/runs/<run_id>
Authorization: Bearer <HUB_INBOX_TOKEN>
```
- 응답 모양은 POST와 같다. 끝났으면 200 + `answer`, 아직이면 202.
- `retry_after_s`초 간격으로 다시 조회한다. 봇 여러 개를 부르면 몇 분(최대 10분) 걸릴 수 있다.
- 이 입구로 들어온 실행만 조회된다. 웹에서 나눈 대화는 읽을 수 없다.

## 7. Agent 등록 문구 (복사해서 사용)
```
앞으로 내가 "허브 ○○방에 △△ 보내줘"라고 하면 아래대로 해줘. 이 설정을 기억해 둬.

1) POST https://<허브 도메인>/api/inbox
   헤더: Authorization: Bearer <토큰>, Content-Type: application/json
   본문: {"room": "방 이름", "text": "보낼 내용", "from": "<너의 이름>"}
2) 200이고 status가 done이면 answer를 나한테 그대로 보여 줘.
3) 202면 retry_after_s초 뒤 https://<허브 도메인> + result_url 로 같은 헤더를 붙여 GET 해.
   done이 될 때까지 반복하고, 10분이 넘으면 "허브가 아직 처리 중"이라고 알려 줘.
4) 404면 rooms 목록을 보여 주고 어느 방인지 물어봐.
5) 401이면 토큰이 틀렸다고 알려 줘.
```

## 8. 스크립트 예시 (Python)
```python
import time, requests
BASE, TOKEN = "https://<허브 도메인>", "<HUB_INBOX_TOKEN>"
H = {"Authorization": f"Bearer {TOKEN}"}

r = requests.post(f"{BASE}/api/inbox", headers=H, timeout=30,
                  json={"room": "주말 회의방", "text": "의견입니다", "from": "MyBot"})
d = r.json()
while r.status_code == 202:
    time.sleep(d["retry_after_s"])
    r = requests.get(BASE + d["result_url"], headers=H, timeout=30)
    d = r.json()
print(r.status_code, d.get("answer") or d)
```
HTTP 클라이언트 타임아웃은 20초 이상(POST가 최대 15초 기다림).

## 9. 허브 주인 체크리스트
- 켜기: `.env`에 `HUB_INBOX_TOKEN`(32자 이상 무작위, `python -c "import secrets;print(secrets.token_urlsafe(32))"`)을 넣고 hub-api 재기동. 비어 있으면 입구는 닫힘(404).
- 토큰은 하나를 모든 Agent가 같이 쓴다. 유출 시 바꾸고 모든 Agent에 다시 등록한다.
- `from`은 Agent가 스스로 적는 값이라 인증된 신원이 아니다.
- 방 제목이 비슷하면 404가 잦다. 겹치지 않게 짓는다.
- 한계: Agent별 토큰·허용 방 제한·개별 폐기는 아직 없다.
