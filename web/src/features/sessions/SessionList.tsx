import { useRef, useState, type MouseEvent as ReactMouseEvent, type ReactNode } from "react";
import type { GroupOut, SessionOut } from "../../api/types";
import { shortTime } from "../../lib/time";
import {
  archiveSession,
  createGroup,
  deleteForever,
  deleteGroup,
  emptyTrash,
  loadTrash,
  moveSession,
  newSessionIn,
  openSession,
  renameGroup,
  renameSession,
  restoreSession,
  trashSession,
  useStore,
} from "../../store";
import { Chevron, FolderPlus, More, Trash } from "./icons";

/** 끌기로 볼 최소 이동 거리(px). 이보다 적게 움직이면 클릭이다 */
const DRAG_START_PX = 5;

/** 놓을 곳 키: "g:<그룹 id>" | "loose"(그룹 없음) | "trash" */
type DropKey = string;

/** 끌기 상태. 마우스 이벤트로 직접 처리한다(HTML5 drag & drop 은 환경에 따라 끌기가 시작되지 않는다) */
interface DragState {
  sid: string;
  title: string;
  over: DropKey | null;
}

function dropKeyAt(x: number, y: number): DropKey | null {
  const el = document.elementFromPoint?.(x, y);
  return (el?.closest("[data-drop]") as HTMLElement | null)?.dataset.drop ?? null;
}

/** 접어 둔 그룹 id(이 브라우저에만) */
const FOLD_KEY = "roundtable.folded-groups";

function readFolded(): string[] {
  try {
    return JSON.parse(localStorage.getItem(FOLD_KEY) ?? "[]");
  } catch {
    return [];
  }
}

function writeFolded(ids: string[]) {
  try {
    localStorage.setItem(FOLD_KEY, JSON.stringify(ids));
  } catch {
    // 저장 불가 — 이번 탭에서만 유지
  }
}

function askName(message: string, initial = ""): string | null {
  const v = window.prompt(message, initial);
  return v && v.trim() ? v.trim() : null;
}

/** 세션을 끌어다 놓을 수 있는 영역. 끌고 있는 세션이 위에 오면 drop-over 로 표시한다 */
function DropZone({
  dropKey,
  over,
  className,
  children,
}: {
  dropKey: DropKey;
  over: DropKey | null;
  className?: string;
  children: ReactNode;
}) {
  return (
    <div data-drop={dropKey} className={`${className ?? ""}${over === dropKey ? " drop-over" : ""}`}>
      {children}
    </div>
  );
}

function SessionItem({
  s,
  active,
  running,
  groups,
  onPress,
  dragging,
}: {
  s: SessionOut;
  active: boolean;
  running: boolean;
  groups: GroupOut[];
  /** 마우스 누름 — 끌기 후보로 받았으면 true */
  onPress: (e: ReactMouseEvent, s: SessionOut) => boolean;
  dragging: boolean;
}) {
  const [editing, setEditing] = useState(false);
  const [menu, setMenu] = useState<null | "main" | "move">(null);
  const [draft, setDraft] = useState(s.title ?? "");
  const title = s.title || "새 세션";

  const commit = () => {
    setEditing(false);
    if (draft.trim() !== (s.title ?? "")) void renameSession(s.id, draft);
  };

  return (
    <li
      className={`session-item${active ? " active" : ""}${dragging ? " drag-source" : ""}`}
      onClick={() => openSession(s.id)}
      onMouseDown={(e) => {
        if (!editing && onPress(e, s)) setMenu(null);
      }}
    >
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
          {s.model && <span className="session-model">{s.model}</span>}
        </span>
      )}
      <span className="session-time">{shortTime(s.last_message_at ?? s.created_at)}</span>
      <button
        className="icon"
        aria-label="세션 메뉴"
        onClick={(e) => {
          e.stopPropagation();
          setMenu(menu ? null : "main");
        }}
      >
        <More />
      </button>
      {menu === "main" && (
        <div className="menu" onClick={(e) => e.stopPropagation()} onMouseLeave={() => setMenu(null)}>
          <button
            onClick={() => {
              setMenu(null);
              setDraft(s.title ?? "");
              setEditing(true);
            }}
          >
            이름 변경
          </button>
          <button onClick={() => setMenu("move")}>그룹으로 옮기기 ›</button>
          <button
            onClick={() => {
              setMenu(null);
              void archiveSession(s.id);
            }}
          >
            보관
          </button>
          <button
            className="danger"
            onClick={() => {
              setMenu(null);
              void trashSession(s.id);
            }}
          >
            휴지통으로
          </button>
        </div>
      )}
      {menu === "move" && (
        <div className="menu" onClick={(e) => e.stopPropagation()} onMouseLeave={() => setMenu(null)}>
          {groups
            .filter((g) => g.id !== s.group_id)
            .map((g) => (
              <button
                key={g.id}
                onClick={() => {
                  setMenu(null);
                  void moveSession(s.id, g.id);
                }}
              >
                {g.name}
              </button>
            ))}
          {s.group_id && (
            <button
              onClick={() => {
                setMenu(null);
                void moveSession(s.id, null);
              }}
            >
              그룹 없음
            </button>
          )}
          <button
            onClick={async () => {
              setMenu(null);
              const name = askName("새 그룹 이름");
              if (!name) return;
              const g = await createGroup(name);
              if (g) await moveSession(s.id, g.id);
            }}
          >
            + 새 그룹…
          </button>
        </div>
      )}
    </li>
  );
}

function GroupHeader({
  g,
  count,
  folded,
  onToggle,
}: {
  g: GroupOut;
  count: number;
  folded: boolean;
  onToggle: () => void;
}) {
  const [menu, setMenu] = useState(false);
  return (
    <div className="group-head" onClick={onToggle}>
      <Chevron open={!folded} />
      <span className="group-name">{g.name}</span>
      <span className="group-count">{count}</span>
      <button
        className="icon"
        aria-label="그룹 메뉴"
        onClick={(e) => {
          e.stopPropagation();
          setMenu(!menu);
        }}
      >
        <More />
      </button>
      {menu && (
        <div className="menu" onClick={(e) => e.stopPropagation()} onMouseLeave={() => setMenu(false)}>
          <button
            onClick={() => {
              setMenu(false);
              newSessionIn(g.id);
            }}
          >
            이 그룹에 새 세션
          </button>
          <button
            onClick={() => {
              setMenu(false);
              const name = askName("그룹 이름", g.name);
              if (name) void renameGroup(g.id, name);
            }}
          >
            이름 변경
          </button>
          <button
            className="danger"
            onClick={() => {
              setMenu(false);
              if (window.confirm(`"${g.name}" 그룹을 지울까요? 안의 세션은 지워지지 않고 그룹 없음으로 옮겨집니다.`))
                void deleteGroup(g.id);
            }}
          >
            그룹 삭제
          </button>
        </div>
      )}
    </div>
  );
}

function TrashSection({ over }: { over: DropKey | null }) {
  const trash = useStore((s) => s.trash);
  const [open, setOpen] = useState(false);
  return (
    <DropZone dropKey="trash" over={over} className="trash">
      <div
        className="group-head"
        onClick={() => {
          if (!open) void loadTrash();
          setOpen(!open);
        }}
      >
        <Trash />
        <span className="group-name">휴지통</span>
        {trash.length > 0 && <span className="group-count">{trash.length}</span>}
      </div>
      {open && (
        <>
          {trash.length === 0 ? (
            <p className="muted trash-empty">비어 있습니다</p>
          ) : (
            <ul>
              {trash.map((s) => (
                <li key={s.id} className="session-item trashed">
                  <span className="session-title">{s.title || "새 세션"}</span>
                  <button className="btn small" onClick={() => void restoreSession(s.id)}>
                    되살리기
                  </button>
                  <button
                    className="btn small danger"
                    onClick={() => {
                      if (window.confirm(`"${s.title || "새 세션"}" 세션을 영구 삭제할까요? 되돌릴 수 없습니다.`))
                        void deleteForever(s.id);
                    }}
                  >
                    영구 삭제
                  </button>
                </li>
              ))}
            </ul>
          )}
          {trash.length > 0 && (
            <button
              className="btn small danger block"
              onClick={() => {
                if (window.confirm(`휴지통의 세션 ${trash.length}개를 모두 영구 삭제할까요? 되돌릴 수 없습니다.`))
                  void emptyTrash();
              }}
            >
              휴지통 비우기
            </button>
          )}
        </>
      )}
    </DropZone>
  );
}

export function SessionList({ onCollapse, footer }: { onCollapse: () => void; footer?: ReactNode }) {
  const sessions = useStore((s) => s.sessions);
  const groups = useStore((s) => s.groups);
  const activeId = useStore((s) => s.activeId);
  const chats = useStore((s) => s.chats);
  const [folded, setFolded] = useState<string[]>(readFolded);
  const [drag, setDrag] = useState<DragState | null>(null);
  const ghost = useRef<HTMLDivElement>(null);
  const pos = useRef({ x: 0, y: 0 });
  // 끌기를 마친 직후의 click(세션 열기)을 한 번 무시한다
  const swallowClick = useRef(false);

  const toggle = (id: string) => {
    const next = folded.includes(id) ? folded.filter((x) => x !== id) : [...folded, id];
    setFolded(next);
    writeFolded(next);
  };
  const item = (s: SessionOut) => (
    <SessionItem
      key={s.id}
      s={s}
      active={s.id === activeId}
      running={!!chats[s.id]?.runs.current}
      groups={groups}
      onPress={press}
      dragging={drag?.sid === s.id}
    />
  );
  const known = new Set(groups.map((g) => g.id));
  const loose = sessions.filter((s) => !s.group_id || !known.has(s.group_id));
  // 놓은 곳에 따라 옮기거나 휴지통으로. 같은 그룹이면 아무것도 하지 않는다
  const drop = (sid: string, key: DropKey | null) => {
    if (!key) return;
    if (key === "trash") {
      void trashSession(sid);
      return;
    }
    const groupId = key === "loose" ? null : key.slice(2);
    const cur = useStore.getState().sessions.find((x) => x.id === sid);
    if (cur && (cur.group_id ?? null) !== groupId) void moveSession(sid, groupId);
  };

  /** 왼쪽 버튼으로 누르면 끌기 후보. 조금 움직이면 끌기가 시작된다. 버튼·입력칸·메뉴에서 누른 것은 제외 */
  function press(e: ReactMouseEvent, s: SessionOut): boolean {
    if (e.button !== 0 || (e.target as Element).closest("button, input, .menu")) return false;
    const x0 = e.clientX;
    const y0 = e.clientY;
    let started = false;
    let over: DropKey | null = null;

    const move = (ev: MouseEvent) => {
      if (!started) {
        if (Math.hypot(ev.clientX - x0, ev.clientY - y0) < DRAG_START_PX) return;
        started = true;
        over = dropKeyAt(ev.clientX, ev.clientY);
        pos.current = { x: ev.clientX, y: ev.clientY };
        setDrag({ sid: s.id, title: s.title || "새 세션", over });
        return;
      }
      ev.preventDefault();
      pos.current = { x: ev.clientX, y: ev.clientY };
      if (ghost.current) ghost.current.style.transform = `translate(${ev.clientX + 12}px, ${ev.clientY + 8}px)`;
      const next = dropKeyAt(ev.clientX, ev.clientY);
      if (next !== over) {
        over = next;
        setDrag((d) => (d ? { ...d, over: next } : d));
      }
    };
    const finish = (ev: MouseEvent | null) => {
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
      window.removeEventListener("keydown", key);
      if (!started) return;
      swallowClick.current = true;
      setTimeout(() => {
        swallowClick.current = false;
      }, 0);
      setDrag(null);
      if (ev) drop(s.id, dropKeyAt(ev.clientX, ev.clientY));
    };
    const up = (ev: MouseEvent) => finish(ev);
    const key = (ev: KeyboardEvent) => {
      if (ev.key === "Escape") finish(null);
    };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
    window.addEventListener("keydown", key);
    return true;
  }

  return (
    <nav
      className={`sessions${drag ? " dragging" : ""}`}
      onClickCapture={(e) => {
        if (swallowClick.current) e.stopPropagation();
      }}
    >
      <div className="sessions-head">
        <button className="btn primary block" onClick={() => newSessionIn(null)}>
          + 새 세션
        </button>
        <button
          className="icon"
          aria-label="새 그룹"
          title="새 그룹"
          onClick={() => {
            const name = askName("새 그룹 이름");
            if (name) void createGroup(name);
          }}
        >
          <FolderPlus />
        </button>
        <button className="icon" aria-label="세션 목록 접기" onClick={onCollapse}>
          «
        </button>
      </div>
      <div className="sessions-scroll">
        {groups.map((g) => {
          const list = sessions.filter((s) => s.group_id === g.id);
          const isFolded = folded.includes(g.id);
          return (
            <DropZone key={g.id} dropKey={`g:${g.id}`} over={drag?.over ?? null} className="group">
              <GroupHeader g={g} count={list.length} folded={isFolded} onToggle={() => toggle(g.id)} />
              {!isFolded && <ul>{list.map(item)}</ul>}
            </DropZone>
          );
        })}
        <DropZone dropKey="loose" over={drag?.over ?? null} className="loose">
          {groups.length > 0 && (loose.length > 0 || drag) && <div className="group-head loose">그룹 없음</div>}
          <ul>{loose.map(item)}</ul>
        </DropZone>
        <TrashSection over={drag?.over ?? null} />
      </div>
      {footer}
      {drag && (
        <div
          ref={ghost}
          className="drag-ghost"
          style={{ transform: `translate(${pos.current.x + 12}px, ${pos.current.y + 8}px)` }}
        >
          {drag.title}
        </div>
      )}
    </nav>
  );
}
