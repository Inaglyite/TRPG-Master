import { createRequire } from "node:module";
import { expect, test } from "@playwright/test";
import { openLocalStartScreen } from "./readiness";

const require = createRequire(import.meta.url);
const { startServer } = require("./helpers/structured-stub-server.cjs");
let stub: { close: () => Promise<void> };
test.beforeAll(async () => {
  stub = await startServer({ port: 8765 });
});
test.afterAll(async () => {
  await stub?.close();
});

for (const width of [1280, 939, 640]) {
  test(`游戏偏好 ${width}px：开关可用、刷新保留、控件无溢出`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize({ width, height: 900 });
    await openLocalStartScreen(page, "http://127.0.0.1:8765/?mode=local");
    await expect(page.locator(".boot-loader")).toHaveCount(0);
    await page.locator("#btn-settings").click();
    const dialog = page.getByRole("dialog", { name: "游戏设置" });
    await dialog.getByRole("tab", { name: "游戏偏好" }).click();
    const toggle = dialog.getByRole("switch", { name: "显示行动提示" });
    await expect(toggle).toBeChecked();
    await toggle.click();
    await expect(toggle).not.toBeChecked();
    await page.waitForTimeout(1000);
    const box = await dialog.boundingBox();
    expect(box).not.toBeNull();
    expect(box!.x).toBeGreaterThanOrEqual(0);
    expect(box!.x + box!.width).toBeLessThanOrEqual(width);
    const measurements = await dialog.getByRole("tab").evaluateAll((buttons) =>
      buttons.map((button) => {
        const rect = button.getBoundingClientRect();
        return {
          height: rect.height,
          fits: button.scrollWidth <= button.clientWidth,
        };
      }),
    );
    expect(new Set(measurements.map((item) => item.height)).size).toBe(1);
    expect(measurements.every((item) => item.fits)).toBe(true);
    await page.screenshot({
      path: testInfo.outputPath(`game-settings-${width}.png`),
    });
    await page.reload();
    await expect(page.locator("#btn-settings")).toBeVisible();
    await expect(page.locator(".boot-loader")).toHaveCount(0);
    await page.locator("#btn-settings").click();
    await page.getByRole("tab", { name: "游戏偏好" }).click();
    await expect(
      page.getByRole("switch", { name: "显示行动提示" }),
    ).not.toBeChecked();
  });
}
