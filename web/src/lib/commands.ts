// 슬래시 명령 해석 (docs/05-ui.md 5장). UI 가 직접 처리하고 Claude 에는 보내지 않는다.

export interface CommandSpec {
  name: string;
  args: string;
  help: string;
}

export const COMMANDS: CommandSpec[] = [
  { name: "/new", args: "", help: "새 세션" },
  { name: "/reset", args: "<봇>", help: "해당 봇 대화 초기화" },
  { name: "/restart", args: "", help: "Claude Code 세션 새로 시작" },
  { name: "/model", args: "opus|sonnet", help: "다음 실행부터 모델 변경" },
  { name: "/stop", args: "", help: "실행 중인 요청 중단" },
];

export type Command =
  | { cmd: "new" }
  | { cmd: "reset"; bot: string }
  | { cmd: "restart" }
  | { cmd: "model"; model: "opus" | "sonnet" }
  | { cmd: "stop" }
  | { cmd: "invalid"; message: string };

/** 명령이 아니면 null(그대로 Claude 에 보낸다) */
export function parseCommand(text: string): Command | null {
  const t = text.trim();
  if (!t.startsWith("/")) return null;
  const [head, ...rest] = t.split(/\s+/);
  const arg = rest.join(" ");
  switch (head) {
    case "/new":
      return { cmd: "new" };
    case "/restart":
      return { cmd: "restart" };
    case "/stop":
      return { cmd: "stop" };
    case "/reset":
      return arg ? { cmd: "reset", bot: arg.replace(/^@/, "") } : { cmd: "invalid", message: "사용법: /reset <봇>" };
    case "/model":
      return arg === "opus" || arg === "sonnet"
        ? { cmd: "model", model: arg }
        : { cmd: "invalid", message: "사용법: /model opus | sonnet" };
    default:
      // 알 수 없는 /단어 는 명령 오타일 가능성이 높다. 경로("/home/…") 같은 일반 텍스트는 보낸다
      if (/^\/[a-z]+$/.test(head)) return { cmd: "invalid", message: `알 수 없는 명령: ${head}` };
      return null;
  }
}
