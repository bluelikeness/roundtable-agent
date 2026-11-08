// 봇 색: 허브 bots.yaml 의 color, 없으면 봇 순서대로 팔레트 (docs/05-ui.md 2장)
import type { BotInfo } from "../api/types";

export const PALETTE = ["#2f7ed8", "#d9822b", "#2e9d6b", "#9b59b6", "#c0392b", "#16a2b8", "#8a6d3b"];

function hash(s: string): number {
  let h = 0;
  for (const ch of s) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return h;
}

export function botColor(name: string, bots: BotInfo[]): string {
  const i = bots.findIndex((b) => b.name === name);
  if (i >= 0) return bots[i].color ?? PALETTE[i % PALETTE.length];
  return PALETTE[hash(name) % PALETTE.length];
}
