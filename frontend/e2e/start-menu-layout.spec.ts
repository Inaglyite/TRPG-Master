import { createRequire } from "node:module";
import { expect, test } from "@playwright/test";
import { openLocalStartScreen } from "./readiness";

const require = createRequire(import.meta.url);
const { startServer } = require("./helpers/structured-stub-server.cjs");
let stub: { close: () => Promise<void> };
const origin = "http://127.0.0.1:8779";
test.beforeAll(async () => {
  stub = await startServer({ port: 8779 });
});
test.afterAll(async () => {
  await stub?.close();
});

for (const width of [1280, 939, 640, 390, 320]) {
  test(`主菜单 ${width}px：主行动与角落工具独立，选角时辅助工具退场`, async ({
    page,
  }, info) => {
    await page.setViewportSize({ width, height: 800 });
    await openLocalStartScreen(page, `${origin}/?mode=local`);
    await expect(page.locator(".boot-loader")).toHaveCount(0);
    const tools = page.getByRole("navigation", { name: "模组工具" });
    await expect(tools.getByRole("button", { name: "导入模组" })).toBeVisible();
    await expect(tools.getByRole("button", { name: "模组工坊" })).toBeVisible();
    await expect(tools.getByRole("button", { name: "模组管理" })).toBeVisible();
    const boxes = await page.evaluate(() => {
      const rect = (selector: string) => {
        const r = document.querySelector(selector)!.getBoundingClientRect();
        return { x: r.x, right: r.right, top: r.top, bottom: r.bottom };
      };
      return {
        tools: rect(".start-module-tools"),
        main: rect(".start-menu-main"),
        footer: rect(".start-corner-actions"),
      };
    });
    expect(boxes.tools.x).toBeGreaterThanOrEqual(0);
    expect(boxes.tools.right).toBeLessThanOrEqual(width);
    expect(boxes.tools.bottom).toBeLessThan(boxes.main.top);
    expect(boxes.main.bottom).toBeLessThan(boxes.footer.top);
    const buttons = await tools.getByRole("button").evaluateAll((items) =>
      items.map((item) => ({
        height: item.getBoundingClientRect().height,
        fits: item.scrollWidth <= item.clientWidth,
      })),
    );
    expect(new Set(buttons.map((item) => item.height)).size).toBe(1);
    expect(buttons.every((item) => item.fits)).toBe(true);
    await page.locator(".module-select-trigger").click();
    await expect(page.getByRole("listbox")).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.locator(".module-select-trigger")).toBeFocused();
    await page.waitForTimeout(1000);
    await page.screenshot({ path: info.outputPath(`start-menu-${width}.png`) });
    await page.locator("#btn-start").click();
    await expect(tools).toBeHidden();
    await expect(page.locator(".start-corner-actions")).toBeHidden();
    await expect(
      page
        .locator("#character-select-view")
        .getByRole("button", { name: "角色库", exact: true }),
    ).toBeVisible();
  });
}

test("角落导入预览仍覆盖整个视口，取消后焦点回到原入口", async ({ page }) => {
  await page.setViewportSize({ width: 939, height: 800 });
  await page.route("**/api/modules/inspect", async (route) =>
    route.fulfill({
      json: {
        ok: true,
        module: {
          module_key: "fixture",
          package_id: "fixture",
          version: "1.0.0",
          title: "布局验收模组",
          author: "测试作者",
          description: "布局检查",
          system: "coc7",
          capabilities: [],
          file_count: 1,
          warnings: [],
        },
      },
    }),
  );
  await openLocalStartScreen(page, `${origin}/?mode=local`);
  await expect(page.locator(".boot-loader")).toHaveCount(0);
  await page.locator("#module-file-input").setInputFiles({
    name: "fixture.trpgmod",
    mimeType: "application/zip",
    buffer: Buffer.from("fixture"),
  });
  await expect(page.getByText("布局验收模组", { exact: true })).toBeVisible();
  const overlay = page.locator("#module-import-overlay");
  expect(
    await overlay.evaluate((node) => node.parentElement === document.body),
  ).toBe(true);
  const box = await overlay.boundingBox();
  expect(box).toEqual({ x: 0, y: 0, width: 939, height: 800 });
  const cancel = page.locator("#module-import-cancel");
  await expect(cancel).toBeEnabled();
  await cancel.click();
  await expect(overlay).toHaveCount(0);
  await expect(page.locator("#btn-import-module")).toBeFocused();
});

test("低高度桌面窗口：退出和设置入口可见，角落工具与主行动不重叠", async ({
  browser,
}, info) => {
  const context = await browser.newContext({
    viewport: { width: 640, height: 480 },
    userAgent: "Mozilla/5.0 Chrome/134.0.0.0 Electron/42.4.1 Safari/537.36",
  });
  try {
    const page = await context.newPage();
    await openLocalStartScreen(page, `${origin}/?mode=local`);
    await expect(page.locator(".boot-loader")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "退出游戏" })).toBeVisible();
    await page.locator("#btn-settings").scrollIntoViewIfNeeded();
    const measurements = await page.evaluate(() => {
      const tools = document
        .querySelector(".start-module-tools")!
        .getBoundingClientRect();
      const main = document
        .querySelector(".start-menu-main")!
        .getBoundingClientRect();
      const footer = document
        .querySelector(".start-corner-actions")!
        .getBoundingClientRect();
      return {
        noOverlap: tools.bottom < main.top && main.bottom < footer.top,
        fits: footer.left >= 0 && footer.right <= window.innerWidth,
      };
    });
    expect(measurements).toEqual({ noOverlap: true, fits: true });
    await page.screenshot({ path: info.outputPath("short-electron-menu.png") });
  } finally {
    await context.close();
  }
});
