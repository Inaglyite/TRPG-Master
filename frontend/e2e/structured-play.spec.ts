/**
 * 结构化操作前端联调 e2e（**测试替身后端**，见 helpers/structured-stub-server.cjs）。
 *
 * 标明来源：本用例跑的是按 M0 schema 驱动的 WS 替身，不是 Kimi 的真实后端。
 * 它证明的是前端这一侧的行为：按钮真的发出结构化请求、收到主持结果后按状态
 * 渲染、协议不可用时明确提示、旧文字通道不再被当作新模式的权威入口。
 * 真实后端的三客户端 human 闭环仍需在后端 M1 落地后另跑（见交付报告）。
 *
 * 取证：替身逐条记录收到的帧，断言里直接核对 outgoing payload。
 */

import { createRequire } from "node:module";

import { expect, test, type Page } from "@playwright/test";

import { openLocalStartScreen } from "./readiness";

// 替身后端是 CommonJS（直接 Node 运行，便于用 ws 库起真实 WS）。
const require = createRequire(import.meta.url);
const { startServer } = require("./helpers/structured-stub-server.cjs") as {
  startServer: (options: { port: number; scenario?: string }) => Promise<{
    close: () => Promise<void>;
    received: { role: string; frame: Record<string, unknown> }[];
  }>;
};

const stubPort = 8775;

let stub: {
  close: () => Promise<void>;
  received: { role: string; frame: Record<string, unknown> }[];
} | null = null;

test.beforeAll(async () => {
  stub = await startServer({ port: stubPort, scenario: "full" });
});

test.afterAll(async () => {
  if (stub) await stub.close();
});

/**
 * 替身下的开局：走真实开局界面（模组选择 → 开始 → 确认调查员），
 * 让 gm_turn_start 正常触发；结构化快照随后把能力与世界投影带进来。
 */
async function enterStructuredGame(
  page: Page,
  role = "player-a",
  port = stubPort,
) {
  await openLocalStartScreen(
    page,
    `http://127.0.0.1:${port}/?mode=local&role=${role}`,
  );
  await page.locator(".module-select-trigger").click();
  await page.getByRole("option", { name: /猩红文档/ }).click();
  await page.locator("#btn-start").click();
  const confirm = page.locator("#btn-character-confirm");
  await expect(confirm).toBeVisible({ timeout: 30_000 });
  await confirm.click();
  await expect(page.getByText("雨幕笼罩着阿卡姆").first()).toBeVisible({
    timeout: 30_000,
  });
  // 结构化能力协商完成后，工具行与顶栏位置出现。
  await expect(page.getByTestId("structured-tool-row")).toBeVisible({
    timeout: 30_000,
  });
  await expect(page.locator(".header-scene-name")).toHaveText(
    "密斯卡托尼克大学",
    { timeout: 30_000 },
  );
  // 开场叙述播放完成（与旧入口同一时序约定）后再交互。
  await expect(page.locator("#user-input")).toBeEnabled({ timeout: 60_000 });
  // 打开角色/线索侧栏：出示与使用入口在调查员面板里。
  await page.locator("#btn-panel").click();
  await expect(page.locator("#char-content .inv-card-clues")).toBeVisible({
    timeout: 30_000,
  });
}

function framesOf(type: string) {
  return (stub?.received ?? []).filter((entry) => entry.frame.type === type);
}

test("主持恢复与草稿卡：真实 DOM 三宽度、不误写重试含义、拒绝草稿有回执（协议替身）", async ({
  page,
}) => {
  test.setTimeout(180_000);
  const hostUi = await startServer({ port: 8770, scenario: "keeper-ui" });
  try {
    await enterStructuredGame(page, "keeper", 8770);
    await page.locator("#btn-panel").click();
    const control = page.getByTestId("keeper-control-notice");
    const draft = page.getByTestId("keeper-draft-card");
    await expect(control).toContainText("此前已结算的行动不会回滚");
    await expect(draft).toContainText("建议先向医生出示死亡证明。");
    for (const width of [1280, 939, 640, 390]) {
      await page.setViewportSize({ width, height: 900 });
      for (const button of [
        control.getByRole("button", { name: /交还 AI 并重试/ }),
        draft.getByTestId("draft-approve"),
        draft.getByTestId("draft-reject"),
      ]) {
        await button.scrollIntoViewIfNeeded();
        const geometry = await button.evaluate((node) => {
          const rect = node.getBoundingClientRect();
          const css = getComputedStyle(node);
          return {
            width: rect.width,
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
        expect(geometry.height).toBeGreaterThanOrEqual(44);
        expect(geometry.padding).toBeGreaterThanOrEqual(10);
        expect(geometry.nowrap).toBe("nowrap");
        expect(geometry.inside).toBe(true);
        expect(geometry.hit).toBe(true);
      }
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth + 1,
        ),
      ).toBe(true);
      await page.screenshot({ path: `/tmp/trpg-keeper-recovery-${width}.png` });
    }
    await draft.getByTestId("draft-reject").click();
    await expect(draft).toHaveCount(0);
    const commands = hostUi.received.filter(
      ({ frame }) => frame.type === "command_request",
    );
    expect(commands).toHaveLength(1);
    expect(commands[0].frame).toMatchObject({
      kind: "resolve_draft",
      payload: { draft_id: "draft-layout", decision: "rejected" },
    });
  } finally {
    await hostUi.close();
  }
});

test("服务端只给草稿和接管标志但未提供命令：只读且零提交（协议替身）", async ({
  page,
}) => {
  test.setTimeout(120_000);
  const readonlyHost = await startServer({
    port: 8769,
    scenario: "keeper-ui-readonly",
  });
  try {
    await enterStructuredGame(page, "keeper", 8769);
    await page.locator("#btn-panel").click();
    const draft = page.getByTestId("keeper-draft-card");
    const control = page.getByTestId("keeper-control-notice");
    await expect(draft).toContainText(
      "服务端未开放草稿审批，不能提交批准或拒绝。",
    );
    await expect(control).toContainText("当前状态仅供查看");
    await expect(control.getByRole("button")).toHaveCount(0);
    for (const width of [1280, 939, 640, 390]) {
      await page.setViewportSize({ width, height: 900 });
      await draft.scrollIntoViewIfNeeded();
      await expect(draft.getByTestId("draft-approve")).toBeDisabled();
      await expect(draft.getByTestId("draft-reject")).toBeDisabled();
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth + 1,
        ),
      ).toBe(true);
      await page.screenshot({ path: `/tmp/trpg-keeper-readonly-${width}.png` });
    }
    expect(
      readonlyHost.received.filter(
        ({ frame }) => frame.type === "command_request",
      ),
    ).toHaveLength(0);
    await expect(draft).toContainText("待批准");
    await expect(draft).not.toContainText("已结算");
    await page.getByTestId("btn-keeper-console").click();
    const workspace = page.getByRole("dialog", { name: "主持工作台" });
    await expect(workspace.getByTestId("keeper-submit")).toBeDisabled();
    await expect(workspace.getByTestId("keeper-cmd-grant_clue")).toBeDisabled();
    await expect(workspace).toContainText("服务端未开放该主持操作");
    await checkDialogLayout(page, "keeper");
    for (const width of [1280, 939, 640, 390]) {
      await page.setViewportSize({ width, height: 900 });
      await workspace.getByTestId("keeper-submit").scrollIntoViewIfNeeded();
      await page.screenshot({
        path: `/tmp/trpg-console-readonly-${width}.png`,
      });
    }
    expect(
      readonlyHost.received.filter(
        ({ frame }) => frame.type === "command_request",
      ),
    ).toHaveLength(0);
  } finally {
    await readonlyHost.close();
  }
});

async function checkDialogLayout(page: Page, kind: "action" | "keeper") {
  const dialog = page.locator(
    kind === "keeper" ? "#keeper-console" : "#structured-action-dialog",
  );
  const close = dialog.getByRole("button", {
    name: kind === "keeper" ? "关闭主持台" : "取消并关闭",
  });
  const body = dialog.locator(".panel-action-body").first();
  for (const width of [1280, 939, 640, 390]) {
    await page.setViewportSize({ width, height: 480 });
    await page.waitForTimeout(1000); // Measure the settled panel, not its entry transform.
    await body.evaluate((node) => {
      node.scrollTop = node.scrollHeight;
    });
    const controls =
      kind === "keeper"
        ? [close]
        : [
            close,
            ...(await dialog.locator(".panel-action-footer button").all()),
          ];
    for (const button of controls) {
      const geometry = await button.evaluate((node) => {
        const rect = node.getBoundingClientRect();
        const css = getComputedStyle(node);
        return {
          height: rect.height,
          padding: parseFloat(css.paddingLeft),
          nowrap: css.whiteSpace,
          inViewport:
            rect.top >= 0 &&
            rect.bottom <= innerHeight &&
            rect.left >= 0 &&
            rect.right <= innerWidth,
          hit: node.contains(
            document.elementFromPoint(
              rect.x + rect.width / 2,
              rect.y + rect.height / 2,
            ),
          ),
        };
      });
      expect(geometry.height).toBeGreaterThanOrEqual(44);
      expect(geometry.padding).toBeGreaterThanOrEqual(10);
      expect(geometry.nowrap).toBe("nowrap");
      expect(geometry.inViewport).toBe(true);
      expect(geometry.hit).toBe(true);
    }
    await close.focus();
    await page.keyboard.press("Shift+Tab");
    expect(
      await dialog.evaluate((node) => {
        const controls = [
          ...node.querySelectorAll<HTMLElement>(
            "button, input, select, textarea, summary, a[href], [tabindex]",
          ),
        ].filter((control) => {
          if (
            control.tabIndex < 0 ||
            control.matches(":disabled") ||
            control.closest('[hidden], [inert], [aria-hidden="true"]')
          )
            return false;
          for (
            let parent: HTMLElement | null = control;
            parent && parent !== node;
            parent = parent.parentElement
          ) {
            const style = getComputedStyle(parent);
            if (style.display === "none" || style.visibility === "hidden")
              return false;
            if (
              parent.tagName === "DETAILS" &&
              !parent.hasAttribute("open") &&
              !parent.querySelector(":scope > summary")?.contains(control)
            )
              return false;
          }
          return true;
        });
        return document.activeElement === controls.at(-1);
      }),
    ).toBe(true);
    await page.keyboard.press("Tab");
    await expect(close).toBeFocused();
    expect(
      await dialog.evaluate((node) => node.scrollWidth <= node.clientWidth),
    ).toBe(true);
    await body.evaluate((node) => {
      node.scrollTop = 0;
    });
    await page.waitForTimeout(1000);
    await page.screenshot({ path: `/tmp/trpg-modal-${kind}-${width}.png` });
  }
  await page.setViewportSize({ width: 1440, height: 900 });
}

test("结构请求取代文字通道：出示 / 检定 / 前往 / 掷骰发的都是 M0 信封", async ({
  page,
}) => {
  test.setTimeout(180_000);
  await enterStructuredGame(page);

  // 1) 出示线索 → action_request{kind:present_clue}；守秘人随后请求检定。
  const presentButton = page.getByRole("button", { name: "出示" }).first();
  await expect(presentButton).toBeEnabled({ timeout: 30_000 });
  await presentButton.click();
  const presentDialog = page.getByRole("dialog", { name: "出示线索" });
  await expect(presentDialog).toBeVisible();
  await checkDialogLayout(page, "action");
  expect(framesOf("action_request")).toHaveLength(0);
  await presentDialog
    .getByLabel("向谁出示 / 说明")
    .selectOption("npc:john_whitcroft");
  // 预览里是真结构请求，不是自然语言句。
  await expect(
    presentDialog.locator(".panel-action-preview-text"),
  ).toContainText("present_clue");
  await presentDialog.getByRole("button", { name: "提交请求" }).click();

  await expect
    .poll(() => framesOf("action_request").length, { timeout: 20_000 })
    .toBe(1);
  const present = framesOf("action_request")[0].frame as any;
  expect(present.protocol_version).toBe(1);
  expect(present.world_id).toBe("world-stub-1");
  expect(present.expected_revision).toBe(12);
  expect(present.investigator_id).toBe("inv-alice");
  expect(present.action).toEqual({
    kind: "present_clue",
    clue_id: "clue_death_certificate",
    presentation: "describe",
    physical_item_id: null,
    target: { kind: "npc", id: "john_whitcroft" },
  });
  expect(JSON.stringify(present)).not.toContain("我向");

  // 2) 检定卡：点击“掷骰”发送 check_response，参数全部来自服务端。
  const card = page.locator(".check-request-card");
  await expect(card).toBeVisible({ timeout: 20_000 });
  await expect(card).toContainText("说服");
  await expect(card).toContainText("常规");
  await expect(card).toContainText("向医生说明来意。");
  // 卡片里没有可改参数的输入控件。
  expect(await card.locator("input, select").count()).toBe(0);
  await expect(card.getByRole("button", { name: "放弃" })).toBeEnabled();

  await card.getByRole("button", { name: "掷骰", exact: true }).click();
  await expect
    .poll(() => framesOf("check_response").length, { timeout: 20_000 })
    .toBe(1);
  const check = framesOf("check_response")[0].frame as any;
  expect(check).toEqual({
    type: "check_response",
    protocol_version: 1,
    request_id: expect.any(String),
    world_id: "world-stub-1",
    check_request_id: "chk-stub-1",
    decision: "roll",
  });
  expect(check).not.toHaveProperty("skill");
  expect(check).not.toHaveProperty("target_value");
  await expect(page.locator(".check-request-card")).toContainText("23 vs 55", {
    timeout: 20_000,
  });

  // 3) 顶栏“前往…”→ action_request{kind:move}
  await page.getByTestId("btn-move").click();
  const moveDialog = page.getByRole("dialog", { name: "前往…" });
  await expect(moveDialog).toBeVisible();
  await moveDialog
    .getByRole("button", { name: /密斯卡托尼克大学医学院/ })
    .click();
  await expect
    .poll(() => framesOf("action_request").length, { timeout: 20_000 })
    .toBe(2);
  const move = framesOf("action_request")[1].frame as any;
  expect(move.action).toEqual({
    kind: "move",
    destination_scene_id: "miskatonic_medical",
  });
  // 位置只由已提交的 scene_changed 更新。
  await expect(page.locator(".header-scene-name")).toHaveText(
    "密斯卡托尼克大学医学院",
    { timeout: 20_000 },
  );
  // 服务器接收不等于行动成功：状态卡把状态与领域结果分开显示。
  const moveCard = page
    .locator(".action-status-card")
    .filter({ hasText: "前往" })
    .first();
  await expect(moveCard).toContainText("已处理完成");
  await expect(moveCard).toContainText("结果：成功");

  // 4) 普通掷骰 → free_roll_request（不带 expected_revision，不是 action_request）
  await page.getByTestId("btn-free-roll").click();
  const rollDialog = page.getByRole("dialog", { name: "普通掷骰" });
  await expect(rollDialog).toBeVisible();
  await rollDialog.getByTestId("roll-confirm").click();
  await expect
    .poll(() => framesOf("free_roll_request").length, { timeout: 20_000 })
    .toBe(1);
  const roll = framesOf("free_roll_request")[0].frame as any;
  expect(roll.spec).toBe("1d100");
  expect(roll).not.toHaveProperty("expected_revision");
  // 骰子结果进入聊天区（服务端结果，不本地重掷）。
  await expect(page.getByText(/普通掷骰 1d100 → 42/)).toBeVisible({
    timeout: 20_000,
  });

  // 检定回应与普通掷骰各只发一次，没有被重复结算。
  expect(framesOf("check_response")).toHaveLength(1);
  expect(framesOf("free_roll_request")).toHaveLength(1);
});

test("自由文本走 freeform 结构请求，不再退回旧文字通道", async ({ page }) => {
  test.setTimeout(120_000);
  const before = framesOf("action").length;
  await enterStructuredGame(page);
  await page.locator("#user-input").fill("我推门进去看看。");
  await page.locator("#btn-send").click();
  // 自由文本仍是旧通道（主持侧解释），结构化请求数量不变。
  await expect
    .poll(
      () =>
        framesOf("action_request").filter(
          (entry) =>
            (entry.frame.action as Record<string, unknown> | undefined)
              ?.kind === "freeform",
        ).length,
      { timeout: 20_000 },
    )
    .toBe(1);
  const freeform = framesOf("action_request").find(
    (entry) =>
      (entry.frame.action as Record<string, unknown> | undefined)?.kind ===
      "freeform",
  )!.frame as any;
  expect(freeform).toMatchObject({
    type: "action_request",
    protocol_version: 1,
    world_id: "world-stub-1",
    action: { kind: "freeform", text: "我推门进去看看。" },
  });
  // 结构化世界不再把文本拼成旧 action 帧（不静默退回自然语言通道）。
  expect(framesOf("action").length).toBe(before);
});

test("主持台：命令信封与私发线索的接收者记录", async ({ page }) => {
  test.setTimeout(120_000);
  await enterStructuredGame(page, "keeper");
  await page.getByTestId("btn-keeper-console").click();
  const console_ = page.getByRole("dialog", { name: "主持工作台" });
  await expect(console_).toBeVisible();
  await checkDialogLayout(page, "keeper");
  expect(framesOf("command_request")).toHaveLength(0);

  await page.getByTestId("keeper-cmd-grant_clue").click();
  await page
    .locator('[data-field="clue_id"] select')
    .selectOption("clue_death_certificate");
  await page
    .locator('[data-field="recipient_investigator_ids"] input[type="text"]')
    .fill("inv-alice");
  await page.locator('[data-field="basis"] input').fill("医生当面说明");
  await page.getByTestId("keeper-submit").click();

  await expect
    .poll(() => framesOf("command_request").length, { timeout: 20_000 })
    .toBe(1);
  const command = framesOf("command_request")[0].frame as any;
  expect(command.type).toBe("command_request");
  expect(command.kind).toBe("grant_clue");
  expect(command.protocol_version).toBe(1);
  expect(command.command_id).toMatch(/^cmd-/);
  expect(command.expected_revision).toBe(12);
  expect(command.payload).toEqual({
    clue_id: "clue_death_certificate",
    recipient_investigator_ids: ["inv-alice"],
    basis: "医生当面说明",
  });
  // 服务端接收不等于执行成功：控制台明确说明。
  await expect(console_.getByText(/不代表执行成功/)).toBeVisible();
});

test("提交冲突：位置不变、可同 ID 重试、拒绝后草稿仍可编辑", async ({
  page,
}) => {
  test.setTimeout(180_000);
  const conflictPort = 8774;
  const conflictStub = await startServer({
    port: conflictPort,
    scenario: "conflict",
  });
  try {
    await enterStructuredGame(page, "player-a", conflictPort);

    await page.getByTestId("btn-move").click();
    await page
      .getByRole("dialog", { name: "前往…" })
      .getByRole("button", { name: /密斯卡托尼克大学医学院/ })
      .click();

    // 状态卡显示拒绝原因与“同一请求 ID 重试”，位置不变。
    await expect(page.locator(".action-status-card")).toContainText(
      "世界状态已更新",
      { timeout: 20_000 },
    );
    await expect(page.locator(".header-scene-name")).toHaveText(
      "密斯卡托尼克大学",
    );
    await expect(
      page.getByRole("button", { name: "重试（同一请求 ID）" }),
    ).toBeVisible();

    // 出示请求被拒后，草稿仍可编辑：状态卡提供“重新编辑”，
    // 内容由原请求载荷回填，不需要用户从头再选一遍。
    await page.getByRole("button", { name: "出示" }).first().click();
    const dialog = page.getByRole("dialog", { name: "出示线索" });
    await dialog
      .getByLabel("向谁出示 / 说明")
      .selectOption("npc:john_whitcroft");
    await dialog.getByLabel("想询问什么（可选）").fill("这是你的签名吗？");
    await dialog.getByRole("button", { name: "提交请求" }).click();

    const presentCard = page
      .locator(".action-status-card")
      .filter({ hasText: "出示：" })
      .first();
    await expect(presentCard).toBeVisible({ timeout: 20_000 });
    await expect(presentCard).toContainText("世界状态已更新", {
      timeout: 20_000,
    });
    const reopen = presentCard.getByTestId("structured-reopen");
    await expect(reopen).toBeVisible();
    await reopen.click();

    const reopened = page.getByRole("dialog", { name: "出示线索" });
    await expect(reopened).toBeVisible();
    await expect(reopened.getByLabel("想询问什么（可选）")).toHaveValue(
      "这是你的签名吗？",
    );
    await expect(reopened.getByLabel("向谁出示 / 说明")).toHaveValue(
      "npc:john_whitcroft",
    );
    // 取消才丢弃草稿。
    await page.keyboard.press("Escape");
    await expect(reopened).toBeHidden({ timeout: 10_000 });
  } finally {
    await conflictStub.close();
  }
});

test("经典编辑器：短窗口出示与使用可关闭，取消不发旧行动且不预扣物品", async ({
  page,
}) => {
  test.setTimeout(180_000);
  const legacy = await startServer({ port: 8772, scenario: "legacy-game" });
  page.setDefaultTimeout(20_000);
  try {
    await openLocalStartScreen(
      page,
      "http://127.0.0.1:8772/?mode=local&role=player-a",
    );
    await page.locator(".module-select-trigger").click();
    await page.getByRole("option", { name: /猩红文档/ }).click();
    await page.locator("#btn-start").click();
    await page.locator("#btn-character-confirm").click();
    await expect(page.locator("#user-input")).toBeEnabled();
    await page.locator("#btn-panel").click();
    const present = page
      .getByRole("button", { name: "出示", exact: true })
      .first();
    const use = page.getByRole("button", { name: "使用", exact: true }).first();
    await expect(present).toBeEnabled();
    await expect(use).toBeEnabled();
    const before = legacy.received.length;
    for (const width of [1280, 939, 640, 390]) {
      await page.setViewportSize({ width, height: width === 390 ? 360 : 480 });
      for (const [trigger, name] of [
        [present, "出示线索"],
        [use, "使用道具"],
      ] as const) {
        if (await page.locator("#char-panel.collapsed").count())
          await page.locator("#btn-panel").click();
        await trigger.click();
        const dialog = page.getByRole("dialog", { name });
        await expect(dialog).toBeVisible();
        await page.waitForTimeout(1000);
        const body = dialog.locator(".panel-action-content");
        const close = dialog.getByRole("button", { name: "取消并关闭" });
        const footer = dialog.locator(".panel-action-footer");
        const beforeY = (await footer.boundingBox())?.y;
        await body.evaluate((node) => {
          node.scrollTop = node.scrollHeight;
        });
        expect((await footer.boundingBox())?.y).toBe(beforeY);
        for (const button of [
          close,
          ...(await footer.getByRole("button").all()),
        ]) {
          const box = await button.evaluate((node) => {
            const r = node.getBoundingClientRect();
            return {
              height: r.height,
              visible:
                r.top >= 0 &&
                r.bottom <= innerHeight &&
                r.left >= 0 &&
                r.right <= innerWidth,
              hit: node.contains(
                document.elementFromPoint(
                  r.x + r.width / 2,
                  r.y + r.height / 2,
                ),
              ),
            };
          });
          expect(box.height).toBeGreaterThanOrEqual(44);
          expect(box.visible).toBe(true);
          expect(box.hit).toBe(true);
        }
        await dialog.getByRole("button", { name: "取消", exact: true }).focus();
        await page.keyboard.press("Tab"); // invalid draft leaves submit disabled
        await expect(close).toBeFocused();
        await body.evaluate((node) => {
          node.scrollTop = 0;
        });
        await page.screenshot({
          path: `/tmp/trpg-legacy-editor-${name === "出示线索" ? "present" : "use"}-${width}.png`,
        });
        await page.keyboard.press("Escape");
        await expect(dialog).toHaveCount(0);
        await expect(
          width <= 999 ? page.locator("#btn-panel") : trigger,
        ).toBeFocused();
      }
    }
    expect(
      legacy.received
        .slice(before)
        .filter(({ frame }) =>
          ["action", "action_request"].includes(String(frame.type)),
        ),
    ).toHaveLength(0);
    await expect(page.locator('.inv-item-row[data-item="手电筒"]')).toHaveCount(
      1,
    );
    await expect(page.getByTestId("structured-tool-row")).toHaveCount(0);
  } finally {
    await legacy.close();
  }
});

test("协议不支持的世界不显示结构化入口，也不退回文字发送", async ({ page }) => {
  test.setTimeout(120_000);
  // 只发旧协议帧的替身：模拟尚未支持 structured_v1 的服务端。
  const legacyPort = 8773;
  const legacy = await startServer({
    port: legacyPort,
    scenario: "legacy-only",
  });
  try {
    await page.goto(`http://127.0.0.1:${legacyPort}/?mode=local&role=player-a`);
    await expect(page.locator(".boot-loader")).toBeHidden({ timeout: 30_000 });
    // 旧协议世界：开局界面仍然可用，但没有任何结构化入口。
    await expect(page.locator("#btn-start")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId("structured-tool-row")).toHaveCount(0);
    await expect(page.getByTestId("btn-keeper-console")).toHaveCount(0);
    await expect(page.getByTestId("btn-move")).toHaveCount(0);
    // 该替身（旧协议）从未收到任何结构化请求。
    expect(legacy.received).toHaveLength(0);
  } finally {
    await legacy.close();
  }
});

test("同 revision 的多条聊天事件都渲染；旧世界迟到事件不污染新世界", async ({
  page,
}) => {
  test.setTimeout(180_000);
  const revisionsPort = 8771;
  const revisions = await startServer({
    port: revisionsPort,
    scenario: "revisions",
  });
  try {
    await enterStructuredGame(page, "player-a", revisionsPort);

    // 自由文本 → 替身回两条同一 revision 的聊天事件。
    await page.locator("#user-input").fill("我问法伦关于莱特的事。");
    await page.locator("#btn-send").click();
    await expect(page.getByText("第一句：法伦抬起头。")).toBeVisible({
      timeout: 20_000,
    });
    await expect(page.getByText("第二句：他把档案推到桌边。")).toBeVisible({
      timeout: 20_000,
    });
    // Separate committed messages must not merge into one eternally streaming
    // legacy bubble, even when they share a speaker and world revision.
    const firstBubble = page
      .locator(".msg.gm")
      .filter({ hasText: "第一句：法伦抬起头。" });
    const secondBubble = page
      .locator(".msg.gm")
      .filter({ hasText: "第二句：他把档案推到桌边。" });
    await expect(firstBubble).toHaveCount(1);
    await expect(secondBubble).toHaveCount(1);
    await expect(firstBubble).not.toContainText("第二句：");
    await expect(secondBubble).not.toContainText("第一句：");
    await expect(page.locator(".msg.gm.streaming-cursor")).toHaveCount(0);

    // 移动 → 替身先切到另一个世界，再发一条属于旧世界的迟到场景事件。
    await page.getByTestId("btn-move").click();
    await page
      .getByRole("dialog", { name: "前往…" })
      .getByRole("button", { name: /密斯卡托尼克大学医学院/ })
      .click();
    await expect(page.locator(".header-scene-name")).toHaveText(
      "霍布豪斯宅邸",
      {
        timeout: 20_000,
      },
    );
    // 旧世界的 scene_changed（world-stub-1）不能改写新世界的位置。
    await page.waitForTimeout(1200);
    await expect(page.locator(".header-scene-name")).toHaveText("霍布豪斯宅邸");
    await expect(page.getByText("旧世界的停尸房")).toHaveCount(0);
  } finally {
    await revisions.close();
  }
});
