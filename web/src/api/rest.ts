// 허브 REST fetch 래퍼. 인증은 HttpOnly 쿠키라 credentials 만 맞추면 된다.
import type { BotInfo, MessageOut, SessionOut } from "./types";

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
  createSession: (title: string | null) => request<SessionOut>("POST", "/api/sessions", { title }),
  updateSession: (id: string, body: { title?: string | null; archived?: boolean }) =>
    request<SessionOut>("PATCH", `/api/sessions/${id}`, body),
  deleteSession: (id: string) => request<void>("DELETE", `/api/sessions/${id}`),
  listMessages: (id: string, sinceSeq = 0) =>
    request<MessageOut[]>("GET", `/api/sessions/${id}/messages?since_seq=${sinceSeq}`),

  listBots: () => request<BotInfo[]>("GET", "/api/bots"),
};
