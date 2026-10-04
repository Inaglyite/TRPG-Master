/** Layout and keyboard against scripted notes responses, not storage acceptance. */
import { createRequire } from "node:module";
import { expect, test, type Page } from "@playwright/test";
import { openLocalStartScreen } from "./readiness";

const require = createRequire(import.meta.url);
const { startServer } = require("./helpers/structured-stub-server.cjs");
let stub: {
  close: () => Promise<void>;
  received: { frame: Record<string, unknown> }[];
};

test.beforeAll(async () => {
  stub = await startServer({ port: 8765 });
});
test.afterAll(async () => {
  await stub?.close();
});

async function openNotes(page: Page, failedRead = false) {
  await page.routeWebSocket(/\/ws(?:\?|$)/, (socket) => {
    const upstream = socket.connectToServer();
    socket.onMessage((message) => {
      const request = JSON.parse(String(message));
      if (request.type === "player_notes_get") {
        socket.send(
          JSON.stringify({
            ...(failedRead
              ? {
                  type: "player_notes_error",
                  message:
                    "尚未收到笔记确认，草稿保留在此窗口。请重新读取核对保存结果，或连接后重试。",
                }
              : {
                  type: "player_notes",
                  revision: 0,
                  text: "仅此窗口的私人草稿",
                }),
            request_id: request.request_id,
            world_id: request.world_id,
          }),
        );
        return;
      }
      upstream.send(message);
    });
  });
  await openLocalStartScreen(
    page,
    "http://127.0.0.1:8765/?mode=local&role=player-a",
  );
  await page.locator(".module-select-trigger").click();
  await page.getByRole("option", { name: /猩红文档/ }).click();
  await page.locator("#btn-start").click();
  await page.locator("#btn-character-confirm").click();
  await expect(page.getByTestId("structured-tool-row")).toBeVisible();
  await expect(page.locator(".boot-loader")).toHaveCount(0);
  await page.locator("#btn-notes").click();
  await expect(page.locator("#player-notes-input")).toBeEnabled();
}

for (const [width, height] of [
  [1280, 480],
  [939, 480],
  [640, 480],
  [390, 480],
  [390, 360],
]) {
  test(`笔记首屏 ${width}×${height}：正文可读，折叠快捷行动不丢草稿`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height });
    await openNotes(page);
    const input = page.locator("#player-notes-input");
    await expect(input).toBeInViewport({ ratio: 0.8 });
    await input.fill("需要保留的调查记录");
    const toggle = page.getByRole("button", { name: "快捷行动", exact: true });
    await expect(toggle).toHaveAttribute("aria-expanded", "false");
    await expect(
      page.getByRole("button", { name: "观察环境", exact: true }),
    ).toBeHidden();
    const before = stub.received.length;
    await toggle.focus();
    await page.keyboard.press("Enter");
    await expect(toggle).toHaveAttribute("aria-expanded", "true");
    await expect(
      page.getByRole("button", { name: "观察环境", exact: true }),
    ).toBeVisible();
    await page.keyboard.press("Space");
    await expect(toggle).toHaveAttribute("aria-expanded", "false");
    await expect(input).toHaveValue("需要保留的调查记录");
    expect(
      stub.received
        .slice(before)
        .filter(
          ({ frame }) =>
            frame.type === "action_request" || frame.type === "action",
        ),
    ).toHaveLength(0);
    // Focus reveals the editable area without moving the game behind the dialog.
    await input.focus();
    await expect(input).toBeInViewport({ ratio: 0.8 });
    await page.screenshot({
      path: `test-results/notebook-reading-${width}-${height}.png`,
    });
    for (const button of await page
      .locator(
        "#utility-close, #player-notes-cancel, #player-notes-save, .notebook-shortcuts-toggle",
      )
      .all()) {
      await button.scrollIntoViewIfNeeded();
      const geometry = await button.evaluate((node) => {
        const rect = node.getBoundingClientRect();
        const css = getComputedStyle(node);
        const panel = node.closest("#utility-panel")!.getBoundingClientRect();
        return {
          height: rect.height,
          bottom: rect.bottom,
          panelBottom: panel.bottom,
          left: rect.left,
          right: rect.right,
          nowrap: css.whiteSpace,
          hit: node.contains(
            document.elementFromPoint(
              rect.x + rect.width / 2,
              rect.y + rect.height / 2,
            ),
          ),
        };
      });
      expect(geometry.height).toBeGreaterThanOrEqual(44);
      expect(geometry.left).toBeGreaterThanOrEqual(0);
      expect(geometry.right).toBeLessThanOrEqual(width);
      expect(geometry.nowrap).toBe("nowrap");
      expect(geometry.hit).toBe(true);
      if (
        (await button.getAttribute("id")) === "player-notes-save" ||
        (await button.getAttribute("id")) === "player-notes-cancel"
      ) {
        expect(geometry.panelBottom - geometry.bottom).toBeGreaterThanOrEqual(
          24,
        );
      }
    }
  });
}

test("短窗恢复提示首屏可读，中文输入期间 Escape 不关闭笔记", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 360 });
  await openNotes(page, true);
  const status = page.locator("#player-notes-status");
  await expect(status).toContainText("草稿保留");
  await expect(status).toBeInViewport({ ratio: 1 });
  const input = page.locator("#player-notes-input");
  await expect(input).toBeInViewport({ ratio: 0.8 });
  await input.focus();
  await input.dispatchEvent("keydown", {
    key: "Escape",
    code: "Escape",
    isComposing: true,
    bubbles: true,
  });
  await expect(page.getByRole("dialog", { name: "调查笔记" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog", { name: "调查笔记" })).toHaveCount(0);
  await expect(page.locator("#btn-notes")).toBeFocused();
});
