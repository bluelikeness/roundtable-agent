// 말풍선 7종 (docs/05-ui.md 2장). 실행 실패 띠는 System 의 경고 변형이다.
import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type {
  BotItem,
  FailedItem,
  FinalItem,
  NoteItem,
  PendingItem,
  SystemItem,
  UserItem,
} from "../../../store/chat";
import { absolute, elapsedSince, seconds } from "../../../lib/time";

const RAW_FOLD_LINES = 20;
const NOTE_FOLD_CHARS = 160;

export function User({ item }: { item: UserItem }) {
  return (
    <div className="row right">
      <div className={`bubble user${item.optimistic ? " optimistic" : ""}`} title={absolute(item.ts)}>
        {item.text}
      </div>
    </div>
  );
}

export function Note({ item }: { item: NoteItem }) {
  const long = item.text.length > NOTE_FOLD_CHARS || item.text.split("\n").length > 2;
  const [open, setOpen] = useState(false);
  return (
    <div className="row left">
      <div className={`bubble note${long && !open ? " folded" : ""}`} title={absolute(item.ts)}>
        <span className="prefix">Claude · </span>
        {item.text}
        {long && (
          <button className="link" onClick={() => setOpen(!open)}>
            {open ? "접기" : "펼치기"}
          </button>
        )}
      </div>
    </div>
  );
}

function BotHead({ bot, color, extra }: { bot: string; color: string; extra?: string }) {
  return (
    <div className="bubble-head" style={{ color }}>
      <span className="bot-name">{bot}</span>
      {extra && <span className="muted"> · {extra}</span>}
    </div>
  );
}

export function BotReply({ item, color }: { item: BotItem; color: string }) {
  const lines = item.raw.split("\n");
  const long = lines.length > RAW_FOLD_LINES;
  const [open, setOpen] = useState(false);
  const shown = long && !open ? lines.slice(0, RAW_FOLD_LINES).join("\n") : item.raw;
  return (
    <div className="row left">
      <div
        className="bubble bot"
        style={{ borderColor: color, background: `color-mix(in srgb, ${color} 7%, var(--bg))` }}
        title={absolute(item.ts)}
      >
        <BotHead bot={item.bot} color={color} extra={item.latencyMs != null ? seconds(item.latencyMs) : undefined} />
        <div className="raw">{shown}</div>
        {item.needsInput && <div className="tag">봇이 추가 정보를 묻고 있습니다</div>}
        {long && (
          <button className="link" onClick={() => setOpen(!open)}>
            {open ? "접기" : `더 보기 (${lines.length - RAW_FOLD_LINES}줄)`}
          </button>
        )}
      </div>
    </div>
  );
}

export function BotError({ item, color }: { item: BotItem; color: string }) {
  return (
    <div className="row left">
      <div className="bubble bot-error" title={absolute(item.ts)}>
        <BotHead bot={item.bot} color={color} />
        <div>{item.error || "응답 실패"}</div>
      </div>
    </div>
  );
}

/** 1초마다 다시 그려 경과 초를 올린다 */
function useNow(intervalMs = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(t);
  }, [intervalMs]);
  return now;
}

export function BotPending({ item, color }: { item: PendingItem; color: string }) {
  const now = useNow();
  return (
    <div className="row left">
      <div className="bubble pending" style={{ borderColor: color }} title={absolute(item.ts)}>
        <BotHead bot={item.bot} color={color} extra={elapsedSince(item.ts, now)} />
        <div className="muted">
          {item.queuePosition > 0 ? `대기 ${item.queuePosition}번째` : "응답 중"}
          <span className="dots" />
        </div>
      </div>
    </div>
  );
}

export function Final({ item }: { item: FinalItem }) {
  return (
    <div className="row left">
      <div className="bubble final" title={absolute(item.ts)}>
        <div className="bubble-head">Claude 정리</div>
        <div className="markdown">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{item.text}</ReactMarkdown>
        </div>
      </div>
    </div>
  );
}

const FAIL_TEXT: Record<string, string> = {
  timeout: "시간 초과",
  cancelled: "대기 중 취소됨",
  hub_restarted: "허브 재시작으로 중단됨",
  runner_stream_ended: "러너 연결이 끊김",
  internal: "내부 오류",
};

export function RunFailed({ item, onRetry }: { item: FailedItem; onRetry?: () => void }) {
  const reason = item.status === "timeout" ? FAIL_TEXT.timeout : (FAIL_TEXT[item.error ?? ""] ?? item.error ?? "실패");
  return (
    <div className="row center">
      <div className="band failed" title={absolute(item.ts)}>
        <span>실행 실패 · {reason}</span>
        {onRetry && (
          <button className="link" onClick={onRetry}>
            다시 시도
          </button>
        )}
      </div>
    </div>
  );
}

export function System({ item }: { item: SystemItem }) {
  return (
    <div className="row center">
      <div className="band system" title={absolute(item.ts)}>
        {item.text}
      </div>
    </div>
  );
}
