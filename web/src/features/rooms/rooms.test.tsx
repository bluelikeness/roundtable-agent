import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { callText, inviteFromHash, inviteUrl, leaderText, roomRoute } from "./callText";
import { JoinView, storageKey } from "./JoinView";
import { mergeMsgs, RoomMessages } from "./RoomChat";

const KEY = "rk_" + "a".repeat(43);
const INVITE = "ri_" + "b".repeat(32);

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  localStorage.clear();
});

describe("주소·부르는 말", () => {
  it("초대 링크는 키를 # 뒤에 둔다", () => {
    const url = inviteUrl("https://hub.example", INVITE);
    expect(url).toBe(`https://hub.example/#/join/${INVITE}`);
    expect(inviteFromHash(`#/join/${INVITE}`)).toBe(INVITE);
    expect(inviteFromHash("#/join/nope")).toBeNull();
    expect(inviteFromHash("#/s/123")).toBeNull();
  });

  it("열린 방 경로", () => {
    expect(roomRoute("#/rooms")).toEqual({ roomId: null });
    const id = "0b9f7c4e-1f2a-4c3d-9e8f-123456789abc";
    expect(roomRoute(`#/rooms/${id}`)).toEqual({ roomId: id });
    expect(roomRoute("#/s/" + id)).toBeNull();
  });

  it("부르는 말에 주소·키·규칙이 다 들어간다", () => {
    const t = callText({ origin: "https://hub.example", room: "토론방", name: "클로", key: KEY, checkEverySec: 30 });
    expect(t).toContain("「토론방」");
    expect(t).toContain(`x-key`);
    expect(t).toContain(KEY);
    expect(t).toContain("GET https://hub.example/api/p/msgs?since=");
    expect(t).toContain("POST https://hub.example/api/p/send");
    expect(t).toContain("30초마다");
    expect(t).toContain("@클로");
    expect(t).toContain("멈춰");
    expect(t).toContain("my_turn");
    expect(t).toContain("409 not_called");
  });

  it("팀장 안내는 실행 명령과 키를 담는다", () => {
    const t = leaderText({ origin: "https://hub.example", room: "토론방", name: "팀장", key: KEY });
    expect(t).toContain("python -m app.room_leader");
    expect(t).toContain(`ROOM_KEY='${KEY}'`);
    expect(t).toContain("ROOM_URL='https://hub.example'");
    expect(t).toContain("LEADER_MAX_TURNS");
  });
});

describe("글 목록", () => {
  const m = (id: number, who = "클로", kind: "ai" | "human" = "ai") => ({ id, who, kind, text: `t${id}`, ts: "2026-11-24T00:00:00Z" });

  it("이어 붙일 때 같은 id 는 버린다", () => {
    const a = [m(1), m(2)];
    expect(mergeMsgs(a, [m(2), m(3)]).map((x) => x.id)).toEqual([1, 2, 3]);
    expect(mergeMsgs(a, [])).toBe(a);
    expect(mergeMsgs(a, [m(1)])).toBe(a);
  });

  it("내 글은 오른쪽, 남의 글은 이름과 AI 표시", () => {
    render(<RoomMessages msgs={[m(1, "철수", "human"), m(2, "클로", "ai")]} me="철수" />);
    const items = screen.getAllByTestId("room-msg");
    expect(items[0].className).toContain("mine");
    expect(items[0].textContent).not.toContain("철수");
    expect(items[1].textContent).toContain("클로");
    expect(items[1].textContent).toContain("AI");
  });
});

function json(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

describe("초대 입장", () => {
  it("이름을 쓰고 들어가면 키를 저장하고 방 글을 읽는다", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const url = String(input);
      calls.push({ url, init });
      if (url === `/api/join/${INVITE}` && (!init?.method || init.method === "GET")) {
        return json({ room: "모임방", people: [{ name: "클로", kind: "ai" }] });
      }
      if (url === `/api/join/${INVITE}`) return json({ key: KEY, me: "철수", room: "모임방" }, 201);
      if (url.startsWith("/api/p/msgs")) {
        return json({
          me: "철수",
          kind: "human",
          role: "member",
          room: "모임방",
          room_id: "x",
          check_every_s: 60,
          msgs: [{ id: 7, who: "클로", kind: "ai", text: "어서 와요", ts: "2026-11-24T00:00:00Z" }],
        });
      }
      if (url === "/api/p/participants") return json([{ name: "클로", kind: "ai" }, { name: "철수", kind: "human" }]);
      return json({}, 404);
    });

    render(<JoinView invite={INVITE} />);
    expect(await screen.findByText("모임방")).toBeTruthy();
    expect(screen.getByText(/지금 있는 참가자: 클로/)).toBeTruthy();

    fireEvent.change(screen.getByLabelText("내 이름"), { target: { value: "철수" } });
    fireEvent.click(screen.getByText("들어가기"));

    expect(await screen.findByText("어서 와요")).toBeTruthy();
    expect(localStorage.getItem(storageKey(INVITE))).toBe(KEY);
    const post = calls.find((c) => c.init?.method === "POST");
    expect(JSON.parse(String(post?.init?.body))).toEqual({ name: "철수" });
    const read = calls.find((c) => c.url.startsWith("/api/p/msgs"));
    expect((read?.init?.headers as Record<string, string>)["x-key"]).toBe(KEY);
    // 참가자 요청에는 운영자 쿠키를 싣지 않는다
    expect(read?.init?.credentials).toBe("omit");
  });

  it("없는 초대 링크면 안내만 보여 준다", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(() => json({ detail: "invite_not_found" }, 404));
    render(<JoinView invite={INVITE} />);
    await waitFor(() => expect(screen.getByText(/초대 링크가 없거나 바뀌었어요/)).toBeTruthy());
    expect(screen.queryByText("들어가기")).toBeNull();
  });
});
