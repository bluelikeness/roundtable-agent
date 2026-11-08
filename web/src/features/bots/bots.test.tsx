// 봇 상태 패널·로그인 시트·모바일 레이아웃 (docs/05-ui.md 4·7장)
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { BotStatusPayload } from "../../api/types";
import { App } from "../../app/App";
import { useStore } from "../../store";
import { BotPanelBody, StatusDots } from "./BotPanel";
import { LoginSheetBody } from "./LoginSheet";
import { Sheet } from "./Sheet";
import { rowView } from "./status";

const initial = useStore.getState();
const bots = [
  { name: "mock-a", aliases: [], color: null },
  { name: "mock-b", aliases: [], color: null },
  { name: "mock-c", aliases: [], color: null },
];
const st = (bot: string, p: Partial<BotStatusPayload> = {}): BotStatusPayload => ({
  bot,
  available: true,
  reason: null,
  last_ok_at: null,
  queue_depth: 0,
  avg_latency_ms: null,
  enabled: true,
  ...p,
});

type Route = (url: string, init?: RequestInit) => unknown;
function mockFetch(route: Route) {
  const f = vi.fn(async (url: string, init?: RequestInit) => {
    const body = route(url, init);
    if (body instanceof Response) return body;
    return new Response(body === undefined ? null : JSON.stringify(body), {
      status: body === undefined ? 204 : 200,
      headers: { "Content-Type": "application/json" },
    });
  });
  vi.stubGlobal("fetch", f);
  return f;
}

function mockMedia(mobile: boolean) {
  vi.stubGlobal("matchMedia", (q: string) => ({
    matches: mobile,
    media: q,
    addEventListener: () => {},
    removeEventListener: () => {},
  }));
}

class FakeSocket {
  static OPEN = 1;
  readyState = 0;
  onopen: (() => void) | null = null;
  onmessage = null;
  onclose = null;
  send() {}
  close() {}
}

beforeEach(() => {
  useStore.setState(initial, true);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("패널 행", () => {
  it("상태별 점·상태어·버튼", () => {
    const now = Date.parse("2026-11-09T10:00:00Z");
    expect(rowView(undefined).label).toBe("확인 중");
    const ok = rowView(st("a", { last_ok_at: "2026-11-09T09:59:00Z", queue_depth: 1, avg_latency_ms: 52_000 }), now);
    expect(ok).toMatchObject({ tone: "ok", label: "응답 중", sub: "마지막 응답 1분 전 · 큐 1 · 평균 52초", problem: false });
    expect(rowView(st("a", { available: false, reason: "login_required" }))).toMatchObject({ tone: "warn", action: "relogin", problem: true });
    expect(rowView(st("a", { available: false, reason: "backend_down" }))).toMatchObject({ tone: "down", label: "점검 필요" });
    expect(rowView(st("a", { available: false, reason: "disabled", enabled: false }))).toMatchObject({ tone: "off", action: "enable" });
  });

  it("문제 있는 봇만 경고, 재로그인 버튼이 시트를 연다", () => {
    useStore.setState({
      bots,
      botStatus: { "mock-a": st("mock-a"), "mock-b": st("mock-b", { available: false, reason: "login_required" }) },
      claude: { runner_ok: true, runs_today: 3, tokens_today: 12_300 },
    });
    render(<BotPanelBody />);
    expect(screen.getByTestId("bot-row-mock-a").className).toContain("tone-ok");
    expect(screen.getByTestId("bot-row-mock-b").className).toContain("tone-warn");
    expect(screen.getByTestId("claude-section").textContent).toContain("오늘 3회 · 12.3k 토큰");
    fireEvent.click(screen.getByRole("button", { name: "재로그인" }));
    expect(useStore.getState().ui.sheet).toEqual({ kind: "login", bot: "mock-b" });
  });

  it("⋯ 메뉴: 비활성화는 허브 API 를 부른다", async () => {
    const f = mockFetch(() => st("mock-a", { enabled: false, available: false, reason: "disabled" }));
    useStore.setState({ bots, botStatus: { "mock-a": st("mock-a") } });
    render(<BotPanelBody />);
    fireEvent.click(screen.getByRole("button", { name: "mock-a 메뉴" }));
    await act(async () => {
      fireEvent.click(screen.getByRole("menuitem", { name: "비활성화" }));
    });
    expect(f).toHaveBeenCalledWith("/api/bots/mock-a/disable", expect.objectContaining({ method: "POST" }));
    expect(useStore.getState().botStatus["mock-a"].enabled).toBe(false);
  });

  it("헤더 상태 점: 문제가 있으면 느낌표, 탭하면 바텀시트", () => {
    useStore.setState({ bots, botStatus: { "mock-a": st("mock-a", { available: false, reason: "backend_down" }) } });
    render(<StatusDots />);
    expect(screen.getByText("!")).toBeTruthy();
    fireEvent.click(screen.getByTestId("status-dots"));
    expect(useStore.getState().ui.botSheet).toBe(true);
  });
});

describe("로그인 시트", () => {
  it("flow=code: 코드 → 비밀번호 → 완료", async () => {
    const calls: unknown[] = [];
    mockFetch((url, init) => {
      if (url.endsWith("/login/start")) return { flow: "code", hint: "아무 코드", vnc_url: null };
      if (url.endsWith("/login/submit")) {
        const body = JSON.parse(String(init?.body));
        calls.push(body);
        return "code" in body ? { ok: false, next: "password" } : { ok: true };
      }
    });
    render(<LoginSheetBody bot="mock-a" onClose={() => {}} />);
    const code = await screen.findByLabelText("인증코드");
    fireEvent.change(code, { target: { value: "12345" } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "확인" }));
    });
    const pw = await screen.findByLabelText("비밀번호");
    fireEvent.change(pw, { target: { value: "pw" } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "확인" }));
    });
    expect(await screen.findByText("로그인되었습니다.")).toBeTruthy();
    expect(calls).toEqual([{ code: "12345" }, { password: "pw" }]);
  });

  it("flow=vnc: noVNC 자리 + 완료 → /login/complete", async () => {
    const f = mockFetch((url) => {
      if (url.endsWith("/login/start")) return { flow: "vnc", hint: null, vnc_url: null };
      if (url.endsWith("/login/complete")) return { ok: true };
    });
    render(<LoginSheetBody bot="mock-a" onClose={() => {}} />);
    expect(await screen.findByTestId("vnc-login")).toBeTruthy();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "완료" }));
    });
    expect(f).toHaveBeenCalledWith("/api/bots/mock-a/login/complete", expect.anything());
    expect(await screen.findByTestId("login-done")).toBeTruthy();
  });

  it("Esc 로 시트를 닫는다", () => {
    const onClose = vi.fn();
    render(
      <Sheet title="t" onClose={onClose}>
        x
      </Sheet>,
    );
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });
});

describe("모바일 레이아웃 (≤768px)", () => {
  function api(url: string): unknown {
    if (url === "/api/auth/me") return { username: "op" };
    if (url === "/api/sessions") return [{ id: "11111111-1111-1111-1111-111111111111", title: "첫 세션", created_at: "2026-11-09T00:00:00Z", last_message_at: null, archived: false }];
    if (url === "/api/bots") return bots;
    if (url === "/api/claude/status") return { runner_ok: true, runs_today: 0, tokens_today: 0 };
    return [];
  }

  it("세션 목록은 드로어, 봇 상태는 바텀시트", async () => {
    mockMedia(true);
    mockFetch(api);
    vi.stubGlobal("WebSocket", FakeSocket);
    const { container } = render(<App />);
    await screen.findByRole("button", { name: "세션 목록 열기" });
    // 대화만 전체 화면: 세션 목록·봇 패널 기둥이 없다
    expect(container.querySelector(".layout.mobile")).toBeTruthy();
    expect(container.querySelector(".bot-panel")).toBeNull();
    expect(screen.queryByText("첫 세션")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "세션 목록 열기" }));
    expect(screen.getByTestId("session-drawer")).toBeTruthy();
    // 세션을 고르면 드로어가 닫힌다
    fireEvent.click(screen.getByText("첫 세션"));
    await waitFor(() => expect(screen.queryByTestId("session-drawer")).toBeNull());

    fireEvent.click(screen.getByTestId("status-dots"));
    expect(screen.getByTestId("bot-sheet").textContent).toContain("mock-a");
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByTestId("bot-sheet")).toBeNull();
  });

  it("데스크톱은 세 기둥", async () => {
    mockMedia(false);
    mockFetch(api);
    vi.stubGlobal("WebSocket", FakeSocket);
    const { container } = render(<App />);
    await screen.findByText("첫 세션");
    expect(container.querySelector(".bot-panel")).toBeTruthy();
    expect(screen.queryByTestId("status-dots")).toBeNull();
  });
});
