import { parseCommand } from "../../lib/commands";
import { knownBots } from "../../lib/bots";
import {
  cancelQueued,
  modelOf,
  notify,
  openSession,
  sendText,
  setModel,
  stopRun,
  useStore,
} from "../../store";
import { Composer } from "./Composer";
import { MessageList } from "./MessageList";
import { RunHeader } from "./RunHeader";

/** 슬래시 명령을 처리하거나 Claude 에 보낸다. 성공하면 true(입력창을 비운다) */
async function submit(text: string, sid: string | null): Promise<boolean> {
  const cmd = parseCommand(text);
  if (!cmd) return sendText(text);
  const chat = sid ? useStore.getState().chats[sid] : undefined;
  switch (cmd.cmd) {
    case "new":
      openSession(null);
      return true;
    case "model":
      setModel(sid, cmd.model);
      return true;
    case "stop":
      if (chat?.runs.current) stopRun(chat.runs.current);
      else notify("실행 중인 요청이 없습니다");
      return true;
    case "reset":
    case "restart":
      // 허브에 해당 API 가 아직 없다(P1-5 보고 참고). 텍스트를 Claude 에 보내지 않는다
      notify(`${cmd.cmd === "reset" ? "/reset" : "/restart"} 은 아직 허브 API 가 없어 쓸 수 없습니다`, "warn");
      return false;
    case "invalid":
      notify(cmd.message, "warn");
      return false;
  }
}

export function ChatView() {
  const activeId = useStore((s) => s.activeId);
  const chat = useStore((s) => (s.activeId ? s.chats[s.activeId] : undefined)) ?? null;
  const session = useStore((s) => s.sessions.find((x) => x.id === s.activeId));
  const ws = useStore((s) => s.ws);
  const hubBots = useStore((s) => s.bots);
  const chats = useStore((s) => s.chats);
  const botStatus = useStore((s) => s.botStatus);
  const notice = useStore((s) => s.notice);
  useStore((s) => s.models); // 모델 배지 갱신
  const bots = knownBots(hubBots, chats, Object.keys(botStatus));
  const queued = chat?.runs.queued ?? [];
  const title = activeId ? (session?.title ?? "새 세션") : "새 세션";

  return (
    <section className="chat">
      <RunHeader title={title} chat={chat} ws={ws} model={modelOf(activeId)} onStop={stopRun} />
      {chat && chat.items.length > 0 ? (
        <MessageList chat={chat} bots={bots} onRetry={(t) => void sendText(t)} />
      ) : (
        <div className="messages empty">
          <p className="muted">무엇을 도와드릴까요? @로 봇을 지정하고 /로 명령을 씁니다.</p>
        </div>
      )}
      <div className="composer-area">
        {queued.length > 0 && (
          <div className="queue-bar" data-testid="queue-bar">
            <span>이전 요청 처리 중 — 대기 {queued.length}</span>
            <button className="link" onClick={() => cancelQueued(queued[queued.length - 1])}>
              대기 취소
            </button>
          </div>
        )}
        {notice && (
          <div key={notice.id} className={`notice ${notice.tone}`} role="status">
            {notice.text}
          </div>
        )}
        <Composer bots={bots} onSubmit={(t) => submit(t, activeId)} />
      </div>
    </section>
  );
}
