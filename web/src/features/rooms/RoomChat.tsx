// 열린 방 대화 영역: 글 목록 + 입력창. 운영자 화면과 초대받은 사람 화면이 같이 쓴다.
import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import type { RoomMsg } from "../../api/rooms";
import { botColor } from "../../lib/colors";
import { absolute } from "../../lib/time";

interface Props {
  msgs: RoomMsg[];
  me: string | null;
  disabled?: boolean;
  placeholder?: string;
  onSend: (text: string) => Promise<boolean>;
}

export function RoomMessages({ msgs, me }: { msgs: RoomMsg[]; me: string | null }) {
  const ref = useRef<HTMLDivElement>(null);
  const stick = useRef(true);

  useEffect(() => {
    const el = ref.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [msgs.length]);

  return (
    <div
      className="room-msgs"
      ref={ref}
      data-testid="room-msgs"
      onScroll={(e) => {
        const el = e.currentTarget;
        stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
      }}
    >
      {msgs.length === 0 && <p className="muted room-empty">아직 글이 없어요. 먼저 말을 걸어 보세요.</p>}
      {msgs.map((m) => {
        const mine = me !== null && m.who === me;
        return (
          <div key={m.id} className={`room-msg ${m.kind} ${mine ? "mine" : ""}`} data-testid="room-msg">
            {!mine && (
              <div className="room-who" style={m.kind === "ai" ? { color: botColor(m.who, []) } : undefined}>
                {m.who}
                {m.kind === "ai" && <span className="room-tag">AI</span>}
              </div>
            )}
            <div className="room-text" title={absolute(m.ts)}>
              {m.text}
            </div>
          </div>
        );
      })}
    </div>
  );
}

export function RoomComposer({ disabled, placeholder, onSend }: Omit<Props, "msgs" | "me">) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    const t = text.trim();
    if (!t || busy) return;
    setBusy(true);
    try {
      if (await onSend(t)) setText("");
    } finally {
      setBusy(false);
    }
  };

  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      void submit();
    }
  };

  return (
    <form
      className="room-composer"
      onSubmit={(e) => {
        e.preventDefault();
        void submit();
      }}
    >
      <textarea
        rows={2}
        value={text}
        disabled={disabled}
        placeholder={placeholder ?? "메시지 입력 (@이름으로 부르기, Enter 보내기)"}
        aria-label="메시지 입력"
        onChange={(e) => setText(e.target.value)}
        onKeyDown={onKey}
      />
      <button className="btn primary" disabled={disabled || busy || !text.trim()}>
        보내기
      </button>
    </form>
  );
}

export function RoomChat({ msgs, me, disabled, placeholder, onSend }: Props) {
  return (
    <>
      <RoomMessages msgs={msgs} me={me} />
      <RoomComposer disabled={disabled} placeholder={placeholder} onSend={onSend} />
    </>
  );
}

/** 새 글을 since 이후로 이어 붙인다(중복 id 는 버린다) */
export function mergeMsgs(prev: RoomMsg[], next: RoomMsg[]): RoomMsg[] {
  if (!next.length) return prev;
  const seen = new Set(prev.map((m) => m.id));
  const add = next.filter((m) => !seen.has(m.id));
  return add.length ? [...prev, ...add] : prev;
}
