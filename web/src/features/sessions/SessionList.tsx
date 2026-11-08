import { useState, type ReactNode } from "react";
import type { SessionOut } from "../../api/types";
import { shortTime } from "../../lib/time";
import { archiveSession, deleteSession, openSession, renameSession, useStore } from "../../store";

function SessionItem({ s, active, running }: { s: SessionOut; active: boolean; running: boolean }) {
  const [editing, setEditing] = useState(false);
  const [menu, setMenu] = useState(false);
  const [draft, setDraft] = useState(s.title ?? "");
  const title = s.title || "새 세션";

  const commit = () => {
    setEditing(false);
    if (draft.trim() !== (s.title ?? "")) void renameSession(s.id, draft);
  };

  return (
    <li className={`session-item${active ? " active" : ""}`} onClick={() => openSession(s.id)}>
      {editing ? (
        <input
          autoFocus
          value={draft}
          maxLength={200}
          onChange={(e) => setDraft(e.target.value)}
          onClick={(e) => e.stopPropagation()}
          onBlur={commit}
          onKeyDown={(e) => {
            if (e.nativeEvent.isComposing) return;
            if (e.key === "Enter") commit();
            if (e.key === "Escape") {
              setDraft(s.title ?? "");
              setEditing(false);
            }
          }}
        />
      ) : (
        <span
          className="session-title"
          title="더블클릭해 이름 변경"
          onDoubleClick={(e) => {
            e.stopPropagation();
            setDraft(s.title ?? "");
            setEditing(true);
          }}
        >
          {running && <span className="running-dot" aria-label="실행 중" />}
          {title}
        </span>
      )}
      <span className="session-time">{shortTime(s.last_message_at ?? s.created_at)}</span>
      <button
        className="icon"
        aria-label="세션 메뉴"
        onClick={(e) => {
          e.stopPropagation();
          setMenu(!menu);
        }}
      >
        ⋯
      </button>
      {menu && (
        <div className="menu" onClick={(e) => e.stopPropagation()} onMouseLeave={() => setMenu(false)}>
          <button
            onClick={() => {
              setMenu(false);
              setDraft(s.title ?? "");
              setEditing(true);
            }}
          >
            이름 변경
          </button>
          <button
            onClick={() => {
              setMenu(false);
              void archiveSession(s.id);
            }}
          >
            보관
          </button>
          <button
            className="danger"
            onClick={() => {
              setMenu(false);
              if (window.confirm(`"${title}" 세션을 삭제할까요? 되돌릴 수 없습니다.`)) void deleteSession(s.id);
            }}
          >
            삭제
          </button>
        </div>
      )}
    </li>
  );
}

export function SessionList({ onCollapse, footer }: { onCollapse: () => void; footer?: ReactNode }) {
  const sessions = useStore((s) => s.sessions);
  const activeId = useStore((s) => s.activeId);
  const chats = useStore((s) => s.chats);
  return (
    <nav className="sessions">
      <div className="sessions-head">
        <button className="btn primary block" onClick={() => openSession(null)}>
          + 새 세션
        </button>
        <button className="icon" aria-label="세션 목록 접기" onClick={onCollapse}>
          «
        </button>
      </div>
      <ul>
        {sessions.map((s) => (
          <SessionItem key={s.id} s={s} active={s.id === activeId} running={!!chats[s.id]?.runs.current} />
        ))}
      </ul>
      {footer}
    </nav>
  );
}
