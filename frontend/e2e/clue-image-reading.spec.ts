/** Scripted legacy projection: image-reading UI, not an authorization/storage test. */
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { expect, test, type Page } from "@playwright/test";
import { openLocalStartScreen } from "./readiness";

const require = createRequire(import.meta.url);
const { startServer } = require("./helpers/structured-stub-server.cjs");
let stub: {
  close: () => Promise<void>;
  received: { frame: Record<string, unknown> }[];
};
const source = "http://127.0.0.1:8765/test-clue-image.webp";
const label = "测试用线索图片";
const picture = readFileSync(
  new URL("../src/assets/ui/printed-compass-v1.webp", import.meta.url),
);
test.beforeAll(async () => {
  stub = await startServer({ port: 8765, scenario: "legacy-game" });
});
test.afterAll(async () => {
  await stub?.close();
});

async function enter(page: Page, initiallyBroken = false) {
  let broken = initiallyBroken;
  let pushState!: (withImage: boolean, differentWorld?: boolean) => void;
  await page.route(source, async (route) => {
    await route.fulfill({
      status: broken ? 404 : 200,
      contentType: "image/webp",
      headers: { "Cache-Control": "no-store" },
      body: broken ? Buffer.from("") : picture,
    });
  });
  await page.routeWebSocket(/\/ws(?:\?|$)/, (socket) => {
    const upstream = socket.connectToServer();
    upstream.onMessage((message) => {
      const frame = JSON.parse(String(message));
      if (frame.type === "state_data") {
        const update = (withImage: boolean) => ({
          ...frame,
          clues: JSON.stringify(
            withImage
              ? {
                  investigation: [
                    {
                      id: "fixture-image",
                      text: label,
                      asset: {
                        file: "test-clue-image.webp",
                        label,
                        asset_url: source,
                      },
                    },
                  ],
                }
              : {},
          ),
        });
        pushState = (withImage, differentWorld = false) => {
          if (differentWorld)
            socket.send(
              JSON.stringify({
                type: "world_context",
                world_id: "different-fixture-world",
                module_name: "猩红文档",
              }),
            );
          socket.send(JSON.stringify(update(withImage)));
        };
        socket.send(JSON.stringify(update(true)));
      } else socket.send(message);
    });
  });
  await openLocalStartScreen(page, "http://127.0.0.1:8765/?mode=local");
  await page.locator(".module-select-trigger").click();
  await page.getByRole("option", { name: /猩红文档/ }).click();
  await page.locator("#btn-start").click();
  await page.locator("#btn-character-confirm").click();
  await expect(page.locator("#user-input")).toBeEnabled({ timeout: 60000 });
  await page.locator("#btn-panel").click();
  const trigger = page.getByRole("button", { name: label, exact: true });
  await expect(trigger).toBeVisible();
  return {
    trigger,
    pushState: (hasImage: boolean, changedWorld?: boolean) =>
      pushState(hasImage, changedWorld),
    recover: () => {
      broken = false;
    },
  };
}

function submittedActions() {
  return stub.received.filter(({ frame }) =>
    ["action", "action_request", "command_request"].includes(
      String(frame.type),
    ),
  ).length;
}

test("线索图片四宽度阅读、键盘返回，撤回与换世界不留旧图", async ({ page }) => {
  test.setTimeout(120000);
  const { trigger, pushState } = await enter(page);
  const before = submittedActions();
  await trigger.click();
  const viewer = page.getByRole("dialog", { name: label });
  await expect(viewer).toBeVisible();
  await expect(
    viewer.getByRole("button", { name: "原尺寸查看" }),
  ).toBeEnabled();
  for (const width of [1280, 939, 640, 390]) {
    await page.setViewportSize({ width, height: 480 });
    const close = viewer.getByRole("button", { name: "关闭材料查看" });
    await expect(close).toBeInViewport();
    const shape = await close.evaluate((node) => {
      const rect = node.getBoundingClientRect();
      const css = getComputedStyle(node);
      return {
        height: rect.height,
        padding: parseFloat(css.paddingLeft),
        nowrap: css.whiteSpace,
        inside:
          rect.left >= 0 &&
          rect.right <= innerWidth &&
          rect.top >= 0 &&
          rect.bottom <= innerHeight,
        hit: node.contains(
          document.elementFromPoint(
            rect.x + rect.width / 2,
            rect.y + rect.height / 2,
          ),
        ),
      };
    });
    expect(shape.height).toBeGreaterThanOrEqual(44);
    expect(shape.padding).toBeGreaterThanOrEqual(10);
    expect(shape.nowrap).toBe("nowrap");
    expect(shape.inside).toBe(true);
    expect(shape.hit).toBe(true);
    await page.screenshot({
      path: `test-results/clue-image-reading-${width}.png`,
    });
  }
  await page.evaluate(() =>
    document.dispatchEvent(
      new KeyboardEvent("keydown", {
        key: "Escape",
        isComposing: true,
        bubbles: true,
      }),
    ),
  );
  await expect(viewer).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(viewer).toHaveCount(0);
  await expect(trigger).toBeFocused();
  await trigger.click();
  pushState(false);
  await expect(viewer).toHaveCount(0);
  pushState(true);
  await trigger.click();
  pushState(false, true);
  await expect(viewer).toHaveCount(0);
  expect(submittedActions()).toBe(before);
});

test("线索原图读取失败后可重试，不转成游戏行动", async ({ page }) => {
  test.setTimeout(120000);
  const { trigger, recover } = await enter(page, true);
  const before = submittedActions();
  await trigger.click();
  const viewer = page.getByRole("dialog", { name: label });
  await expect(viewer.getByRole("alert")).toContainText("图片未能加载");
  recover();
  await viewer.getByRole("button", { name: "重新加载图片" }).click();
  await expect(
    viewer.getByRole("button", { name: "原尺寸查看" }),
  ).toBeEnabled();
  await viewer.getByRole("button", { name: "关闭材料查看" }).click();
  await expect(trigger).toBeFocused();
  expect(submittedActions()).toBe(before);
});
