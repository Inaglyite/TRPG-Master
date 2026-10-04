/** Layout/keyboard checks against a scripted WS service, not engine acceptance. */
import { createRequire } from "node:module";
import { expect, test, type Locator, type Page } from "@playwright/test";
import { openLocalStartScreen } from "./readiness";

const require = createRequire(import.meta.url);
const { startServer } = require("./helpers/structured-stub-server.cjs");
const port = 8765;
let stub: {
  close: () => Promise<void>;
  received: { frame: Record<string, unknown> }[];
};

test.beforeAll(async () => {
  stub = await startServer({
    port,
    destinations: Array.from({ length: 24 }, (_, i) => ({
      id: `public_fixture_destination_${i}`,
      name:
        i === 0
          ? "大学医学院"
          : `已公开地点 ${i} · 一条足够长但仍需要完整显示的地点名称`,
    })),
  });
});
test.afterAll(async () => {
  await stub?.close();
});

async function start(page: Page) {
  await openLocalStartScreen(
    page,
    `http://127.0.0.1:${port}/?mode=local&role=player-a`,
  );
  await page.locator(".module-select-trigger").click();
  await page.getByRole("option", { name: /猩红文档/ }).click();
  await page.locator("#btn-start").click();
  await page.locator("#btn-character-confirm").click();
  await expect(page.getByTestId("structured-tool-row")).toBeVisible();
  await expect(page.locator("#user-input")).toBeEnabled();
}

async function buttonGeometry(button: Locator) {
  const measured = await button.evaluate((node) => {
    const rect = node.getBoundingClientRect();
    const style = getComputedStyle(node);
    return {
      height: rect.height,
      top: rect.top,
      bottom: rect.bottom,
      left: rect.left,
      right: rect.right,
      viewportHeight: innerHeight,
      viewportWidth: innerWidth,
      padding: parseFloat(style.paddingLeft),
      whiteSpace: style.whiteSpace,
      hit: node.contains(
        document.elementFromPoint(
          rect.x + rect.width / 2,
          rect.y + rect.height / 2,
        ),
      ),
    };
  });
  expect(measured.height).toBeGreaterThanOrEqual(44);
  expect(measured.padding).toBeGreaterThanOrEqual(10);
  expect(measured.top).toBeGreaterThanOrEqual(0);
  expect(measured.bottom).toBeLessThanOrEqual(measured.viewportHeight);
  expect(measured.left).toBeGreaterThanOrEqual(0);
  expect(measured.right).toBeLessThanOrEqual(measured.viewportWidth);
  expect(measured.whiteSpace).toBe("nowrap");
  expect(measured.hit).toBe(true);
}

for (const width of [1280, 939, 640, 390]) {
  test(`档案夹面板 ${width}×480：中段滚动、操作常驻、真实键盘可达`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 480 });
    await start(page);
    const before = stub.received.length;
    const moveTrigger = page.getByTestId("btn-move");
    await moveTrigger.click();
    const move = page.getByRole("dialog", { name: "前往…" });
    await expect(move).toBeVisible();
    await expect(move).toContainText("当前场景 · 密斯卡托尼克大学");
    await expect(move).not.toContainText("public_fixture_destination");
    const body = move.locator(".panel-action-body");
    expect(
      await body.evaluate((node) => node.scrollHeight > node.clientHeight),
    ).toBe(true);
    const cancel = move.getByRole("button", { name: "取消" });
    const close = move.getByRole("button", { name: "关闭前往面板" });
    const topBefore = await cancel.boundingBox();
    await body.evaluate((node) => {
      node.scrollTop = node.scrollHeight;
    });
    await expect(move.locator(".compact-dialog-location")).toBeVisible();
    expect((await cancel.boundingBox())?.y).toBe(topBefore?.y);
    await buttonGeometry(close);
    await buttonGeometry(cancel);
    await cancel.focus();
    await page.keyboard.press("Tab");
    await expect(close).toBeFocused();
    await page.keyboard.press("Shift+Tab");
    await expect(cancel).toBeFocused();
    await page.screenshot({ path: `/tmp/trpg-compact-move-${width}.png` });
    await page.keyboard.press("Escape");
    await expect(move).toHaveCount(0);
    await expect(moveTrigger).toBeFocused();

    const rollTrigger = page.getByTestId("btn-free-roll");
    await rollTrigger.click();
    const roll = page.getByRole("dialog", { name: "普通掷骰" });
    await expect(roll.getByRole("combobox")).toBeFocused();
    await buttonGeometry(roll.getByRole("button", { name: "取消" }));
    await buttonGeometry(roll.getByTestId("roll-confirm"));
    await buttonGeometry(roll.getByRole("button", { name: "关闭掷骰面板" }));
    await page.screenshot({ path: `/tmp/trpg-compact-roll-${width}.png` });
    await page.keyboard.press("Escape");
    await expect(roll).toHaveCount(0);
    await expect(rollTrigger).toBeFocused();
    const newFrames = stub.received
      .slice(before)
      .filter(({ frame }) =>
        ["action_request", "free_roll_request"].includes(String(frame.type)),
      );
    expect(newFrames).toHaveLength(0);
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
  });
}

test("390×360 的横屏级短窗口仍能关闭和取消，不被文件夹边框遮住", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 360 });
  await start(page);
  await page.getByTestId("btn-move").click();
  const move = page.getByRole("dialog", { name: "前往…" });
  await buttonGeometry(move.getByRole("button", { name: "关闭前往面板" }));
  await buttonGeometry(move.getByRole("button", { name: "取消" }));
  await expect(move.locator(".compact-dialog-location")).toBeVisible();
  await move.getByRole("button", { name: "取消" }).click();
  await page.getByTestId("btn-free-roll").click();
  const roll = page.getByRole("dialog", { name: "普通掷骰" });
  await buttonGeometry(roll.getByTestId("roll-confirm"));
  await buttonGeometry(roll.getByRole("button", { name: "取消" }));
  await expect(roll.getByRole("combobox")).toBeInViewport();
  await page.screenshot({ path: "/tmp/trpg-compact-roll-390-short.png" });
});
