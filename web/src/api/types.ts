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
export type ModelName = "sonnet" | "opus";

export interface SessionOut {
  id: string;
  title: string | null;
  created_at: string;
  last_message_at: string | null;
  archived: boolean;
  /** 세션 그룹. null = 그룹 없음 */
  group_id: string | null;
  /** 세션별 Claude 모델. null = 기본(sonnet) */
  model: ModelName | null;
  /** 휴지통으로 옮긴 시각. null = 휴지통 아님 */
  trashed_at: string | null;
}

/** GET /api/groups 항목 (hub/app/schemas.py GroupOut) */
export interface GroupOut {
  id: string;
  name: string;
  position: number;
  created_at: string;
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

/** POST /api/bots/<bot>/login/start. flow 로 시트가 CodeLogin·VncLogin 을 고른다 */
export interface LoginStart {
  flow: "code" | "vnc";
  hint: string | null;
  /** 허브가 프록시하는 noVNC 주소. P2 전까지 null */
  vnc_url: string | null;
}

export interface LoginSubmitResult {
  ok: boolean;
  /** 다음 단계(password 등) */
  next?: string;
}

/** GET /api/bots/<bot>/logs 항목(어댑터 GET /logs). 디버깅용이라 느슨하게 받는다 */
export type BotLogEntry = Record<string, unknown>;

/** GET /api/claude/status: 봇 상태 패널 하단 Claude Code 섹션 */
export interface ClaudeStatus {
  runner_ok: boolean;
  runs_today: number;
  tokens_today: number;
}

/** structured (docs/03-adapters.md 3장). 어댑터가 뽑을 수 있는 것만 온다 */
export interface StructuredButton {
  label: string;
  action_id: string;
  kind?: "callback" | "url" | "reply";
}
export interface StructuredLink {
  title: string;
  url: string;
}
export interface StructuredCard {
  title: string;
  subtitle?: string | null;
  body?: string | null;
  url?: string | null;
  image_url?: string | null;
}
export interface StructuredImage {
  url: string;
  alt?: string | null;
}
export interface Structured {
  buttons?: StructuredButton[];
  links?: StructuredLink[];
  cards?: StructuredCard[];
  images?: StructuredImage[];
  choices?: string[];
  confirmation_required?: { summary: string; action_id?: string | null } | null;
}
