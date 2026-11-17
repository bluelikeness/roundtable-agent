// 세션 목록: 그룹·옮기기·휴지통, 세션별 모델 (docs/05-ui.md 5장)
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { GroupOut, SessionOut } from "../../api/types";
import { modelOf, setModel, useStore } from "../../store";
import { SessionList } from "./SessionList";

const initial = useStore.getState();
const sess = (id: string, p: Partial<SessionOut> = {}): SessionOut => ({
  id,
  title: id,
  created_at: "2026-11-17T00:00:00Z",
  last_message_at: null,
  archived: false,
  group_id: null,
  model: null,
  trashed_at: null,
  ...p,
});
const grp = (id: string, name: string, position = 1): GroupOut => ({ id, name, position, created_at: "2026-11-17T00:00:00Z" });

type Route = (url: string, init?: RequestInit) => unknown;
function mockFetch(route: Route) {
  const f = vi.fn(async (url: string, init?: RequestInit) => {
    const body = route(url, init);
    return new Response(body === undefined ? null : JSON.stringify(body), {
      status: body === undefined ? 204 : 200,
      headers: { "Content-Type": "application/json" },
    });
  });
  vi.stubGlobal("fetch", f);
  return f;
}

beforeEach(() => {
  try {
    localStorage.clear();
  } catch {
    // 무시
  }
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  useStore.setState(initial, true);
});

describe("세션 목록 그룹·휴지통", () => {
  it("그룹별로 나누고 그룹 없는 세션과 휴지통을 따로 보인다", () => {
    useStore.setState({
      groups: [grp("g1", "여행")],
      sessions: [sess("제주", { group_id: "g1", model: "opus" }), sess("회의록")],
    });
    render(<SessionList onCollapse={() => {}} />);
    expect(screen.getByText("여행")).toBeTruthy();
    expect(screen.getByText("그룹 없음")).toBeTruthy();
    expect(screen.getByText("휴지통")).toBeTruthy();
    // 세션 모델 표시
    expect(screen.getByText("opus")).toBeTruthy();
    // 그룹 접기
    fireEvent.click(screen.getByText("여행"));
    expect(screen.queryByText("제주")).toBeNull();
    expect(screen.getByText("회의록")).toBeTruthy();
  });

  it("휴지통으로 옮기면 목록에서 빠지고 휴지통에서 되살린다", async () => {
    const calls: [string, string | undefined][] = [];
    mockFetch((url, init) => {
      calls.push([url, init?.body as string | undefined]);
      if (url.endsWith("/api/sessions/s1") && init?.method === "PATCH") {
        const body = JSON.parse(init.body as string);
        return sess("s1", { trashed_at: body.trashed ? "2026-11-17T01:00:00Z" : null });
      }
      if (url.endsWith("/api/sessions?trashed=true")) return [sess("s1", { trashed_at: "2026-11-17T01:00:00Z" })];
      return undefined;
    });
    useStore.setState({ sessions: [sess("s1")] });
    render(<SessionList onCollapse={() => {}} />);

    fireEvent.click(screen.getByLabelText("세션 메뉴"));
    await act(async () => {
      fireEvent.click(screen.getByText("휴지통으로"));
    });
    await waitFor(() => expect(useStore.getState().sessions).toHaveLength(0));
    expect(useStore.getState().trash.map((s) => s.id)).toEqual(["s1"]);
    expect(calls[0][1]).toContain('"trashed":true');

    await act(async () => {
      fireEvent.click(screen.getByText("휴지통"));
    });
    await act(async () => {
      fireEvent.click(screen.getByText("되살리기"));
    });
    await waitFor(() => expect(useStore.getState().sessions.map((s) => s.id)).toEqual(["s1"]));
    expect(useStore.getState().trash).toHaveLength(0);
  });

  it("다른 그룹으로 옮긴다", async () => {
    mockFetch((url, init) => {
      if (url.endsWith("/api/sessions/s1") && init?.method === "PATCH") return sess("s1", { group_id: "g2" });
      return undefined;
    });
    useStore.setState({ groups: [grp("g1", "여행"), grp("g2", "업무", 2)], sessions: [sess("s1", { group_id: "g1" })] });
    render(<SessionList onCollapse={() => {}} />);
    fireEvent.click(screen.getByLabelText("세션 메뉴"));
    fireEvent.click(screen.getByText("그룹으로 옮기기 ›"));
    await act(async () => {
      fireEvent.click(screen.getByText("업무", { selector: ".menu button" }));
    });
    await waitFor(() => expect(useStore.getState().sessions[0].group_id).toBe("g2"));
  });
});

/** jsdom 에는 elementFromPoint 가 없어, 마우스가 가리키는 요소를 테스트에서 정한다 */
function pointAt(el: Element | null) {
  (document as unknown as { elementFromPoint: () => Element | null }).elementFromPoint = () => el;
}

describe("세션 끌어다 놓기", () => {
  it("다른 그룹에 놓으면 옮기고, 휴지통에 놓으면 휴지통으로 보낸다", async () => {
    const bodies: string[] = [];
    mockFetch((url, init) => {
      if (url.endsWith("/api/sessions/s1") && init?.method === "PATCH") {
        bodies.push(init.body as string);
        const b = JSON.parse(init.body as string);
        return b.trashed ? sess("s1", { trashed_at: "2026-11-17T01:00:00Z" }) : sess("s1", { group_id: b.group_id });
      }
      return undefined;
    });
    useStore.setState({
      activeId: null,
      groups: [grp("g1", "여행"), grp("g2", "업무", 2)],
      sessions: [sess("s1", { group_id: "g1" })],
    });
    const { container } = render(<SessionList onCollapse={() => {}} />);
    const item = () => screen.getByText("s1", { selector: ".session-title" });
    const g1 = screen.getByText("여행");
    const g2 = screen.getByText("업무");

    // 조금만 움직이면 끌기가 아니다(클릭)
    fireEvent.mouseDown(item(), { button: 0, clientX: 10, clientY: 10 });
    fireEvent.mouseMove(window, { clientX: 12, clientY: 11 });
    expect(container.querySelector(".sessions.dragging")).toBeNull();
    fireEvent.mouseUp(window, { clientX: 12, clientY: 11 });

    // 같은 그룹에 놓으면 아무 요청도 없다
    fireEvent.mouseDown(item(), { button: 0, clientX: 10, clientY: 10 });
    pointAt(g1);
    fireEvent.mouseMove(window, { clientX: 10, clientY: 40 });
    expect(container.querySelector(".sessions.dragging")).toBeTruthy();
    expect(container.querySelector(".drag-ghost")?.textContent).toBe("s1");
    expect(g1.closest(".group")!.classList.contains("drop-over")).toBe(true);
    fireEvent.mouseUp(window, { clientX: 10, clientY: 40 });
    expect(bodies).toHaveLength(0);
    expect(container.querySelector(".sessions.dragging")).toBeNull();

    // 다른 그룹으로. 놓은 뒤 따라오는 click 은 세션을 열지 않는다
    fireEvent.mouseDown(item(), { button: 0, clientX: 10, clientY: 10 });
    pointAt(g2);
    fireEvent.mouseMove(window, { clientX: 10, clientY: 80 });
    fireEvent.mouseMove(window, { clientX: 10, clientY: 90 });
    expect(g2.closest(".group")!.classList.contains("drop-over")).toBe(true);
    await act(async () => {
      fireEvent.mouseUp(window, { clientX: 10, clientY: 90 });
      fireEvent.click(item());
    });
    await waitFor(() => expect(useStore.getState().sessions[0].group_id).toBe("g2"));
    expect(useStore.getState().activeId).toBeNull();

    // Esc 로 취소
    fireEvent.mouseDown(item(), { button: 0, clientX: 10, clientY: 10 });
    pointAt(screen.getByText("휴지통"));
    fireEvent.mouseMove(window, { clientX: 10, clientY: 200 });
    fireEvent.keyDown(window, { key: "Escape" });
    fireEvent.mouseUp(window, { clientX: 10, clientY: 200 });
    expect(bodies).toHaveLength(1);

    // 휴지통에 놓기
    fireEvent.mouseDown(item(), { button: 0, clientX: 10, clientY: 10 });
    fireEvent.mouseMove(window, { clientX: 10, clientY: 200 });
    await act(async () => {
      fireEvent.mouseUp(window, { clientX: 10, clientY: 200 });
    });
    await waitFor(() => expect(useStore.getState().sessions).toHaveLength(0));
    expect(bodies[1]).toContain('"trashed":true');
  });
});

describe("세션별 모델", () => {
  it("허브에 저장된 세션 모델을 쓰고, 새 세션은 첫 메시지 전 선택을 기억한다", async () => {
    mockFetch((url, init) => {
      if (url.endsWith("/api/sessions/s1") && init?.method === "PATCH") return sess("s1", { model: "opus" });
      return undefined;
    });
    useStore.setState({ sessions: [sess("s1")] });
    expect(modelOf("s1")).toBeNull();
    await setModel("s1", "opus");
    expect(modelOf("s1")).toBe("opus");
    await setModel(null, "sonnet");
    expect(modelOf(null)).toBe("sonnet");
  });
});
