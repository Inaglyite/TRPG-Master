/** Layout/download evidence only; real owner-authorized saves use the 3p spec. */
import { createRequire } from "node:module";
import { resolve } from "node:path";
import { expect, test } from "@playwright/test";
import { openLocalStartScreen } from "./readiness";

const require = createRequire(import.meta.url);
const { startServer } = require("./helpers/structured-stub-server.cjs");

test("结案角色：四宽度控件与显式保存、导出", async ({ page }) => {
  test.setTimeout(180000);
  const stub = await startServer({ port: 8774, scenario: "combat-ending" });
  const writes: Record<string, unknown>[] = [];
  let saved = false;
  const card = {
    name: "爱丽丝",
    occupation: "调查员",
    career: {
      reputation: 12,
      completed_modules: ["scarlet"],
      case_history: [],
    },
  };
  try {
    await page.route("**/api/character-library/from-case**", async (route) => {
      const path = new URL(route.request().url()).pathname;
      const source = route.request().postDataJSON();
      if (path.endsWith("/preview")) {
        await route.fulfill({
          json: {
            ok: true,
            card,
            warnings: [],
            revision: 12,
            receipt_digest: "a".repeat(64),
            saved_entry: saved
              ? { id: "case-alice", name: "爱丽丝的结案副本" }
              : null,
          },
        });
      } else if (path.endsWith("/export")) {
        await route.fulfill({
          json: {
            format: "trpg-character-card",
            format_version: 1,
            card: { ...card, name: source.name },
          },
        });
      } else {
        writes.push(source);
        saved = true;
        await route.fulfill({
          json: {
            ok: true,
            entry: { id: "case-alice", name: source.name },
            warnings: [],
            deduplicated: false,
          },
        });
      }
    });
    await openLocalStartScreen(page, "http://127.0.0.1:8774/?mode=local");
    await page.locator(".module-select-trigger").click();
    await page.getByRole("option", { name: /猩红文档/ }).click();
    await page.locator("#btn-start").click();
    await page.locator("#btn-character-confirm").click();
    const sheet = page.getByTestId("case-character-actions");
    await expect(sheet.getByLabel("新角色名")).toHaveValue("爱丽丝");
    expect(writes).toHaveLength(0);
    for (const [width, height] of [
      [1280, 900],
      [939, 900],
      [640, 900],
      [390, 900],
      [390, 360],
    ]) {
      await page.setViewportSize({ width, height });
      const heights: number[] = [];
      for (const label of ["保存为新角色", "导出角色卡"]) {
        const button = sheet.getByRole("button", { name: label, exact: true });
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
          };
        });
        expect(metric.height).toBeGreaterThanOrEqual(44);
        expect(metric.padding).toBeGreaterThanOrEqual(10);
        expect(metric.whitespace).toBe("nowrap");
        expect(metric.hit).toBe(true);
        heights.push(metric.height);
      }
      expect(Math.abs(heights[0] - heights[1])).toBeLessThanOrEqual(1);
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      ).toBe(true);
      await sheet.screenshot({
        path: resolve(
          import.meta.dirname,
          `../../docs/design/platform-ui/career-save-${width}${height === 360 ? "-short" : ""}.png`,
        ),
      });
    }
    await sheet.getByLabel("新角色名").fill("爱丽丝的结案副本");
    const download = page.waitForEvent("download");
    await sheet
      .getByRole("button", { name: "导出角色卡", exact: true })
      .click();
    expect((await download).suggestedFilename()).toBe(
      "character-爱丽丝的结案副本.json",
    );
    expect(writes).toHaveLength(0);
    await sheet
      .getByRole("button", { name: "保存为新角色", exact: true })
      .click();
    await expect(
      sheet.getByRole("button", { name: "已保存", exact: true }),
    ).toBeDisabled();
    expect(writes).toHaveLength(1);
    expect(writes[0]).toMatchObject({
      world_id: "world-stub-1",
      investigator_id: "inv-alice",
      case_id: "layout-case",
      expected_revision: 12,
      receipt_digest: "a".repeat(64),
      name: "爱丽丝的结案副本",
    });
    await sheet.screenshot({
      path: resolve(
        import.meta.dirname,
        "../../docs/design/platform-ui/career-save-complete-390.png",
      ),
    });
  } finally {
    await stub.close();
  }
});
