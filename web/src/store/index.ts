/**
 * 앱 스토어(zustand). 세션 목록·세션별 대화·봇·연결 상태와 그것을 바꾸는 동작.
 * 대화 상태 전이는 전부 ./chat.ts 의 순수 리듀서가 한다. 여기서는 REST·WebSocket 을 엮기만 한다.
 */
import { create } from "zustand";
import { api, HttpError } from "../api/rest";
import type { BotInfo, BotStatusPayload, ClaudeStatus, GroupOut, HubEvent, SessionOut } from "../api/types";
import { HubSocket, type WsStatus } from "../api/ws";
import { ackNeedsFetch, addOptimisticUser, applyEvent, emptyChat, loadMessages, type ChatState } from "./chat";

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
  /** 세션 그룹(GET /api/groups, position 순) */
  groups: GroupOut[];
  /** 휴지통의 세션(목록을 펼칠 때 불러온다) */
  trash: SessionOut[];
  /** 아직 DB 에 없는 새 세션에 쓸 모델·그룹(첫 메시지 때 세션과 함께 만든다) */
  draft: { model: Model | null; groupId: string | null };
}

// 세션별 모델은 허브(session.model)에 둔다. 예전에 이 브라우저에만 저장한 선택은 처음 불러올 때 허브로 옮긴다
const MODEL_KEY = "roundtable.models";
const OLD_MODEL_KEY = "multibot.models";
const TITLE_LEN = 20;

function readModels(): Record<string, Model> {
  try {
    return JSON.parse(localStorage.getItem(MODEL_KEY) ?? localStorage.getItem(OLD_MODEL_KEY) ?? "{}");
  } catch {
    return {};
  }
}

function clearLocalModels() {
  try {
    localStorage.removeItem(MODEL_KEY);
    localStorage.removeItem(OLD_MODEL_KEY);
  } catch {
    // 저장소를 못 쓰는 환경 — 무시
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
  groups: [],
  trash: [],
  draft: { model: null, groupId: null },
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

export function errorText(e: unknown): string {
  if (e instanceof HttpError) return `허브 오류 (${e.status} ${e.code})`;
  return "허브에 연결할 수 없습니다";
}

// --- 주소(새로고침 복원) ---

export function sessionFromHash(hash = window.location.hash): string | null {
  const m = /^#\/s\/([0-9a-f-]{36})$/i.exec(hash);
  return m ? m[1] : null;
}

function writeHash(sid: string | null) {
  // 열린 방·초대 화면에 있을 때는 세션 주소로 덮어쓰지 않는다
  if (/^#\/(rooms|join)(\/|$)/.test(window.location.hash)) return;
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
  const needFetch = ackNeedsFetch(get().chats[sid] ?? emptyChat(), ev);
  updateChat(sid, (c) => applyEvent(c, ev));
  if (ev.type === "message.ack") touchSession(sid, ev.ts);
  if (needFetch && get().activeId === sid) void fetchNewMessages(sid);
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

/** 바깥에서 들어온 사용자 메시지처럼 이벤트에 글이 없는 메시지를 REST 로 채운다 */
async function fetchNewMessages(sid: string) {
  try {
    const msgs = await api.listMessages(sid, (get().chats[sid] ?? emptyChat()).lastMessageSeq);
    updateChat(sid, (c) => loadMessages(c, msgs));
  } catch (e) {
    notify(errorText(e), "warn");
  }
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
    const [sessions, groups] = await Promise.all([api.listSessions(), api.listGroups()]);
    set({ sessions, groups });
    await migrateLocalModels(sessions);
  } catch (e) {
    notify(errorText(e), "warn");
  }
}

/** 예전 버전이 이 브라우저에만 저장한 세션별 모델을 허브로 옮긴다(허브에 값이 없는 세션만) */
async function migrateLocalModels(sessions: SessionOut[]) {
  const local = readModels();
  const todo = sessions.filter((s) => !s.model && local[s.id]);
  for (const s of todo) {
    try {
      replaceSession(await api.updateSession(s.id, { model: local[s.id] }));
    } catch {
      return; // 다음 접속 때 다시 시도
    }
  }
  if (Object.keys(local).length) clearLocalModels();
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

function replaceSession(s: SessionOut) {
  set((st) => ({ sessions: st.sessions.map((x) => (x.id === s.id ? s : x)) }));
}

/** 다른 그룹으로 옮기기. groupId=null 이면 그룹 없음 */
export async function moveSession(sid: string, groupId: string | null) {
  try {
    replaceSession(await api.updateSession(sid, { group_id: groupId }));
  } catch (e) {
    notify(errorText(e), "warn");
  }
}

/** 휴지통으로(되살릴 수 있다) */
export async function trashSession(sid: string) {
  try {
    const s = await api.updateSession(sid, { trashed: true });
    dropSession(sid);
    set((st) => ({ trash: [s, ...st.trash] }));
    notify("휴지통으로 옮겼습니다");
  } catch (e) {
    notify(errorText(e), "warn");
  }
}

export async function loadTrash() {
  try {
    set({ trash: (await api.listTrash()) ?? [] });
  } catch (e) {
    notify(errorText(e), "warn");
  }
}

export async function restoreSession(sid: string) {
  try {
    const s = await api.updateSession(sid, { trashed: false });
    set((st) => ({
      trash: st.trash.filter((x) => x.id !== sid),
      sessions: s.archived ? st.sessions : sortSessions([s, ...st.sessions]),
    }));
    notify("되살렸습니다");
  } catch (e) {
    notify(errorText(e), "warn");
  }
}

/** 영구 삭제(휴지통에 있는 세션만, 되돌릴 수 없다) */
export async function deleteForever(sid: string) {
  try {
    await api.deleteSession(sid);
    set((st) => ({ trash: st.trash.filter((x) => x.id !== sid) }));
  } catch (e) {
    notify(errorText(e), "warn");
  }
}

export async function emptyTrash() {
  try {
    await api.emptyTrash();
    set({ trash: [] });
  } catch (e) {
    notify(errorText(e), "warn");
  }
}

function sortSessions(list: SessionOut[]): SessionOut[] {
  const at = (s: SessionOut) => s.last_message_at ?? s.created_at;
  return [...list].sort((a, b) => (at(a) < at(b) ? 1 : at(a) > at(b) ? -1 : 0));
}

// --- 그룹 ---

export async function createGroup(name: string): Promise<GroupOut | null> {
  if (!name.trim()) return null;
  try {
    const g = await api.createGroup(name.trim());
    set((st) => ({ groups: [...st.groups, g] }));
    return g;
  } catch (e) {
    notify(errorText(e), "warn");
    return null;
  }
}

export async function renameGroup(gid: string, name: string) {
  if (!name.trim()) return;
  try {
    const g = await api.updateGroup(gid, { name: name.trim() });
    set((st) => ({ groups: st.groups.map((x) => (x.id === gid ? g : x)) }));
  } catch (e) {
    notify(errorText(e), "warn");
  }
}

/** 그룹만 지운다. 속한 세션은 그룹 없음이 된다 */
export async function deleteGroup(gid: string) {
  try {
    await api.deleteGroup(gid);
    set((st) => ({
      groups: st.groups.filter((x) => x.id !== gid),
      sessions: st.sessions.map((s) => (s.group_id === gid ? { ...s, group_id: null } : s)),
      draft: st.draft.groupId === gid ? { ...st.draft, groupId: null } : st.draft,
    }));
  } catch (e) {
    notify(errorText(e), "warn");
  }
}

/** 이 그룹에 새 세션(첫 메시지 때 그룹에 들어간 채로 만들어진다) */
export function newSessionIn(groupId: string | null) {
  set((st) => ({ draft: { ...st.draft, groupId } }));
  openSession(null);
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
  if (!sid) return get().draft.model;
  return get().sessions.find((s) => s.id === sid)?.model ?? null;
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
      const { model, groupId } = get().draft;
      const title = text.trim().replace(/\s+/g, " ").slice(0, TITLE_LEN);
      const s = await api.createSession(title, groupId, model);
      sid = s.id;
      set((st) => ({
        sessions: [s, ...st.sessions],
        chats: { ...st.chats, [s.id]: emptyChat() },
        activeId: s.id,
        draft: { model: null, groupId: null },
      }));
      writeHash(s.id);
      socket.send({ type: "subscribe", payload: { session_id: s.id, since_seq: 0 } });
    } catch (e) {
      notify(errorText(e), "warn");
      return false;
    }
  }
  // 모델은 허브가 세션에 저장된 값으로 정한다
  const ok = socket.send({ type: "send", session_id: sid, payload: { text } });
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

/** 세션 모델 저장(허브). 새 세션이면 첫 메시지 때 함께 만든다. null 이면 기본 */
export async function setModel(sid: string | null, model: Model | null) {
  if (!sid) {
    set((st) => ({ draft: { ...st.draft, model } }));
    notify(`이 세션은 ${model ?? "기본"} 모델을 씁니다`);
    return;
  }
  try {
    replaceSession(await api.updateSession(sid, { model }));
    notify(`다음 실행부터 ${model ?? "기본"} 모델을 씁니다`);
  } catch (e) {
    notify(errorText(e), "warn");
  }
}

// 테스트용
export const __test = { handleEvent, socketRef: () => socket };
