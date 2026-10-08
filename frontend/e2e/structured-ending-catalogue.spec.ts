/** UI layout and wire evidence; keeper privacy is checked with real 3p backend. */
import { createRequire } from "node:module";
import { resolve } from "node:path";
import { expect, test } from "@playwright/test";
import { openLocalStartScreen } from "./readiness";
const { startServer } = createRequire(import.meta.url)(
  "./helpers/structured-stub-server.cjs",
);

test("结局核对页：缺条件可读、已齐只准备表单、四宽度与短窗", async ({
  page,
}) => {
  const stub = await startServer({
    port: 8774,
    scenario: "keeper-ending-catalog",
  });
  try {
    await openLocalStartScreen(
      page,
      "http://127.0.0.1:8774/?mode=local&role=keeper",
    );
    await page.locator(".module-select-trigger").click();
    await page.getByRole("option", { name: /猩红文档/ }).click();
    await page.locator("#btn-start").click();
    await page.locator("#btn-character-confirm").click();
    await page.getByTestId("btn-keeper-console").click();
    await page
      .getByTestId("keeper-ruling-audit")
      .locator(":scope > summary")
      .click();
    const catalogue = page.getByTestId("keeper-ending-catalogue");
    const missing = catalogue.locator('[data-ending-id="truth_and_seal"]');
    const ready = catalogue.locator('[data-ending-id="leave"]');
    await expect(ready.getByRole("button", { name: "准备结算" })).toBeEnabled();
    await expect(missing).toContainText("条件未齐");
    await expect(
      missing.getByRole("button", { name: "准备结算" }),
    ).toBeDisabled();
    await missing.locator(".ending-condition-details > summary").click();
    await expect(missing).toContainText("monster_defeated");
    await expect(missing).toContainText("false");
    for (const [width, height] of [
      [1280, 900],
      [939, 900],
      [640, 900],
      [390, 900],
      [390, 360],
    ]) {
      await page.setViewportSize({ width, height });
      const summary = missing.locator(".ending-condition-details > summary");
      await summary.scrollIntoViewIfNeeded();
      expect(await summary.evaluate((el) => getComputedStyle(el).color)).toBe(
        "rgb(49, 42, 33)",
      );
      expect(
        await summary.evaluate((el) => {
          const r = el.getBoundingClientRect();
          return (
            r.height >= 44 &&
            el.contains(
              document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2),
            )
          );
        }),
      ).toBe(true);
      await page.screenshot({
        path: resolve(
          import.meta.dirname,
          `../../docs/design/platform-ui/ending-catalogue-reading-${width}${height === 360 ? "-short" : ""}.png`,
        ),
      });
      const button = ready.getByRole("button", { name: "准备结算" });
      await button.scrollIntoViewIfNeeded();
      const metric = await button.evaluate((el) => {
        const r = el.getBoundingClientRect(),
          s = getComputedStyle(el);
        return {
          color: s.color,
          h: r.height,
          padding: parseFloat(s.paddingLeft),
          nowrap: s.whiteSpace,
          hit: el.contains(
            document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2),
          ),
        };
      });
      expect(metric.h).toBeGreaterThanOrEqual(44);
      expect(metric.color).toBe("rgb(49, 42, 33)");
      expect(metric.padding).toBeGreaterThanOrEqual(10);
      expect(metric.nowrap).toBe("nowrap");
      expect(metric.hit).toBe(true);
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      ).toBe(true);
      await page.screenshot({
        path: resolve(
          import.meta.dirname,
          `../../docs/design/platform-ui/ending-catalogue-${width}${height === 360 ? "-short" : ""}.png`,
        ),
      });
    }
    expect(
      stub.received.some(
        ({ frame }: { frame: Record<string, unknown> }) =>
          frame.type === "command_request",
      ),
    ).toBe(false);
    await ready.getByRole("button", { name: "准备结算" }).click();
    await expect(page.getByLabel("模组结局 ID")).toHaveValue("leave");
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
