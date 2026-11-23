// 열린 방 REST (docs/proposals/open-rooms.md). 운영자는 쿠키, 참가자는 x-key 헤더.
import { HttpError } from "./rest";

export type ParticipantKind = "human" | "ai";
export type ParticipantRole = "member" | "leader";

export interface RoomOut {
  id: string;
  name: string;
  created_at: string;
  closed_at: string | null;
  invite_on: boolean;
}

export interface ParticipantOut {
  id: string;
  name: string;
  kind: ParticipantKind;
  role: ParticipantRole;
  check_every_s: number;
  enabled: boolean;
  last_seen_at: string | null;
  created_at: string;
}

export interface ParticipantKeyOut extends ParticipantOut {
  key: string;
}

export interface RoomMsg {
  id: number;
  who: string;
  kind: ParticipantKind;
  text: string;
  ts: string;
}

export interface MsgsOut {
  room: string;
  room_id: string;
  msgs: RoomMsg[];
}

export interface PMsgsOut extends MsgsOut {
  me: string;
  kind: ParticipantKind;
  role: ParticipantRole;
  check_every_s: number;
}

export interface Person {
  name: string;
  kind: ParticipantKind;
  role?: ParticipantRole;
  enabled?: boolean;
  last_seen_at?: string | null;
}

async function call<T>(method: string, path: string, body?: unknown, key?: string): Promise<T> {
  const headers: Record<string, string> = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (key) headers["x-key"] = key;
  const res = await fetch(path, {
    method,
    credentials: key ? "omit" : "same-origin",
    headers,
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
    throw new HttpError(res.status, code);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

const enc = encodeURIComponent;

export const roomsApi = {
  list: () => call<RoomOut[]>("GET", "/api/rooms"),
  create: (name: string) => call<RoomOut>("POST", "/api/rooms", { name }),
  rename: (id: string, name: string) => call<RoomOut>("PATCH", `/api/rooms/${id}`, { name }),
  close: (id: string) => call<RoomOut>("DELETE", `/api/rooms/${id}`),
  messages: (id: string, since = 0) => call<MsgsOut>("GET", `/api/rooms/${id}/messages?since=${since}`),
  send: (id: string, text: string) => call<{ id: number }>("POST", `/api/rooms/${id}/messages`, { text }),
  participants: (id: string) => call<ParticipantOut[]>("GET", `/api/rooms/${id}/participants`),
  addParticipant: (id: string, body: { name: string; kind: ParticipantKind; check_every_s?: number }) =>
    call<ParticipantKeyOut>("POST", `/api/rooms/${id}/participants`, body),
  updateParticipant: (
    id: string,
    pid: string,
    body: { role?: ParticipantRole; enabled?: boolean; check_every_s?: number },
  ) => call<ParticipantOut>("PATCH", `/api/rooms/${id}/participants/${pid}`, body),
  rotateKey: (id: string, pid: string) => call<ParticipantKeyOut>("POST", `/api/rooms/${id}/participants/${pid}/rotate`),
  removeParticipant: (id: string, pid: string) => call<void>("DELETE", `/api/rooms/${id}/participants/${pid}`),
  makeInvite: (id: string) => call<{ invite: string }>("POST", `/api/rooms/${id}/invite`),
  stopInvite: (id: string) => call<RoomOut>("DELETE", `/api/rooms/${id}/invite`),
};

export const joinApi = {
  info: (invite: string) => call<{ room: string; people: Person[] }>("GET", `/api/join/${enc(invite)}`),
  join: (invite: string, name: string) =>
    call<{ key: string; me: string; room: string }>("POST", `/api/join/${enc(invite)}`, { name }),
};

export const participantApi = {
  msgs: (key: string, since = 0) => call<PMsgsOut>("GET", `/api/p/msgs?since=${since}`, undefined, key),
  send: (key: string, text: string) => call<{ id: number }>("POST", "/api/p/send", { text }, key),
  people: (key: string) => call<Person[]>("GET", "/api/p/participants", undefined, key),
};
