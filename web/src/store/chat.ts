/**
 * 세션 하나의 대화 상태와 그 리듀서 (docs/05-ui.md 2·6·8장).
 *
 * 서버가 진실이고 이 모듈은 이벤트를 화면 항목으로 접는 순수 함수뿐이다.
 * 낙관적 갱신은 사용자 말풍선 하나만(ack 전까지 회색).
 *
 * 복원 규칙
 * - REST 메시지(loadMessages)와 WebSocket 이벤트가 같은 seq 를 공유한다(허브가 같은 seq 로 쓴다).
 *   그래서 항목 중복은 seq 로 막는다.
 * - 이미 처리한 이벤트(seq <= lastEventSeq)는 통째로 무시한다. 재연결 시 since_seq 로 쓴다.
 * - 대기 말풍선은 bot.pending 의 seq 자리에 놓고, bot.reply 가 같은 tool_call_id 로 그 자리를 차지한다.
 */
import type { HubEvent, MessageOut } from "../api/types";

export interface BaseItem {
  /** React key. 서버 항목은 "s<seq>", 대기 말풍선은 "t<tool_call_id>", 낙관적 항목은 "l<n>" */
  key: string;
  /** 서버 seq. 낙관적 사용자 말풍선만 null */
  seq: number | null;
  /** 화면 정렬 기준. 대기 말풍선을 교체한 원문은 대기 말풍선의 seq 를 물려받는다 */
  order: number;
  ts: string;
}

export interface UserItem extends BaseItem {
  kind: "user";
  text: string;
  /** ack 전(회색) */
  optimistic: boolean;
}
export interface NoteItem extends BaseItem {
  kind: "note";
  text: string;
}
export interface BotItem extends BaseItem {
  kind: "bot";
  bot: string;
  ok: boolean;
  raw: string;
  structured: Record<string, unknown> | null;
  needsInput: boolean;
  error: string | null;
  latencyMs: number | null;
  toolCallId: string | null;
}
export interface PendingItem extends BaseItem {
  kind: "pending";
  bot: string;
  toolCallId: string;
  runId: string;
  queuePosition: number;
}
export interface FinalItem extends BaseItem {
  kind: "final";
  text: string;
}
export interface FailedItem extends BaseItem {
  kind: "failed";
  runId: string;
  status: "failed" | "timeout";
  error: string | null;
}
export interface SystemItem extends BaseItem {
  kind: "system";
  text: string;
}

export type ChatItem = UserItem | NoteItem | BotItem | PendingItem | FinalItem | FailedItem | SystemItem;

export interface RunProgress {
  /** bot.pending 수 */
  asked: number;
  /** bot.reply 수 */
  replied: number;
}

export interface RunsState {
  /** 실행 중인 run */
  current: string | null;
  /** 대기열(queued) run, 들어온 순서 */
  queued: string[];
  /** run 별 봇 응답 카운터(헤더 "2/3 응답") */
  progress: Record<string, RunProgress>;
  /** 마지막으로 끝난 run 의 결과(헤더 "완료"·"실패") */
  last: { runId: string; status: "done" | "failed" | "timeout" } | null;
  /** run → 사용자 입력. "다시 시도"에 쓴다(이번 접속에서 본 것만) */
  userText: Record<string, string>;
}

export interface ChatState {
  items: ChatItem[];
  /** WebSocket 으로 처리한 마지막 이벤트 seq. 재연결 since_seq */
  lastEventSeq: number;
  /** REST 로 받은 마지막 메시지 seq */
  lastMessageSeq: number;
  runs: RunsState;
  /** 낙관적 항목 번호 */
  localCounter: number;
}

export const emptyChat = (): ChatState => ({
  items: [],
  lastEventSeq: 0,
  lastMessageSeq: 0,
  runs: { current: null, queued: [], progress: {}, last: null, userText: {} },
  localCounter: 0,
});

// 낙관적 항목은 서버 항목 뒤에 둔다
const OPTIMISTIC_ORDER = Number.MAX_SAFE_INTEGER;

function sortItems(items: ChatItem[]): ChatItem[] {
  return [...items].sort((a, b) => a.order - b.order || (a.seq ?? OPTIMISTIC_ORDER) - (b.seq ?? OPTIMISTIC_ORDER));
}

function hasSeq(state: ChatState, seq: number): boolean {
  return state.items.some((i) => i.seq === seq);
}

// --- REST 메시지 → 항목 ---

export function itemFromMessage(m: MessageOut): ChatItem | null {
  const base = { key: `s${m.seq}`, seq: m.seq, order: m.seq, ts: m.created_at };
  switch (m.role) {
    case "user":
      return { ...base, kind: "user", text: m.raw, optimistic: false };
    case "claude":
      if (m.structured?.kind === "final") return { ...base, kind: "final", text: m.raw };
      return { ...base, kind: "note", text: m.raw };
    case "bot":
      // 메시지 행에는 ok·error 가 없다. 실패 응답은 원문이 비어 있다(hub/app/runs.py)
      return {
        ...base,
        kind: "bot",
        bot: m.bot_name ?? "?",
        ok: m.raw !== "",
        raw: m.raw,
        structured: m.structured,
        needsInput: false,
        error: m.raw === "" ? "응답 실패" : null,
        latencyMs: null,
        toolCallId: null,
      };
    case "system":
      return { ...base, kind: "system", text: m.raw };
    default:
      return null;
  }
}

/** REST 로 받은 메시지를 합친다. 이미 있는 seq 는 건너뛴다(이벤트로 먼저 받은 항목이 더 자세하다). */
export function loadMessages(state: ChatState, messages: MessageOut[]): ChatState {
  const items = [...state.items];
  let lastMessageSeq = state.lastMessageSeq;
  for (const m of messages) {
    lastMessageSeq = Math.max(lastMessageSeq, m.seq);
    if (items.some((i) => i.seq === m.seq)) continue;
    const item = itemFromMessage(m);
    if (item) items.push(item);
  }
  return { ...state, items: sortItems(items), lastMessageSeq };
}

// --- 사용자 입력(낙관적) ---

export function addOptimisticUser(state: ChatState, text: string, ts: string): ChatState {
  const n = state.localCounter + 1;
  const item: UserItem = { key: `l${n}`, seq: null, order: OPTIMISTIC_ORDER, ts, kind: "user", text, optimistic: true };
  return { ...state, items: [...state.items, item], localCounter: n };
}

// --- 이벤트 ---

function progressOf(runs: RunsState, runId: string): RunProgress {
  return runs.progress[runId] ?? { asked: 0, replied: 0 };
}

function withProgress(runs: RunsState, runId: string, f: (p: RunProgress) => RunProgress): RunsState {
  return { ...runs, progress: { ...runs.progress, [runId]: f(progressOf(runs, runId)) } };
}

export function applyEvent(state: ChatState, ev: HubEvent): ChatState {
  if (ev.seq == null) return state; // bot.status 등 세션 무관 이벤트는 여기서 다루지 않는다
  if (ev.seq <= state.lastEventSeq) return state; // 이미 처리함(재전송)
  const seq = ev.seq;
  const next = applyNew(state, ev, seq);
  return { ...next, lastEventSeq: seq };
}

function applyNew(state: ChatState, ev: HubEvent, seq: number): ChatState {
  const dup = hasSeq(state, seq);
  const base = { key: `s${seq}`, seq, order: seq, ts: ev.ts };
  let { items, runs } = state;

  switch (ev.type) {
    case "message.ack": {
      const p = ev.payload;
      if (!dup) {
        // 가장 먼저 보낸 낙관적 말풍선을 확정한다
        const idx = items.findIndex((i) => i.kind === "user" && i.optimistic);
        if (idx >= 0) {
          const u = items[idx] as UserItem;
          items = [...items];
          items[idx] = { ...u, ...base, optimistic: false };
          runs = { ...runs, userText: { ...runs.userText, [p.run_id]: u.text } };
        }
      } else {
        const u = items.find((i) => i.seq === seq);
        if (u?.kind === "user") runs = { ...runs, userText: { ...runs.userText, [p.run_id]: u.text } };
      }
      if (p.status === "running") {
        runs = { ...runs, current: p.run_id, last: null };
      } else if (!runs.queued.includes(p.run_id)) {
        runs = { ...runs, queued: [...runs.queued, p.run_id] };
      }
      break;
    }
    case "run.started": {
      const id = ev.payload.run_id;
      runs = { ...runs, current: id, queued: runs.queued.filter((r) => r !== id), last: null };
      break;
    }
    case "claude.note":
      if (!dup) items = [...items, { ...base, kind: "note", text: ev.payload.text }];
      break;
    case "bot.pending": {
      const p = ev.payload;
      runs = withProgress(runs, p.run_id, (x) => ({ ...x, asked: x.asked + 1 }));
      const replied = items.some((i) => i.kind === "bot" && i.toolCallId === p.tool_call_id);
      if (!replied && !items.some((i) => i.kind === "pending" && i.toolCallId === p.tool_call_id)) {
        items = [
          ...items,
          {
            ...base,
            key: `t${p.tool_call_id}`,
            kind: "pending",
            bot: p.bot,
            toolCallId: p.tool_call_id,
            runId: p.run_id,
            queuePosition: p.queue_position,
          },
        ];
      }
      break;
    }
    case "bot.reply": {
      const p = ev.payload;
      runs = withProgress(runs, p.run_id, (x) => ({ ...x, replied: x.replied + 1 }));
      const pending = items.find((i) => i.kind === "pending" && i.toolCallId === p.tool_call_id);
      const order = pending ? pending.order : seq;
      const reply: BotItem = {
        ...base,
        order,
        kind: "bot",
        bot: p.bot,
        ok: p.ok,
        raw: p.raw,
        structured: p.structured,
        needsInput: p.needs_input,
        error: p.error,
        latencyMs: p.latency_ms,
        toolCallId: p.tool_call_id,
      };
      // REST 로 먼저 받은 같은 seq 항목도 이벤트 쪽(ok·error·소요 시간이 있음)으로 바꾼다
      items = items.filter((i) => i !== pending && i.seq !== seq);
      items = [...items, reply];
      break;
    }
    case "claude.final":
      if (!dup) items = [...items, { ...base, kind: "final", text: ev.payload.text }];
      break;
    case "run.finished": {
      const p = ev.payload;
      runs = {
        ...runs,
        current: runs.current === p.run_id ? null : runs.current,
        queued: runs.queued.filter((r) => r !== p.run_id),
        last: { runId: p.run_id, status: p.status },
      };
      // 끝난 run 의 남은 대기 말풍선은 응답이 오지 않는다
      items = items.filter((i) => !(i.kind === "pending" && i.runId === p.run_id));
      if (p.status !== "done" && !dup) {
        items = [...items, { ...base, kind: "failed", runId: p.run_id, status: p.status, error: p.error ?? null }];
      }
      break;
    }
    case "session.system":
      if (!dup) items = [...items, { ...base, kind: "system", text: ev.payload.text }];
      break;
    case "bot.status":
      break;
  }
  return { ...state, items: sortItems(items), runs };
}

// --- 파생 값 ---

export type HeaderStatus =
  | { kind: "idle" }
  | { kind: "running"; asked: number; replied: number; queued: number }
  | { kind: "queued"; queued: number }
  | { kind: "done" }
  | { kind: "failed"; status: "failed" | "timeout" };

export function headerStatus(state: ChatState): HeaderStatus {
  const { current, queued, last } = state.runs;
  if (current) {
    const p = progressOf(state.runs, current);
    return { kind: "running", asked: p.asked, replied: p.replied, queued: queued.length };
  }
  if (queued.length) return { kind: "queued", queued: queued.length };
  if (last?.status === "done") return { kind: "done" };
  if (last) return { kind: "failed", status: last.status };
  return { kind: "idle" };
}
