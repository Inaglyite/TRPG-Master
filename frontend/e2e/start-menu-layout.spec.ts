import { createRequire } from "node:module";
import { expect, test, type Page } from "@playwright/test";
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

async function expectUnifiedTools(page: Page, desktop = false) {
  const tools = page.getByRole("navigation", { name: "游戏工具" });
  const names = ["导入模组", "模组工坊", "模组管理", "角色库", "游戏设置"];
  if (desktop) names.push("退出游戏");
  const buttons = tools.getByRole("button");
  await expect(buttons).toHaveCount(names.length);
  for (const [index, name] of names.entries()) {
    await expect(buttons.nth(index)).toHaveAccessibleName(name);
    await expect(buttons.nth(index)).toBeVisible();
  }
  await expect(page.locator(".start-corner-actions")).toHaveCount(0);
  const measurements = await page.evaluate(() => {
    const panel = document.querySelector(".start-module-tools")!;
    const tools = panel.getBoundingClientRect();
    const main = document
      .querySelector(".start-menu-main")!
      .getBoundingClientRect();
    const controls = [...panel.querySelectorAll("button")].map((button) => {
      const rect = button.getBoundingClientRect();
      return {
        x: rect.x,
        right: rect.right,
        top: rect.top,
        bottom: rect.bottom,
        height: rect.height,
        fits: button.scrollWidth <= button.clientWidth,
        oneLine: [...button.querySelectorAll("span")]
          .filter(
            (label) =>
              label.textContent?.trim() &&
              label.getAttribute("aria-hidden") !== "true",
          )
          .every((label) => {
            const range = document.createRange();
            range.selectNodeContents(label);
            return range.getClientRects().length <= 1;
          }),
      };
    });
    return {
      panelFits: tools.left >= 0 && tools.right <= window.innerWidth,
      noMainOverlap:
        tools.right <= main.left ||
        tools.left >= main.right ||
        tools.bottom <= main.top ||
        tools.top >= main.bottom,
      buttonsInside: controls.every(
        (button) =>
          button.x >= tools.left &&
          button.right <= tools.right &&
          button.top >= tools.top &&
          button.bottom <= tools.bottom,
      ),
      buttonsSeparate: controls.every((button, index) =>
        controls
          .slice(index + 1)
          .every(
            (other) =>
              button.right <= other.x ||
              button.x >= other.right ||
              button.bottom <= other.top ||
              button.top >= other.bottom,
          ),
      ),
      heights: controls.map((button) => button.height),
      labelsFit: controls.every((button) => button.fits && button.oneLine),
    };
  });
  expect(measurements.panelFits).toBe(true);
  expect(measurements.noMainOverlap).toBe(true);
  expect(measurements.buttonsInside).toBe(true);
  expect(measurements.buttonsSeparate).toBe(true);
  expect(measurements.labelsFit).toBe(true);
  expect(new Set(measurements.heights).size).toBe(1);
  return tools;
}

for (const width of [1280, 939, 640, 390, 320]) {
  test(`主菜单 ${width}px：五个辅助入口集中且不重叠，选角时整体退场`, async ({
    page,
  }, info) => {
    await page.setViewportSize({ width, height: 800 });
    await openLocalStartScreen(page, `${origin}/?mode=local`);
    await expect(page.locator(".boot-loader")).toHaveCount(0);
    const tools = await expectUnifiedTools(page);
    await page.locator(".module-select-trigger").click();
    await expect(page.getByRole("listbox")).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.locator(".module-select-trigger")).toBeFocused();
    await page.waitForTimeout(1000);
    await page.screenshot({ path: info.outputPath(`start-menu-${width}.png`) });
    await page.locator("#btn-start").click();
    await expect(tools).toBeHidden();
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

test("低高度 Electron 窗口：六个辅助入口集中，按钮完整且与主行动不重叠", async ({
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
    await expectUnifiedTools(page, true);
    await page.screenshot({ path: info.outputPath("short-electron-menu.png") });
  } finally {
    await context.close();
  }
});
