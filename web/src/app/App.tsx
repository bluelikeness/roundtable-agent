// 화면 하나: 세션 목록 | 대화 | 봇 상태 (docs/05-ui.md 1·7장). 로그인 전에는 로그인 화면.
// 모바일(≤768px)은 대화만 전체 화면: 세션 목록은 왼쪽 드로어, 봇 상태는 헤더 상태 점 → 바텀시트.
import { useEffect, useState } from "react";
import { setUnauthorizedHandler } from "../api/rest";
import { BotLogsBody } from "../features/bots/BotLogs";
import { BotPanel, BotPanelBody, StatusDots } from "../features/bots/BotPanel";
import { LoginSheetBody } from "../features/bots/LoginSheet";
import { Sheet } from "../features/bots/Sheet";
import { ChatView } from "../features/chat/ChatView";
import { SessionList } from "../features/sessions/SessionList";
import { useMobile } from "../lib/media";
import { roomRoute } from "../features/rooms/callText";
import { RoomsView } from "../features/rooms/RoomsView";
import {
  checkAuth,
  closeSheet,
  logout,
  logoutLocal,
  openSession,
  reconnectNow,
  sessionFromHash,
  setUi,
  useStore,
} from "../store";
import { Login } from "./Login";

function Account() {
  const username = useStore((s) => s.username);
  return (
    <div className="account">
      <span className="muted">{username}</span>
      <button className="link" onClick={() => (window.location.hash = "#/rooms")}>
        열린 방
      </button>
      <button className="link" onClick={() => void logout()}>
        로그아웃
      </button>
    </div>
  );
}

function ActiveSheet() {
  const sheet = useStore((s) => s.ui.sheet);
  if (!sheet) return null;
  if (sheet.kind === "login") {
    return (
      <Sheet title={`${sheet.bot} 재로그인`} onClose={closeSheet} testId="login-sheet">
        <LoginSheetBody bot={sheet.bot} onClose={closeSheet} />
      </Sheet>
    );
  }
  return (
    <Sheet title={`${sheet.bot} 로그`} onClose={closeSheet} testId="logs-sheet">
      <BotLogsBody bot={sheet.bot} />
    </Sheet>
  );
}

export function App() {
  const auth = useStore((s) => s.auth);
  const ui = useStore((s) => s.ui);
  const mobile = useMobile();
  const [collapsed, setCollapsed] = useState(false);
  const [rooms, setRooms] = useState(() => roomRoute());
  const username = useStore((s) => s.username);

  useEffect(() => {
    setUnauthorizedHandler(logoutLocal);
    void checkAuth();
    const onVisible = () => document.visibilityState === "visible" && reconnectNow();
    const onHash = () => {
      const r = roomRoute();
      setRooms(r);
      if (!r && useStore.getState().auth === "authed") openSession(sessionFromHash());
    };
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("online", reconnectNow);
    window.addEventListener("hashchange", onHash);
    return () => {
      setUnauthorizedHandler(null);
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("online", reconnectNow);
      window.removeEventListener("hashchange", onHash);
    };
  }, []);

  // 데스크톱으로 돌아오면 모바일 전용 열림 상태를 닫는다
  useEffect(() => {
    if (!mobile && (ui.drawer || ui.botSheet)) setUi({ drawer: false, botSheet: false });
  }, [mobile, ui.drawer, ui.botSheet]);

  if (auth === "unknown") return <main className="loading muted">불러오는 중…</main>;
  if (auth === "anon") return <Login />;
  if (rooms) return <RoomsView roomId={rooms.roomId} me={username} />;

  if (mobile) {
    return (
      <div className="layout mobile">
        <ChatView
          leading={
            <button className="icon menu-btn" aria-label="세션 목록 열기" onClick={() => setUi({ drawer: true })}>
              ☰
            </button>
          }
          trailing={<StatusDots />}
        />
        {ui.drawer && (
          <Sheet title="세션" variant="drawer" onClose={() => setUi({ drawer: false })} testId="session-drawer">
            <SessionList onCollapse={() => setUi({ drawer: false })} footer={<Account />} />
          </Sheet>
        )}
        {ui.botSheet && (
          <Sheet title="봇 상태" variant="bottom" onClose={() => setUi({ botSheet: false })} testId="bot-sheet">
            <BotPanelBody />
          </Sheet>
        )}
        <ActiveSheet />
      </div>
    );
  }

  const cls = ["layout", collapsed ? "sessions-collapsed" : "", ui.botPanelCollapsed ? "bots-collapsed" : ""].join(" ");
  return (
    <div className={cls.trim()}>
      {collapsed ? (
        <button className="icon expand" aria-label="세션 목록 펼치기" onClick={() => setCollapsed(false)}>
          »
        </button>
      ) : (
        <SessionList onCollapse={() => setCollapsed(true)} footer={<Account />} />
      )}
      <ChatView />
      {ui.botPanelCollapsed ? (
        <button className="icon expand" aria-label="봇 상태 펼치기" onClick={() => setUi({ botPanelCollapsed: false })}>
          «
        </button>
      ) : (
        <BotPanel onCollapse={() => setUi({ botPanelCollapsed: true })} />
      )}
      <ActiveSheet />
    </div>
  );
}
