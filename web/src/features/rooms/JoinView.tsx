// 초대받은 사람 화면 (#/join/<초대 키>). 운영자 로그인 없이, 이름만 쓰고 들어와 참가자 키로 읽고 쓴다.
// 받은 키는 이 브라우저(localStorage)에만 두어, 다시 열면 같은 이름으로 이어진다.
import { useEffect, useRef, useState } from "react";
import { HttpError } from "../../api/rest";
import { joinApi, participantApi, type Person, type RoomMsg } from "../../api/rooms";
import { mergeMsgs, RoomChat } from "./RoomChat";

// 분당 읽기 상한(12)보다 넉넉하게: 글 6초, 참가자 60초
const POLL_MS = 6000;
const PEOPLE_MS = 60000;

const ERR: Record<string, string> = {
  invite_not_found: "초대 링크가 없거나 바뀌었어요. 초대한 사람에게 새 링크를 받아 주세요.",
  room_closed: "이 방은 닫혔어요.",
  name_taken: "이미 같은 이름이 있어요. 다른 이름을 써 주세요.",
  room_full: "방 정원이 찼어요.",
  rate_limited: "잠시 뒤 다시 시도해 주세요.",
  invalid_key: "입장 정보가 바뀌었어요. 다시 들어와 주세요.",
  participant_paused: "방 주인이 잠깐 빼 두었어요.",
};

function errText(e: unknown): string {
  return e instanceof HttpError ? (ERR[e.code] ?? `오류 (${e.code})`) : "연결할 수 없어요";
}

export function storageKey(invite: string): string {
  return `room-key:${invite.slice(0, 16)}`;
}

function loadKey(invite: string): string | null {
  try {
    return localStorage.getItem(storageKey(invite));
  } catch {
    return null;
  }
}

function saveKey(invite: string, key: string | null) {
  try {
    if (key) localStorage.setItem(storageKey(invite), key);
    else localStorage.removeItem(storageKey(invite));
  } catch {
    // 저장 못 하면 이번 창에서만 쓴다
  }
}

export function JoinView({ invite }: { invite: string }) {
  const [key, setKey] = useState<string | null>(() => loadKey(invite));
  const [room, setRoom] = useState<string | null>(null);
  const [people, setPeople] = useState<Person[]>([]);
  const [me, setMe] = useState<string | null>(null);
  const [msgs, setMsgs] = useState<RoomMsg[]>([]);
  const [name, setName] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [fatal, setFatal] = useState(false);
  const lastId = useRef(0);

  // 들어오기 전: 방 이름과 누가 있는지
  useEffect(() => {
    if (key) return;
    joinApi
      .info(invite)
      .then((i) => {
        setRoom(i.room);
        setPeople(i.people);
      })
      .catch((e) => {
        setErr(errText(e));
        setFatal(true);
      });
  }, [invite, key]);

  // 들어온 뒤: 글 이어 읽기
  useEffect(() => {
    if (!key) return;
    let alive = true;
    const tick = async () => {
      try {
        const out = await participantApi.msgs(key, lastId.current);
        if (!alive) return;
        setRoom(out.room);
        setMe(out.me);
        if (out.msgs.length) lastId.current = out.msgs[out.msgs.length - 1].id;
        setMsgs((prev) => mergeMsgs(prev, out.msgs));
        setErr(null);
      } catch (e) {
        if (!alive) return;
        if (e instanceof HttpError && e.status === 401) {
          // 키가 바뀌었거나 지워짐: 다시 들어오게 한다
          saveKey(invite, null);
          setKey(null);
        }
        if (!(e instanceof HttpError && e.status === 429)) setErr(errText(e));
      }
    };
    const loadPeople = () =>
      participantApi
        .people(key)
        .then((p) => alive && setPeople(p))
        .catch(() => undefined);
    void tick();
    void loadPeople();
    const t = window.setInterval(tick, POLL_MS);
    const tp = window.setInterval(loadPeople, PEOPLE_MS);
    return () => {
      alive = false;
      window.clearInterval(t);
      window.clearInterval(tp);
    };
  }, [invite, key]);

  const join = async () => {
    const n = name.trim();
    if (!n) return;
    setErr(null);
    try {
      const out = await joinApi.join(invite, n);
      saveKey(invite, out.key);
      lastId.current = 0;
      setMsgs([]);
      setKey(out.key);
    } catch (e) {
      setErr(errText(e));
    }
  };

  const send = async (text: string) => {
    if (!key) return false;
    try {
      await participantApi.send(key, text);
      const out = await participantApi.msgs(key, lastId.current);
      if (out.msgs.length) lastId.current = out.msgs[out.msgs.length - 1].id;
      setMsgs((prev) => mergeMsgs(prev, out.msgs));
      return true;
    } catch (e) {
      setErr(errText(e));
      return false;
    }
  };

  if (!key) {
    return (
      <main className="login join" data-testid="join-view">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void join();
          }}
        >
          <h1>{room ?? "초대받은 방"}</h1>
          {!fatal && (
            <>
              {people.length > 0 && (
                <p className="muted small">지금 있는 참가자: {people.map((p) => p.name).join(", ")}</p>
              )}
              <label>
                내 이름
                <input value={name} maxLength={40} onChange={(e) => setName(e.target.value)} autoFocus required />
              </label>
              <p className="muted small">대화는 방 참가자 모두가 봐요. 비밀번호·인증코드는 올리지 마세요.</p>
            </>
          )}
          {err && <p className="error">{err}</p>}
          {!fatal && (
            <button className="btn primary block" disabled={!name.trim()}>
              들어가기
            </button>
          )}
        </form>
      </main>
    );
  }

  return (
    <div className="rooms-layout join-room" data-testid="join-room">
      <main className="room-main">
        <header className="room-head row between">
          <strong>{room ?? "…"}</strong>
          <span className="muted small">
            {me && `나: ${me}`} · {people.length}명
          </span>
        </header>
        {err && <p className="error">{err}</p>}
        <RoomChat msgs={msgs} me={me} onSend={send} />
      </main>
    </div>
  );
}
