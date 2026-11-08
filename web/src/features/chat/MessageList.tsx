import { useEffect, useLayoutEffect, useRef } from "react";
import type { BotInfo } from "../../api/types";
import { botColor } from "../../lib/colors";
import type { ChatItem, ChatState } from "../../store/chat";
import { BotError, BotPending, BotReply, Final, Note, RunFailed, System, User } from "./bubbles";

interface Props {
  chat: ChatState;
  bots: BotInfo[];
  onRetry: (text: string) => void;
}

export function MessageList({ chat, bots, onRetry }: Props) {
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

  const render = (item: ChatItem) => {
    switch (item.kind) {
      case "user":
        return <User key={item.key} item={item} />;
      case "note":
        return <Note key={item.key} item={item} />;
      case "bot":
        return item.ok ? (
          <BotReply key={item.key} item={item} color={botColor(item.bot, bots)} />
        ) : (
          <BotError key={item.key} item={item} color={botColor(item.bot, bots)} />
        );
      case "pending":
        return <BotPending key={item.key} item={item} color={botColor(item.bot, bots)} />;
      case "final":
        return <Final key={item.key} item={item} />;
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
