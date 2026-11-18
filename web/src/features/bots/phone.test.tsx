// 봇 상태 패널 "사용할 폰"
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { PhoneOut } from "../../api/types";
import { PhoneSection, phoneLabel, switching } from "./PhoneSection";

const row = (device: string, connected = false) => ({
  device,
  android: "17",
  sdk: 37,
  version: "v",
  last_seen_at: 1,
  connected,
  preferred: false,
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("사용할 폰", () => {
  it("이름을 알아보기 쉽게 보이고, 바꾸는 중인지 판단한다", () => {
    expect(phoneLabel("samsung SM-S938N")).toBe("갤럭시 S25 울트라 (SM-S938N)");
    expect(phoneLabel("pixel 9")).toBe("pixel 9");
    expect(phoneLabel("samsung SM-X920N")).toBe("갤럭시 탭 (SM-X920N)");
    expect(switching({ prefer: "", current: "a", devices: [] })).toBe(false);
    expect(switching({ prefer: "SM-N960N", current: "samsung SM-S938N", devices: [] })).toBe(true);
    expect(switching({ prefer: "SM-N960N", current: "samsung SM-N960N", devices: [] })).toBe(false);
  });

  it("목록을 보여 주고 고르면 허브에 저장한다", async () => {
    let state: PhoneOut = {
      prefer: "",
      current: "samsung SM-S938N",
      devices: [row("samsung SM-S938N", true), row("samsung SM-N960N")],
    };
    const bodies: unknown[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        if (url === "/api/phone/prefer") {
          const b = JSON.parse(init!.body as string);
          bodies.push(b);
          state = { ...state, prefer: b.device };
        }
        return new Response(JSON.stringify(state), { status: 200, headers: { "Content-Type": "application/json" } });
      }),
    );
    render(<PhoneSection />);
    await waitFor(() => expect(screen.getByText(/연결: 갤럭시 S25 울트라/)).toBeTruthy());
    await act(async () => {
      fireEvent.change(screen.getByLabelText("사용할 폰"), { target: { value: "samsung SM-N960N" } });
    });
    expect(bodies).toEqual([{ device: "samsung SM-N960N" }]);
    await waitFor(() => expect(screen.getByText(/바꾸는 중/)).toBeTruthy());
  });

  it("게이트웨이가 꺼져 있으면(404) 숨긴다", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "phone_gateway_off" }), { status: 404 })));
    const { container } = render(<PhoneSection />);
    await waitFor(() => expect(container.querySelector("[data-testid=phone-section]")).toBeNull());
  });
});
