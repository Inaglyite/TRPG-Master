import { expect, test } from "@playwright/test";

const baseUrl = process.env.TRPG_E2E_EXTERNAL_BASE_URL?.replace(/\/+$/, "");
const username = process.env.TRPG_E2E_RECOVERY_USERNAME;
const worldId = process.env.TRPG_E2E_RECOVERY_WORLD_ID;

test("服务重启后恢复房间历史、行动权和存档", async ({ page }) => {
  test.skip(
    !baseUrl || !username || !worldId,
    "仅在提供 staging 恢复验收参数时运行",
  );
  test.setTimeout(90_000);

  await page.goto(`${baseUrl}/?mode=online`);
  await page.getByLabel("用户名").fill(username!);
  await page.getByLabel("密码").fill("multiplayer test password");
  await page.getByRole("button", { name: "登录" }).click();
  await expect(page.getByRole("heading", { name: "联机大厅" })).toBeVisible();

  const room = page
    .locator(".room-card", { hasText: "双客户端验收房" })
    .first();
  await expect(room).toBeVisible();
  await room.click();
  await expect(page.locator(".online-subtitle").first()).toContainText(
    worldId!,
  );
  await expect(page.getByTestId("game-room-bar")).toBeVisible();
  await expect(
    page.getByText(/验收行动-.*我检查门锁和附近的脚印/),
  ).toBeVisible();
  await expect(page.locator("#user-input")).toBeEnabled();

  await page.getByRole("button", { name: "打开存档管理" }).click();
  await expect(
    page.getByRole("button", { name: "读取存档" }).first(),
  ).toBeVisible();
});
