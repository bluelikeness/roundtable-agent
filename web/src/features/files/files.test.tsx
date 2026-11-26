// 파일 첨부: 입력창 📎·말풍선 파일 보기·structured.files (docs/05-ui.md 3·5장)
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { FileRef, MessageOut } from "../../api/types";
import { BotReply, User } from "../chat/bubbles";
import { Composer } from "../chat/Composer";
import { emptyChat, loadMessages, type BotItem, type UserItem } from "../../store/chat";
import { parseStructured } from "../structured";
import { humanSize } from "./FileList";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const ID = "f_abcdefghijklmnop1234";
const img: FileRef = { id: ID, name: "고양이.png", mime: "image/png", size: 2048 };
const vid: FileRef = { id: "f_videoVIDEOvideo00001", name: "clip.mp4", mime: "video/mp4", size: 3 * 1024 * 1024 };
const doc: FileRef = { id: "f_docDOCdocDOCdoc00001", name: "a.pdf", mime: "application/pdf", size: 10 };

describe("파일 첨부", () => {
  it("📎 로 고르면 바로 올리고, 다 올라가면 ID 와 함께 보낸다(글 없이도)", async () => {
    let resolve!: (r: Response) => void;
    const fetchMock = vi.fn(() => new Promise<Response>((r) => (resolve = r)));
    vi.stubGlobal("fetch", fetchMock);
    const onSubmit = vi.fn().mockResolvedValue(true);
    render(<Composer bots={[]} onSubmit={onSubmit} />);
    const input = screen.getByTestId("file-input") as HTMLInputElement;
    const file = new File(["png"], "고양이.png", { type: "image/png" });
    fireEvent.change(input, { target: { files: [file] } });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/files");
    expect((init.headers as Record<string, string>)["X-File-Name"]).toBe(encodeURIComponent("고양이.png"));
    expect(screen.getByText("올리는 중…")).toBeTruthy();
    // 올리는 중에는 못 보낸다
    expect((screen.getByText("전송") as HTMLButtonElement).disabled).toBe(true);

    await act(async () => resolve(new Response(JSON.stringify(img), { status: 201 })));
    await waitFor(() => expect((screen.getByText("전송") as HTMLButtonElement).disabled).toBe(false));
    await act(async () => {
      fireEvent.click(screen.getByText("전송"));
    });
    expect(onSubmit).toHaveBeenCalledWith("", [img]);
    expect(screen.queryByTestId("attach-list")).toBeNull();
  });

  it("너무 큰 파일은 표시만 하고 보내지 않는다, ✕ 로 뺀다", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "file_too_large" }), { status: 413 })));
    render(<Composer bots={[]} onSubmit={vi.fn()} />);
    fireEvent.change(screen.getByTestId("file-input"), { target: { files: [new File(["x"], "big.mp4")] } });
    await screen.findByText("너무 큼");
    expect((screen.getByText("전송") as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByLabelText("big.mp4 빼기"));
    expect(screen.queryByTestId("attach-list")).toBeNull();
  });

  it("말풍선: 이미지는 그림, 영상은 재생기, 그 밖은 내려받기", () => {
    const item: UserItem = {
      key: "s1", seq: 1, order: 1, ts: "2026-11-26T10:00:00Z", kind: "user", text: "", optimistic: false,
      attachments: [img, vid, doc],
    };
    const { container } = render(<User item={item} />);
    expect(container.querySelector("img")?.getAttribute("src")).toBe(`/api/files/${ID}`);
    expect(container.querySelector("video")?.getAttribute("src")).toBe(`/api/files/${vid.id}`);
    const link = screen.getByText(/a\.pdf/).closest("a")!;
    expect(link.getAttribute("href")).toBe(`/api/files/${doc.id}`);
    expect(link.hasAttribute("download")).toBe(true);
  });

  it("structured.files: 봇이 만든 파일을 말풍선에 그린다. 모양이 틀린 ID 는 버린다", () => {
    const s = parseStructured({ files: [vid, { id: "../etc/passwd", name: "x", mime: "text/plain", size: 1 }, "x"] });
    expect(s.files).toEqual([vid]);
    const item: BotItem = {
      key: "s2", seq: 2, order: 2, ts: "2026-11-26T10:00:00Z", kind: "bot", bot: "pickle", ok: true, raw: "영상 만들었어요",
      structured: { files: [vid] }, needsInput: false, error: null, latencyMs: 1000, toolCallId: "t",
    };
    const { container } = render(<BotReply item={item} color="#888" />);
    expect(container.querySelector("video")).toBeTruthy();
  });

  it("새로고침: 사용자 메시지의 structured.attachments 를 되살린다", () => {
    const rest: MessageOut[] = [
      {
        id: "u1", session_id: "s", role: "user", bot_name: null, raw: "이걸로", structured: { attachments: [img] },
        seq: 1, created_at: "2026-11-26T10:00:00Z",
      },
    ];
    const c = loadMessages(emptyChat(), rest);
    expect((c.items[0] as UserItem).attachments).toEqual([img]);
  });

  it("크기 표시", () => {
    expect(humanSize(10)).toBe("10B");
    expect(humanSize(2048)).toBe("2.0KB");
    expect(humanSize(3 * 1024 * 1024)).toBe("3.0MB");
  });
});
