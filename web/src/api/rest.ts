// 허브 REST fetch 래퍼. 인증은 HttpOnly 쿠키라 credentials 만 맞추면 된다.
import type { BotInfo, BotLogEntry, BotStatusPayload, ClaudeStatus, LoginStart, PhoneOut, LoginSubmitResult, MessageOut, SessionOut, GroupOut, ModelName } from "./types";

export class HttpError extends Error {
  constructor(
    public status: number,
    public code: string,
  ) {
    super(`${status} ${code}`);
  }
}

/** 로그인이 풀렸을 때 앱이 로그인 화면으로 돌아가게 한다 */
let onUnauthorized: (() => void) | null = null;
export function setUnauthorizedHandler(f: (() => void) | null) {
  onUnauthorized = f;
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method,
    credentials: "same-origin",
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    let code = res.statusText;
    try {
      const data = await res.json();
      if (typeof data?.detail === "string") code = data.detail;
    } catch {
      // 본문 없음
    }
    if (res.status === 401 && path !== "/api/auth/login") onUnauthorized?.();
    throw new HttpError(res.status, code);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  me: () => request<{ username: string }>("GET", "/api/auth/me"),
  login: (username: string, password: string, totp: string) =>
    request<{ username: string }>("POST", "/api/auth/login", { username, password, totp }),
  logout: () => request<void>("POST", "/api/auth/logout"),

  listSessions: () => request<SessionOut[]>("GET", "/api/sessions"),
  listTrash: () => request<SessionOut[]>("GET", "/api/sessions?trashed=true"),
  createSession: (title: string | null, groupId: string | null = null, model: ModelName | null = null) =>
    request<SessionOut>("POST", "/api/sessions", { title, group_id: groupId, model }),
  updateSession: (
    id: string,
    body: { title?: string | null; archived?: boolean; group_id?: string | null; model?: ModelName | null; trashed?: boolean },
  ) => request<SessionOut>("PATCH", `/api/sessions/${id}`, body),
  /** 영구 삭제(휴지통에 있는 세션만) */
  deleteSession: (id: string) => request<void>("DELETE", `/api/sessions/${id}`),
  emptyTrash: () => request<void>("DELETE", "/api/trash"),
  listGroups: () => request<GroupOut[]>("GET", "/api/groups"),
  createGroup: (name: string) => request<GroupOut>("POST", "/api/groups", { name }),
  updateGroup: (id: string, body: { name?: string; position?: number }) =>
    request<GroupOut>("PATCH", `/api/groups/${id}`, body),
  deleteGroup: (id: string) => request<void>("DELETE", `/api/groups/${id}`),
  listMessages: (id: string, sinceSeq = 0) =>
    request<MessageOut[]>("GET", `/api/sessions/${id}/messages?since_seq=${sinceSeq}`),

  listBots: () => request<BotInfo[]>("GET", "/api/bots"),

  // 봇 관리 (허브가 어댑터 관리 HTTP 를 대신 부른다, docs/05-ui.md 4장)
  loginStart: (bot: string) => request<LoginStart>("POST", `/api/bots/${enc(bot)}/login/start`),
  loginSubmit: (bot: string, body: { code: string } | { password: string }) =>
    request<LoginSubmitResult>("POST", `/api/bots/${enc(bot)}/login/submit`, body),
  loginComplete: (bot: string) => request<{ ok: boolean }>("POST", `/api/bots/${enc(bot)}/login/complete`),
  probeBot: (bot: string) => request<BotStatusPayload>("POST", `/api/bots/${enc(bot)}/probe`),
  setBotEnabled: (bot: string, enabled: boolean) =>
    request<BotStatusPayload>("POST", `/api/bots/${enc(bot)}/${enabled ? "enable" : "disable"}`),
  resetBot: (bot: string, sessionId: string | null) =>
    request<{ ok: boolean }>("POST", `/api/bots/${enc(bot)}/reset`, sessionId ? { session_id: sessionId } : {}),
  botLogs: (bot: string) => request<BotLogEntry[]>("GET", `/api/bots/${enc(bot)}/logs`),
  restartSession: (id: string) => request<{ ok: boolean; had_session: boolean }>("POST", `/api/sessions/${id}/restart`),
  claudeStatus: () => request<ClaudeStatus>("GET", "/api/claude/status"),
  phone: () => request<PhoneOut>("GET", "/api/phone"),
  setPhone: (device: string) => request<PhoneOut>("PUT", "/api/phone/prefer", { device }),
};

const enc = encodeURIComponent;

/** 봇 이미지 URL. 어댑터 로컬 경로(/images/<id>)는 허브 프록시로 바꾼다(docs/05-ui.md 3장) */
export function imageSrc(bot: string, url: string): string | null {
  const m = /^\/images\/([0-9A-Za-z_-]+)$/.exec(url);
  if (m) return `/api/bots/${enc(bot)}/images/${m[1]}`;
  if (/^https:\/\//.test(url)) return url;
  return null;
}
