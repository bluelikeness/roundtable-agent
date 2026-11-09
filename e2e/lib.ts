// E2E 공용 도우미: 스택 명령, TOTP, 시나리오 전환, 화면 조작.
import { expect, type Locator, type Page } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { createHmac } from "node:crypto";
import { copyFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));

/** e2e 스택에 docker compose 명령을 보낸다. stdout 을 돌려준다(출력은 로그에 찍지 않는다) */
export function compose(args: string[], input?: string): string {
  return execFileSync(
    "docker",
    ["compose", "-p", "multibot-e2e", "--env-file", join(HERE, "out", "env"), "-f", join(HERE, "compose.yml"), ...args],
    { input, encoding: "utf-8", stdio: ["pipe", "pipe", "pipe"] },
  );
}

/** RFC 6238 TOTP(SHA-1, 6자리, 30초). 허브(pyotp) 기본값과 같다 */
export function totp(secretB32: string, now = Date.now()): string {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  let bits = "";
  for (const ch of secretB32.replace(/=+$/, "")) bits += alphabet.indexOf(ch).toString(2).padStart(5, "0");
  const key = Buffer.from(bits.match(/.{8}/g)!.map((b) => parseInt(b, 2)));
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(Math.floor(now / 1000 / 30)));
  const h = createHmac("sha1", key).update(counter).digest();
  const off = h[h.length - 1] & 0x0f;
  const code = (h.readUInt32BE(off) & 0x7fffffff) % 1_000_000;
  return code.toString().padStart(6, "0");
}

export type Scenario = "default" | "budget" | "confirm" | "b-down";

/** 허브 bots.yaml 을 시나리오 파일로 바꾼다. 허브는 실행마다·헬스체크(2초)마다 다시 읽는다 */
export function useScenario(name: Scenario): void {
  const dir = join(HERE, "out", "bots");
  mkdirSync(dir, { recursive: true });
  copyFileSync(join(HERE, "bots", `${name}.yaml`), join(dir, "bots.yaml"));
}

/** 봇 상태 패널 행이 이 상태어가 될 때까지 기다린다(시나리오 전환이 허브에 반영됐는지) */
export async function expectBotState(page: Page, bot: string, label: string): Promise<void> {
  await expect(page.getByTestId(`bot-row-${bot}`).locator(".bot-state")).toHaveText(label, { timeout: 20_000 });
}

/** 화면을 열고 WebSocket 이 열릴 때까지 기다린다(닫혀 있으면 전송을 거절하고 안내만 띄운다) */
export async function openApp(page: Page): Promise<void> {
  await page.goto("/");
  await expect(page.getByLabel("메시지 입력")).toBeVisible();
  // 헤더 경고 배지("연결 끊김 · 다시 연결 중", "연결 중…")가 사라지면 open
  await expect(page.locator(".run-header .badge.warn")).toHaveCount(0, { timeout: 20_000 });
}

/** 새 세션을 열고 첫 메시지를 보낸다 */
export async function startSession(page: Page, text: string): Promise<void> {
  await openApp(page);
  await page.getByRole("button", { name: "+ 새 세션" }).click();
  await send(page, text);
}

export async function send(page: Page, text: string): Promise<void> {
  const box = page.getByLabel("메시지 입력");
  await box.fill(text);
  await box.press("Enter");
  await expect(page.locator(".bubble.user").filter({ hasText: text }).last()).toBeVisible();
}

/** n 번째 Claude 정리가 오고 헤더가 "완료"가 될 때까지 기다린다 */
export async function waitFinal(page: Page, n = 1): Promise<Locator> {
  const finals = page.locator(".bubble.final");
  await expect(finals).toHaveCount(n, { timeout: 5 * 60_000 });
  await expect(page.getByTestId("run-status")).toHaveText("완료", { timeout: 30_000 });
  return finals.nth(n - 1);
}

/** 봇 원문 말풍선(머리글 봇 이름으로 거른다) */
export function botReplies(page: Page, bot: string): Locator {
  return page.locator(".bubble.bot").filter({ has: page.locator(".bot-name", { hasText: new RegExp(`^${bot}$`) }) });
}

export function pendings(page: Page, bot: string): Locator {
  return page.locator(".bubble.pending").filter({ has: page.locator(".bot-name", { hasText: new RegExp(`^${bot}$`) }) });
}
