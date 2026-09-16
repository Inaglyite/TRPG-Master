/**
 * 结构化操作界面的截图验收（**测试替身后端**）。
 *
 * 覆盖：桌面 / 窄屏 / 长名称 / 主持台 / 禁用原因可见。
 * 截图落在 docs/screenshots/structured-*.png，供人工复核布局与文案。
 */

import { createRequire } from "node:module";
import { resolve } from "node:path";

import { expect, test, type Page } from "@playwright/test";

import { openLocalStartScreen } from "./readiness";

const require = createRequire(import.meta.url);
const { startServer } = require("./helpers/structured-stub-server.cjs") as {
  startServer: (options: { port: number; scenario?: string }) => Promise<{
    close: () => Promise<void>;
    received: { role: string; frame: Record<string, unknown> }[];
  }>;
};

const port = 8772;
const screenshotsDir = resolve(import.meta.dirname, "../../docs/screenshots");

let stub: { close: () => Promise<void> } | null = null;

test.beforeAll(async () => {
  stub = await startServer({ port, scenario: "full" });
});

test.afterAll(async () => {
  if (stub) await stub.close();
});

async function enterGame(page: Page, role = "player-a") {
  await openLocalStartScreen(
    page,
    `http://127.0.0.1:${port}/?mode=local&role=${role}`,
  );
  await page.locator(".module-select-trigger").click();
  await page.getByRole("option", { name: /猩红文档/ }).click();
  await page.locator("#btn-start").click();
  await page.locator("#btn-character-confirm").click();
  await expect(page.locator("#user-input")).toBeEnabled({ timeout: 60_000 });
  await expect(page.getByTestId("structured-tool-row")).toBeVisible({
    timeout: 30_000,
  });
  await page.locator("#btn-panel").click();
  await expect(page.locator("#char-content .inv-card-clues")).toBeVisible({
    timeout: 30_000,
  });
}

test("结构化界面截图：桌面 / 窄屏 / 长名称 / 主持台", async ({ page }) => {
  test.setTimeout(180_000);
  await enterGame(page);

  // 让待办抽屉里同时出现“等待回应”“已完成”和“普通掷骰”三种卡片。
  await page.getByRole("button", { name: "出示" }).first().click();
  const presentDialog = page.getByRole("dialog", { name: "出示线索" });
  await presentDialog
    .getByLabel("向谁出示 / 说明")
    .selectOption("npc:john_whitcroft");
  await presentDialog.getByRole("button", { name: "提交请求" }).click();
  await expect(page.locator(".check-request-card")).toBeVisible({
    timeout: 20_000,
  });
  await page.getByTestId("btn-move").click();
  await page
    .getByRole("dialog", { name: "前往…" })
    .getByRole("button", { name: /密斯卡托尼克大学医学院/ })
    .click();
  await expect(page.locator(".header-scene-name")).toHaveText(
    "密斯卡托尼克大学医学院",
    { timeout: 20_000 },
  );
  await page.getByTestId("btn-free-roll").click();
  await page.getByTestId("roll-confirm").click();
  await expect(page.getByText(/普通掷骰 1d100 → 42/)).toBeVisible({
    timeout: 20_000,
  });
  await page.waitForTimeout(600);
  await page.screenshot({
    path: `${screenshotsDir}/structured-desktop.png`,
  });

  // 窄屏：按钮不挤压、正文不被遮挡。
  await page.setViewportSize({ width: 390, height: 780 });
  await page.waitForTimeout(400);
  await page.screenshot({
    path: `${screenshotsDir}/structured-narrow.png`,
  });
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.waitForTimeout(300);

  // 长名称：出示编辑器里的线索标题与目的地 ID 都不截断关键信息。
  await page.getByRole("button", { name: "出示" }).first().click();
  await expect(page.getByRole("dialog", { name: "出示线索" })).toBeVisible();
  await page.screenshot({
    path: `${screenshotsDir}/structured-present-editor.png`,
  });
  await page.keyboard.press("Escape");

  await page.getByTestId("btn-move").click();
  await expect(page.getByRole("dialog", { name: "前往…" })).toBeVisible();
  await page.screenshot({
    path: `${screenshotsDir}/structured-move-dialog.png`,
  });
  await page.keyboard.press("Escape");
});

test("主持台截图：命令表单与待处理行动", async ({ page }) => {
  test.setTimeout(180_000);
  await enterGame(page, "keeper");
  await page.getByTestId("btn-move").click();
  await page
    .getByRole("dialog", { name: "前往…" })
    .getByRole("button", { name: /密斯卡托尼克大学医学院/ })
    .click();
  await page.waitForTimeout(400);

  await page.getByTestId("btn-keeper-console").click();
  const console_ = page.getByRole("dialog", { name: "主持台" });
  await expect(console_).toBeVisible();
  await page.getByTestId("keeper-cmd-grant_clue").click();
  await expect(page.locator('[data-field="clue_id"] select')).toBeVisible();
  await page.screenshot({
    path: `${screenshotsDir}/structured-keeper-console.png`,
  });
});
