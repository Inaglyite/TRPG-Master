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
    const button = buttons.nth(index);
    const icon = button.locator("img[data-menu-icon]");
    await expect(button).toHaveAccessibleName(name);
    await expect(button).toHaveAttribute("title", /.+/);
    await expect(button).toBeVisible();
    await expect(icon).toBeVisible();
    await expect(icon).toHaveAttribute("src", /\.png(?:$|\?)/);
    await expect
      .poll(() =>
        icon.evaluate(
          (node: HTMLImageElement) => node.complete && node.naturalWidth > 0,
        ),
      )
      .toBe(true);
    const before = await button.boundingBox();
    const iconBefore = await icon.boundingBox();
    await button.hover();
    await expect(icon).toBeVisible();
    await expect.poll(() => button.boundingBox()).toEqual(before);
    await expect.poll(() => icon.boundingBox()).toEqual(iconBefore);
    expect(
      await button.evaluate((node) => getComputedStyle(node).backgroundImage),
    ).toBe("none");
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
      const icon = button.querySelector("img")!.getBoundingClientRect();
      return {
        x: rect.x,
        right: rect.right,
        top: rect.top,
        bottom: rect.bottom,
        width: rect.width,
        height: rect.height,
        iconSize: icon.width,
        iconFits:
          icon.left >= rect.left &&
          icon.right <= rect.right &&
          icon.top >= rect.top &&
          icon.bottom <= rect.bottom,
      };
    });
    return {
      panelFits: tools.left >= 0 && tools.right <= window.innerWidth,
      noMainOverlap:
        tools.right <= main.left ||
        tools.left >= main.right ||
        tools.bottom <= main.top ||
        tools.top >= main.bottom,
      transparent:
        getComputedStyle(panel).backgroundColor === "rgba(0, 0, 0, 0)" &&
        getComputedStyle(panel).backgroundImage === "none" &&
        getComputedStyle(panel).boxShadow === "none",
      buttonsInside: controls.every(
        (button) =>
          button.x >= tools.left &&
          button.right <= tools.right &&
          button.top >= tools.top &&
          button.bottom <= tools.bottom,
      ),
      oneRow: controls.every((button) => button.top === controls[0].top),
      buttonsSeparate: controls.every((button, index) =>
        controls.slice(index + 1).every((other) => button.right <= other.x),
      ),
      sizesCorrect: controls.every(
        (button) =>
          button.width === (window.innerWidth <= 360 ? 40 : 44) &&
          button.height === button.width &&
          button.iconSize === (window.innerWidth <= 360 ? 28 : 32) &&
          button.iconFits,
      ),
    };
  });
  expect(measurements.panelFits).toBe(true);
  expect(measurements.noMainOverlap).toBe(true);
  expect(measurements.transparent).toBe(true);
  expect(measurements.buttonsInside).toBe(true);
  expect(measurements.oneRow).toBe(true);
  expect(measurements.buttonsSeparate).toBe(true);
  expect(measurements.sizesCorrect).toBe(true);
  return tools;
}

for (const width of [1280, 939, 640, 390, 320]) {
  test(`主菜单 ${width}px：五个小图标单行且不重叠，悬停正常，选角时整体退场`, async ({
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

for (const width of [640, 320]) {
  test(`低高度 Electron ${width}px：六个小图标单行，悬停正常且与主行动不重叠`, async ({
    browser,
  }, info) => {
    const context = await browser.newContext({
      viewport: { width, height: 480 },
      userAgent: "Mozilla/5.0 Chrome/134.0.0.0 Electron/42.4.1 Safari/537.36",
    });
    try {
      const page = await context.newPage();
      await openLocalStartScreen(page, `${origin}/?mode=local`);
      await expect(page.locator(".boot-loader")).toHaveCount(0);
      await expectUnifiedTools(page, true);
      await page.screenshot({
        path: info.outputPath(`short-electron-menu-${width}.png`),
      });
    } finally {
      await context.close();
    }
  });
}
