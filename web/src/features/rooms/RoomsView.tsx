// 운영자 열린 방 화면 (#/rooms, #/rooms/<id>): 방 목록 | 대화 | 참가자 패널.
// 기존 1인 방(WebSocket) 과 따로, REST 를 3초마다 이어 읽는다.
import { useCallback, useEffect, useRef, useState } from "react";
import { HttpError } from "../../api/rest";
import {
  roomsApi,
  type ParticipantKeyOut,
  type ParticipantKind,
  type ParticipantOut,
  type RoomMsg,
  type RoomOut,
} from "../../api/rooms";
import { useMobile } from "../../lib/media";
import { callText, inviteUrl, leaderText } from "./callText";
import { mergeMsgs, RoomChat } from "./RoomChat";

const POLL_MS = 3000;

const ERR: Record<string, string> = {
  name_taken: "이미 같은 이름이 있어요",
  room_full: "방 정원이 찼어요",
  room_closed: "닫힌 방이에요",
};

function errText(e: unknown): string {
  return e instanceof HttpError ? (ERR[e.code] ?? `오류 (${e.code})`) : "허브에 연결할 수 없어요";
}

async function copy(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

function go(hash: string) {
  if (window.location.hash !== hash) window.location.hash = hash;
}

/** 한 번만 보여 주는 키·부르는 말·초대 링크 */
interface Reveal {
  title: string;
  body: string;
  note: string;
}

function RevealBox({ reveal, onClose }: { reveal: Reveal; onClose: () => void }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="room-reveal" data-testid="room-reveal">
      <div className="room-reveal-head">
        <strong>{reveal.title}</strong>
        <button className="icon" aria-label="닫기" onClick={onClose}>
          ✕
        </button>
      </div>
      <p className="muted small">{reveal.note}</p>
      <textarea readOnly value={reveal.body} rows={Math.min(14, reveal.body.split("\n").length + 1)} />
      <button
        className="btn primary small"
        onClick={async () => setCopied(await copy(reveal.body))}
      >
        {copied ? "복사했어요" : "복사"}
      </button>
    </div>
  );
}

function Participants({
  room,
  people,
  onChange,
  onReveal,
}: {
  room: RoomOut;
  people: ParticipantOut[];
  onChange: () => void;
  onReveal: (r: Reveal) => void;
}) {
  const [name, setName] = useState("");
  // "leader" 는 팀장 AI(역할 leader 인 AI 참가자)
  const [kind, setKind] = useState<ParticipantKind | "leader">("ai");
  const [err, setErr] = useState<string | null>(null);
  const closed = room.closed_at !== null;

  const revealKey = (p: ParticipantKeyOut, rotated: boolean) => {
    if (p.kind === "ai" && p.role === "leader") {
      onReveal({
        title: `팀장 ${p.name} 실행 방법`,
        body: leaderText({ origin: window.location.origin, room: room.name, name: p.name, key: p.key }),
        note: `${rotated ? "키를 바꿨어요. 예전 키는 이제 안 돼요. " : ""}팀장은 claude 가 로그인된 PC 에서 이 명령으로 돌려요. 키는 지금 한 번만 보여요.`,
      });
    } else if (p.kind === "ai") {
      onReveal({
        title: `${p.name}에게 보낼 부르는 말`,
        body: callText({ origin: window.location.origin, room: room.name, name: p.name, key: p.key, checkEverySec: p.check_every_s }),
        note: `${rotated ? "키를 바꿨어요. 예전 키는 이제 안 돼요. " : ""}이 글을 ${p.name} AI 에게 그대로 붙여 넣으세요. 키는 지금 한 번만 보여요.`,
      });
    } else {
      onReveal({
        title: `${p.name} 의 키`,
        body: p.key,
        note: "사람 참가자는 보통 초대 링크로 들어와요. 이 키는 지금 한 번만 보여요.",
      });
    }
  };

  const add = async () => {
    const n = name.trim();
    if (!n) return;
    setErr(null);
    try {
      const p = await roomsApi.addParticipant(
        room.id,
        kind === "leader" ? { name: n, kind: "ai", role: "leader" } : { name: n, kind },
      );
      setName("");
      revealKey(p, false);
      onChange();
    } catch (e) {
      setErr(errText(e));
    }
  };

  const act = async (f: () => Promise<unknown>) => {
    setErr(null);
    try {
      await f();
      onChange();
    } catch (e) {
      setErr(errText(e));
    }
  };

  const invite = async () => {
    setErr(null);
    try {
      const { invite } = await roomsApi.makeInvite(room.id);
      onReveal({
        title: "사람 초대 링크",
        body: inviteUrl(window.location.origin, invite),
        note: "이 링크를 받은 사람은 이름만 쓰고 들어와요. 새로 만들면 예전 링크는 막혀요.",
      });
      onChange();
    } catch (e) {
      setErr(errText(e));
    }
  };

  return (
    <aside className="room-people" aria-label="참가자">
      <div className="room-people-head">
        <strong>참가자 {people.length}</strong>
      </div>

      <section className="room-invite">
        <div className="muted small">사람 초대 {room.invite_on ? "켜짐" : "꺼짐"}</div>
        <div className="row">
          <button className="btn small" disabled={closed} onClick={invite}>
            {room.invite_on ? "링크 새로 만들기" : "초대 링크 만들기"}
          </button>
          {room.invite_on && (
            <button className="btn small" onClick={() => act(() => roomsApi.stopInvite(room.id))}>
              링크 끄기
            </button>
          )}
        </div>
      </section>

      <ul className="room-people-list">
        {people.map((p) => (
          <li key={p.id} className={p.enabled ? "" : "paused"} data-testid="room-person">
            <div className="row between">
              <span>
                <strong>{p.name}</strong> <span className="room-tag">{p.kind === "ai" ? "AI" : "사람"}</span>
                {p.role === "leader" && <span className="room-tag leader">팀장</span>}
                {!p.enabled && <span className="room-tag">쉬는 중</span>}
              </span>
            </div>
            <div className="muted small">
              {p.last_seen_at ? `마지막 확인 ${new Date(p.last_seen_at).toLocaleTimeString("ko-KR")}` : "아직 안 들어왔어요"}
              {p.kind === "ai" && ` · ${p.check_every_s}초마다`}
            </div>
            <div className="row wrap">
              <button
                className="link"
                onClick={() =>
                  act(() => roomsApi.updateParticipant(room.id, p.id, { role: p.role === "leader" ? "member" : "leader" }))
                }
              >
                {p.role === "leader" ? "팀장 해제" : "팀장 맡기기"}
              </button>
              <button className="link" onClick={() => act(() => roomsApi.updateParticipant(room.id, p.id, { enabled: !p.enabled }))}>
                {p.enabled ? "잠깐 빼기" : "다시 넣기"}
              </button>
              <button
                className="link"
                onClick={async () => {
                  setErr(null);
                  try {
                    revealKey(await roomsApi.rotateKey(room.id, p.id), true);
                    onChange();
                  } catch (e) {
                    setErr(errText(e));
                  }
                }}
              >
                {p.kind === "ai" ? "부르는 말 다시 받기" : "키 바꾸기"}
              </button>
              <button
                className="link danger"
                onClick={() => {
                  if (window.confirm(`${p.name} 을(를) 끊고 지울까요? 쓴 글은 남아요.`)) {
                    void act(() => roomsApi.removeParticipant(room.id, p.id));
                  }
                }}
              >
                끊고 지우기
              </button>
            </div>
          </li>
        ))}
      </ul>

      {!closed && (
        <form
          className="room-add"
          onSubmit={(e) => {
            e.preventDefault();
            void add();
          }}
        >
          <div className="muted small">참가자 넣기</div>
          <div className="row">
            <input aria-label="참가자 이름" placeholder="이름 (예: 클로)" maxLength={40} value={name} onChange={(e) => setName(e.target.value)} />
            <select aria-label="참가자 종류" value={kind} onChange={(e) => setKind(e.target.value as ParticipantKind | "leader")}>
              <option value="ai">AI</option>
              <option value="leader">팀장 AI</option>
              <option value="human">사람</option>
            </select>
            <button className="btn small primary" disabled={!name.trim()}>
              넣기
            </button>
          </div>
        </form>
      )}
      {err && <p className="error">{err}</p>}
    </aside>
  );
}

export function RoomsView({ roomId, me = null }: { roomId: string | null; me?: string | null }) {
  const mobile = useMobile();
  const [rooms, setRooms] = useState<RoomOut[]>([]);
  const [msgs, setMsgs] = useState<RoomMsg[]>([]);
  const [people, setPeople] = useState<ParticipantOut[]>([]);
  const [reveal, setReveal] = useState<Reveal | null>(null);
  const [newName, setNewName] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [showPeople, setShowPeople] = useState(false);
  const lastId = useRef(0);

  const room = rooms.find((r) => r.id === roomId) ?? null;

  const loadRooms = useCallback(async () => {
    try {
      setRooms(await roomsApi.list());
    } catch (e) {
      setErr(errText(e));
    }
  }, []);

  const loadPeople = useCallback(async () => {
    if (!roomId) return;
    try {
      setPeople(await roomsApi.participants(roomId));
    } catch {
      // 다음 주기에 다시
    }
  }, [roomId]);

  useEffect(() => {
    void loadRooms();
  }, [loadRooms]);

  // 방이 바뀌면 처음부터 읽고, 3초마다 이어 읽는다
  useEffect(() => {
    setMsgs([]);
    setPeople([]);
    setReveal(null);
    lastId.current = 0;
    if (!roomId) return;
    let alive = true;
    const tick = async () => {
      try {
        const out = await roomsApi.messages(roomId, lastId.current);
        if (!alive) return;
        if (out.msgs.length) lastId.current = out.msgs[out.msgs.length - 1].id;
        setMsgs((prev) => mergeMsgs(prev, out.msgs));
      } catch {
        // 다음 주기에 다시
      }
    };
    void tick();
    void loadPeople();
    const t = window.setInterval(tick, POLL_MS);
    const tp = window.setInterval(() => void loadPeople(), POLL_MS * 5);
    return () => {
      alive = false;
      window.clearInterval(t);
      window.clearInterval(tp);
    };
  }, [roomId, loadPeople]);

  const send = async (text: string) => {
    if (!roomId) return false;
    try {
      await roomsApi.send(roomId, text);
      const out = await roomsApi.messages(roomId, lastId.current);
      if (out.msgs.length) lastId.current = out.msgs[out.msgs.length - 1].id;
      setMsgs((prev) => mergeMsgs(prev, out.msgs));
      return true;
    } catch (e) {
      setErr(errText(e));
      return false;
    }
  };

  const create = async () => {
    const n = newName.trim();
    if (!n) return;
    try {
      const r = await roomsApi.create(n);
      setNewName("");
      await loadRooms();
      go(`#/rooms/${r.id}`);
    } catch (e) {
      setErr(errText(e));
    }
  };

  const list = (
    <nav className="room-list" aria-label="열린 방 목록">
      <div className="row between room-list-head">
        <strong>열린 방</strong>
        <button className="link" onClick={() => go("#/")}>
          ← 내 대화
        </button>
      </div>
      <form
        className="row"
        onSubmit={(e) => {
          e.preventDefault();
          void create();
        }}
      >
        <input aria-label="새 방 이름" placeholder="새 방 이름" maxLength={100} value={newName} onChange={(e) => setNewName(e.target.value)} />
        <button className="btn small primary" disabled={!newName.trim()}>
          만들기
        </button>
      </form>
      <ul>
        {rooms.map((r) => (
          <li key={r.id}>
            <button className={`room-item ${r.id === roomId ? "active" : ""}`} onClick={() => go(`#/rooms/${r.id}`)}>
              {r.name}
              {r.closed_at && <span className="room-tag">닫힘</span>}
            </button>
          </li>
        ))}
      </ul>
    </nav>
  );

  const panel = room && (
    <Participants room={room} people={people} onChange={() => void Promise.all([loadPeople(), loadRooms()])} onReveal={setReveal} />
  );

  return (
    <div className={`rooms-layout ${mobile ? "mobile" : ""}`} data-testid="rooms-view">
      {(!mobile || !roomId) && list}
      {roomId && (
        <main className="room-main">
          <header className="room-head row between">
            <span>
              {mobile && (
                <button className="icon" aria-label="방 목록" onClick={() => go("#/rooms")}>
                  ←
                </button>
              )}
              <strong>{room?.name ?? "…"}</strong>
              {room?.closed_at && <span className="room-tag">닫힘</span>}
            </span>
            <span className="row">
              {room && !room.closed_at && (
                <label className="room-toggle small" title="켜면 팀장이 아닌 AI 는 @이름 으로 부른 글에만 답할 수 있어요">
                  <input
                    type="checkbox"
                    checked={room.call_only}
                    onChange={(e) =>
                      void roomsApi
                        .update(room.id, { call_only: e.target.checked })
                        .then(loadRooms)
                        .catch((x) => setErr(errText(x)))
                    }
                  />
                  AI는 부를 때만
                </label>
              )}
              {mobile && (
                <button className="btn small" onClick={() => setShowPeople((v) => !v)}>
                  참가자
                </button>
              )}
              {room && !room.closed_at && (
                <button
                  className="btn small danger"
                  onClick={() => {
                    if (window.confirm("이 방을 닫을까요? 참가자 모두 더 이상 읽고 쓸 수 없어요.")) {
                      void roomsApi.close(room.id).then(loadRooms);
                    }
                  }}
                >
                  방 닫기
                </button>
              )}
            </span>
          </header>
          {reveal && <RevealBox reveal={reveal} onClose={() => setReveal(null)} />}
          {err && (
            <p className="error" onClick={() => setErr(null)}>
              {err}
            </p>
          )}
          {mobile && showPeople ? panel : <RoomChat msgs={msgs} me={me} disabled={!!room?.closed_at} onSend={send} />}
        </main>
      )}
      {!mobile && panel}
      {!roomId && !mobile && <main className="room-main room-empty muted">방을 고르거나 새로 만드세요.</main>}
    </div>
  );
}
