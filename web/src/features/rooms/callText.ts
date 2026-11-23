// AI 참가자에게 붙여 넣을 "부르는 말". 키가 들어가므로 화면에서 한 번만 보여 준다.

export interface CallTextInput {
  origin: string;
  room: string;
  name: string;
  key: string;
  checkEverySec: number;
}

export function callText({ origin, room, name, key, checkEverySec }: CallTextInput): string {
  return [
    `너는 대화방 「${room}」에 참여하는 AI 「${name}」이다. 아래 방법으로 이 방을 계속 읽고, 필요할 때 답해 줘.`,
    "",
    "## 접속",
    `- 주소: ${origin}`,
    `- 내 키: ${key}  (모든 요청에 헤더 x-key 로 붙인다. 어디에도 출력·공유하지 않는다)`,
    "",
    "## 읽기",
    `- GET ${origin}/api/p/msgs?since=<마지막으로 본 글 id>  (처음엔 since=0)`,
    "- 응답: {me, room, check_every_s, call_only, my_turn, msgs:[{id, who, kind, text, ts, to_me}]}",
    "- to_me: 그 글이 나를(@내이름·@모두) 불렀는지. my_turn: 아직 답하지 않은 부름이 있는지.",
    `- ${checkEverySec}초마다 한 번 확인한다. 429 가 오면 retry_after_s 만큼 쉰다.`,
    "",
    "## 쓰기",
    `- POST ${origin}/api/p/send  본문 {"text": "내 말"}  (Content-Type: application/json)`,
    "- 보낸 이는 키로 정해진다. 이름을 본문에 붙이지 않는다.",
    "",
    "## 대화 규칙 (여러 AI 가 같이 있으므로 꼭 지킬 것)",
    `- my_turn 이 true 일 때만(나를 @${name} 으로 불렀을 때만) 답한다. 답은 한 번이면 된다.`,
    "- call_only 가 true 인 방에서는 부르지 않았는데 보내면 409 not_called 가 온다. 그때는 다음 부름을 기다린다.",
    "- 한 번에 한 메시지, 5문장 이내로 짧게.",
    "- 같은 흐름에서 연속 3번 넘게 말하지 않는다.",
    "- 사람이 「멈춰」라고 하면 바로 멈춘다.",
    "- 비밀번호·인증코드·개인정보는 쓰지 않는다.",
    "",
    "## 응답 코드",
    "- 401: 키가 바뀌었거나 지워짐 → 멈추고 알려 줘. 403: 잠깐 빠짐 → 나중에 다시. 410: 방이 닫힘 → 멈춤.",
    "",
    `접속에 성공하면 부름이 없어도 되는 방(call_only=false)일 때만 「${name} 들어왔어요」라고 한 줄 남겨 줘.`,
  ].join("\n");
}

/**
 * 팀장용 안내. 팀장은 허브에 들어 있는 실행기(python -m app.room_leader)를 이 PC 에서 돌린다.
 * 누구를 부를지는 그 PC 의 claude -p(구독)가 정한다.
 */
export function leaderText({ origin, room, name, key }: Omit<CallTextInput, "checkEverySec">): string {
  return [
    `「${room}」 방의 팀장 「${name}」 실행 방법`,
    "",
    "Roundtable-Agent 저장소의 hub 폴더에서 (claude 로그인된 PC):",
    "",
    "PowerShell:",
    `  $env:ROOM_URL='${origin}'; $env:ROOM_KEY='${key}'; uv run python -m app.room_leader`,
    "",
    "bash:",
    `  ROOM_URL='${origin}' ROOM_KEY='${key}' uv run python -m app.room_leader`,
    "",
    "선택 설정: LEADER_MAX_TURNS(결론까지 지목 수, 기본 6), LEADER_POLL_S(확인 간격, 기본 20초),",
    "LEADER_REPLY_WAIT_S(지목한 AI 답을 기다리는 초, 기본 300), LEADER_LLM_CMD(기본 'claude -p').",
    "",
    "사람이 글을 쓰면 팀장이 AI 를 한 명씩 @이름 으로 부르고, 정한 수만큼 돌면 「결론:」 을 올린 뒤 쉰다.",
    "방을 'AI는 부를 때만' 으로 켜 두면 부르지 않은 AI 의 글은 허브가 막는다.",
    "키는 어디에도 출력·공유하지 않는다. 멈추려면 Ctrl+C.",
  ].join("\n");
}

/** 초대 링크. 키를 # 뒤에 두어 서버 접근 로그에 남지 않게 한다 */
export function inviteUrl(origin: string, invite: string): string {
  return `${origin}/#/join/${invite}`;
}

export function inviteFromHash(hash = window.location.hash): string | null {
  const m = /^#\/join\/(ri_[A-Za-z0-9_-]{10,})$/.exec(hash);
  return m ? m[1] : null;
}

/** 운영자 열린 방 화면: #/rooms 또는 #/rooms/<id> */
export function roomRoute(hash = window.location.hash): { roomId: string | null } | null {
  if (hash === "#/rooms") return { roomId: null };
  const m = /^#\/rooms\/([0-9a-f-]{36})$/i.exec(hash);
  return m ? { roomId: m[1] } : null;
}
