// 스토어 리듀서: 고정 이벤트 시퀀스 → 스냅샷 (docs/05-ui.md 8장 테스트)
import { describe, expect, it } from "vitest";
import type { HubEvent, MessageOut } from "../api/types";
import { addOptimisticUser, applyEvent, emptyChat, headerStatus, loadMessages, type ChatState } from "./chat";

const S = "11111111-1111-1111-1111-111111111111";
const R1 = "aaaaaaaa-0000-0000-0000-000000000001";
const R2 = "aaaaaaaa-0000-0000-0000-000000000002";
const ts = (n: number) => `2026-11-08T10:00:${String(n).padStart(2, "0")}Z`;

let seq = 0;
function ev<T extends HubEvent["type"]>(type: T, payload: Extract<HubEvent, { type: T }>["payload"]): HubEvent {
  seq += 1;
  return { type, session_id: S, seq, ts: ts(seq), payload } as HubEvent;
}

function run(events: HubEvent[], start: ChatState = emptyChat()): ChatState {
  return events.reduce(applyEvent, start);
}

/** 스냅샷을 읽기 쉽게: 항목은 한 줄 요약 */
function view(c: ChatState) {
  return {
    items: c.items.map((i) => {
      switch (i.kind) {
        case "user":
          return `${i.seq ?? "-"} user${i.optimistic ? "(낙관)" : ""}: ${i.text}`;
        case "note":
        case "final":
        case "system":
          return `${i.seq} ${i.kind}: ${i.text}`;
        case "bot":
          return `${i.seq}@${i.order} bot ${i.bot} ok=${i.ok}${i.error ? ` error=${i.error}` : ""}: ${i.raw}`;
        case "pending":
          return `${i.seq} pending ${i.bot} q=${i.queuePosition}`;
        case "failed":
          return `${i.seq} failed ${i.status} ${i.error}`;
      }
    }),
    lastEventSeq: c.lastEventSeq,
    header: headerStatus(c),
    queued: c.runs.queued,
  };
}

const reply = (bot: string, tc: string, raw: string, ok = true) => ({
  run_id: R1,
  bot,
  tool_call_id: tc,
  ok,
  raw,
  structured: null,
  needs_input: false,
  error: ok ? null : "unavailable",
  latency_ms: 1200,
  message_id: `m-${tc}`,
});

describe("chat reducer", () => {
  it("T3 팬아웃: 대기 말풍선 자리에 원문이 들어오고 헤더 카운터가 오른다", () => {
    seq = 0;
    let c = addOptimisticUser(emptyChat(), "셋 다한테 도쿄 왕복 최저가", ts(0));
    const steps: ChatState[] = [];
    const events = [
      ev("message.ack", { message_id: "u1", run_id: R1, status: "running" }),
      ev("run.started", { run_id: R1, claude_session_id: "c1" }),
      ev("claude.note", { run_id: R1, text: "세 봇에 물어봅니다" }),
      ev("bot.pending", { run_id: R1, bot: "mock-a", tool_call_id: "ta", queue_position: 0 }),
      ev("bot.pending", { run_id: R1, bot: "mock-b", tool_call_id: "tb", queue_position: 0 }),
      ev("bot.pending", { run_id: R1, bot: "mock-c", tool_call_id: "tc", queue_position: 1 }),
      ev("bot.reply", reply("mock-b", "tb", "B: 32만원")),
      ev("bot.reply", reply("mock-a", "ta", "A: 35만원")),
      ev("bot.reply", reply("mock-c", "tc", "", false)),
      ev("claude.final", { run_id: R1, text: "| 봇 | 가격 |\n|---|---|\n| B | 32만 |", message_id: "f1", usage: null }),
      ev("run.finished", { run_id: R1, status: "done" }),
    ];
    for (const e of events) {
      c = applyEvent(c, e);
      steps.push(c);
    }
    expect(view(steps[5])).toMatchSnapshot("대기 3개");
    expect(view(steps[6])).toMatchSnapshot("B 먼저 도착");
    expect(view(c)).toMatchSnapshot("완료");
  });

  it("T7 needs_input: 봇 질문이 표시되고 실행이 끝난다", () => {
    seq = 0;
    const c = run([
      ev("message.ack", { message_id: "u1", run_id: R1, status: "running" }),
      ev("run.started", { run_id: R1, claude_session_id: null }),
      ev("bot.pending", { run_id: R1, bot: "mock-a", tool_call_id: "ta", queue_position: 0 }),
      ev("bot.reply", { ...reply("mock-a", "ta", "예산은?"), needs_input: true }),
      ev("claude.final", { run_id: R1, text: "mock-a가 예산을 묻습니다.", message_id: "f1", usage: null }),
      ev("run.finished", { run_id: R1, status: "done" }),
    ]);
    expect(c.items.find((i) => i.kind === "bot")).toMatchObject({ needsInput: true });
    expect(view(c)).toMatchSnapshot();
  });

  it("대기열·실패·취소", () => {
    seq = 0;
    let c = addOptimisticUser(emptyChat(), "첫째", ts(0));
    c = addOptimisticUser(c, "둘째", ts(0));
    c = run(
      [
        ev("message.ack", { message_id: "u1", run_id: R1, status: "running" }),
        ev("message.ack", { message_id: "u2", run_id: R2, status: "queued" }),
      ],
      c,
    );
    expect(view(c)).toMatchSnapshot("실행 1 + 대기 1");
    c = run(
      [
        ev("bot.pending", { run_id: R1, bot: "mock-a", tool_call_id: "ta", queue_position: 0 }),
        ev("run.finished", { run_id: R1, status: "timeout", error: "timeout" }),
        ev("run.finished", { run_id: R2, status: "failed", error: "cancelled" }),
      ],
      c,
    );
    expect(view(c)).toMatchSnapshot("시간 초과 + 대기 취소");
    expect(c.runs.userText[R2]).toBe("둘째");
  });

  it("재전송된 이벤트(seq <= lastEventSeq)는 무시한다", () => {
    seq = 0;
    const events = [
      ev("message.ack", { message_id: "u1", run_id: R1, status: "running" }),
      ev("claude.note", { run_id: R1, text: "노트" }),
    ];
    const once = run(events, addOptimisticUser(emptyChat(), "안녕", ts(0)));
    expect(run(events, once)).toBe(once);
  });

  it("새로고침: REST 메시지 + 버퍼 재생(since_seq=0)이 라이브와 같은 화면을 만든다", () => {
    seq = 0;
    const live = [
      ev("message.ack", { message_id: "u1", run_id: R1, status: "running" }), // 1
      ev("run.started", { run_id: R1, claude_session_id: "c1" }), // 2
      ev("bot.pending", { run_id: R1, bot: "mock-a", tool_call_id: "ta", queue_position: 0 }), // 3
      ev("bot.pending", { run_id: R1, bot: "mock-b", tool_call_id: "tb", queue_position: 0 }), // 4
      ev("bot.reply", reply("mock-b", "tb", "B 원문")), // 5
    ];
    const liveState = run(live, addOptimisticUser(emptyChat(), "질문", ts(0)));

    // 같은 시점에 새로고침: REST 는 user(1)·bot(5) 만 갖고 있다
    const rest: MessageOut[] = [
      { id: "u1", session_id: S, role: "user", bot_name: null, raw: "질문", structured: null, seq: 1, created_at: ts(1) },
      { id: "m-tb", session_id: S, role: "bot", bot_name: "mock-b", raw: "B 원문", structured: null, seq: 5, created_at: ts(5) },
    ];
    const restored = run(live, loadMessages(emptyChat(), rest));
    expect(view(restored)).toEqual(view(liveState));
    expect(view(restored)).toMatchSnapshot();
  });

  it("허브 재시작 뒤(버퍼 없음): REST 만으로 그린다. 실패 응답은 원문이 비어 있다", () => {
    const rest: MessageOut[] = [
      { id: "u1", session_id: S, role: "user", bot_name: null, raw: "질문", structured: null, seq: 1, created_at: ts(1) },
      { id: "n1", session_id: S, role: "claude", bot_name: null, raw: "노트", structured: { kind: "note" }, seq: 3, created_at: ts(3) },
      { id: "b1", session_id: S, role: "bot", bot_name: "mock-a", raw: "", structured: null, seq: 5, created_at: ts(5) },
      { id: "f1", session_id: S, role: "claude", bot_name: null, raw: "정리", structured: { kind: "final" }, seq: 6, created_at: ts(6) },
      { id: "s1", session_id: S, role: "system", bot_name: null, raw: "새 Claude 세션으로 이어집니다", structured: null, seq: 7, created_at: ts(7) },
    ];
    const c = loadMessages(emptyChat(), rest);
    expect(view(c)).toMatchSnapshot();
    expect(c.lastMessageSeq).toBe(7);
    // 같은 메시지를 다시 합쳐도 늘지 않는다(재연결 since_seq 겹침)
    expect(loadMessages(c, rest).items).toHaveLength(5);
  });
});
