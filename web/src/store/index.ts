/**
 * 앱 스토어(zustand). 세션 목록·세션별 대화·봇·연결 상태와 그것을 바꾸는 동작.
 * 대화 상태 전이는 전부 ./chat.ts 의 순수 리듀서가 한다. 여기서는 REST·WebSocket 을 엮기만 한다.
 */
import { create } from "zustand";
import { api, HttpError } from "../api/rest";
import type { BotInfo, BotStatusPayload, ClaudeStatus, HubEvent, SessionOut } from "../api/types";
import { HubSocket, type WsStatus } from "../api/ws";
import { addOptimisticUser, applyEvent, emptyChat, loadMessages, type ChatState } from "./chat";

export type Model = "sonnet" | "opus";
export type AuthState = "unknown" | "anon" | "authed";

export interface Notice {
  id: number;
  text: string;
  tone: "info" | "warn";
}

/** 봇 패널에서 여는 시트 (docs/05-ui.md 4장) */
export type Sheet = { kind: "login"; bot: string } | { kind: "logs"; bot: string };

export interface UiState {
  /** 모바일: 세션 목록 드로어 */
  drawer: boolean;
  /** 모바일: 봇 상태 바텀시트 */
  botSheet: boolean;
  /** 데스크톱: 봇 상태 패널 접힘 */
  botPanelCollapsed: boolean;
  sheet: Sheet | null;
}

export interface AppState {
  auth: AuthState;
  username: string | null;
  sessions: SessionOut[];
  /** null = 아직 DB 에 없는 새 세션(첫 메시지 전) */
  activeId: string | null;
  chats: Record<string, ChatState>;
  /** 허브 봇 목록(GET /api/bots, bots.yaml 순서) */
  bots: BotInfo[];
  botStatus: Record<string, BotStatusPayload>;
  /** 봇 상태 패널 하단 Claude Code 섹션 */
  claude: ClaudeStatus | null;
  ui: UiState;
  ws: WsStatus;
  notice: Notice | null;
  /** 세션별 모델(/model). 허브에 저장할 곳이 없어 이 브라우저에만 둔다 */
  models: Record<string, Model>;
}

const MODEL_KEY = "multibot.models";
const TITLE_LEN = 20;

function readModels(): Record<string, Model> {
  try {
    return JSON.parse(localStorage.getItem(MODEL_KEY) ?? "{}");
  } catch {
    return {};
  }
}

function writeModels(m: Record<string, Model>) {
  try {
    localStorage.setItem(MODEL_KEY, JSON.stringify(m));
  } catch {
    // 저장 불가(사생활 모드 등) — 이번 탭에서만 유지
  }
}

export const useStore = create<AppState>(() => ({
  auth: "unknown",
  username: null,
  sessions: [],
  activeId: null,
  chats: {},
  bots: [],
  botStatus: {},
  claude: null,
  ui: { drawer: false, botSheet: false, botPanelCollapsed: false, sheet: null },
  ws: "closed",
  notice: null,
  models: readModels(),
}));

const set = useStore.setState;
const get = useStore.getState;

// --- 공통 ---

let noticeSeq = 0;
export function notify(text: string, tone: Notice["tone"] = "info") {
  set({ notice: { id: ++noticeSeq, text, tone } });
}
export function clearNotice() {
  set({ notice: null });
}

function updateChat(sid: string, f: (c: ChatState) => ChatState) {
  set((s) => ({ chats: { ...s.chats, [sid]: f(s.chats[sid] ?? emptyChat()) } }));
}

function errorText(e: unknown): string {
  if (e instanceof HttpError) return `허브 오류 (${e.status} ${e.code})`;
  return "허브에 연결할 수 없습니다";
}

// --- 주소(새로고침 복원) ---

export function sessionFromHash(hash = window.location.hash): string | null {
  const m = /^#\/s\/([0-9a-f-]{36})$/i.exec(hash);
  return m ? m[1] : null;
}

function writeHash(sid: string | null) {
  const h = sid ? `#/s/${sid}` : "#/";
  if (window.location.hash !== h) history.replaceState(null, "", h);
}

// --- WebSocket ---

let socket: HubSocket | null = null;

function handleEvent(ev: HubEvent) {
  if (ev.type === "bot.status") {
    set((s) => ({ botStatus: { ...s.botStatus, [ev.payload.bot]: ev.payload } }));
    return;
  }
  const sid = ev.session_id;
  if (!sid) return;
  updateChat(sid, (c) => applyEvent(c, ev));
  if (ev.type === "message.ack") touchSession(sid, ev.ts);
  if (ev.type === "run.finished") void loadClaudeStatus();
}

function touchSession(sid: string, ts: string) {
  set((s) => {
    const cur = s.sessions.find((x) => x.id === sid);
    if (!cur) return {};
    const rest = s.sessions.filter((x) => x.id !== sid);
    return { sessions: [{ ...cur, last_message_at: ts }, ...rest] };
  });
}

/** 활성 세션을 REST 로 채우고 놓친 이벤트를 구독으로 받는다 */
async function syncActive() {
  const sid = get().activeId;
  if (!sid) return;
  const before = get().chats[sid] ?? emptyChat();
  try {
    const msgs = await api.listMessages(sid, before.lastMessageSeq);
    if (get().activeId !== sid) return;
    updateChat(sid, (c) => loadMessages(c, msgs));
  } catch (e) {
    if (e instanceof HttpError && e.status === 404) {
      notify("세션이 없습니다(삭제됨)", "warn");
      set((s) => ({ sessions: s.sessions.filter((x) => x.id !== sid) }));
      openSession(null);
      return;
    }
    notify(errorText(e), "warn");
  }
  const since = (get().chats[sid] ?? emptyChat()).lastEventSeq;
  socket?.send({ type: "subscribe", payload: { session_id: sid, since_seq: since } });
}

function startSocket() {
  socket?.stop();
  socket = new HubSocket({
    onEvent: handleEvent,
    onOpen: () => void syncActive(),
    onStatus: (ws) => {
      set({ ws });
      if (ws === "rejected") void checkAuth();
    },
  });
  socket.start();
}

export function reconnectNow() {
  socket?.reconnectNow();
}

// --- 인증·기동 ---

export async function checkAuth() {
  try {
    const me = await api.me();
    if (get().auth !== "authed") {
      set({ auth: "authed", username: me.username });
      await afterLogin();
    } else if (get().ws === "rejected") {
      notify("실시간 연결이 거부되었습니다. 새로고침해 주세요.", "warn");
    }
  } catch {
    logoutLocal();
  }
}

export function logoutLocal() {
  socket?.stop();
  socket = null;
  set({ auth: "anon", username: null, chats: {}, sessions: [], ws: "closed", botStatus: {}, claude: null });
}

export async function login(username: string, password: string, totp: string) {
  const me = await api.login(username, password, totp);
  set({ auth: "authed", username: me.username });
  await afterLogin();
}

export async function logout() {
  try {
    await api.logout();
  } finally {
    logoutLocal();
  }
}

async function afterLogin() {
  await Promise.all([loadSessions(), loadBots(), loadClaudeStatus()]);
  const fromHash = sessionFromHash();
  set({ activeId: fromHash && get().sessions.some((s) => s.id === fromHash) ? fromHash : null });
  writeHash(get().activeId);
  startSocket();
}

export async function loadSessions() {
  try {
    set({ sessions: await api.listSessions() });
  } catch (e) {
    notify(errorText(e), "warn");
  }
}

export async function loadBots() {
  try {
    set({ bots: await api.listBots() });
  } catch (e) {
    notify(errorText(e), "warn");
  }
}

export async function loadClaudeStatus() {
  try {
    set({ claude: await api.claudeStatus() });
  } catch {
    // 패널 보조 정보. 실패해도 알리지 않는다
  }
}

// --- 화면(드로어·시트) ---

export function setUi(patch: Partial<UiState>) {
  set((s) => ({ ui: { ...s.ui, ...patch } }));
}

export function openSheet(sheet: Sheet) {
  // 바텀시트 위에 겹쳐 열지 않는다(Esc 한 번에 둘 다 닫히는 것을 막는다)
  setUi({ sheet, botSheet: false });
}

export function closeSheet() {
  setUi({ sheet: null });
}

// --- 봇 관리 (허브가 어댑터 관리 HTTP 를 대신 부른다) ---

function knownBot(name: string): boolean {
  return get().bots.some((b) => b.name === name);
}

function putStatus(p: BotStatusPayload) {
  set((s) => ({ botStatus: { ...s.botStatus, [p.bot]: p } }));
}

export async function probeBot(bot: string) {
  try {
    putStatus(await api.probeBot(bot));
  } catch (e) {
    notify(errorText(e), "warn");
  }
}

export async function setBotEnabled(bot: string, enabled: boolean) {
  try {
    putStatus(await api.setBotEnabled(bot, enabled));
    notify(`${bot} ${enabled ? "활성화" : "비활성화"}`);
  } catch (e) {
    notify(errorText(e), "warn");
  }
}

/** /reset <봇>: 현재 세션의 그 봇 대화만 초기화. 새 세션(아직 DB 에 없음)이면 할 것이 없다 */
export async function resetBot(bot: string): Promise<boolean> {
  if (!knownBot(bot)) {
    notify(`알 수 없는 봇: ${bot}`, "warn");
    return false;
  }
  const sid = get().activeId;
  if (!sid) {
    notify("이 세션에는 아직 봇 대화가 없습니다");
    return true;
  }
  try {
    await api.resetBot(bot, sid);
    notify(`${bot} 대화를 초기화했습니다. 다음 질문부터 새 대화로 시작합니다`);
    return true;
  } catch (e) {
    notify(errorText(e), "warn");
    return false;
  }
}

/** /restart: 다음 실행부터 새 Claude Code 세션(이전 대화 요약을 넘긴다) */
export async function restartClaude(): Promise<boolean> {
  const sid = get().activeId;
  if (!sid) {
    notify("새 세션입니다. 첫 메시지가 새 Claude 세션으로 시작합니다");
    return true;
  }
  try {
    await api.restartSession(sid);
    notify("다음 메시지부터 새 Claude 세션으로 시작합니다");
    return true;
  } catch (e) {
    notify(errorText(e), "warn");
    return false;
  }
}

// --- 세션 ---

export function openSession(sid: string | null) {
  if (get().ui.drawer) setUi({ drawer: false });
  if (get().activeId === sid) return;
  set({ activeId: sid });
  writeHash(sid);
  if (sid && socket?.isOpen) void syncActive();
}

export async function renameSession(sid: string, title: string) {
  try {
    const s = await api.updateSession(sid, { title: title.trim() || null });
    set((st) => ({ sessions: st.sessions.map((x) => (x.id === sid ? s : x)) }));
  } catch (e) {
    notify(errorText(e), "warn");
  }
}

export async function archiveSession(sid: string) {
  try {
    await api.updateSession(sid, { archived: true });
    dropSession(sid);
  } catch (e) {
    notify(errorText(e), "warn");
  }
}

export async function deleteSession(sid: string) {
  try {
    await api.deleteSession(sid);
    dropSession(sid);
  } catch (e) {
    notify(errorText(e), "warn");
  }
}

function dropSession(sid: string) {
  set((s) => {
    const chats = { ...s.chats };
    delete chats[sid];
    return { sessions: s.sessions.filter((x) => x.id !== sid), chats };
  });
  if (get().activeId === sid) openSession(null);
}

// --- 전송 ---

export function modelOf(sid: string | null): Model | null {
  return sid ? (get().models[sid] ?? null) : null;
}

/** 사용자 메시지 전송. 성공하면 true(입력창을 비운다) */
export async function sendText(text: string): Promise<boolean> {
  if (!text.trim()) return false;
  if (!socket?.isOpen) {
    notify("허브와 연결이 끊겨 있습니다. 다시 연결되면 보내 주세요.", "warn");
    return false;
  }
  let sid = get().activeId;
  if (!sid) {
    // 빈 세션은 첫 메시지 때 만든다. 제목은 첫 메시지 앞 20자
    try {
      const s = await api.createSession(text.trim().replace(/\s+/g, " ").slice(0, TITLE_LEN));
      sid = s.id;
      set((st) => ({ sessions: [s, ...st.sessions], chats: { ...st.chats, [s.id]: emptyChat() }, activeId: s.id }));
      writeHash(s.id);
      socket.send({ type: "subscribe", payload: { session_id: s.id, since_seq: 0 } });
    } catch (e) {
      notify(errorText(e), "warn");
      return false;
    }
  }
  const model = modelOf(sid);
  const ok = socket.send({ type: "send", session_id: sid, payload: model ? { text, model } : { text } });
  if (!ok) {
    notify("전송하지 못했습니다. 연결을 확인해 주세요.", "warn");
    return false;
  }
  updateChat(sid, (c) => addOptimisticUser(c, text, new Date().toISOString()));
  return true;
}

export function stopRun(runId: string) {
  socket?.send({ type: "stop_run", payload: { run_id: runId } });
}

export function cancelQueued(runId: string) {
  socket?.send({ type: "cancel_queued", payload: { run_id: runId } });
}

export function setModel(sid: string | null, model: Model) {
  if (!sid) {
    notify("첫 메시지를 보낸 뒤에 모델을 바꿀 수 있습니다", "warn");
    return;
  }
  const models = { ...get().models, [sid]: model };
  writeModels(models);
  set({ models });
  notify(`다음 실행부터 ${model} 모델을 씁니다`);
}

// 테스트용
export const __test = { handleEvent, socketRef: () => socket };
