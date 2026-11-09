// 테스트 운영자를 E2E 전용 DB(tmpfs)에 만들고 로그인 화면으로 로그인해 쿠키를 out/state.json 에 둔다.
// 비밀번호·TOTP 시크릿은 이 프로세스 메모리에만 있다. 저장소·로그·픽스처에 남기지 않는다.
// out/state.json 의 쿠키는 E2E 스택(실행마다 새 DB·새 서명 키)에서만 유효하고 커밋하지 않는다.
import { chromium, expect, type FullConfig } from "@playwright/test";
import { randomBytes } from "node:crypto";
import { mkdirSync } from "node:fs";
import { compose, totp, useScenario } from "./lib";

const USERNAME = "e2e";

/** 운영자를 만들거나(새 스택) 재설정해(재사용 스택) TOTP 시크릿을 돌려받는다 */
function provisionOperator(password: string): string {
  const admin = (cmd: string) =>
    compose(["exec", "-T", "hub-api", "python", "-m", "app.admin", cmd, "--username", USERNAME, "--password-stdin"], `${password}\n`);
  let out = "";
  try {
    out = admin("create-operator");
  } catch {
    // 이미 있는 운영자(E2E_KEEP=1 로 스택을 재사용) → 아래에서 재설정
  }
  if (!/secret:/.test(out)) {
    admin("reset-password");
    out = admin("reset-totp");
  }
  const m = /secret:\s*([A-Z2-7]+)/.exec(out);
  if (!m) throw new Error("운영자 생성 결과에서 TOTP 시크릿을 찾지 못했습니다");
  return m[1];
}

export default async function globalSetup(config: FullConfig) {
  mkdirSync("out", { recursive: true });
  useScenario("default");
  const password = randomBytes(18).toString("base64url");
  const secret = provisionOperator(password);

  const baseURL = config.projects[0].use.baseURL!;
  const browser = await chromium.launch();
  const page = await browser.newPage({ baseURL, locale: "ko-KR" });
  // 로그인 화면 자체도 확인한다(05-ui 7장: 아이디·비밀번호·TOTP)
  await page.goto("/");
  await page.getByLabel("아이디").fill(USERNAME);
  await page.getByLabel("비밀번호").fill(password);
  await page.getByLabel("인증 코드(TOTP)").fill(totp(secret));
  await page.getByRole("button", { name: "로그인" }).click();
  await expect(page.getByLabel("메시지 입력")).toBeVisible({ timeout: 15_000 });
  await page.context().storageState({ path: "out/state.json" });
  await browser.close();
}
