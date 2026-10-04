/** Scripted HTTP faults in the real UI; not backend auth/storage acceptance. */
import { createRequire } from "node:module";
import { expect, test } from "@playwright/test";

const require = createRequire(import.meta.url);
const { startServer } = require("./helpers/structured-stub-server.cjs");
let stub: { close: () => Promise<void> };
test.beforeAll(async () => {
  stub = await startServer({ port: 8765 });
});
test.afterAll(async () => {
  await stub?.close();
});

test("登录无回执有界恢复，手动核对后才重试，凭据草稿不丢", async ({ page }) => {
  test.setTimeout(90000);
  let submissions = 0;
  let release!: () => void;
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.addInitScript(() => {
    localStorage.setItem("trpg-cloud-origin", "http://127.0.0.1:8765");
  });
  await page.route("**/api/auth/me", async (route) => {
    await route.fulfill({
      status: 401,
      contentType: "application/json",
      body: JSON.stringify({
        code: "not_authenticated",
        message: "尚未登录",
      }),
    });
  });
  await page.route("**/api/auth/login", async (route) => {
    submissions += 1;
    if (submissions === 1) await held;
    try {
      await route.fulfill({
        status: 401,
        contentType: "application/json",
        body: JSON.stringify({
          code: "bad_credentials",
          message: "用户名或密码错误",
        }),
      });
    } catch {
      /* The first fetch may already have been aborted by its deadline. */
    }
  });
  await page.goto("http://127.0.0.1:8765/?mode=online");
  await expect(page.locator(".boot-loader")).toHaveCount(0);
  await page.getByLabel("用户名", { exact: true }).fill("deadline-test-only");
  await page.getByLabel("密码", { exact: true }).fill("not-a-real-password");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect.poll(() => submissions).toBe(1);
  await expect(page.getByRole("alert")).toContainText("不代表", {
    timeout: 35000,
  });
  expect(submissions).toBe(1);
  const check = page.getByRole("button", { name: "重新检查", exact: true });
  await expect(check).toBeEnabled();
  for (const width of [1280, 939, 640, 390]) {
    await page.setViewportSize({ width, height: 480 });
    await expect(check).toBeInViewport();
    await page.screenshot({
      path: `test-results/http-login-recovery-${width}.png`,
    });
  }
  release();
  await check.click();
  await expect(page.getByLabel("用户名", { exact: true })).toHaveValue(
    "deadline-test-only",
  );
  await expect(page.getByLabel("密码", { exact: true })).toHaveValue(
    "not-a-real-password",
  );
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("用户名或密码错误");
  expect(submissions).toBe(2);
});
