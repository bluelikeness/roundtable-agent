// 서버 이벤트 타입은 make types 가 만든 src/types/events.ts 를 그대로 쓴다.
// 여기에는 REST 응답 타입만 손으로 둔다(hub/app/schemas.py 와 맞춘다).
export type {
  HubEvent,
  ClientMessage,
  MessageAck,
  RunStarted,
  ClaudeNote,
  BotPending,
  BotReply,
  ClaudeFinal,
  RunFinished,
  BotStatus,
  BotStatusPayload,
  SessionSystem,
} from "../types/events";

/** GET /api/sessions 항목 (hub/app/schemas.py SessionOut) */
export interface SessionOut {
  id: string;
  title: string | null;
  created_at: string;
  last_message_at: string | null;
  archived: boolean;
}

export type MessageRole = "user" | "claude" | "bot" | "system";

/** GET /api/sessions/<id>/messages 항목 (hub/app/schemas.py MessageOut) */
export interface MessageOut {
  id: string;
  session_id: string;
  role: MessageRole;
  bot_name: string | null;
  raw: string;
  structured: Record<string, unknown> | null;
  seq: number;
  created_at: string;
}

/** 봇 목록(@자동완성·색). 허브 bots.yaml 의 name·aliases·color */
export interface BotInfo {
  name: string;
  aliases: string[];
  color: string | null;
}
