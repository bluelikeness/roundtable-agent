// 시간 표시. 머리글에는 소요·경과 초만, 절대 시각은 툴팁 (docs/05-ui.md 2장)

export function seconds(ms: number): string {
  return `${Math.max(0, Math.round(ms / 1000))}초`;
}

/** 서버 ts 기준 경과 초. 서버가 초마다 보내지 않으므로 클라이언트가 계산한다 */
export function elapsedSince(ts: string, now: number = Date.now()): string {
  return seconds(now - Date.parse(ts));
}

export function absolute(ts: string): string {
  const d = new Date(ts);
  return Number.isNaN(d.getTime()) ? ts : d.toLocaleString("ko-KR");
}

/** 세션 목록의 마지막 메시지 시각: 오늘이면 시:분, 아니면 월/일 */
export function shortTime(ts: string | null, now: Date = new Date()): string {
  if (!ts) return "";
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return "";
  if (d.toDateString() === now.toDateString()) {
    return d.toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit", hour12: false });
  }
  return `${d.getMonth() + 1}/${d.getDate()}`;
}
