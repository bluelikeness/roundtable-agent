import type { WsStatus } from "../../api/ws";
import { headerStatus, type ChatState } from "../../store/chat";

interface Props {
  title: string;
  chat: ChatState | null;
  ws: WsStatus;
  model: string | null;
  onStop: (runId: string) => void;
}

export function statusText(chat: ChatState | null): string {
  if (!chat) return "";
  const h = headerStatus(chat);
  switch (h.kind) {
    case "running": {
      const parts = ["실행 중"];
      if (h.asked > 0) parts.push(`${h.replied}/${h.asked} 응답`);
      if (h.queued > 0) parts.push(`대기 ${h.queued}`);
      return parts.join(" · ");
    }
    case "queued":
      return `대기 ${h.queued}`;
    case "done":
      return "완료";
    case "failed":
      return h.status === "timeout" ? "시간 초과" : "실패";
    case "idle":
      return "";
  }
}

const WS_TEXT: Record<WsStatus, string> = {
  open: "",
  connecting: "연결 중…",
  closed: "연결 끊김 · 다시 연결 중",
  rejected: "연결 거부됨",
};

export function RunHeader({ title, chat, ws, model, onStop }: Props) {
  const status = statusText(chat);
  const current = chat?.runs.current ?? null;
  return (
    <header className="run-header">
      <div className="title" title={title}>
        {title}
      </div>
      <div className="status" data-testid="run-status">
        {status}
      </div>
      {model && <span className="badge">{model}</span>}
      {WS_TEXT[ws] && <span className={`badge ${ws === "open" ? "" : "warn"}`}>{WS_TEXT[ws]}</span>}
      {current && (
        <button className="btn small" onClick={() => onStop(current)}>
          중단
        </button>
      )}
    </header>
  );
}
