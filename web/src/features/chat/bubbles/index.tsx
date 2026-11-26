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
import { CANCEL_TEXT, CONFIRM_TEXT, isConfirmQuestion } from "../../../lib/confirm";
import { linkify } from "../../../lib/linkify";
import { useMobile } from "../../../lib/media";
import { absolute, elapsedSince, seconds } from "../../../lib/time";
import { ConfirmBox, hasInside, parseStructured, StructuredInside } from "../../structured";
import { FileList } from "../../files/FileList";

// 봇 원문 접기: 데스크톱 20줄, 모바일 10줄 (docs/05-ui.md 2·7장)
const RAW_FOLD_LINES = 20;
const RAW_FOLD_LINES_MOBILE = 10;
const NOTE_FOLD_CHARS = 160;

export function User({ item }: { item: UserItem }) {
  return (
    <div className="row right">
      <div className={`bubble user${item.optimistic ? " optimistic" : ""}`} title={absolute(item.ts)}>
        {item.text}
        {item.attachments && <FileList files={item.attachments} />}
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

interface BotReplyProps {
  item: BotItem;
  color: string;
  /** 이 봇의 가장 최근 응답인지(아니면 버튼·선택지 만료). 기본 true */
  active?: boolean;
  /** 칩 클릭 → 입력을 대신 써서 보낸다 */
  onSend?: (text: string) => void;
}

export function BotReply({ item, color, active = true, onSend }: BotReplyProps) {
  const mobile = useMobile();
  const fold = mobile ? RAW_FOLD_LINES_MOBILE : RAW_FOLD_LINES;
  const lines = item.raw.split("\n");
  const long = lines.length > fold;
  const [open, setOpen] = useState(false);
  const shown = long && !open ? lines.slice(0, fold).join("\n") : item.raw;
  const s = parseStructured(item.structured);
  return (
    <div className="row left column">
      <div
        className="bubble bot"
        style={{ borderColor: color, background: `color-mix(in srgb, ${color} 7%, var(--bg))` }}
        title={absolute(item.ts)}
      >
        <BotHead bot={item.bot} color={color} extra={item.latencyMs != null ? seconds(item.latencyMs) : undefined} />
        <div className="raw">{linkify(shown)}</div>
        {long && (
          <button className="link" onClick={() => setOpen(!open)}>
            {open ? "접기" : `더 보기 (${lines.length - fold}줄)`}
          </button>
        )}
        {hasInside(s) && (
          <div className="structured">
            <StructuredInside s={s} bot={item.bot} color={color} active={active && !!onSend} onSend={(t) => onSend?.(t)} />
          </div>
        )}
        {item.needsInput && <div className="tag">봇이 추가 정보를 묻고 있습니다</div>}
      </div>
      {s.confirmation_required && <ConfirmBox summary={s.confirmation_required.summary} />}
    </div>
  );
}

export function BotError({ item, color, onRelogin }: { item: BotItem; color: string; onRelogin?: () => void }) {
  const login = (item.error ?? "").includes("login_required");
  return (
    <div className="row left">
      <div className="bubble bot-error" title={absolute(item.ts)}>
        <BotHead bot={item.bot} color={color} />
        <div>{item.error || "응답 실패"}</div>
        {login && onRelogin && (
          <button className="btn small" onClick={onRelogin}>
            재로그인
          </button>
        )}
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
      <div className="bubble pending" style={{ borderColor: color }} title={absolute(item.ts)} aria-live="polite">
        <BotHead bot={item.bot} color={color} extra={elapsedSince(item.ts, now)} />
        <div className="muted">
          {item.queuePosition > 0 ? `대기 ${item.queuePosition}번째` : "응답 중"}
          <span className="dots" />
        </div>
      </div>
    </div>
  );
}

interface FinalProps {
  item: FinalItem;
  /** 세션의 마지막 정리인지. 지난 확정 질문에는 버튼을 붙이지 않는다. 기본 true */
  latest?: boolean;
  /** 이 정리 전 봇 응답에 confirmation_required 가 있었다(1차 신호). 정리 문구와 상관없이 [진행]을 붙인다 */
  confirmPending?: boolean;
  onSend?: (text: string) => void;
}

export function Final({ item, latest = true, confirmPending = false, onSend }: FinalProps) {
  // 1차 신호는 봇의 구조화 마커(confirmation_required), 문구 정규식은 마커를 못 뽑은 봇을 위한 보조 신호
  const confirm = latest && !!onSend && (confirmPending || isConfirmQuestion(item.text));
  return (
    <div className="row left">
      <div className="bubble final" title={absolute(item.ts)} aria-live="assertive">
        <div className="bubble-head">Claude 정리</div>
        <div className="markdown">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{item.text}</ReactMarkdown>
        </div>
        {confirm && (
          <div className="final-actions" data-testid="final-actions">
            <button className="btn primary small" onClick={() => onSend?.(CONFIRM_TEXT)}>
              진행
            </button>
            <button className="btn small" onClick={() => onSend?.(CANCEL_TEXT)}>
              취소
            </button>
          </div>
        )}
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
