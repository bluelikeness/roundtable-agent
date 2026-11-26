import { parseCommand } from "../../lib/commands";
import {
  cancelQueued,
  modelOf,
  notify,
  openSession,
  openSheet,
  resetBot,
  restartClaude,
  sendText,
  setModel,
  stopRun,
  useStore,
} from "../../store";
import type { ReactNode } from "react";
import type { FileRef } from "../../api/types";
import { Composer } from "./Composer";
import { MessageList } from "./MessageList";
import { RunHeader } from "./RunHeader";

/** 슬래시 명령을 처리하거나 Claude 에 보낸다. 성공하면 true(입력창을 비운다) */
async function submit(text: string, sid: string | null, files: FileRef[] = []): Promise<boolean> {
  // 파일을 붙였으면 명령이 아니라 보내는 글이다
  const cmd = files.length ? null : parseCommand(text);
  if (!cmd) return sendText(text, files);
  const chat = sid ? useStore.getState().chats[sid] : undefined;
  switch (cmd.cmd) {
    case "new":
      openSession(null);
      return true;
    case "model":
      await setModel(sid, cmd.model);
      return true;
    case "stop":
      if (chat?.runs.current) stopRun(chat.runs.current);
      else notify("실행 중인 요청이 없습니다");
      return true;
    case "reset":
      return resetBot(cmd.bot);
    case "restart":
      return restartClaude();
    case "invalid":
      notify(cmd.message, "warn");
      return false;
  }
}

interface ChatViewProps {
  /** 헤더 왼쪽(모바일: 세션 드로어 버튼) */
  leading?: ReactNode;
  /** 헤더 오른쪽(모바일: 봇 상태 점) */
  trailing?: ReactNode;
}

export function ChatView({ leading, trailing }: ChatViewProps) {
  const activeId = useStore((s) => s.activeId);
  const chat = useStore((s) => (s.activeId ? s.chats[s.activeId] : undefined)) ?? null;
  const session = useStore((s) => s.sessions.find((x) => x.id === s.activeId));
  const ws = useStore((s) => s.ws);
  const bots = useStore((s) => s.bots);
  const notice = useStore((s) => s.notice);
  useStore((s) => s.draft); // 새 세션 모델 선택 갱신
  const queued = chat?.runs.queued ?? [];
  const title = activeId ? (session?.title ?? "새 세션") : "새 세션";

  return (
    <section className="chat">
      <RunHeader
        title={title}
        chat={chat}
        ws={ws}
        model={modelOf(activeId)}
        onModel={(m) => void setModel(activeId, m)}
        onStop={stopRun}
        leading={leading}
        trailing={trailing}
      />
      {chat && chat.items.length > 0 ? (
        <MessageList
          chat={chat}
          bots={bots}
          onRetry={(t) => void sendText(t)}
          onSend={(t) => void sendText(t)}
          onRelogin={(bot) => openSheet({ kind: "login", bot })}
        />
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
        <Composer bots={bots} onSubmit={(t, f) => submit(t, activeId, f)} />
      </div>
    </section>
  );
}
