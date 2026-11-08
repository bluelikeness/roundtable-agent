// @자동완성·색에 쓰는 봇 목록. 허브 목록이 없으면 대화·상태 이벤트에 나온 봇 이름으로 대신한다.
import type { BotInfo } from "../api/types";
import type { ChatState } from "../store/chat";

export function knownBots(
  hubBots: BotInfo[] | null,
  chats: Record<string, ChatState>,
  statusNames: string[],
): BotInfo[] {
  if (hubBots) return hubBots;
  const names = new Set<string>(statusNames);
  for (const c of Object.values(chats)) {
    for (const i of c.items) if (i.kind === "bot" || i.kind === "pending") names.add(i.bot);
  }
  return [...names].sort().map((name) => ({ name, aliases: [], color: null }));
}
