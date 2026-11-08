// 화면 하나: 세션 목록 | 대화 (봇 상태 패널은 P1-6). 로그인 전에는 로그인 화면.
import { useEffect, useState } from "react";
import { setUnauthorizedHandler } from "../api/rest";
import { ChatView } from "../features/chat/ChatView";
import { SessionList } from "../features/sessions/SessionList";
import { checkAuth, logout, logoutLocal, openSession, reconnectNow, sessionFromHash, useStore } from "../store";
import { Login } from "./Login";

export function App() {
  const auth = useStore((s) => s.auth);
  const username = useStore((s) => s.username);
  const [collapsed, setCollapsed] = useState(false);

  useEffect(() => {
    setUnauthorizedHandler(logoutLocal);
    void checkAuth();
    const onVisible = () => document.visibilityState === "visible" && reconnectNow();
    const onHash = () => {
      if (useStore.getState().auth === "authed") openSession(sessionFromHash());
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

  if (auth === "unknown") return <main className="loading muted">불러오는 중…</main>;
  if (auth === "anon") return <Login />;

  return (
    <div className={`layout${collapsed ? " collapsed" : ""}`}>
      {collapsed ? (
        <button className="icon expand" aria-label="세션 목록 펼치기" onClick={() => setCollapsed(false)}>
          »
        </button>
      ) : (
        <SessionList onCollapse={() => setCollapsed(true)} />
      )}
      <ChatView />
      <div className="account">
        <span className="muted">{username}</span>
        <button className="link" onClick={() => void logout()}>
          로그아웃
        </button>
      </div>
    </div>
  );
}
