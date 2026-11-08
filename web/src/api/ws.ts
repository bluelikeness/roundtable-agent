/**
 * 허브 WebSocket(/ws) 연결 하나. 끊기면 지수 백오프로 다시 붙고, 붙을 때마다 onOpen 을 불러
 * 상위(스토어)가 subscribe since_seq 로 놓친 이벤트를 받게 한다 (docs/05-ui.md 6장).
 */
import type { ClientMessage, HubEvent } from "./types";

/** rejected: 허브가 정책 위반(1008: 로그인 만료·Origin 불일치)으로 닫음. 다시 붙지 않는다 */
export type WsStatus = "connecting" | "open" | "closed" | "rejected";

export interface WsHandlers {
  onEvent: (ev: HubEvent) => void;
  onOpen: () => void;
  onStatus: (s: WsStatus) => void;
}

/** 테스트에서 가짜 소켓을 넣을 수 있게 생성자를 받는다 */
export type SocketFactory = (url: string) => WebSocket;

const MIN_DELAY = 500;
const MAX_DELAY = 10_000;
// hub/app/ws.py CLOSE_POLICY
const CLOSE_POLICY = 1008;

export function wsUrl(loc: Location = window.location): string {
  return `${loc.protocol === "https:" ? "wss:" : "ws:"}//${loc.host}/ws`;
}

export class HubSocket {
  private ws: WebSocket | null = null;
  private delay = MIN_DELAY;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private stopped = false;

  constructor(
    private handlers: WsHandlers,
    private url: string = wsUrl(),
    private factory: SocketFactory = (u) => new WebSocket(u),
  ) {}

  start() {
    this.stopped = false;
    this.connect();
  }

  stop() {
    this.stopped = true;
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    this.ws?.close();
    this.ws = null;
  }

  get isOpen(): boolean {
    return this.ws?.readyState === WebSocket.OPEN;
  }

  send(msg: ClientMessage): boolean {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return false;
    this.ws.send(JSON.stringify(msg));
    return true;
  }

  /** 탭이 다시 보이거나 네트워크가 돌아왔을 때 기다리지 않고 바로 붙는다 */
  reconnectNow() {
    if (this.stopped || this.ws) return;
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    this.connect();
  }

  private connect() {
    this.handlers.onStatus("connecting");
    const ws = this.factory(this.url);
    this.ws = ws;
    ws.onopen = () => {
      this.delay = MIN_DELAY;
      this.handlers.onStatus("open");
      this.handlers.onOpen();
    };
    ws.onmessage = (e) => {
      let ev: HubEvent;
      try {
        ev = JSON.parse(typeof e.data === "string" ? e.data : "");
      } catch {
        return;
      }
      this.handlers.onEvent(ev);
    };
    ws.onclose = (e) => {
      if (this.ws !== ws) return;
      this.ws = null;
      if (e.code === CLOSE_POLICY) {
        this.stopped = true;
        this.handlers.onStatus("rejected");
        return;
      }
      this.handlers.onStatus("closed");
      if (this.stopped) return;
      this.timer = setTimeout(() => {
        this.timer = null;
        this.connect();
      }, this.delay);
      this.delay = Math.min(this.delay * 2, MAX_DELAY);
    };
  }
}
