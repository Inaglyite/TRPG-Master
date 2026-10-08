/** Stub proves responsive controls and exact frames, not production rules. */
import { createRequire } from "node:module";
import { resolve } from "node:path";
import { expect, test } from "@playwright/test";
import { openLocalStartScreen } from "./readiness";
const { startServer } = createRequire(import.meta.url)(
  "./helpers/structured-stub-server.cjs",
);

test("战斗申报：四宽度/短窗可用，取消零提交，字段明确不执行", async ({
  page,
}) => {
  const stub = await startServer({ port: 8793, scenario: "combat-declare" });
  try {
    await openLocalStartScreen(
      page,
      "http://127.0.0.1:8793/?mode=local&role=player-a",
    );
    await page.locator(".module-select-trigger").click();
    await page.getByRole("option", { name: /猩红文档/ }).click();
    await page.locator("#btn-start").click();
    await page.locator("#btn-character-confirm").click();
    const open = page.getByRole("button", {
      name: "申报战斗动作",
      exact: true,
    });
    await expect(open).toBeEnabled();
    await open.click();
    let dialog = page.getByRole("dialog", { name: "申报战斗动作" });
    await expect(
      dialog.getByRole("button", { name: "提交申报" }),
    ).toBeDisabled();
    await dialog.getByRole("button", { name: "取消", exact: true }).click();
    expect(
      stub.received.some(
        ({ frame }: { frame: Record<string, unknown> }) =>
          frame.type === "action_request",
      ),
    ).toBe(false);
    await open.click();
    dialog = page.getByRole("dialog", { name: "申报战斗动作" });
    await dialog.getByLabel("动作", { exact: true }).selectOption("firearm");
    await dialog
      .getByLabel("使用的持有物品", { exact: true })
      .selectOption("weapon-second");
    await dialog
      .getByLabel("目标", { exact: true })
      .selectOption("john_whitcroft");
    await dialog.getByLabel("补充做法（选填）").fill("掩护同伴");
    for (const [width, height] of [
      [1280, 800],
      [939, 800],
      [640, 800],
      [390, 800],
      [390, 360],
    ]) {
      await page.setViewportSize({ width, height });
      for (const label of ["动作", "目标", "使用的持有物品"]) {
        const select = dialog.getByLabel(label, { exact: true });
        await select.scrollIntoViewIfNeeded();
        expect(
          await select.evaluate((el) => {
            const r = el.getBoundingClientRect();
            return (
              r.height >= 44 &&
              el.contains(
                document.elementFromPoint(
                  r.x + r.width / 2,
                  r.y + r.height / 2,
                ),
              )
            );
          }),
        ).toBe(true);
      }
      for (const label of ["取消", "提交申报"]) {
        const button = dialog.getByRole("button", { name: label, exact: true });
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
      }
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      ).toBe(true);
      await page.screenshot({
        path: resolve(
          import.meta.dirname,
          `../../docs/design/platform-ui/combat-declare-${width}${height === 360 ? "-short" : ""}.png`,
        ),
      });
    }
    await dialog.getByRole("button", { name: "提交申报", exact: true }).click();
    await expect
      .poll(
        () =>
          stub.received.filter(
            ({ frame }: { frame: Record<string, unknown> }) =>
              frame.type === "action_request",
          ).length,
      )
      .toBe(1);
    expect(
      stub.received.find(
        ({ frame }: { frame: Record<string, unknown> }) =>
          frame.type === "action_request",
      ).frame.action,
    ).toEqual({
      kind: "combat",
      encounter_id: "layout-encounter",
      action_type: "firearm",
      target_id: "john_whitcroft",
      weapon_item_id: "weapon-second",
      approach: "掩护同伴",
    });
    await expect(open).toBeDisabled();
    await expect(
      page.getByRole("button", { name: "掷骰", exact: true }),
    ).toHaveCount(0);
    expect(
      stub.received.some(
        ({ frame }: { frame: Record<string, unknown> }) =>
          frame.kind === "combat_action" || frame.kind === "combat_roll",
      ),
    ).toBe(false);
  } finally {
    await stub.close();
  }
});
