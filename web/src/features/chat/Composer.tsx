/**
 * 입력창 (docs/05-ui.md 5장): Enter 전송, Shift+Enter 줄바꿈, @봇 자동완성, / 명령 자동완성.
 * @봇 은 텍스트일 뿐이고 해석은 Claude 가 한다.
 *
 * 📎 로 파일을 붙인다: 고르는 즉시 허브 파일 보관함에 올리고(POST /api/files), 보낼 때는 ID 만 보낸다.
 */
import { useMemo, useRef, useState, type KeyboardEvent } from "react";
import { HttpError, uploadFile } from "../../api/rest";
import type { BotInfo, FileRef } from "../../api/types";
import { COMMANDS } from "../../lib/commands";
import { humanSize } from "../files/FileList";

/** 한 메시지에 붙이는 최대 개수(허브 MAX_ATTACHMENTS) */
export const MAX_FILES = 5;

interface Pending {
  key: number;
  name: string;
  size: number;
  ref?: FileRef;
  error?: string;
}

interface Suggestion {
  label: string;
  hint: string;
  /** 고르면 [start, caret) 구간을 이 문자열로 바꾼다 */
  insert: string;
}

interface Props {
  bots: BotInfo[];
  disabled?: boolean;
  onSubmit: (text: string, files: FileRef[]) => Promise<boolean>;
}

interface Trigger {
  start: number;
  items: Suggestion[];
}

export function findTrigger(text: string, caret: number, bots: BotInfo[]): Trigger | null {
  const before = text.slice(0, caret);
  // 명령: 맨 앞 "/단어" 를 치는 중
  const cmd = /^\/[a-z]*$/.exec(before);
  if (cmd) {
    const items = COMMANDS.filter((c) => c.name.startsWith(before)).map((c) => ({
      label: c.args ? `${c.name} ${c.args}` : c.name,
      hint: c.help,
      insert: c.args ? `${c.name} ` : c.name,
    }));
    return items.length ? { start: 0, items } : null;
  }
  // 봇: 공백 뒤(또는 맨 앞) "@글자" 를 치는 중
  const at = /(^|\s)@([^\s@]*)$/.exec(before);
  if (at) {
    const q = at[2].toLowerCase();
    const items = bots
      .filter((b) => b.name.toLowerCase().startsWith(q) || b.aliases.some((a) => a.toLowerCase().startsWith(q)))
      // 별칭으로 찾아도 정식 이름을 넣는다(Claude 가 툴 이름과 바로 맞춘다)
      .map((b) => ({ label: `@${b.name}`, hint: b.aliases.join(", "), insert: `@${b.name} ` }));
    return items.length ? { start: before.length - at[2].length - 1, items } : null;
  }
  return null;
}

export function Composer({ bots, disabled, onSubmit }: Props) {
  const [text, setText] = useState("");
  const [caret, setCaret] = useState(0);
  const [active, setActive] = useState(0);
  const [dismissed, setDismissed] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [files, setFiles] = useState<Pending[]>([]);
  const ref = useRef<HTMLTextAreaElement>(null);
  const picker = useRef<HTMLInputElement>(null);
  const counter = useRef(0);

  const uploading = files.some((f) => !f.ref && !f.error);
  const ready = files.filter((f) => f.ref).map((f) => f.ref!);
  const canSend = !busy && !uploading && (!!text.trim() || ready.length > 0);

  const addFiles = (list: FileList | null) => {
    if (!list) return;
    const room = MAX_FILES - files.length;
    for (const file of Array.from(list).slice(0, Math.max(0, room))) {
      const key = ++counter.current;
      setFiles((fs) => [...fs, { key, name: file.name, size: file.size }]);
      uploadFile(file).then(
        (r) => setFiles((fs) => fs.map((f) => (f.key === key ? { ...f, ref: r } : f))),
        (e) => {
          const msg = e instanceof HttpError && e.status === 413 ? "너무 큼" : "올리기 실패";
          setFiles((fs) => fs.map((f) => (f.key === key ? { ...f, error: msg } : f)));
        },
      );
    }
  };

  const trigger = useMemo(() => {
    const t = findTrigger(text, caret, bots);
    return t && dismissed !== text ? t : null;
  }, [text, caret, bots, dismissed]);

  const pick = (s: Suggestion) => {
    if (!trigger) return;
    const next = text.slice(0, trigger.start) + s.insert + text.slice(caret);
    const pos = trigger.start + s.insert.length;
    setText(next);
    setCaret(pos);
    setActive(0);
    requestAnimationFrame(() => {
      ref.current?.focus();
      ref.current?.setSelectionRange(pos, pos);
    });
  };

  const submit = async () => {
    if (!canSend) return;
    setBusy(true);
    try {
      if (await onSubmit(text, ready)) {
        setText("");
        setCaret(0);
        setFiles([]);
      }
    } finally {
      setBusy(false);
    }
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    // 한글 조합 중 Enter 는 글자 확정이다
    if (e.nativeEvent.isComposing || e.keyCode === 229) return;
    if (trigger) {
      const n = trigger.items.length;
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setActive((active + 1) % n);
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setActive((active - 1 + n) % n);
        return;
      }
      if (e.key === "Tab" || (e.key === "Enter" && !e.shiftKey)) {
        e.preventDefault();
        pick(trigger.items[Math.min(active, n - 1)]);
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        setDismissed(text);
        return;
      }
    }
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void submit();
    }
  };

  return (
    <div className="composer">
      {files.length > 0 && (
        <ul className="attach-list" data-testid="attach-list">
          {files.map((f) => (
            <li key={f.key} className={f.error ? "error" : f.ref ? "" : "uploading"}>
              <span className="name">{f.name}</span>
              <span className="muted">{f.error ?? (f.ref ? humanSize(f.size) : "올리는 중…")}</span>
              <button
                className="link"
                aria-label={`${f.name} 빼기`}
                onClick={() => setFiles((fs) => fs.filter((x) => x.key !== f.key))}
              >
                ✕
              </button>
            </li>
          ))}
        </ul>
      )}
      {trigger && (
        <ul className="suggest" role="listbox" data-testid="suggest">
          {trigger.items.map((s, i) => (
            <li
              key={s.label}
              role="option"
              aria-selected={i === active}
              className={i === active ? "active" : ""}
              onMouseDown={(e) => {
                e.preventDefault();
                pick(s);
              }}
            >
              <span>{s.label}</span>
              <span className="muted">{s.hint}</span>
            </li>
          ))}
        </ul>
      )}
      <input
        ref={picker}
        type="file"
        multiple
        hidden
        data-testid="file-input"
        onChange={(e) => {
          addFiles(e.target.files);
          e.target.value = "";
        }}
      />
      <button
        className="btn attach"
        onClick={() => picker.current?.click()}
        disabled={disabled || files.length >= MAX_FILES}
        aria-label="파일 붙이기"
        title={`파일 붙이기(최대 ${MAX_FILES}개)`}
      >
        📎
      </button>
      <textarea
        ref={ref}
        value={text}
        rows={2}
        placeholder="메시지 (Enter 전송, Shift+Enter 줄바꿈, @봇, /명령)"
        disabled={disabled}
        onChange={(e) => {
          setText(e.target.value);
          setCaret(e.target.selectionStart);
          setActive(0);
        }}
        onSelect={(e) => setCaret(e.currentTarget.selectionStart)}
        onKeyDown={onKeyDown}
        aria-label="메시지 입력"
      />
      <button className="btn primary" onClick={() => void submit()} disabled={disabled || !canSend}>
        전송
      </button>
    </div>
  );
}
