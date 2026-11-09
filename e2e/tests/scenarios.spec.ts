// UI E2E 5개 (docs/05-ui.md 8장). 오케스트레이터 시나리오(docs/04-orchestrator.md 10장)와 대응:
//   T2 단일 봇 + 새로고침 복원 / T3 팬아웃 3봇 / T7 되물음 사용자 전달 / T8→T9 확정과 [진행] 버튼 / T10 봇 불가
// 실제 claude -p 가 Mock 을 부른다. 판정은 화면(말풍선·헤더·패널)으로만 한다. 정리 문장 내용은
// LLM 마다 달라서 최소한(사실 키워드)만 본다. 형식 판정은 make eval 이 맡는다.
import { expect, test } from "@playwright/test";
import { botReplies, expectBotState, openApp, pendings, startSession, useScenario, waitFinal } from "../lib";

test("T2 단일 봇: 대기→원문→정리, 새로고침 후 복원", async ({ page }) => {
  useScenario("default");
  await openApp(page);
  for (const b of ["mock-a", "mock-b", "mock-c"]) await expectBotState(page, b, "연결");

  const text = "강남 한식 내일 7시 2명";
  await startSession(page, text);
  // 실행 중 헤더와 대기 말풍선(봇 색 점선, "응답 중")
  await expect(page.getByTestId("run-status")).toContainText("실행 중", { timeout: 60_000 });
  await expect(pendings(page, "mock-a")).toBeVisible({ timeout: 120_000 });
  await expect(pendings(page, "mock-a")).toContainText("응답 중");
  // 대기 말풍선이 그 자리에서 원문으로 바뀐다
  const reply = botReplies(page, "mock-a");
  await expect(reply).toHaveCount(1, { timeout: 60_000 });
  await expect(reply).toContainText("한옥집");
  await expect(pendings(page, "mock-a")).toHaveCount(0);

  const final = await waitFinal(page);
  await expect(final.locator(".bubble-head")).toHaveText("Claude 정리");
  await expect(page.getByTestId("final-actions")).toHaveCount(0); // 확정 질문이 아니면 버튼 없음
  // 다른 봇은 부르지 않았다
  await expect(page.locator(".bubble.bot")).toHaveCount(1);
  // 세션 목록 제목 = 첫 메시지 앞 20자
  await expect(page.locator(".session-item.active .session-title")).toContainText(text.slice(0, 20));

  // 새로고침: REST 전체 로드 + subscribe since_seq 로 같은 화면이 복원된다
  const finalText = await final.innerText();
  await page.reload();
  await expect(page.locator(".run-header .badge.warn")).toHaveCount(0, { timeout: 20_000 });
  await expect(page.locator(".bubble.user").filter({ hasText: text })).toHaveCount(1);
  await expect(botReplies(page, "mock-a")).toContainText("한옥집");
  await expect(page.locator(".bubble.final")).toHaveCount(1);
  expect(await page.locator(".bubble.final").innerText()).toBe(finalText);
});

test("T3 팬아웃: 셋 다 응답, 헤더 카운터, 비교표", async ({ page }) => {
  useScenario("default");
  await openApp(page);
  for (const b of ["mock-a", "mock-b", "mock-c"]) await expectBotState(page, b, "연결");

  await startSession(page, "셋 다한테 도쿄 왕복 최저가 물어봐줘");
  // 실행 중 헤더에 "n/3 응답" 카운터
  await expect(page.getByTestId("run-status")).toHaveText(/실행 중 · \d\/3 응답/, { timeout: 120_000 });
  await expect(botReplies(page, "mock-a")).toHaveCount(1, { timeout: 120_000 });
  await expect(botReplies(page, "mock-b")).toHaveCount(1, { timeout: 120_000 });
  await expect(botReplies(page, "mock-c")).toHaveCount(1, { timeout: 120_000 });
  await expect(botReplies(page, "mock-b")).toContainText("238,000");
  await expect(botReplies(page, "mock-c")).toContainText("ZIPAIR");

  const final = await waitFinal(page);
  // 팬아웃 정리에는 봇×항목 비교표가 마크다운 표로 그려진다
  await expect(final.locator("table")).toBeVisible();
  await expect(final.locator("table")).toContainText("mock-c");
});

test("T7 되물음 전달: 봇이 예산을 물으면 사용자에게 넘기고 끝낸다", async ({ page }) => {
  useScenario("budget");
  await openApp(page);
  await expectBotState(page, "mock-a", "연결");

  await startSession(page, "강남 한식 내일 7시 2명 예약 가능한 곳 찾아줘");
  const reply = botReplies(page, "mock-a");
  await expect(reply).toHaveCount(1, { timeout: 180_000 });
  await expect(reply).toContainText("예산");
  await expect(reply.locator(".tag")).toHaveText("봇이 추가 정보를 묻고 있습니다");

  const final = await waitFinal(page);
  await expect(final).toContainText("예산");
  await expect(page.getByTestId("final-actions")).toHaveCount(0);
  // 사용자만 아는 값이라 Claude 가 대신 답하지 않았다(봇 응답 1개뿐)
  await expect(page.locator(".bubble.bot")).toHaveCount(1);
});

test("T8→T9 확정: 확인 박스와 [진행] 버튼, 누르면 [tap:] 으로 확정", async ({ page }) => {
  useScenario("confirm");
  await openApp(page);
  await expectBotState(page, "mock-a", "연결");

  await startSession(page, "강남 한옥집 내일 19시 2명 예약해줘");
  await expect(botReplies(page, "mock-a")).toHaveCount(1, { timeout: 180_000 });
  // structured.confirmation_required → 말풍선 밖 확인 박스(버튼 없음)
  const box = page.getByTestId("confirm-box");
  await expect(box).toContainText("예약금 20,000원");
  await expect(box).toContainText("이 작업은 확인이 필요합니다");
  await expect(box.locator("button")).toHaveCount(0);

  const final = await waitFinal(page);
  await expect(final).toContainText("20,000");
  // 자동으로 확정하지 않았다
  await expect(page.locator(".bubble.bot").filter({ hasText: "확정되었습니다" })).toHaveCount(0);
  // 정리가 확정 질문이면 [진행] [취소]
  const actions = final.getByTestId("final-actions");
  await expect(actions.getByRole("button", { name: "진행" })).toBeVisible();
  await expect(actions.getByRole("button", { name: "취소" })).toBeVisible();

  await actions.getByRole("button", { name: "진행" }).click();
  await expect(page.locator(".bubble.user").filter({ hasText: /^진행해$/ })).toBeVisible();
  // 다음 실행에서 직전 action_id 로 [tap:] → Mock 이 확정 완료로 답한다
  const done = page.locator(".bubble.bot").filter({ hasText: "확정되었습니다" });
  await expect(done).toHaveCount(1, { timeout: 180_000 });
  await expect(done).toContainText("HK-1109");
  const final2 = await waitFinal(page, 2);
  await expect(final2).toContainText("HK-1109");
  // 지난 정리의 버튼은 사라지고, 완료 보고에는 버튼이 없다
  await expect(page.getByTestId("final-actions")).toHaveCount(0);
});

test("T10 봇 불가: 로그인 만료 봇은 패널에 경고, 부르지 않는다", async ({ page }) => {
  useScenario("b-down");
  await openApp(page);
  await expectBotState(page, "mock-b", "로그인 필요");
  const row = page.getByTestId("bot-row-mock-b");
  await expect(row.getByRole("button", { name: "재로그인" })).toBeVisible();
  await expect(page.getByTestId("bot-row-mock-a").locator(".bot-state")).toHaveText("연결");

  await startSession(page, "셋 다한테 도쿄 왕복 최저가 물어봐줘");
  const final = await waitFinal(page);
  await expect(botReplies(page, "mock-a")).toHaveCount(1);
  await expect(botReplies(page, "mock-c")).toHaveCount(1);
  // 허브가 available=false 봇을 mcp.json 에서 빼므로 mock-b 는 대기·원문·오류 어느 말풍선도 없다
  const mockB = page.locator(".bubble.bot, .bubble.pending, .bubble.bot-error").filter({ has: page.locator(".bot-name", { hasText: /^mock-b$/ }) });
  await expect(mockB).toHaveCount(0);
  await expect(final).toBeVisible();

  // 재로그인 시트가 열린다(어댑터 login/start 응답으로 그리는 공통 껍데기)
  await row.getByRole("button", { name: "재로그인" }).click();
  await expect(page.getByTestId("login-sheet")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("login-sheet")).toHaveCount(0);
});
