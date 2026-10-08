/** 主持待办长名称布局：协议替身，不调用模型或写真实世界。 */
import { createRequire } from "node:module";
import { expect, test } from "@playwright/test";
import { openLocalStartScreen } from "./readiness";

const require = createRequire(import.meta.url);
const { startServer } = require("./helpers/structured-stub-server.cjs");

test("主持待办：160字符连续标题完整换行，四窗口不侵入按钮区", async ({
  page,
}) => {
  const stub = await startServer({
    port: 8791,
    scenario: "keeper-ui-long-label",
  });
  try {
    await openLocalStartScreen(
      page,
      "http://127.0.0.1:8791/?mode=local&role=keeper",
    );
    await page.locator(".module-select-trigger").click();
    await page.getByRole("option", { name: /猩红文档/ }).click();
    await page.locator("#btn-start").click();
    await page.locator("#btn-character-confirm").click();
    await expect(page.locator("#user-input")).toBeEnabled();
    await page.getByTestId("btn-keeper-console").click();
    const card = page.getByTestId("keeper-pending-request");
    const title = card.locator(".keeper-pending-label");
    await expect(title).toHaveText("Destination_" + "X".repeat(148));
    for (const width of [1280, 939, 640, 390]) {
      await page.setViewportSize({ width, height: 640 });
      await title.scrollIntoViewIfNeeded();
      const fits = await title.evaluate((element) => {
        const range = document.createRange();
        range.selectNodeContents(element);
        const text = range.getBoundingClientRect();
        const card = element.closest("li")!.getBoundingClientRect();
        return text.left >= card.left && text.right <= card.right;
      });
      await page.screenshot({
        path: `/tmp/trpg-keeper-long-title-${width}.png`,
      });
      expect(fits, `标题必须完整换行于${width}宽度卡片内`).toBe(true);
      const button = card.getByRole("button", { name: "准备回应" });
      await button.scrollIntoViewIfNeeded();
      const geometry = await button.evaluate((element) => {
        const rect = element.getBoundingClientRect();
        const style = getComputedStyle(element);
        return {
          hit: element.contains(
            document.elementFromPoint(
              rect.x + rect.width / 2,
              rect.y + rect.height / 2,
            ),
          ),
          height: rect.height,
          padding: parseFloat(style.paddingLeft),
          nowrap: style.whiteSpace,
          fits: element.scrollWidth <= element.clientWidth + 1,
        };
      });
      expect(geometry.hit).toBe(true);
      expect(geometry.height).toBeGreaterThanOrEqual(40);
      expect(geometry.padding).toBeGreaterThanOrEqual(10);
      expect(geometry.nowrap).toBe("nowrap");
      expect(geometry.fits).toBe(true);
    }
    expect(
      stub.received.filter(
        ({ frame }: { frame: { type: string } }) =>
          frame.type === "command_request",
      ),
    ).toHaveLength(0);
  } finally {
    await stub.close();
  }
});
