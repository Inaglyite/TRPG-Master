/** Layout/read-only display using a stub; real clock settlement is in 3p. */
import { createRequire } from "node:module";
import { resolve } from "node:path";
import { expect, test } from "@playwright/test";
import { openLocalStartScreen } from "./readiness";

const { startServer } = createRequire(import.meta.url)(
  "./helpers/structured-stub-server.cjs",
);

test("游戏时间：旧服务未提供不伪装0，四宽度/短窗HUD及时间表单可读", async ({
  page,
}) => {
  test.setTimeout(180000);
  const stub = await startServer({
    port: 8765,
    scenario: "keeper-ending-catalog",
  });
  try {
    await openLocalStartScreen(
      page,
      "http://127.0.0.1:8765/?mode=local&role=keeper",
    );
    await page.locator(".module-select-trigger").click();
    await page.getByRole("option", { name: /猩红文档/ }).click();
    await page.locator("#btn-start").click();
    await page.locator("#btn-character-confirm").click();
    await expect(page.getByTestId("header-game-clock")).toHaveText(
      "游戏时间 · 未提供",
    );
    await page.getByTestId("btn-keeper-console").click();
    await page.getByTestId("keeper-cmd-advance_time").click();
    const reference = page.getByTestId("reference-game-clock");
    await expect(reference).toContainText("未提供");
    for (const [width, height] of [
      [1280, 900],
      [939, 900],
      [640, 900],
      [390, 900],
      [390, 360],
    ]) {
      await page.setViewportSize({ width, height });
      const activity = page.getByRole("combobox", {
        name: "活动类型",
        exact: true,
      });
      await expect(activity).toHaveValue("");
      await expect(
        activity.getByRole("option", { name: "未指定（按等待计时）" }),
      ).toHaveCount(1);
      await activity.scrollIntoViewIfNeeded();
      const activityBox = await activity.evaluate((el) => {
        const r = el.getBoundingClientRect();
        return {
          height: r.height,
          within:
            r.left >= 0 &&
            r.right <= innerWidth &&
            r.top >= 0 &&
            r.bottom <= innerHeight,
          hit: el.contains(
            document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2),
          ),
        };
      });
      expect(activityBox.height).toBeGreaterThanOrEqual(44);
      expect(activityBox.within).toBe(true);
      expect(activityBox.hit).toBe(true);
      await reference.scrollIntoViewIfNeeded();
      await expect(reference).toBeVisible();
      expect(await reference.evaluate((el) => getComputedStyle(el).color)).toBe(
        "rgb(49, 42, 33)",
      );
      await page.screenshot({
        path: resolve(
          import.meta.dirname,
          `../../docs/design/platform-ui/game-clock-form-${width}${height === 360 ? "-short" : ""}.png`,
        ),
      });
      const submit = page.getByTestId("keeper-submit");
      await submit.scrollIntoViewIfNeeded();
      const box = await submit.evaluate((el) => {
        const r = el.getBoundingClientRect(),
          s = getComputedStyle(el);
        return {
          height: r.height,
          nowrap: s.whiteSpace,
          padding: parseFloat(s.paddingLeft),
          hit: el.contains(
            document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2),
          ),
        };
      });
      expect(box.height).toBeGreaterThanOrEqual(44);
      expect(box.nowrap).toBe("nowrap");
      expect(box.padding).toBeGreaterThanOrEqual(13);
      expect(box.hit).toBe(true);
      await page.getByRole("button", { name: "关闭主持台" }).click();
      await expect(page.getByTestId("header-game-clock")).toBeVisible();
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      ).toBe(true);
      await page.screenshot({
        path: resolve(
          import.meta.dirname,
          `../../docs/design/platform-ui/game-clock-header-${width}${height === 360 ? "-short" : ""}.png`,
        ),
      });
      await page.getByTestId("btn-keeper-console").click();
    }
  } finally {
    await stub.close();
  }
});
