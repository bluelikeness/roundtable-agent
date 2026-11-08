// 말풍선·입력창 렌더 확인 (vitest + testing-library, docs/05-ui.md 8장)
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { BotItem, ChatState } from "../../store/chat";
import { emptyChat } from "../../store/chat";
import { parseCommand } from "../../lib/commands";
import { BotError, BotPending, BotReply, Final, Note, RunFailed, System, User } from "./bubbles";
import { Composer, findTrigger } from "./Composer";
import { statusText } from "./RunHeader";

afterEach(cleanup);

const base = { seq: 1, order: 1, ts: "2026-11-08T10:00:00Z" };
const bot: BotItem = {
  ...base,
  key: "s1",
  kind: "bot",
  bot: "mock-a",
  ok: true,
  raw: "원문",
  structured: null,
  needsInput: false,
  error: null,
  latencyMs: 2400,
  toolCallId: "t",
};
const bots = [
  { name: "mock-a", aliases: ["목에이"], color: null },
  { name: "mock-b", aliases: ["목비"], color: "#123456" },
];

describe("말풍선 7종", () => {
  it("사용자: 낙관적이면 회색", () => {
    const { container } = render(<User item={{ ...base, key: "l1", seq: null, kind: "user", text: "안녕", optimistic: true }} />);
    expect(screen.getByText("안녕")).toBeTruthy();
    expect(container.querySelector(".optimistic")).toBeTruthy();
  });

  it("Claude 노트: 접두와 접기", () => {
    render(<Note item={{ ...base, key: "s1", kind: "note", text: "가\n나\n다" }} />);
    expect(screen.getByText("Claude ·")).toBeTruthy();
    expect(screen.getByText("펼치기")).toBeTruthy();
  });

  it("봇 원문: 이름·소요 초, 20줄 넘으면 더 보기", () => {
    const raw = Array.from({ length: 25 }, (_, i) => `줄${i}`).join("\n");
    render(<BotReply item={{ ...bot, raw }} color="#000" />);
    expect(screen.getByText("mock-a")).toBeTruthy();
    expect(screen.getByText("· 2초")).toBeTruthy();
    expect(screen.getByText("더 보기 (5줄)")).toBeTruthy();
  });

  it("봇 대기: 경과 초가 오른다, 큐 대기 순번", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-11-08T10:00:03Z"));
    render(
      <BotPending
        item={{ ...base, key: "tt", kind: "pending", bot: "mock-a", toolCallId: "t", runId: "r", queuePosition: 2 }}
        color="#000"
      />,
    );
    expect(screen.getByText("· 3초")).toBeTruthy();
    expect(screen.getByText("대기 2번째")).toBeTruthy();
    act(() => {
      vi.advanceTimersByTime(2000);
    });
    expect(screen.getByText("· 5초")).toBeTruthy();
    vi.useRealTimers();
  });

  it("봇 오류: 사유 한 줄", () => {
    render(<BotError item={{ ...bot, ok: false, raw: "", error: "unavailable: login_required" }} color="#000" />);
    expect(screen.getByText("unavailable: login_required")).toBeTruthy();
  });

  it("Claude 정리: 마크다운 표", () => {
    const { container } = render(<Final item={{ ...base, key: "s1", kind: "final", text: "| a | b |\n|---|---|\n| 1 | 2 |" }} />);
    expect(container.querySelector("table")).toBeTruthy();
    expect(screen.getByText("Claude 정리")).toBeTruthy();
  });

  it("실행 실패 띠와 다시 시도, 시스템 한 줄", () => {
    const retry = vi.fn();
    render(<RunFailed item={{ ...base, key: "s1", kind: "failed", runId: "r", status: "timeout", error: null }} onRetry={retry} />);
    fireEvent.click(screen.getByText("다시 시도"));
    expect(retry).toHaveBeenCalled();
    render(<System item={{ ...base, key: "s2", kind: "system", text: "새 Claude 세션으로 이어집니다" }} />);
    expect(screen.getByText("새 Claude 세션으로 이어집니다")).toBeTruthy();
  });
});

describe("헤더 상태", () => {
  it("실행 중 · 2/3 응답 · 대기 1", () => {
    const c: ChatState = emptyChat();
    c.runs = { ...c.runs, current: "r", queued: ["q"], progress: { r: { asked: 3, replied: 2 } } };
    expect(statusText(c)).toBe("실행 중 · 2/3 응답 · 대기 1");
  });
});

describe("입력창", () => {
  it("@ 자동완성: 이름·별칭으로 찾는다", () => {
    expect(findTrigger("@mo", 3, bots)?.items.map((i) => i.label)).toEqual(["@mock-a", "@mock-b"]);
    const t = findTrigger("가격 @목비", 7, bots);
    expect(t?.items).toHaveLength(1);
    expect(t?.items[0].insert).toBe("@mock-b ");
    expect(findTrigger("메일@주소", 6, bots)).toBeNull();
  });

  it("/ 명령 자동완성은 맨 앞에서만", () => {
    expect(findTrigger("/", 1, bots)?.items.map((i) => i.label)).toContain("/model opus|sonnet");
    expect(findTrigger("a /", 3, bots)).toBeNull();
  });

  it("Enter 전송, 자동완성이 열려 있으면 Enter 는 선택", async () => {
    const onSubmit = vi.fn().mockResolvedValue(true);
    render(<Composer bots={bots} onSubmit={onSubmit} />);
    const ta = screen.getByLabelText("메시지 입력") as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: "@목", selectionStart: 2 } });
    fireEvent.keyDown(ta, { key: "Enter" });
    expect(ta.value).toBe("@mock-a ");
    expect(onSubmit).not.toHaveBeenCalled();
    fireEvent.change(ta, { target: { value: "@mock-a 강남 한식", selectionStart: 13 } });
    await act(async () => {
      fireEvent.keyDown(ta, { key: "Enter" });
    });
    expect(onSubmit).toHaveBeenCalledWith("@mock-a 강남 한식");
    expect(ta.value).toBe("");
  });

  it("명령 해석", () => {
    expect(parseCommand("/model opus")).toEqual({ cmd: "model", model: "opus" });
    expect(parseCommand("/reset @mock-a")).toEqual({ cmd: "reset", bot: "mock-a" });
    expect(parseCommand("/model gpt")?.cmd).toBe("invalid");
    expect(parseCommand("/home/me 경로")).toBeNull();
    expect(parseCommand("그냥 질문")).toBeNull();
  });
});
