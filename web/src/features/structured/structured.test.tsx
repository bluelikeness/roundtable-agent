// structured 6종·확정 박스·[진행] 버튼 (docs/05-ui.md 2·3장)
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { imageSrc } from "../../api/rest";
import { isConfirmQuestion } from "../../lib/confirm";
import { linkify } from "../../lib/linkify";
import type { BotItem, ChatItem } from "../../store/chat";
import { BotReply, Final } from "../chat/bubbles";
import { latestKeys } from "../chat/MessageList";
import { parseStructured } from ".";

afterEach(cleanup);

const base = { seq: 1, order: 1, ts: "2026-11-09T10:00:00Z" };
const reply = (structured: Record<string, unknown>, raw = "원문"): BotItem => ({
  ...base,
  key: "s1",
  kind: "bot",
  bot: "mock-a",
  ok: true,
  raw,
  structured,
  needsInput: false,
  error: null,
  latencyMs: 1000,
  toolCallId: "t",
});

describe("structured 렌더링", () => {
  it("buttons: Mock 의 [버튼: X] 가 칩으로 뜨고 클릭하면 @봇 [tap:] 을 보낸다", () => {
    const onSend = vi.fn();
    const s = { buttons: [{ label: "예약 확정", action_id: "btn_1" }] };
    render(<BotReply item={reply(s, "[버튼: 예약 확정]")} color="#2f7ed8" onSend={onSend} />);
    fireEvent.click(screen.getByRole("button", { name: "예약 확정" }));
    expect(onSend).toHaveBeenCalledWith("@mock-a [tap:btn_1]");
  });

  it("buttons·choices: 최근 응답이 아니면 비활성 + 만료됨", () => {
    const onSend = vi.fn();
    const s = { buttons: [{ label: "확정", action_id: "btn_1" }], choices: ["18:00"] };
    render(<BotReply item={reply(s)} color="#000" active={false} onSend={onSend} />);
    const chip = screen.getByRole("button", { name: "확정" }) as HTMLButtonElement;
    expect(chip.disabled).toBe(true);
    expect(chip.title).toBe("만료됨");
    expect((screen.getByRole("button", { name: "18:00" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("choices: 클릭하면 @봇 선택지", () => {
    const onSend = vi.fn();
    render(<BotReply item={reply({ choices: ["18:00", "18:30"] })} color="#000" onSend={onSend} />);
    fireEvent.click(screen.getByRole("button", { name: "18:30" }));
    expect(onSend).toHaveBeenCalledWith("@mock-a 18:30");
  });

  it("links·cards·images: 새 탭 링크, 이미지는 허브 프록시", () => {
    const s = {
      links: [{ title: "네이버 예약", url: "https://example.com/r" }],
      cards: [{ title: "OO식당", subtitle: "강남 · 한식", body: "18:30 2명", url: "https://example.com/c", image_url: "/images/abc123" }],
      images: [{ url: "/images/0123456789abcdef", alt: "메뉴" }],
    };
    const { container } = render(<BotReply item={reply(s)} color="#000" />);
    const link = screen.getByRole("link", { name: "네이버 예약" }) as HTMLAnchorElement;
    expect(link.target).toBe("_blank");
    expect(link.rel).toContain("noopener");
    expect(screen.getByText("OO식당")).toBeTruthy();
    const imgs = [...container.querySelectorAll("img")].map((i) => i.getAttribute("src"));
    expect(imgs).toEqual(["/api/bots/mock-a/images/abc123", "/api/bots/mock-a/images/0123456789abcdef"]);
    fireEvent.click(screen.getByRole("button", { name: "이미지 확대: 메뉴" }));
    expect(screen.getByRole("dialog", { name: "이미지 확대" })).toBeTruthy();
  });

  it("confirmation_required: 말풍선 밖 강조 박스, 버튼 없음", () => {
    const s = { confirmation_required: { summary: "KTX 결제 58,800원", action_id: "btn_pay" } };
    render(<BotReply item={reply(s)} color="#000" onSend={vi.fn()} />);
    const box = screen.getByTestId("confirm-box");
    expect(box.textContent).toContain("KTX 결제 58,800원");
    expect(box.textContent).toContain("이 작업은 확인이 필요합니다");
    expect(box.querySelector("button")).toBeNull();
  });

  it("봇이 만든 값은 거른다: javascript: 링크·모양이 틀린 버튼은 버린다", () => {
    const s = parseStructured({
      links: [{ title: "x", url: "javascript:alert(1)" }, { title: "ok", url: "https://a.test" }],
      buttons: [{ label: "a" }, { label: "b", action_id: "btn_2" }, "junk"],
      cards: [{ title: "c", url: "javascript:x" }],
      choices: ["a", 3],
    });
    expect(s.links).toEqual([{ title: "ok", url: "https://a.test/" }]);
    expect(s.buttons).toEqual([{ label: "b", action_id: "btn_2" }]);
    expect(s.cards?.[0].url).toBeNull();
    expect(s.choices).toEqual(["a"]);
    expect(parseStructured(null)).toEqual({});
  });

  it("raw 는 마크다운이 아니라 URL 만 링크", () => {
    render(<BotReply item={reply({}, "**굵게** https://example.com/a.")} color="#000" />);
    expect(screen.getByText(/\*\*굵게\*\*/)).toBeTruthy();
    expect(screen.getByRole("link").getAttribute("href")).toBe("https://example.com/a");
    expect(linkify("링크 없음")).toHaveLength(1);
  });

  it("imageSrc: 어댑터 로컬 경로만 프록시, 이상한 경로는 버린다", () => {
    expect(imageSrc("mock-a", "/images/abc")).toBe("/api/bots/mock-a/images/abc");
    expect(imageSrc("mock-a", "https://cdn.test/x.jpg")).toBe("https://cdn.test/x.jpg");
    expect(imageSrc("mock-a", "/images/../x")).toBeNull();
    expect(imageSrc("mock-a", "javascript:x")).toBeNull();
  });
});

describe("Claude 정리의 [진행] 버튼", () => {
  const final = { ...base, key: "s9", kind: "final" as const };

  it("마지막 줄이 확정 질문이면 [진행]·[취소]가 입력을 대신 보낸다", () => {
    const onSend = vi.fn();
    render(<Final item={{ ...final, text: "| 항목 | 값 |\n|---|---|\n| 금액 | 58,800원 |\n\n결제를 진행할까요?" }} onSend={onSend} />);
    fireEvent.click(screen.getByRole("button", { name: "진행" }));
    expect(onSend).toHaveBeenCalledWith("진행해");
    fireEvent.click(screen.getByRole("button", { name: "취소" }));
    expect(onSend).toHaveBeenLastCalledWith("취소");
  });

  it("지난 정리이거나 확정 질문이 아니면 버튼이 없다", () => {
    render(<Final item={{ ...final, text: "예약을 확정할까요?" }} latest={false} onSend={vi.fn()} />);
    render(<Final item={{ ...final, key: "s10", text: "내일은 맑습니다." }} onSend={vi.fn()} />);
    expect(screen.queryByTestId("final-actions")).toBeNull();
  });

  it("확정 질문 감지", () => {
    expect(isConfirmQuestion("요약\n\n**예약을 진행할까요?**")).toBe(true);
    expect(isConfirmQuestion("결제하시겠어요? 아니면…\n결제를 진행하시겠습니까?")).toBe(true);
    expect(isConfirmQuestion("진행할까요?\n\n참고로 내일은 비가 옵니다.")).toBe(false);
    expect(isConfirmQuestion("어느 식당이 좋을까요?")).toBe(false);
    expect(isConfirmQuestion('진행하려면 "진행해"라고 말씀해 주세요.')).toBe(true);
  });

  it("latestKeys: 봇별 최근 응답, 사용자가 답한 뒤의 정리는 지난 것", () => {
    const items = [
      { key: "a1", kind: "bot", bot: "a" },
      { key: "b1", kind: "bot", bot: "b" },
      { key: "f1", kind: "final" },
      { key: "a2", kind: "pending", bot: "a" },
      { key: "u1", kind: "user" },
    ] as unknown as ChatItem[];
    expect(latestKeys(items)).toEqual({ bots: { a: "a2", b: "b1" }, final: null, confirm: false });
    expect(latestKeys(items.slice(0, 3)).final).toBe("f1");
  });

  it("latestKeys: 마지막 사용자 메시지 이후 confirmation_required 가 있으면 확정 대기", () => {
    const conf = { ...reply({ confirmation_required: { summary: "예약금 20,000원", action_id: "btn_1" } }), key: "c1" };
    const user = { ...base, key: "u1", kind: "user", text: "진행해", optimistic: false } as ChatItem;
    const fin = { ...final, text: "예약금 20,000원 결제가 필요합니다. 결제 후 예약이 확정됩니다." } as ChatItem;
    expect(latestKeys([conf, fin]).confirm).toBe(true);
    expect(latestKeys([conf, fin, user]).confirm).toBe(false);
    expect(latestKeys([conf, user, reply({}), fin]).confirm).toBe(false);
    expect(latestKeys([{ ...conf, ok: false }, fin]).confirm).toBe(false);
  });

  it("문구가 확정 질문이 아니어도 확정 대기면 [진행]을 붙인다(P1-6 관찰)", () => {
    const onSend = vi.fn();
    render(<Final item={{ ...final, text: "예약금 20,000원 결제가 필요합니다. 말씀해 주시면 바로 확정하겠습니다." }} confirmPending onSend={onSend} />);
    fireEvent.click(screen.getByRole("button", { name: "진행" }));
    expect(onSend).toHaveBeenCalledWith("진행해");
  });

  it("확정 대기여도 지난 정리에는 버튼이 없다", () => {
    render(<Final item={{ ...final, text: "결제가 필요합니다." }} confirmPending latest={false} onSend={vi.fn()} />);
    expect(screen.queryByTestId("final-actions")).toBeNull();
  });
});
