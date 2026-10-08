/** Layout and frame-shape evidence using the stub backend, not battle settlement. */
import { createRequire } from "node:module";
import { resolve } from "node:path";
import { expect, test, type Page } from "@playwright/test";
import { openLocalStartScreen } from "./readiness";
const require = createRequire(import.meta.url);
const { startServer } = require("./helpers/structured-stub-server.cjs");
const images = resolve(import.meta.dirname, "../../docs/design/platform-ui");
async function enter(page: Page, port: number, role = "player-a") {
  await openLocalStartScreen(
    page,
    `http://127.0.0.1:${port}/?mode=local&role=${role}`,
  );
  await page.locator(".module-select-trigger").click();
  await page.getByRole("option", { name: /猩红文档/ }).click();
  await page.locator("#btn-start").click();
  await page.locator("#btn-character-confirm").click();
  await expect(page.getByTestId("structured-tool-row")).toBeVisible({
    timeout: 30_000,
  });
}
test("调查员对抗：确认掷骰不是已结算，四宽度与短窗按钮可点", async ({
  page,
}) => {
  test.setTimeout(180000);
  const stub = await startServer({ port: 8793, scenario: "combat-pvp-ready" });
  try {
    await enter(page, 8793);
    const card = page.getByTestId("combat-field-record");
    await expect(card).toContainText("双方确认前不会产生骰点");
    await expect(page.getByTestId("combat-result-history")).toHaveCount(0);
    for (const [width, height] of [
      [1280, 800],
      [939, 800],
      [640, 800],
      [390, 800],
      [390, 360],
    ]) {
      await page.setViewportSize({ width, height });
      const heights: number[] = [];
      for (const label of ["确认掷骰", "取消动作"]) {
        const button = card.getByRole("button", { name: label, exact: true });
        await button.scrollIntoViewIfNeeded();
        const metric = await button.evaluate((el) => {
          const r = el.getBoundingClientRect(),
            s = getComputedStyle(el);
          return {
            height: r.height,
            padding: parseFloat(s.paddingLeft),
            nowrap: s.whiteSpace,
            hit: el.contains(
              document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2),
            ),
          };
        });
        expect(metric.height).toBeGreaterThanOrEqual(44);
        expect(metric.padding).toBeGreaterThanOrEqual(10);
        expect(metric.nowrap).toBe("nowrap");
        expect(metric.hit).toBe(true);
        heights.push(metric.height);
      }
      expect(Math.abs(heights[0] - heights[1])).toBeLessThanOrEqual(1);
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      ).toBe(true);
      await card.screenshot({
        path: `${images}/combat-pvp-${width}${height === 360 ? "-short" : ""}.png`,
      });
    }
    await card.getByRole("button", { name: "确认掷骰", exact: true }).click();
    await expect
      .poll(
        () =>
          stub.received.filter(
            ({ frame }: { frame: Record<string, unknown> }) =>
              frame.kind === "combat_roll",
          ).length,
      )
      .toBe(1);
    const request = stub.received.find(
      ({ frame }: { frame: Record<string, unknown> }) =>
        frame.kind === "combat_roll",
    ).frame;
    expect(request.payload).toEqual({
      roll_id: "layout-roll",
      response: "roll",
    });
  } finally {
    await stub.close();
  }
});
test("战斗记录：四种窗口按钮可读可点，发送指定 nonce", async ({ page }) => {
  test.setTimeout(180_000);
  const stub = await startServer({ port: 8793, scenario: "combat-roll" });
  try {
    await enter(page, 8793);
    const card = page.getByTestId("combat-field-record");
    await expect(card).toBeVisible();
    for (const width of [1280, 939, 640, 390]) {
      await page.setViewportSize({ width, height: 800 });
      const roll = card.getByRole("button", { name: "掷骰", exact: true });
      await roll.scrollIntoViewIfNeeded();
      await expect(roll).toBeEnabled();
      const metrics = await roll.evaluate((el) => {
        const r = el.getBoundingClientRect(),
          s = getComputedStyle(el);
        return {
          height: r.height,
          width: r.width,
          padding: parseFloat(s.paddingLeft),
          wrap: s.whiteSpace,
          hit: el.contains(
            document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2),
          ),
          overflow: document.documentElement.scrollWidth > window.innerWidth,
        };
      });
      expect(metrics.height).toBeGreaterThanOrEqual(44);
      expect(metrics.padding).toBeGreaterThanOrEqual(10);
      expect(metrics.wrap).toBe("nowrap");
      expect(metrics.hit).toBe(true);
      expect(metrics.overflow).toBe(false);
      await card.screenshot({ path: `${images}/combat-card-${width}.png` });
    }
    await card.getByRole("button", { name: "掷骰", exact: true }).click();
    await expect
      .poll(
        () =>
          stub.received.filter(
            ({ frame }: { frame: Record<string, unknown> }) =>
              frame.kind === "combat_roll",
          ).length,
      )
      .toBe(1);
    const command = stub.received.find(
      ({ frame }: { frame: Record<string, unknown> }) =>
        frame.kind === "combat_roll",
    ).frame;
    expect(command).toMatchObject({
      type: "command_request",
      payload: { roll_id: "layout-roll", response: "roll" },
    });
    expect(command.command_id).toBeTruthy();
  } finally {
    await stub.close();
  }
});
test("其他调查员只读战斗待办，没有代掷按钮", async ({ page }) => {
  const stub = await startServer({ port: 8793, scenario: "combat-observer" });
  try {
    await enter(page, 8793, "player-b");
    const card = page.getByTestId("combat-field-record");
    await expect(card).toBeVisible();
    await expect(card.getByRole("button")).toHaveCount(0);
    await expect(card).toContainText("主持不能代选或代掷");
  } finally {
    await stub.close();
  }
});
test("结局记录：本人奖励与只读终态在窄屏可见", async ({ page }) => {
  const stub = await startServer({ port: 8793, scenario: "combat-ending" });
  try {
    await enter(page, 8793);
    const card = page.getByTestId("combat-ending-record");
    await expect(card).toContainText("真相与封印");
    await expect(card).toContainText("当前声望");
    await expect(page.getByTestId("combat-field-record")).toHaveCount(0);
    for (const width of [1280, 390]) {
      await page.setViewportSize({ width, height: 800 });
      await card.scrollIntoViewIfNeeded();
      await card.screenshot({ path: `${images}/ending-card-${width}.png` });
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      ).toBe(true);
    }
  } finally {
    await stub.close();
  }
});

test("已结算记录：四宽度可读，展开阅读不重骰", async ({ page }) => {
  const stub = await startServer({ port: 8793, scenario: "combat-record" });
  try {
    await enter(page, 8793);
    const history = page.getByTestId("combat-result-history");
    await expect(history).toBeVisible();
    await history.locator("summary").click();
    await expect(history).toContainText("d100=17");
    await expect(history).toContainText("HP 8 → 5");
    for (const width of [1280, 939, 640, 390]) {
      await page.setViewportSize({ width, height: 800 });
      await history.scrollIntoViewIfNeeded();
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      ).toBe(true);
      await history.screenshot({
        path: `${images}/combat-result-${width}.png`,
      });
    }
    expect(
      stub.received.some(
        ({ frame }: { frame: Record<string, unknown> }) =>
          frame.kind === "combat_roll",
      ),
    ).toBe(false);
  } finally {
    await stub.close();
  }
});
