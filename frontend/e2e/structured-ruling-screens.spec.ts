/** Stub-backed layout/payload checks; real transaction evidence is separate. */
import { createRequire } from "node:module";
import { resolve } from "node:path";
import { expect, test } from "@playwright/test";
import { openLocalStartScreen } from "./readiness";
const require = createRequire(import.meta.url);
const { startServer } = require("./helpers/structured-stub-server.cjs");
test("剧情裁定：四宽度真实控件，结局准备不执行", async ({ page }) => {
  test.setTimeout(180000);
  const stub = await startServer({ port: 8774, scenario: "keeper-ruling" });
  try {
    await openLocalStartScreen(page, "http://127.0.0.1:8774/?mode=local");
    await page.locator(".module-select-trigger").click();
    await page.getByRole("option", { name: /猩红文档/ }).click();
    await page.locator("#btn-start").click();
    await page.locator("#btn-character-confirm").click();
    await expect(page.getByTestId("btn-keeper-console")).toBeVisible();
    await page.getByTestId("btn-keeper-console").click();
    await page.getByTestId("keeper-cmd-record_ruling").click();
    await page.getByLabel("剧情条件（模组编号）").selectOption("sealed");
    await page.getByRole("checkbox", { name: /裁定后的状态/ }).check();
    await page
      .getByLabel("裁定依据")
      .fill(
        "玩家已落实仪式，由人类主持确认。只改变已有剧情条件，不立即结束游戏。",
      );
    for (const width of [1280, 939, 640, 390]) {
      await page.setViewportSize({ width, height: 900 });
      const read = page.getByRole("button", { name: "读取当前值" });
      await read.scrollIntoViewIfNeeded();
      const box = await read.evaluate((el) => {
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
      expect(box.height).toBeGreaterThanOrEqual(44);
      expect(box.padding).toBeGreaterThanOrEqual(10);
      expect(box.nowrap).toBe("nowrap");
      expect(box.hit).toBe(true);
      const toggle = page.getByRole("checkbox", { name: /裁定后的状态/ });
      const toggleSize = await toggle.boundingBox();
      expect(toggleSize!.width).toBeLessThanOrEqual(24);
      expect(toggleSize!.height).toBeLessThanOrEqual(24);
      expect(
        await toggle
          .locator("..")
          .evaluate((el) => el.getBoundingClientRect().height),
      ).toBeGreaterThanOrEqual(44);
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      ).toBe(true);
      await page.screenshot({
        path: resolve(
          import.meta.dirname,
          `../../docs/design/platform-ui/ruling-${width}.png`,
        ),
      });
    }
    await page.getByTestId("keeper-submit").click();
    await expect
      .poll(
        () =>
          stub.received.filter(
            ({ frame }: { frame: Record<string, unknown> }) =>
              frame.kind === "record_ruling",
          ).length,
      )
      .toBe(1);
    const command = stub.received.find(
      ({ frame }: { frame: Record<string, unknown> }) =>
        frame.kind === "record_ruling",
    ).frame;
    expect(command.payload).toMatchObject({
      flag_id: "sealed",
      value: true,
      expected_before: false,
    });
    const audit = page.getByTestId("keeper-ruling-audit");
    await audit.locator("summary").click();
    await expect(audit).toContainText("封印完成");
    await audit.getByRole("button", { name: "准备结算" }).click();
    await expect(page.getByLabel("模组结局 ID")).toHaveValue("seal");
    expect(
      stub.received.some(
        ({ frame }: { frame: Record<string, unknown> }) =>
          frame.kind === "end_game",
      ),
    ).toBe(false);
  } finally {
    await stub.close();
  }
});
