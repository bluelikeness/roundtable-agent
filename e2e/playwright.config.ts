// UI E2E (docs/05-ui.md 8장: Mock 어댑터 + 실제 허브·러너로 시나리오 5개, 오케스트레이터 T2·T3·T7·T8·T10 대응).
// 스택은 make e2e 가 띄운다(e2e/compose.yml). 여기서는 붙기만 한다.
import { defineConfig, devices } from "@playwright/test";

const port = process.env.E2E_WEB_PORT ?? "8090";

export default defineConfig({
  testDir: "./tests",
  // 시나리오마다 허브 bots.yaml 을 갈아 끼우고 러너도 하나라 한 번에 하나씩 돈다
  workers: 1,
  fullyParallel: false,
  // 실제 claude -p 가 돌므로 한 시나리오가 수십 초~수 분 걸린다(러너 전체 상한 600초)
  timeout: 6 * 60_000,
  expect: { timeout: 10_000 },
  // LLM 응답은 매번 다르다. 재시도로 덮지 않고 실패를 그대로 본다
  retries: 0,
  reporter: [["list"], ["html", { outputFolder: "out/report", open: "never" }]],
  outputDir: "out/results",
  globalSetup: "./global-setup.ts",
  use: {
    ...devices["Desktop Chrome"],
    baseURL: `http://127.0.0.1:${port}`,
    storageState: "out/state.json",
    locale: "ko-KR",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
});
