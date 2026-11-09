import { useEffect, useLayoutEffect, useRef } from "react";
import type { BotInfo } from "../../api/types";
import { botColor } from "../../lib/colors";
import type { ChatItem, ChatState } from "../../store/chat";
import { parseStructured } from "../structured";
import { BotError, BotPending, BotReply, Final, Note, RunFailed, System, User } from "./bubbles";

interface Props {
  chat: ChatState;
  bots: BotInfo[];
  onRetry: (text: string) => void;
  /** 칩·[진행] 버튼: 입력을 대신 써서 보낸다 */
  onSend?: (text: string) => void;
  onRelogin?: (bot: string) => void;
}

/**
 * 봇별 가장 최근 응답 key 와 마지막 정리 key. 그보다 오래된 버튼은 만료(action_id 는 직전 응답에서만 유효).
 * confirm: 마지막 사용자 메시지 이후 봇 응답에 confirmation_required 가 있었는지(확정 대기 중).
 */
export function latestKeys(items: ChatItem[]): { bots: Record<string, string>; final: string | null; confirm: boolean } {
  const bots: Record<string, string> = {};
  let final: string | null = null;
  let confirm = false;
  for (const i of items) {
    if (i.kind === "bot" || i.kind === "pending") bots[i.bot] = i.key;
    if (i.kind === "bot" && i.ok && parseStructured(i.structured).confirmation_required) confirm = true;
    if (i.kind === "final") final = i.key;
    if (i.kind === "user") {
      final = null; // 이미 답했다
      confirm = false;
    }
  }
  return { bots, final, confirm };
}

export function MessageList({ chat, bots, onRetry, onSend, onRelogin }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const stick = useRef(true);

  // 맨 아래를 보고 있을 때만 새 항목을 따라 내려간다
  useLayoutEffect(() => {
    const el = ref.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [chat.items]);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const onScroll = () => {
      stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
    };
    el.addEventListener("scroll", onScroll);
    return () => el.removeEventListener("scroll", onScroll);
  }, []);

  const latest = latestKeys(chat.items);
  const render = (item: ChatItem) => {
    switch (item.kind) {
      case "user":
        return <User key={item.key} item={item} />;
      case "note":
        return <Note key={item.key} item={item} />;
      case "bot":
        return item.ok ? (
          <BotReply
            key={item.key}
            item={item}
            color={botColor(item.bot, bots)}
            active={latest.bots[item.bot] === item.key}
            onSend={onSend}
          />
        ) : (
          <BotError
            key={item.key}
            item={item}
            color={botColor(item.bot, bots)}
            onRelogin={onRelogin ? () => onRelogin(item.bot) : undefined}
          />
        );
      case "pending":
        return <BotPending key={item.key} item={item} color={botColor(item.bot, bots)} />;
      case "final":
        return (
          <Final
            key={item.key}
            item={item}
            latest={latest.final === item.key && !chat.runs.current}
            confirmPending={latest.confirm}
            onSend={onSend}
          />
        );
      case "failed": {
        const text = chat.runs.userText[item.runId];
        return <RunFailed key={item.key} item={item} onRetry={text ? () => onRetry(text) : undefined} />;
      }
      case "system":
        return <System key={item.key} item={item} />;
    }
  };

  return (
    <div className="messages" ref={ref} data-testid="messages">
      <div className="messages-inner">{chat.items.map(render)}</div>
    </div>
  );
}
