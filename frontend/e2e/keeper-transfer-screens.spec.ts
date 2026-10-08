/** Stub-backed layout/payload proof; real custody circuit lives in human-3p. */
import { createRequire } from "node:module";
import { resolve } from "node:path";
import { expect, test } from "@playwright/test";
import { openLocalStartScreen } from "./readiness";
const require = createRequire(import.meta.url);
const { startServer } = require("./helpers/structured-stub-server.cjs");
test("转交表单：NPC/场景候选、四宽度与短窗真实按钮、不预改归属", async ({
  page,
}) => {
  test.setTimeout(180000);
  const stub = await startServer({
    port: 8774,
    scenario: "keeper-ui",
    keeperProgress: {
      clues: [],
      clocks: [],
      holdings: {
        holders: [
          { kind: "npc", id: "old-keeper", name: "老看守" },
          { kind: "scene", id: "study", name: "书房" },
        ],
        items: [
          {
            id: "key",
            label: "库房钥匙",
            quantity: 1,
            holder: { kind: "npc", id: "old-keeper" },
          },
        ],
      },
    },
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
    await page.getByTestId("keeper-cmd-transfer_item").click();
    await page.getByLabel("物品", { exact: true }).selectOption("key");
    await expect(page.getByLabel("来源", { exact: true })).toHaveValue(
      "npc/old-keeper",
    );
    await page.getByLabel("去向", { exact: true }).selectOption("scene/study");
    await page.getByLabel("数量", { exact: true }).fill("1");
    for (const [width, height] of [
      [1280, 900],
      [939, 900],
      [640, 900],
      [390, 900],
      [390, 360],
    ]) {
      await page.setViewportSize({ width, height });
      for (const control of [
        page.getByLabel("来源", { exact: true }),
        page.getByLabel("去向", { exact: true }),
        page.getByTestId("keeper-submit"),
      ]) {
        await control.scrollIntoViewIfNeeded();
        const metric = await control.evaluate((el) => {
          const r = el.getBoundingClientRect(),
            s = getComputedStyle(el);
          return {
            tag: el.tagName,
            height: r.height,
            padding: parseFloat(s.paddingLeft),
            nowrap: s.whiteSpace,
            hit: el.contains(
              document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2),
            ),
            left: r.left,
            right: r.right,
            viewport: innerWidth,
          };
        });
        expect(metric.height).toBeGreaterThanOrEqual(44);
        expect(metric.padding).toBeGreaterThanOrEqual(10);
        if (metric.tag === "BUTTON") expect(metric.nowrap).toBe("nowrap");
        expect(metric.hit).toBe(true);
        expect(metric.left).toBeGreaterThanOrEqual(0);
        expect(metric.right).toBeLessThanOrEqual(metric.viewport);
      }
      await page.screenshot({
        path: resolve(
          import.meta.dirname,
          `../../test-results/keeper-platform/transfer-${width}${height === 360 ? "-short" : ""}.png`,
        ),
      });
    }
    expect(
      stub.received.filter(
        ({ frame }: { frame: { kind?: string } }) =>
          frame.kind === "transfer_item",
      ),
    ).toHaveLength(0);
    await page.getByTestId("keeper-submit").click();
    await expect
      .poll(
        () =>
          stub.received.filter(
            ({ frame }: { frame: { kind?: string } }) =>
              frame.kind === "transfer_item",
          ).length,
      )
      .toBe(1);
    const frame = stub.received.find(
      ({ frame }: { frame: { kind?: string } }) =>
        frame.kind === "transfer_item",
    ).frame;
    expect(frame.payload).toEqual({
      item_id: "key",
      quantity: 1,
      from: { kind: "npc", id: "old-keeper" },
      to: { kind: "scene", id: "study" },
    });
  } finally {
    await stub.close();
  }
});
