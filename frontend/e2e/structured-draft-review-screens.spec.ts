/** Stub layout/payload evidence; real transaction/approval proof is separate. */
import { createRequire } from "node:module";
import { resolve } from "node:path";
import { expect, test } from "@playwright/test";
import { openLocalStartScreen } from "./readiness";
const require = createRequire(import.meta.url);
const { startServer } = require("./helpers/structured-stub-server.cjs");

test("主持草稿审阅：中文参数、默认折叠原文、四宽度与短窗", async ({ page }) => {
  test.setTimeout(180000);
  const stub = await startServer({
    port: 8774,
    scenario: "keeper-draft-review",
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
    const card = page.getByTestId("keeper-draft-card");
    await expect(card.getByText("批准战斗动作")).toBeVisible();
    await expect(card.getByText("爱丽丝", { exact: true })).toBeVisible();
    await expect(card.getByText("近战", { exact: true })).toBeVisible();
    await expect(card.locator("pre").first()).not.toBeVisible();
    for (const [width, height] of [
      [1280, 900],
      [939, 900],
      [640, 900],
      [390, 900],
      [390, 360],
    ]) {
      await page.setViewportSize({ width, height });
      const title = card.locator(".structured-card-title");
      await title.scrollIntoViewIfNeeded();
      // The card owns scrolling. After native focus/reflow settles, its title
      // must still be readable; no scroll of the hidden parent is acceptable.
      await page.waitForTimeout(600);
      expect(
        await title.evaluate((el) => {
          const r = el.getBoundingClientRect();
          return el.contains(
            document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2),
          );
        }),
      ).toBe(true);
      await page.screenshot({
        path: resolve(
          import.meta.dirname,
          `../../docs/design/platform-ui/draft-review-reading-${width}${height === 360 ? "-short" : ""}.png`,
        ),
      });
      for (const id of ["draft-approve", "draft-reject"]) {
        const button = card.getByTestId(id);
        await button.scrollIntoViewIfNeeded();
        const metric = await button.evaluate((el) => {
          const rect = el.getBoundingClientRect(),
            style = getComputedStyle(el);
          return {
            height: rect.height,
            padding: parseFloat(style.paddingLeft),
            whitespace: style.whiteSpace,
            hit: el.contains(
              document.elementFromPoint(
                rect.x + rect.width / 2,
                rect.y + rect.height / 2,
              ),
            ),
            top: rect.top,
            bottom: rect.bottom,
            dockTop: document
              .querySelector(".structured-dock")!
              .getBoundingClientRect().top,
            dockBottom: document
              .querySelector(".structured-dock")!
              .getBoundingClientRect().bottom,
          };
        });
        expect(metric.height).toBeGreaterThanOrEqual(44);
        expect(metric.padding).toBeGreaterThanOrEqual(10);
        expect(metric.whitespace).toBe("nowrap");
        expect(metric.hit).toBe(true);
        expect(metric.top).toBeGreaterThanOrEqual(metric.dockTop - 1);
        expect(metric.bottom).toBeLessThanOrEqual(metric.dockBottom + 1);
      }
      await page.locator("#user-input").focus();
      const preserved = await page.locator("#user-input").evaluate((el) => {
        const rect = el.getBoundingClientRect();
        return {
          top: rect.top,
          bottom: rect.bottom,
          panelScroll: document.querySelector("#chat-panel")!.scrollTop,
        };
      });
      expect(preserved.panelScroll).toBe(0);
      expect(preserved.top).toBeGreaterThanOrEqual(0);
      expect(preserved.bottom).toBeLessThanOrEqual(height);
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      ).toBe(true);
      await page.screenshot({
        path: resolve(
          import.meta.dirname,
          `../../docs/design/platform-ui/draft-review-${width}${height === 360 ? "-short" : ""}.png`,
        ),
      });
    }
    await card.locator("summary").first().click();
    await expect(card.locator("pre").first()).toContainText(
      '"actor_id": "inv-alice"',
    );
    expect(
      stub.received.filter(
        ({ frame }: { frame: Record<string, unknown> }) =>
          frame.type === "command_request",
      ),
    ).toHaveLength(0);
    await card.getByTestId("draft-approve").click();
    await expect(card).toHaveCount(0);
    const commands = stub.received.filter(
      ({ frame }: { frame: Record<string, unknown> }) =>
        frame.type === "command_request",
    );
    expect(commands).toHaveLength(1);
    expect(commands[0].frame).toMatchObject({
      kind: "resolve_draft",
      payload: { draft_id: "draft-layout", decision: "approved" },
    });
  } finally {
    await stub.close();
  }
});
