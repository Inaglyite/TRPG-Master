/** Local protocol stub: layout proof only, not owner/DB authorization proof. */
import { createRequire } from "node:module";
import { resolve } from "node:path";
import { expect, test } from "@playwright/test";
import { openLocalStartScreen } from "./readiness";

const { startServer } = createRequire(import.meta.url)(
  "./helpers/structured-stub-server.cjs",
);

test("主持存档操作：四宽度/短窗正文可读，三按钮44px且不挤压", async ({
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
    await page.getByTestId("btn-keeper-console").click();
    const section = page.getByRole("region", { name: "存档与续团" });
    for (const [width, height] of [
      [1280, 900],
      [939, 900],
      [640, 900],
      [390, 900],
      [390, 360],
    ]) {
      await page.setViewportSize({ width, height });
      await section.scrollIntoViewIfNeeded();
      await expect(page.locator(".boot-loader")).toHaveCount(0);
      await expect(section).toContainText("未保存进度不会保留");
      const heights = [];
      for (const id of ["keeper-save", "keeper-load", "keeper-save-panel"]) {
        const button = page.getByTestId(id);
        await button.scrollIntoViewIfNeeded();
        await expect(button).toBeEnabled();
        const box = await button.evaluate((node) => {
          const r = node.getBoundingClientRect(),
            s = getComputedStyle(node);
          const ancestor = node
            .closest(".keeper-workspace-reference")!
            .getBoundingClientRect();
          return {
            height: r.height,
            padding: parseFloat(s.paddingLeft),
            nowrap: s.whiteSpace,
            top: r.top,
            bottom: r.bottom,
            limitTop: ancestor.top,
            limitBottom: ancestor.bottom,
            hit: node.contains(
              document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2),
            ),
          };
        });
        expect(box.height).toBeGreaterThanOrEqual(44);
        expect(box.padding).toBeGreaterThanOrEqual(13);
        expect(box.nowrap).toBe("nowrap");
        expect(box.hit).toBe(true);
        expect(box.top).toBeGreaterThanOrEqual(box.limitTop - 1);
        expect(box.bottom).toBeLessThanOrEqual(box.limitBottom + 1);
        heights.push(box.height);
      }
      expect(Math.max(...heights) - Math.min(...heights)).toBeLessThanOrEqual(
        1,
      );
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      ).toBe(true);
      await page.waitForTimeout(1000); // capture the settled folder, not its entry animation
      await page.screenshot({
        path: resolve(
          import.meta.dirname,
          `../../docs/design/platform-ui/keeper-save-actions-${width}${height === 360 ? "-short" : ""}.png`,
        ),
      });
    }
  } finally {
    await stub.close();
  }
});
