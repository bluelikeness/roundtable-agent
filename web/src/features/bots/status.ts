// 봇 상태 패널 행의 표시 값 (docs/05-ui.md 4장). bot.status 페이로드에서만 파생한다.
import type { BotStatusPayload } from "../../api/types";

/** 상태 점: 초록 연결, 노랑 로그인 필요·쿨다운, 회색 비활성·미확인, 빨강 backend_down */
export type Tone = "ok" | "warn" | "off" | "down";

export interface RowView {
  tone: Tone;
  /** 상태어 */
  label: string;
  /** 부제 */
  sub: string;
  /** 주 버튼 */
  action: "relogin" | "enable" | "menu";
  /** 문제가 있는 봇(헤더 점 느낌표 배지) */
  problem: boolean;
}

export function relative(ts: string | null | undefined, now: number = Date.now()): string | null {
  if (!ts) return null;
  const t = Date.parse(ts);
  if (Number.isNaN(t)) return null;
  const s = Math.max(0, Math.round((now - t) / 1000));
  if (s < 60) return "방금";
  if (s < 3600) return `${Math.floor(s / 60)}분 전`;
  if (s < 86400) return `${Math.floor(s / 3600)}시간 전`;
  return `${Math.floor(s / 86400)}일 전`;
}

export function rowView(st: BotStatusPayload | undefined, now: number = Date.now()): RowView {
  if (!st) return { tone: "off", label: "확인 중", sub: "상태를 아직 받지 못했습니다", action: "menu", problem: false };
  const parts: string[] = [];
  const last = relative(st.last_ok_at, now);
  if (last) parts.push(`마지막 응답 ${last}`);
  if (st.queue_depth > 0) parts.push(`큐 ${st.queue_depth}`);
  if (st.avg_latency_ms) parts.push(`평균 ${Math.round(st.avg_latency_ms / 1000)}초`);
  const sub = parts.join(" · ");
  if (!st.enabled || st.reason === "disabled") return { tone: "off", label: "비활성", sub, action: "enable", problem: false };
  switch (st.reason) {
    case "login_required":
      return { tone: "warn", label: "로그인 필요", sub, action: "relogin", problem: true };
    case "cooldown":
      return { tone: "warn", label: "쿨다운", sub, action: "menu", problem: true };
    case "backend_down":
      return { tone: "down", label: "점검 필요", sub, action: "menu", problem: true };
  }
  if (!st.available) return { tone: "down", label: "점검 필요", sub, action: "menu", problem: true };
  return { tone: "ok", label: st.queue_depth > 0 ? "응답 중" : "연결", sub, action: "menu", problem: false };
}

export function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}
