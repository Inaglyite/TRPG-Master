/**
 * 三客户端人类主持闭环（真实后端，一个 uvicorn 进程，无模型 Key）。
 *
 * 一位人类主持 + 两位玩家，在 structured_v1 + human 主持房间里：
 * 建房（勾选结构化模式）→ 邀请 → 选角（主持不占角色）→ 准备 → 开局 →
 * ① 主持定向私发线索给甲（乙不可见）
 * ② 主持请求检定 → 甲在持久检定卡上掷骰 → 服务端结算
 * ③ 主持调整 SAN → 甲的数值面板按事件更新
 * ④ 整队移动 → 两端场景一致
 * ⑤ 存档入口可用 + 甲刷新重连后位置与结构化入口恢复
 *
 * 全程不调用模型：模型 base URL 指向关闭端口，任何误调用都会以错误暴露。
 */

import { spawn, type ChildProcess } from "node:child_process";
import { existsSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

import {
  expect,
  request,
  test,
  type BrowserContext,
  type Page,
} from "@playwright/test";
import { assertGameHeaderFits } from "./header-layout";

const port = 8765;
const baseUrl = `http://127.0.0.1:${port}`;
const repositoryRoot = resolve(import.meta.dirname, "../..");
const runId = Math.random().toString(36).slice(2, 8);
let runtimeRoot = "";
let server: ChildProcess | null = null;
let serverOutput = "";

function pythonPath(): string {
  return (
    process.env.TRPG_E2E_PYTHON ??
    (existsSync(resolve(repositoryRoot, ".venv/bin/python"))
      ? resolve(repositoryRoot, ".venv/bin/python")
      : "python")
  );
}

async function waitForServer(): Promise<void> {
  const client = await request.newContext();
  try {
    for (let attempt = 0; attempt < 80; attempt += 1) {
      try {
        const response = await client.get(`${baseUrl}/api/health`);
        if (response.ok()) return;
      } catch {
        // 启动中
      }
      await new Promise((resolveWait) => setTimeout(resolveWait, 125));
    }
  } finally {
    await client.dispose();
  }
  throw new Error(`E2E server did not start:\n${serverOutput.slice(-4000)}`);
}

test.beforeAll(async () => {
  runtimeRoot = mkdtempSync(join(tmpdir(), "trpg-structured-3p-"));
  server = spawn(
    pythonPath(),
    [
      "-m",
      "uvicorn",
      "server:app",
      "--host",
      "127.0.0.1",
      "--port",
      String(port),
      "--workers",
      "1",
    ],
    {
      cwd: repositoryRoot,
      env: {
        ...Object.fromEntries(
          Object.entries(process.env).filter(
            ([key]) => !/^(http_proxy|https_proxy|all_proxy)$/i.test(key),
          ),
        ),
        NO_PROXY: "127.0.0.1,localhost",
        TRPG_RUNTIME_ROOT: runtimeRoot,
        TRPG_DATABASE_URL: `sqlite:///${join(runtimeRoot, "e2e.db")}`,
        TRPG_ALLOWED_ORIGINS: baseUrl,
        TRPG_REQUIRE_AUTH: "1",
        TRPG_WRITE_COMPAT_EXPORTS: "0",
        OPENAI_API_KEY: "e2e-placeholder",
        OPENAI_BASE_URL: "http://127.0.0.1:9/v1",
        TRPG_STREAM_USAGE: "0",
      },
      stdio: ["ignore", "pipe", "pipe"],
    },
  );
  server.stdout?.on("data", (chunk) => {
    serverOutput += String(chunk);
  });
  server.stderr?.on("data", (chunk) => {
    serverOutput += String(chunk);
  });
  await waitForServer();
});

test.afterAll(async () => {
  if (server && server.exitCode === null) {
    server.kill("SIGTERM");
    await new Promise<void>((resolveWait) => {
      const timeout = setTimeout(resolveWait, 3000);
      server?.once("exit", () => {
        clearTimeout(timeout);
        resolveWait();
      });
    });
    if (server.exitCode === null) server.kill("SIGKILL");
  }
  if (runtimeRoot) rmSync(runtimeRoot, { recursive: true, force: true });
});

function collectFrames(page: Page): { sent: string[]; received: string[] } {
  const sent: string[] = [];
  const received: string[] = [];
  page.on("websocket", (socket) => {
    socket.on("framesent", (event) => sent.push(String(event.payload)));
    socket.on("framereceived", (event) => received.push(String(event.payload)));
  });
  return { sent, received };
}

async function register(page: Page, username: string): Promise<void> {
  await page.goto(`${baseUrl}/?mode=online`);
  await page.getByRole("tab", { name: "注册" }).click();
  await page.getByLabel("用户名").fill(username);
  await page
    .getByLabel("密码", { exact: true })
    .fill("structured e2e password");
  await page.getByLabel("确认密码").fill("structured e2e password");
  await page.getByRole("button", { name: "注册并登录" }).click();
  await expect(page.getByRole("heading", { name: "联机大厅" })).toBeVisible();
}

function framesOf(received: string[], type: string): string[] {
  return received.filter((frame) => frame.includes(`"type":"${type}"`));
}

async function captureFlowLayout(page: Page, label: string) {
  for (const width of [1280, 939, 640]) {
    await page.setViewportSize({ width, height: 900 });
    await expect(page.locator(".boot-loader")).toHaveCount(0);
    await page.waitForTimeout(1000);
    const geometry = await page
      .locator(
        ".member-actions button, .structured-card-actions button, .lobby-refresh, .keeper-command, [data-testid=keeper-submit], .platform-create-actions > button",
      )
      .evaluateAll((nodes) =>
        nodes
          .filter((node) => (node as HTMLElement).offsetWidth > 0)
          .map((node) => {
            const button = node as HTMLElement;
            const css = getComputedStyle(button);
            return {
              text: button.textContent,
              padding: parseFloat(css.paddingLeft),
              nowrap: css.whiteSpace,
              fits: button.scrollWidth <= button.clientWidth + 1,
              height: button.getBoundingClientRect().height,
            };
          }),
      );
    for (const item of geometry) {
      expect(
        item.padding,
        `${label}/${width}/${item.text}: missing padding`,
      ).toBeGreaterThan(0);
      expect(item.nowrap).toBe("nowrap");
      expect(item.fits, `${label}/${width}/${item.text}: clipped`).toBe(true);
      expect(item.height).toBeGreaterThanOrEqual(28);
    }
    const fieldHeights = await page
      .locator(".lobby-form > input, .lobby-form > select")
      .evaluateAll((nodes) =>
        nodes
          .filter((node) => (node as HTMLElement).offsetWidth > 0)
          .map((node) => node.getBoundingClientRect().height),
      );
    for (const height of fieldHeights) {
      expect(height, `${label}/${width}: inflated form field`).toBeLessThan(65);
    }
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth + 1,
      ),
    ).toBe(true);
    await page.screenshot({
      path: `/tmp/trpg-flow-ui-${label}-${width}.png`,
      fullPage: true,
    });
    if (label === "workspace") {
      const checkboxSize = await page
        .locator(".keeper-field-inline input[type=checkbox]")
        .evaluate((node) => {
          const rect = node.getBoundingClientRect();
          return { width: rect.width, height: rect.height };
        });
      expect(checkboxSize.width).toBeLessThanOrEqual(24);
      expect(checkboxSize.height).toBeLessThanOrEqual(24);
      await page.getByTestId("keeper-submit").scrollIntoViewIfNeeded();
      await expect(page.getByTestId("keeper-submit")).toBeInViewport();
      await page.screenshot({
        path: `/tmp/trpg-flow-ui-workspace-actions-${width}.png`,
        fullPage: true,
      });
      await page.locator("#keeper-console").evaluate((node) => {
        node.scrollTop = 0;
      });
    }
  }
  await page.setViewportSize({ width: 1440, height: 900 });
}

/** 用当前选择器（input 或 select）给主持表单字段赋值。 */
async function setKeeperField(page: Page, field: string, value: string) {
  const control = page.locator(
    `[data-field="${field}"] select, [data-field="${field}"] input:not([type="checkbox"]), [data-field="${field}"] textarea`,
  );
  const tag = await control.evaluate((node) => node.tagName);
  if (tag === "SELECT") {
    await control.selectOption(value);
  } else {
    await control.fill(value);
  }
}

/**
 * 提交主持命令并等一个确定结局（受理或拒绝）。
 *
 * 世界 revision 会因别人的命令前进，主持端可能带着上一版提交 → 服务端按冻结
 * 错误码回 `revision_conflict`。这里走的是**界面自己的恢复动作**
 * （“用最新版本重新提交”），既让用例稳定，也顺带验收这条恢复路径。
 */
async function submitKeeperCommand(
  page: Page,
  frames: { sent: string[]; received: string[] },
  options: { expectAck?: boolean; attempts?: number } = {},
): Promise<{ accepted: boolean; lastError: string }> {
  const attempts = options.attempts ?? 3;
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    // 命令的结局帧是 request_error 或 action_status（受理确认）；
    // `action_ack` 不保证出现，不能只等它。
    const settled = () =>
      framesOf(frames.received, "request_error").length +
      framesOf(frames.received, "action_status").length +
      framesOf(frames.received, "action_ack").length;
    const before = settled();
    const errorsBefore = framesOf(frames.received, "request_error").length;
    await page.getByTestId("keeper-submit").click();
    await expect.poll(settled, { timeout: 20_000 }).toBeGreaterThan(before);
    const errors = framesOf(frames.received, "request_error");
    if (errors.length === errorsBefore)
      return { accepted: true, lastError: "" };
    const last = JSON.parse(errors[errors.length - 1]) as {
      payload?: { code?: string };
    };
    const code = last.payload?.code ?? "";
    if (code !== "revision_conflict") {
      return { accepted: false, lastError: errors[errors.length - 1] };
    }
    // 世界已前进：冲突事件的信封里带着新的 revision，store 已据此更新；
    // 再点一次提交就是用最新版本 + 新 command_id 重发同一意图（主持台的
    // 提交按钮每次都生成新的 command_id）。状态卡上的“用最新版本重新提交”
    // 是给玩家的同一恢复动作，但它位于聊天区、会被主持台浮层遮住，
    // 因此这里在控制台内完成重发。
    await page.waitForTimeout(500);
  }
  return { accepted: false, lastError: "revision_conflict 多次未能提交" };
}

test("三客户端：人类主持 + 两位玩家，无模型完成私发/检定/SAN/道具/移动/存档重连", async ({
  browser,
}) => {
  test.setTimeout(420_000);
  const keeperContext: BrowserContext = await browser.newContext();
  const playerAContext: BrowserContext = await browser.newContext();
  const playerBContext: BrowserContext = await browser.newContext();
  const viewerContext: BrowserContext = await browser.newContext();
  const keeper = await keeperContext.newPage();
  const playerA = await playerAContext.newPage();
  const playerB = await playerBContext.newPage();
  const viewer = await viewerContext.newPage();
  // 本项目 Playwright 配置没有 actionTimeout：不显式给超时，不可交互的定位
  // 会一直等到用例超时，把真正的失败原因藏起来。
  for (const page of [keeper, playerA, playerB, viewer])
    page.setDefaultTimeout(20_000);
  const keeperFrames = collectFrames(keeper);
  const playerAFrames = collectFrames(playerA);
  const playerBFrames = collectFrames(playerB);
  const viewerFrames = collectFrames(viewer);
  const keeperName = `keeper${runId}`;
  const playerAName = `alice${runId}`;
  const playerBName = `bob${runId}`;
  const roomName = `结构化验收房${runId}`;

  try {
    // ---- 建房：勾选“结构化操作模式（人类主持）” ----
    await register(keeper, keeperName);
    await keeper.getByLabel("房间名称").fill(roomName);
    await keeper.getByRole("radio", { name: "AI 辅助主持" }).click();
    await captureFlowLayout(keeper, "lobby");
    await keeper.getByRole("radio", { name: "人类主持" }).click();
    await keeper.getByRole("button", { name: "创建房间" }).click();
    await expect(keeper.getByRole("heading", { name: roomName })).toBeVisible();
    // 主持不认领调查员（规格 §7）：模组只有两个可选角色，主持占一个会让
    // 第二位玩家无角可选。
    const inviteRequests: string[] = [];
    keeper.on("request", (request) => {
      if (
        request.method() === "POST" &&
        /\/invites(?:\?|$)/.test(request.url())
      )
        inviteRequests.push(request.postData() ?? "");
    });
    await keeper.getByLabel("有效期（小时）").fill("0");
    await keeper.getByRole("button", { name: "生成邀请码" }).click();
    await expect(keeper.getByRole("alert")).toContainText("1 至 168");
    await expect(keeper.getByLabel("有效期（小时）")).toHaveValue("0");
    expect(inviteRequests).toHaveLength(0);
    await keeper.getByLabel("有效期（小时）").fill("72");
    await keeper.getByLabel("使用次数").fill("17");
    await keeper.getByRole("button", { name: "生成邀请码" }).click();
    await expect(keeper.getByRole("alert")).toContainText("1 至 16");
    expect(inviteRequests).toHaveLength(0);
    await keeper.getByLabel("使用次数").fill("5");
    await keeper.getByRole("button", { name: "生成邀请码" }).click();
    const invite = (
      await keeper.locator(".invite-token").textContent()
    )?.trim();
    expect(invite).toBeTruthy();
    expect(inviteRequests).toHaveLength(1);
    await keeper.evaluate(() => {
      Object.defineProperty(navigator, "clipboard", {
        configurable: true,
        value: {
          writeText: async () => {
            throw new Error("clipboard denied for test");
          },
        },
      });
    });
    await keeper.getByRole("button", { name: "复制邀请码" }).click();
    await expect(keeper.getByRole("alert")).toContainText("手动复制");
    for (const width of [1280, 939, 640, 390]) {
      await keeper.setViewportSize({ width, height: 900 });
      const copy = keeper.getByRole("button", { name: "复制邀请码" });
      await copy.scrollIntoViewIfNeeded();
      const geometry = await copy.evaluate((node) => {
        const box = node.getBoundingClientRect();
        const css = getComputedStyle(node);
        return {
          height: box.height,
          padding: parseFloat(css.paddingLeft),
          nowrap: css.whiteSpace,
          fits: node.scrollWidth <= node.clientWidth + 1,
          hit: node.contains(
            document.elementFromPoint(
              box.x + box.width / 2,
              box.y + box.height / 2,
            ),
          ),
        };
      });
      expect(geometry.height).toBeGreaterThanOrEqual(44);
      expect(geometry.padding).toBeGreaterThanOrEqual(10);
      expect(geometry.nowrap).toBe("nowrap");
      expect(geometry.fits).toBe(true);
      expect(geometry.hit).toBe(true);
      expect(
        await keeper.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth + 1,
        ),
      ).toBe(true);
      await keeper.screenshot({ path: `/tmp/trpg-room-invite-${width}.png` });
    }
    await keeper.evaluate(() => {
      Object.defineProperty(navigator, "clipboard", {
        configurable: true,
        value: {
          writeText: async (value: string) => {
            (window as unknown as { copiedInvite: string }).copiedInvite =
              value;
          },
        },
      });
    });
    await keeper.getByRole("button", { name: "复制邀请码" }).click();
    await expect(keeper.getByRole("button", { name: "已复制" })).toBeEnabled();
    expect(
      await keeper.evaluate(
        () => (window as unknown as { copiedInvite: string }).copiedInvite,
      ),
    ).toBe(invite);
    await expect(keeper.getByRole("alert")).toHaveCount(0);
    await keeper.setViewportSize({ width: 1280, height: 900 });

    // ---- 两位玩家加入并各认领一名调查员 ----
    for (const [page, name] of [
      [playerA, playerAName],
      [playerB, playerBName],
    ] as const) {
      await register(page, name);
      await page
        .getByRole("textbox", { name: "邀请码", exact: true })
        .fill(invite!);
      await page.getByRole("button", { name: "加入房间" }).click();
      await expect(page.getByRole("heading", { name: roomName })).toBeVisible();
      await page.getByRole("button", { name: "选择" }).first().click();
      await expect(page.getByRole("button", { name: "释放" })).toBeVisible();
    }

    // 甲的调查员标识（结构化层的 character_key）：从服务端 REST 投影里取，
    // 而不是猜候选顺序 —— 后面所有定向操作都必须打在甲身上。
    const playerAInvestigator = await playerA.evaluate(async () => {
      const worldId = localStorage.getItem("trpg-online-world-id") ?? "";
      const me = await (
        await fetch("/api/auth/me", { credentials: "include" })
      ).json();
      const info = await (
        await fetch(`/api/worlds/${encodeURIComponent(worldId)}/members`, {
          credentials: "include",
        })
      ).json();
      const rows = (info.members ?? []) as {
        user_id: string;
        investigator?: { character_key?: string } | null;
      }[];
      return (
        rows.find((row) => row.user_id === me.id)?.investigator
          ?.character_key ?? ""
      );
    });
    expect(playerAInvestigator, "甲没有认领到调查员").not.toBe("");
    await keeper
      .getByRole("button", { name: "授权主持", exact: true })
      .first()
      .click();
    await expect(
      keeper.getByRole("button", { name: "确认授权（可见主持秘密）" }),
    ).toBeVisible();
    await captureFlowLayout(keeper, "room");
    await keeper.getByRole("button", { name: "取消", exact: true }).click();

    // ---- 准备（以服务端广播的 room_state.ready_user_ids 为准） ----
    for (const page of [keeper, playerA, playerB]) {
      await expect(page.locator(".member-row").first()).toBeVisible();
      await page.getByRole("button", { name: "准备" }).click();
    }
    const readyCount = () => {
      const states = framesOf(keeperFrames.received, "room_state");
      const latest = JSON.parse(states[states.length - 1] ?? "{}") as {
        ready_user_ids?: string[];
      };
      return latest.ready_user_ids?.length ?? 0;
    };
    await expect
      .poll(readyCount, { timeout: 30_000, message: "三人准备状态没有生效" })
      .toBe(3);

    // ---- 开局（human：不建模型会话、不要求 BYOK） ----
    const start = keeper.getByRole("button", { name: "开始游戏" });
    await expect(start, "全员已准备后开局按钮仍不可用").toBeEnabled();
    await start.click();
    for (const page of [playerA, playerB]) {
      await expect(page.locator("#user-input")).toBeEnabled({
        timeout: 90_000,
      });
      await expect(page.getByTestId("structured-tool-row")).toBeVisible({
        timeout: 60_000,
      });
      await expect(page.getByTestId("btn-move")).toBeVisible();
    }
    // Notes use the current room's immediate transport, not its reconnect queue.
    const privateNote = `alice-private-note-${runId}`;
    await playerA.locator("#btn-notes").click();
    await expect(playerA.locator("#player-notes-input")).toBeEnabled();
    await playerA.locator("#player-notes-input").fill(privateNote);
    await playerA.locator("#player-notes-save").click();
    await expect(playerA.locator("#player-notes-status")).toContainText(
      "已保存",
    );
    await playerA.locator("#utility-close").click();
    await playerB.locator("#btn-notes").click();
    await expect(playerB.locator("#player-notes-input")).toBeEnabled();
    await expect(playerB.locator("#player-notes-input")).toHaveValue("");
    expect(
      playerBFrames.received.some((frame) => frame.includes(privateNote)),
    ).toBe(false);
    expect(
      keeperFrames.received.some((frame) => frame.includes(privateNote)),
    ).toBe(false);
    await playerB.locator("#utility-close").click();
    // The same compact header must work for keeper and player permissions.
    for (const page of [keeper, playerA]) {
      for (const width of [1280, 939, 640, 390]) {
        await page.setViewportSize({ width, height: 900 });
        await assertGameHeaderFits(page);
        await page.screenshot({
          path: `/tmp/trpg-header-${page === keeper ? "keeper" : "player"}-${width}.png`,
        });
      }
      await page.setViewportSize({ width: 1440, height: 900 });
    }
    expect(framesOf(keeperFrames.sent, "start")).toHaveLength(1);
    await expect(
      keeper.getByRole("button", { name: "接管主持", exact: true }),
    ).toBeVisible();
    await captureFlowLayout(keeper, "control");
    expect(
      playerAFrames.received.filter((frame) =>
        frame.includes("session_snapshot"),
      ),
    ).not.toHaveLength(0);

    // ---- ① 主持定向私发线索给甲（乙不可见） ----
    await keeper.getByTestId("btn-keeper-console").click();
    await expect(
      keeper.getByRole("dialog", { name: "主持工作台" }),
    ).toBeVisible();
    await captureFlowLayout(keeper, "workspace");
    // ---- 主持资料库：真实私密投影 → 单张预览 → 显式定向分发 ----
    // No legacy model turn will load these cards: the structured snapshot must
    // restore each player's own authoritative sheet at the first start.
    for (const [page, frames] of [
      [playerA, playerAFrames],
      [playerB, playerBFrames],
    ] as const) {
      await expect
        .poll(
          () =>
            JSON.parse(
              framesOf(frames.received, "session_snapshot").at(-1) ?? "{}",
            ).payload?.character?.name,
        )
        .toBeTruthy();
      const ownCard = JSON.parse(
        framesOf(frames.received, "session_snapshot").at(-1)!,
      ).payload.character;
      expect(ownCard?.name).toBeTruthy();
      expect(typeof ownCard?.san).toBe("number");
      await expect(page.locator("#char-name")).toHaveText(ownCard.name);
      await expect(page.locator("#san-bar")).toContainText(String(ownCard.san));
      await expect(page.locator("#char-content")).not.toContainText(
        "undefined",
      );
    }
    const bSanBefore = await playerB.locator("#san-bar").textContent();
    const library = keeper.getByTestId("keeper-library");
    await library.getByRole("button", { name: /打开资料库/ }).click();
    await expect(library.getByRole("article")).toContainText("法伦");
    for (const frames of [playerAFrames, playerBFrames]) {
      const snapshots = framesOf(frames.received, "session_snapshot").map(
        (raw) => JSON.parse(raw),
      );
      expect(snapshots.length).toBeGreaterThan(0);
      for (const snapshot of snapshots) {
        expect(snapshot.payload).not.toHaveProperty("keeper_material");
        expect(snapshot.payload).not.toHaveProperty("keeper_assets");
        expect(snapshot.payload).not.toHaveProperty("keeper_investigators");
      }
    }
    await library.getByRole("button", { name: "图片", exact: true }).click();
    const preview = library.locator("article img");
    await expect(preview).toBeVisible();
    expect(
      await preview.evaluate((node) => (node as HTMLImageElement).naturalWidth),
    ).toBeGreaterThan(0);
    expect(framesOf(playerAFrames.received, "handout_presented")).toHaveLength(
      0,
    );
    expect(framesOf(playerBFrames.received, "handout_presented")).toHaveLength(
      0,
    );
    for (const width of [1280, 939, 640]) {
      await keeper.setViewportSize({ width, height: 1000 });
      await library.scrollIntoViewIfNeeded();
      await keeper.screenshot({
        path: `/tmp/trpg-keeper-library-${width}.png`,
        fullPage: true,
      });
      await library
        .getByRole("button", { name: "展示给所选调查员" })
        .scrollIntoViewIfNeeded();
      await expect(
        library.getByRole("button", { name: "展示给所选调查员" }),
      ).toBeInViewport();
      await keeper.screenshot({
        path: `/tmp/trpg-keeper-library-actions-${width}.png`,
        fullPage: true,
      });
      const sizes = await library
        .locator("button:not(.keeper-library-entry)")
        .evaluateAll((nodes) =>
          nodes.map((node) => {
            const css = getComputedStyle(node);
            const rect = node.getBoundingClientRect();
            return {
              height: rect.height,
              padding: parseFloat(css.paddingLeft),
              fits: node.scrollWidth <= node.clientWidth + 1,
            };
          }),
        );
      for (const size of sizes) {
        expect(size.height).toBeGreaterThanOrEqual(40);
        expect(size.padding).toBeGreaterThan(0);
        expect(size.fits).toBe(true);
      }
      expect(
        await keeper.evaluate(
          () => document.documentElement.scrollWidth <= window.innerWidth + 1,
        ),
      ).toBe(true);
    }
    await keeper.setViewportSize({ width: 1440, height: 900 });
    const firstAssetId = await library
      .locator("article details code")
      .textContent();
    const firstAssetLabel = await library.locator("article h5").textContent();
    const libraryWorldId = JSON.parse(
      framesOf(keeperFrames.received, "session_snapshot").at(-1)!,
    ).world_id as string;
    // Full author manual is on-demand private reading, never a chat or command.
    const commandsBeforeManual = framesOf(
      keeperFrames.sent,
      "command_request",
    ).length;
    await library
      .getByRole("button", { name: "模组手册", exact: true })
      .click();
    await expect(library.locator("h5")).toHaveText("模组正文");
    await expect(library.locator(".keeper-library-text")).toContainText(
      "# NPC",
    );
    await expect(library.locator(".keeper-library-text")).toContainText(
      "secret:",
    );
    expect(framesOf(keeperFrames.sent, "command_request")).toHaveLength(
      commandsBeforeManual,
    );
    const playerManualStatus = await playerB.evaluate(
      async (worldId) =>
        (
          await fetch(
            `/api/worlds/${encodeURIComponent(worldId)}/keeper-guide`,
            { credentials: "include" },
          )
        ).status,
      libraryWorldId,
    );
    expect(playerManualStatus).toBe(404);
    await expect(playerB.locator("#messages")).not.toContainText("# NPC");
    // Human keeper can inspect exact current party skills; no model or text
    // recognition decides who/what the sheet belongs to.
    const ownSheet = JSON.parse(
      framesOf(playerAFrames.received, "session_snapshot").at(-1)!,
    ).payload.character;
    await library.getByRole("button", { name: "队员", exact: true }).click();
    await library
      .getByRole("button", { name: new RegExp(ownSheet.name) })
      .click();
    const partySheet = library.getByTestId("keeper-party-sheet");
    await expect(partySheet).toContainText(
      `HP ${ownSheet.hp}/${ownSheet.max_hp}`,
    );
    expect(
      await partySheet
        .locator(".keeper-party-skills")
        .first()
        .locator("li")
        .count(),
    ).toBe(Object.keys(ownSheet.skills).length);
    const prepareSkill = partySheet
      .getByRole("button", { name: /^准备 .* 检定$/ })
      .first();
    for (const width of [1280, 939, 640]) {
      await keeper.setViewportSize({ width, height: 1000 });
      await expect(keeper.locator(".boot-loader")).toHaveCount(0);
      await prepareSkill.scrollIntoViewIfNeeded();
      await expect(prepareSkill).toBeInViewport();
      const geometry = await prepareSkill.evaluate((node) => {
        const rect = node.getBoundingClientRect();
        const hit = document.elementFromPoint(
          rect.x + rect.width / 2,
          rect.y + rect.height / 2,
        );
        return {
          height: rect.height,
          fits: node.scrollWidth <= node.clientWidth + 1,
          padding: parseFloat(getComputedStyle(node).paddingLeft),
          hit: node.contains(hit),
        };
      });
      expect(geometry.height).toBeGreaterThanOrEqual(40);
      expect(geometry.padding).toBeGreaterThan(0);
      expect(geometry.fits).toBe(true);
      expect(geometry.hit).toBe(true);
      expect(
        await keeper.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth + 1,
        ),
      ).toBe(true);
      await keeper.waitForTimeout(1000);
      await keeper.screenshot({ path: `/tmp/trpg-keeper-party-${width}.png` });
    }
    await keeper.setViewportSize({ width: 1440, height: 900 });
    const commandsBeforeSkill = framesOf(
      keeperFrames.sent,
      "command_request",
    ).length;
    await prepareSkill.click();
    await expect(keeper.getByLabel("调查员", { exact: true })).toHaveValue(
      playerAInvestigator,
    );
    expect(framesOf(keeperFrames.sent, "command_request")).toHaveLength(
      commandsBeforeSkill,
    );
    await keeper.getByTestId("keeper-cmd-publish_message").click();
    await library.getByRole("button", { name: /打开资料库/ }).click();
    await library.getByRole("button", { name: "图片", exact: true }).click();
    await expect(library.getByRole("img")).toBeVisible();
    // Guessing an existing ID must not bypass the committed recipient grant.
    const beforeAccess = await playerA.evaluate(
      async ({ worldId, assetId }) =>
        (
          await fetch(
            `/api/worlds/${encodeURIComponent(worldId)}/handouts/${encodeURIComponent(assetId)}`,
            { credentials: "include" },
          )
        ).status,
      { worldId: libraryWorldId, assetId: firstAssetId! },
    );
    expect(beforeAccess).toBe(404);
    // Names may be character or member labels. Choose the server-owned stable ID,
    // not a guessed display name or an assumed position in the roster.
    const recipientChoices = library.getByRole("checkbox");
    const recipientIds = await recipientChoices.evaluateAll((nodes) =>
      nodes.map((node) => (node as HTMLInputElement).value),
    );
    expect(recipientIds).toContain(playerAInvestigator);
    const aRecipient = recipientChoices.nth(
      recipientIds.indexOf(playerAInvestigator),
    );
    await expect(aRecipient).toHaveValue(playerAInvestigator);
    await aRecipient.check();
    await library.getByRole("button", { name: "展示给所选调查员" }).click();
    await expect(library.getByRole("status")).toContainText(
      "服务端已确认图片分发",
      { timeout: 30_000 },
    );
    await expect(
      playerA.locator("#handout-container img").first(),
    ).toBeVisible();
    expect(
      await playerA
        .locator("#handout-container img")
        .first()
        .evaluate((node) => (node as HTMLImageElement).naturalWidth),
    ).toBeGreaterThan(0);
    // Read an actually authorized image. Viewing never submits a game command.
    const actionsBeforeViewing = framesOf(
      playerAFrames.sent,
      "action_request",
    ).length;
    const materialEntry = playerA.locator(".handout-open").first();
    await materialEntry.focus();
    await playerA.keyboard.press("Enter");
    const materialDialog = playerA.getByRole("dialog", {
      name: firstAssetLabel!,
    });
    await expect(materialDialog).toBeVisible();
    await expect(materialDialog.getByRole("img")).toBeVisible();
    await expect(
      materialDialog.getByRole("button", { name: "原尺寸查看" }),
    ).toBeEnabled();
    for (const width of [1280, 939, 640, 390]) {
      await playerA.setViewportSize({ width, height: 480 });
      const closeMaterial = materialDialog.getByRole("button", {
        name: "关闭材料查看",
      });
      const geometry = await closeMaterial.evaluate((node) => {
        const rect = node.getBoundingClientRect();
        const css = getComputedStyle(node);
        return {
          height: rect.height,
          padding: parseFloat(css.paddingLeft),
          nowrap: css.whiteSpace,
          left: rect.left,
          right: rect.right,
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
      expect(geometry.left).toBeGreaterThanOrEqual(0);
      expect(geometry.right).toBeLessThanOrEqual(width);
      expect(geometry.hit).toBe(true);
      expect(
        await materialDialog.evaluate(
          (node) => node.scrollWidth <= node.clientWidth + 1,
        ),
      ).toBe(true);
      await playerA.screenshot({
        path: `test-results/material-viewer-${width}.png`,
      });
    }
    await playerA.waitForTimeout(11000);
    await expect(materialDialog).toBeVisible();
    await materialDialog.getByRole("button", { name: "原尺寸查看" }).click();
    await expect(
      materialDialog.getByRole("button", { name: "适应窗口" }),
    ).toHaveAttribute("aria-pressed", "true");
    await playerA.keyboard.press("Escape");
    await expect(materialDialog).toHaveCount(0);
    await expect(materialEntry).toBeFocused();
    expect(framesOf(playerAFrames.sent, "action_request").length).toBe(
      actionsBeforeViewing,
    );
    await playerA.setViewportSize({ width: 1440, height: 900 });
    // A delivered image is a durable recipient-only catalog entry, not just a
    // transient toast. Reopening it must be an HTTP read, never a game action.
    const openReceivedGallery = async () => {
      if (!(await playerA.locator("#char-content").isVisible())) {
        await playerA.locator("#btn-panel").click();
      }
      const clueToggle = playerA.locator("#inv-card-toggle-clues");
      if ((await clueToggle.getAttribute("aria-expanded")) !== "true") {
        await clueToggle.click();
      }
      const gallery = playerA.getByRole("region", { name: "收到的图片" });
      const toggle = gallery.getByRole("button", { name: /收到的图片/ });
      await toggle.scrollIntoViewIfNeeded();
      if ((await toggle.getAttribute("aria-expanded")) !== "true")
        await toggle.click();
      return gallery;
    };
    for (const width of [1280, 939, 640, 390]) {
      await playerA.setViewportSize({ width, height: 480 });
      const gallery = await openReceivedGallery();
      const read = gallery.getByRole("button", { name: /^查看图片：/ }).first();
      await read.scrollIntoViewIfNeeded();
      const size = await read.evaluate((node) => {
        const rect = node.getBoundingClientRect();
        const css = getComputedStyle(node);
        return {
          height: rect.height,
          padding: parseFloat(css.paddingLeft),
          nowrap: css.whiteSpace,
          hit: node.contains(
            document.elementFromPoint(
              rect.x + rect.width / 2,
              rect.y + rect.height / 2,
            ),
          ),
        };
      });
      expect(size.height).toBeGreaterThanOrEqual(44);
      expect(size.padding).toBeGreaterThanOrEqual(10);
      expect(size.nowrap).toBe("nowrap");
      expect(size.hit).toBe(true);
      await playerA.screenshot({
        path: `test-results/received-materials-${width}.png`,
      });
    }
    const receivedRead = (await openReceivedGallery())
      .getByRole("button", { name: /^查看图片：/ })
      .first();
    await receivedRead.click();
    const reopened = playerA
      .getByRole("dialog")
      .filter({ has: playerA.locator("img") });
    await expect(reopened).toBeVisible();
    await expect(
      reopened.getByRole("button", { name: "原尺寸查看" }),
    ).toBeEnabled();
    await reopened.getByRole("button", { name: "关闭材料查看" }).click();
    await expect(receivedRead).toBeFocused();
    expect(framesOf(playerAFrames.sent, "action_request").length).toBe(
      actionsBeforeViewing,
    );
    await expect(
      playerB.getByRole("region", { name: "收到的图片" }),
    ).toHaveCount(0);
    await playerA.setViewportSize({ width: 1440, height: 900 });
    expect(framesOf(playerBFrames.received, "handout_presented")).toHaveLength(
      0,
    );
    expect(await playerB.locator("#handout-container img").count()).toBe(0);
    const afterAccess = await playerB.evaluate(
      async ({ worldId, assetId }) =>
        (
          await fetch(
            `/api/worlds/${encodeURIComponent(worldId)}/handouts/${encodeURIComponent(assetId)}`,
            { credentials: "include" },
          )
        ).status,
      { worldId: libraryWorldId, assetId: firstAssetId! },
    );
    expect(afterAccess).toBe(404);
    expect(firstAssetLabel).toBeTruthy();
    await library.getByRole("button", { name: /收起资料库/ }).click();
    // 主持正常叙事不是命令行：多行编辑 → 真实提交 → 玩家收到，零模型。
    await setKeeperField(
      keeper,
      "text",
      "无模型主持验收：窗边的钟敲了两声。\n雨水沿着玻璃滑落。",
    );
    const narration = await submitKeeperCommand(keeper, keeperFrames);
    expect(narration.accepted, narration.lastError).toBe(true);
    for (const page of [playerA, playerB]) {
      await expect(page.locator("body")).toContainText(
        "无模型主持验收：窗边的钟敲了两声。",
      );
    }
    await expect(keeper.locator(".keeper-feedback")).toContainText(
      "服务端已确认提交",
    );
    await keeper.getByTestId("keeper-cmd-grant_clue").click();
    const clueOptions = await keeper
      .locator('[data-field="clue_id"] select option')
      .evaluateAll((nodes) =>
        nodes
          .map((node) => (node as HTMLOptionElement).value)
          .filter((value) => value.length > 0),
      );
    expect(clueOptions.length, "主持台没有可发放的线索候选").toBeGreaterThan(0);
    await keeper
      .locator('[data-field="clue_id"] select')
      .selectOption(clueOptions[0]);

    // 接收者候选是结构化层的调查员标识（character_key）。
    const recipients = (
      (await keeper
        .locator('[data-field="recipient_investigator_ids"] input[type="text"]')
        .getAttribute("placeholder")) ?? ""
    )
      .split(",")
      .map((value) => value.trim())
      .filter(Boolean);
    expect(recipients.length, "主持台没有调查员候选").toBeGreaterThan(0);
    // 定向发放必须打在甲身上（候选里的标识应与 REST 投影一致）。
    expect(
      recipients,
      `主持台候选中没有甲的调查员 ${playerAInvestigator}`,
    ).toContain(playerAInvestigator);
    const targetInvestigator = playerAInvestigator;
    await keeper
      .locator('[data-field="recipient_investigator_ids"] input[type="text"]')
      .fill(targetInvestigator);
    await keeper.locator('[data-field="basis"] input').fill("医生当面说明");
    const grantSubmit = await submitKeeperCommand(keeper, keeperFrames);
    expect(grantSubmit.accepted, `私发线索被拒：${grantSubmit.lastError}`).toBe(
      true,
    );

    await expect
      .poll(() => framesOf(keeperFrames.sent, "command_request").length, {
        timeout: 20_000,
      })
      .toBe(3); // 一次图片分发 + 一次公开叙事 + 一次私发线索；不能多发命令。
    const grantFrame = JSON.parse(
      framesOf(keeperFrames.sent, "command_request")[2],
    ) as { kind: string; payload: Record<string, unknown> };
    expect(grantFrame.kind).toBe("grant_clue");
    expect(grantFrame.payload).toMatchObject({
      basis: "医生当面说明",
      recipient_investigator_ids: [targetInvestigator],
    });

    await expect
      .poll(() => framesOf(playerAFrames.received, "clue_granted").length, {
        timeout: 30_000,
      })
      .toBeGreaterThan(0);
    expect(framesOf(playerBFrames.received, "clue_granted")).toHaveLength(0);
    // M5：记忆是主持侧资料 —— 玩家连接（帧级）不应收到记忆记录或查询结果。
    for (const player of [playerAFrames, playerBFrames]) {
      expect(framesOf(player.received, "memory_recorded")).toHaveLength(0);
      expect(framesOf(player.received, "memory_query_result")).toHaveLength(0);
    }
    // 玩家页面上也不应出现主持只读的记忆查询入口。
    expect(
      await playerB.locator('[data-testid="keeper-memory-query"]').count(),
    ).toBe(0);
    await expect(keeper.locator(".action-status-card").last()).toContainText(
      "已处理完成",
    );

    // ---- ② 主持请求检定 → 甲在持久检定卡上掷骰 ----
    // 技能必须用角色卡上真实存在的技能键（服务端按技能键校验）。
    await keeper.getByTestId("keeper-cmd-request_check").click();
    await setKeeperField(keeper, "investigator_id", targetInvestigator);
    const skillOptions = await keeper
      .locator("#keeper-skill-options option")
      .evaluateAll((nodes) =>
        nodes.map((node) => (node as HTMLOptionElement).value),
      );
    expect(skillOptions.length, "主持台没有技能候选").toBeGreaterThan(0);

    let checkAccepted = false;
    let lastCheckError = "";
    for (const skill of skillOptions.slice(0, 4)) {
      await keeper.getByTestId("keeper-cmd-request_check").click();
      await setKeeperField(keeper, "investigator_id", targetInvestigator);
      await setKeeperField(keeper, "skill", skill);
      await keeper
        .locator('[data-field="difficulty"] select')
        .selectOption("regular");
      await keeper
        .locator('[data-field="attempt"] input')
        .fill("向医生说明来意");
      await keeper
        .locator('[data-field="visibility"] select')
        .selectOption("public");
      const before = framesOf(keeperFrames.received, "check_requested").length;
      const submitted = await submitKeeperCommand(keeper, keeperFrames);
      if (
        submitted.accepted &&
        framesOf(keeperFrames.received, "check_requested").length > before
      ) {
        checkAccepted = true;
        break;
      }
      lastCheckError = submitted.lastError;
    }
    expect(checkAccepted, `检定被拒：${lastCheckError}`).toBe(true);

    const checkCard = playerA.locator(".check-request-card");
    await expect(checkCard).toBeVisible({ timeout: 30_000 });
    // 检定参数来自服务端请求：卡片里没有可改参数的控件。
    expect(await checkCard.locator("input, select").count()).toBe(0);
    await checkCard.getByRole("button", { name: "掷骰", exact: true }).click();
    await expect
      .poll(() => framesOf(playerAFrames.sent, "check_response").length, {
        timeout: 30_000,
      })
      .toBe(1);
    const checkResponse = JSON.parse(
      framesOf(playerAFrames.sent, "check_response")[0],
    ) as Record<string, unknown>;
    expect(checkResponse).toMatchObject({ decision: "roll" });
    // 按钮不携带任何能影响结果的参数。
    expect(checkResponse).not.toHaveProperty("skill");
    expect(checkResponse).not.toHaveProperty("target_value");
    await expect(playerA.locator(".check-request-card")).toContainText("vs", {
      timeout: 30_000,
    });
    await expect(
      playerA.locator(".action-status-card").filter({ hasText: "掷骰" }),
    ).toContainText("已处理完成");
    // 乙不是被指定的调查员：他没有可掷骰的卡。
    await expect(
      playerB
        .locator(".check-request-card")
        .getByRole("button", { name: "掷骰" }),
    ).toHaveCount(0);

    // ---- ③ 主持调整 SAN ----
    const statusText = () =>
      playerA.evaluate(
        () => document.querySelector(".inv-card-status")?.textContent ?? "",
      );
    const sanBefore = await statusText();
    await keeper.getByTestId("keeper-cmd-adjust_stat").click();
    await setKeeperField(keeper, "investigator_id", targetInvestigator);
    await keeper.locator('[data-field="field"] select').selectOption("san");
    await keeper.locator('[data-field="delta"] input').fill("-3");
    await keeper.locator('[data-field="reason"] input').fill("目击遗体");
    const sanSubmit = await submitKeeperCommand(keeper, keeperFrames);
    expect(sanSubmit.accepted, `调整 SAN 被拒：${sanSubmit.lastError}`).toBe(
      true,
    );
    await expect
      .poll(() => framesOf(playerAFrames.received, "state_changed").length, {
        timeout: 30_000,
      })
      .toBeGreaterThan(0);
    await expect.poll(statusText, { timeout: 30_000 }).not.toBe(sanBefore);
    await expect(playerB.locator("#san-bar")).toHaveText(bSanBefore!);

    // ---- 道具真实闭环：玩家按钮只申报，主持核对后扣减；拒绝不改库存。 ----
    const aSheet = JSON.parse(
      framesOf(playerAFrames.received, "session_snapshot").at(-1)!,
    ).payload;
    const originalItems = aSheet.items as {
      id: string;
      label: string;
      quantity: number;
    }[];
    expect(originalItems.length).toBeGreaterThanOrEqual(2);
    const item = originalItems.find((entry) => entry.quantity === 1)!;
    expect(item, "本用例需要一件数量为 1 的真实开局物品").toBeTruthy();
    if (
      await playerA
        .locator("#char-panel")
        .getAttribute("class")
        .then((value) => value?.includes("collapsed"))
    ) {
      await playerA.locator("#btn-panel").click();
    }
    // Stable server ID, never fuzzy label matching or a guessed inventory index.
    const ownItem = playerA.locator(`.inv-item-row[data-item-id="${item.id}"]`);
    await expect(ownItem).toBeVisible();
    const bInventoryBefore = await playerB
      .locator(".inv-card-items")
      .textContent();
    const changesBefore = framesOf(
      playerAFrames.received,
      "inventory_changed",
    ).length;
    await ownItem.getByRole("button", { name: "使用", exact: true }).click();
    const useDialog = playerA.getByRole("dialog", { name: "使用道具" });
    await useDialog
      .getByLabel("补充做法（即兴用法）")
      .fill("我决定丢弃这件随身物品，腾出手来调查。");
    await useDialog
      .getByRole("button", { name: "提交请求", exact: true })
      .click();
    await expect(useDialog).toHaveCount(0);
    const useRequest = keeper
      .getByTestId("keeper-pending-request")
      .filter({ hasText: item.id });
    await expect(useRequest).toBeVisible();
    const useId = await useRequest.locator(".keeper-pending-id").textContent();
    const useFrame = JSON.parse(
      framesOf(playerAFrames.sent, "action_request").at(-1)!,
    );
    expect(useFrame.action).toMatchObject({
      kind: "use_item",
      item_id: item.id,
      quantity: 1,
      operation: "custom",
    });
    expect(framesOf(playerAFrames.received, "inventory_changed")).toHaveLength(
      changesBefore,
    );
    await expect(ownItem).toBeVisible();
    const commandsBeforeUse = framesOf(
      keeperFrames.sent,
      "command_request",
    ).length;
    for (const width of [1280, 939, 640]) {
      await keeper.setViewportSize({ width, height: 900 });
      const prepare = useRequest.getByRole("button", {
        name: "准备使用",
        exact: true,
      });
      await prepare.scrollIntoViewIfNeeded();
      await expect(keeper.locator(".boot-loader")).toHaveCount(0);
      await keeper.waitForTimeout(1000);
      const geometry = await prepare.evaluate((node) => {
        const rect = node.getBoundingClientRect();
        return {
          height: rect.height,
          padding: parseFloat(getComputedStyle(node).paddingLeft),
          nowrap: getComputedStyle(node).whiteSpace,
          fits: node.scrollWidth <= node.clientWidth + 1,
          hit: node.contains(
            document.elementFromPoint(
              rect.x + rect.width / 2,
              rect.y + rect.height / 2,
            ),
          ),
        };
      });
      expect(geometry.height).toBeGreaterThanOrEqual(40);
      expect(geometry.padding).toBeGreaterThan(0);
      expect(geometry.nowrap).toBe("nowrap");
      expect(geometry.fits).toBe(true);
      expect(geometry.hit).toBe(true);
      expect(
        await keeper.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth + 1,
        ),
      ).toBe(true);
      await keeper.screenshot({ path: `/tmp/trpg-item-request-${width}.png` });
    }
    await keeper.setViewportSize({ width: 1440, height: 900 });
    await useRequest
      .getByRole("button", { name: "准备使用", exact: true })
      .click();
    await expect(keeper.getByLabel("调查员", { exact: true })).toHaveValue(
      playerAInvestigator,
    );
    await expect(keeper.getByLabel("物品", { exact: true })).toHaveValue(
      item.id,
    );
    await expect(keeper.getByLabel("数量", { exact: true })).toHaveValue("1");
    await expect(
      keeper.getByLabel("扣减物品", { exact: true }),
    ).not.toBeChecked();
    expect(framesOf(keeperFrames.sent, "command_request")).toHaveLength(
      commandsBeforeUse,
    );
    // An actual server rejection: ask to consume two when only one is held.
    await setKeeperField(keeper, "quantity", "2");
    await keeper.getByLabel("扣减物品", { exact: true }).check();
    const rejectedUse = await submitKeeperCommand(keeper, keeperFrames);
    expect(rejectedUse.accepted).toBe(false);
    expect(JSON.parse(rejectedUse.lastError).payload.code).toBe(
      "object_not_held",
    );
    await expect(keeper.locator(".keeper-feedback")).toContainText("数量不足");
    await expect(keeper.getByLabel("数量", { exact: true })).toHaveValue("2");
    await expect(ownItem).toBeVisible();
    expect(framesOf(playerAFrames.received, "inventory_changed")).toHaveLength(
      changesBefore,
    );
    await expect(playerB.locator(".inv-card-items")).toHaveText(
      bInventoryBefore!,
    );
    // Correcting the preserved form performs exactly one committed consumption.
    await setKeeperField(keeper, "quantity", "1");
    const used = await submitKeeperCommand(keeper, keeperFrames);
    expect(used.accepted, used.lastError).toBe(true);
    await expect(ownItem).toHaveCount(0);
    await expect(playerB.locator(".inv-card-items")).toHaveText(
      bInventoryBefore!,
    );
    const committedItems = JSON.parse(
      framesOf(playerAFrames.received, "inventory_changed").at(-1)!,
    ).payload.items;
    expect(
      committedItems.find(
        (entry: { id: string; quantity: number }) => entry.id === item.id,
      )?.quantity,
    ).toBe(0);
    // Item use and request resolution are intentionally separate commands.
    await expect(useRequest).toBeVisible();
    await useRequest
      .getByRole("button", { name: "准备裁定", exact: true })
      .click();
    await expect(keeper.getByLabel("玩家请求", { exact: true })).toHaveValue(
      useId!,
    );
    await setKeeperField(keeper, "resolution", "completed");
    await setKeeperField(keeper, "outcome", "success");
    await setKeeperField(
      keeper,
      "note",
      "物品已按丢弃请求扣减。未进行其他调查。",
    );
    const itemResolved = await submitKeeperCommand(keeper, keeperFrames);
    expect(itemResolved.accepted, itemResolved.lastError).toBe(true);
    await expect(useRequest).toHaveCount(0);

    // Move a different real item to the other PC: both backpacks update using
    // server-owned recipient IDs, not whoever happens to be the current player.
    const otherItem = originalItems.find((entry) => entry.id !== item.id)!;
    const bInvestigator = JSON.parse(
      framesOf(playerBFrames.received, "session_snapshot").at(-1)!,
    ).payload.investigator_id as string;
    await keeper.getByTestId("keeper-cmd-transfer_item").click();
    await setKeeperField(keeper, "item_id", otherItem.id);
    await setKeeperField(keeper, "quantity", String(otherItem.quantity));
    await setKeeperField(keeper, "from_investigator_id", playerAInvestigator);
    await setKeeperField(keeper, "to_investigator_id", bInvestigator);
    const transferred = await submitKeeperCommand(keeper, keeperFrames);
    expect(transferred.accepted, transferred.lastError).toBe(true);
    await expect(
      playerA.locator(`[data-item-id="${otherItem.id}"]`),
    ).toHaveCount(0);
    // The receiver opens their actual collapsed sidebar to inspect delivery.
    if (
      (await playerB.locator("#char-panel").getAttribute("class"))?.includes(
        "collapsed",
      )
    ) {
      await playerB.locator("#btn-panel").click();
    }
    await expect(
      playerB.locator(`.inv-item-row[data-item-id="${otherItem.id}"]`),
    ).toBeVisible();
    await playerA.reload();
    await expect(playerA.getByTestId("structured-tool-row")).toBeVisible({
      timeout: 90_000,
    });
    await expect
      .poll(
        () =>
          JSON.parse(
            framesOf(playerAFrames.received, "session_snapshot").at(-1)!,
          ).payload.received_assets,
      )
      .toEqual(
        expect.arrayContaining([{ id: firstAssetId, label: firstAssetLabel }]),
      );
    const recoveredGallery = await openReceivedGallery();
    await recoveredGallery
      .getByRole("button", { name: `查看图片：${firstAssetLabel}` })
      .click();
    const recoveredImage = playerA
      .getByRole("dialog")
      .filter({ has: playerA.locator("img") });
    await expect(
      recoveredImage.getByRole("button", { name: "原尺寸查看" }),
    ).toBeEnabled();
    await recoveredImage.getByRole("button", { name: "关闭材料查看" }).click();
    // Server snapshot after reconnect must agree with live event projections.
    await expect
      .poll(() =>
        JSON.parse(
          framesOf(playerAFrames.received, "session_snapshot").at(-1)!,
        ).payload.items.filter(
          (entry: { id: string; quantity: number }) =>
            [item.id, otherItem.id].includes(entry.id) && entry.quantity > 0,
        ),
      )
      .toEqual([]);

    // Human-only core loop: a player's full freeform request becomes a live
    // keeper task without refreshing or calling a model. Preparation is inert.
    const fullQuestion =
      "我说明自己的调查计划并询问许可。".repeat(10) +
      "末尾问题：可以先查阅那份登记吗？";
    await playerA.locator("#user-input").fill(fullQuestion);
    await playerA.locator("#btn-send").click();
    const pendingCard = keeper
      .getByTestId("keeper-pending-request")
      .filter({ hasText: fullQuestion.slice(0, 60) });
    await expect(pendingCard).toBeVisible();
    await pendingCard.getByText("查看完整请求", { exact: true }).click();
    await expect(pendingCard.locator(".keeper-pending-body")).toHaveText(
      fullQuestion,
    );
    for (const width of [1280, 939, 640]) {
      await keeper.setViewportSize({ width, height: 900 });
      await pendingCard.scrollIntoViewIfNeeded();
      await expect(keeper.locator(".boot-loader")).toHaveCount(0);
      await keeper.waitForTimeout(1000);
      const controls = pendingCard.getByRole("button");
      expect(await controls.count()).toBe(3);
      const geometry = await controls.evaluateAll((nodes) =>
        nodes.map((node) => {
          const button = node as HTMLElement;
          const css = getComputedStyle(button);
          return {
            height: button.getBoundingClientRect().height,
            padding: parseFloat(css.paddingLeft),
            nowrap: css.whiteSpace,
            fits: button.scrollWidth <= button.clientWidth + 1,
          };
        }),
      );
      for (const button of geometry) {
        expect(button.height).toBeGreaterThanOrEqual(40);
        expect(button.padding).toBeGreaterThan(0);
        expect(button.nowrap).toBe("nowrap");
        expect(button.fits).toBe(true);
      }
      await expect(controls.first()).toBeInViewport();
      expect(
        await keeper.evaluate(
          () => document.documentElement.scrollWidth <= window.innerWidth + 1,
        ),
      ).toBe(true);
      await keeper.screenshot({
        path: `/tmp/trpg-keeper-pending-${width}.png`,
      });
    }
    // A keeper without a character uses the workspace, not a player action.
    await expect(keeper.locator("#user-input")).toBeDisabled();
    await expect(keeper.getByTestId("btn-free-roll")).toBeDisabled();
    await keeper.setViewportSize({ width: 1440, height: 900 });
    expect(framesOf(playerBFrames.received, "intent_pending")).toHaveLength(0);
    const pendingId = await pendingCard
      .locator(".keeper-pending-id")
      .textContent();
    const commandsBeforePrepare = framesOf(
      keeperFrames.sent,
      "command_request",
    ).length;
    const sceneBeforePrepare = await playerA
      .locator(".header-scene-name")
      .innerText();
    await pendingCard.getByRole("button", { name: "准备裁定" }).click();
    await expect(keeper.getByLabel("玩家请求")).toHaveValue(pendingId!);
    await expect(keeper.getByLabel("领域结果")).toHaveValue("not_executed");
    expect(framesOf(keeperFrames.sent, "command_request")).toHaveLength(
      commandsBeforePrepare,
    );
    await expect(playerA.locator(".header-scene-name")).toHaveText(
      sceneBeforePrepare,
    );
    await setKeeperField(keeper, "note", "已听取计划，尚未执行调查。");
    const resolvedIntent = await submitKeeperCommand(keeper, keeperFrames);
    expect(resolvedIntent.accepted, resolvedIntent.lastError).toBe(true);
    await expect(pendingCard).toHaveCount(0);
    await expect(
      playerA
        .locator(".action-status-card")
        .filter({ hasText: fullQuestion.slice(0, 20) }),
    ).toContainText("未执行");
    await expect(playerA.locator(".header-scene-name")).toHaveText(
      sceneBeforePrepare,
    );

    // ---- ④ 整队移动：主持用 move_party（玩家式 action_request{move} 需要
    // 认领调查员；主持不占角色，所以权威路径是主持命令），两端场景一致。 ----
    const sceneBefore = await playerA.locator(".header-scene-name").innerText();
    await keeper.getByTestId("keeper-cmd-move_party").click();
    const sceneOptions = await keeper
      .locator(
        '[data-field="destination_scene_id"] select option, [data-field="destination_scene_id"] datalist option',
      )
      .evaluateAll((nodes) =>
        nodes
          .map((node) => (node as HTMLOptionElement).value)
          .filter((value) => value.length > 0),
      );
    const destination = sceneOptions.find((value) => value !== "") ?? "";
    expect(destination, "主持台没有可选目的地").not.toBe("");
    await setKeeperField(keeper, "destination_scene_id", destination);
    const moveSubmit = await submitKeeperCommand(keeper, keeperFrames);
    expect(moveSubmit.accepted, `整队移动被拒：${moveSubmit.lastError}`).toBe(
      true,
    );
    await keeper.getByRole("button", { name: "关闭主持台" }).click();
    await expect
      .poll(async () => playerA.locator(".header-scene-name").innerText(), {
        timeout: 30_000,
      })
      .not.toBe(sceneBefore);
    expect(await playerB.locator(".header-scene-name").innerText()).toBe(
      await playerA.locator(".header-scene-name").innerText(),
    );

    // ---- ⑤ 存档入口 + 甲刷新重连 ----
    await keeper.getByTestId("btn-keeper-console").click();
    await expect(keeper.getByTestId("keeper-save-panel")).toBeVisible();
    await keeper.getByRole("button", { name: "关闭主持台" }).click();

    const sceneNow = await keeper.locator(".header-scene-name").innerText();
    await playerA.reload();
    await expect(playerA.getByTestId("structured-tool-row")).toBeVisible({
      timeout: 90_000,
    });
    await expect(playerA.locator(".header-scene-name")).toHaveText(sceneNow, {
      timeout: 30_000,
    });

    // The room is asynchronous, not an old alternating-turn lobby.
    await expect(playerA.getByTestId("online-room-dock")).toContainText(
      "行动交由守秘人处理",
    );
    await keeper.locator(".online-room-dock-toggle").click();
    await expect(keeper.locator(".online-room-dock-toggle")).toHaveAttribute(
      "aria-expanded",
      "true",
    );
    await expect(
      keeper.getByRole("button", { name: "跳过行动者" }),
    ).toHaveCount(0);
    await keeper.getByRole("button", { name: "房间管理" }).click();
    await expect(keeper.getByRole("heading", { name: roomName })).toBeVisible();
    await expect(keeper.getByRole("button", { name: "指定行动" })).toHaveCount(
      0,
    );
    await expect(
      keeper.getByRole("button", { name: "退出房间", exact: true }),
    ).toBeDisabled();

    // A new viewer joins through the real invitation UI after the game starts.
    if (await keeper.locator(".invite-box").count()) {
      await keeper
        .locator(".invite-box")
        .getByRole("button", { name: "撤销", exact: true })
        .click();
    }
    await keeper.getByLabel("邀请角色").selectOption("viewer");
    await keeper.getByRole("button", { name: "生成邀请码" }).click();
    const viewerInvite = await keeper.locator(".invite-token").textContent();
    expect(viewerInvite).toBeTruthy();
    await register(viewer, `viewer${runId}`);
    await viewer
      .getByRole("textbox", { name: "邀请码", exact: true })
      .fill(viewerInvite!);
    await viewer.getByRole("button", { name: "加入房间" }).click();
    await expect(viewer.getByTestId("online-room-dock")).toContainText(
      "旁观中 · 只读",
    );
    await expect(viewer.locator("#user-input")).toBeDisabled();
    await expect(viewer.getByTestId("btn-free-roll")).toBeDisabled();
    await expect(viewer.getByTestId("btn-move")).toBeDisabled();
    await expect(viewer.getByTestId("btn-keeper-console")).toHaveCount(0);
    await expect(viewer.locator("#messages")).toContainText(
      "无模型主持验收：窗边的钟敲了两声。",
    );
    expect(framesOf(viewerFrames.received, "clue_granted")).toHaveLength(0);
    expect(framesOf(viewerFrames.received, "handout_presented")).toHaveLength(
      0,
    );
    for (const raw of framesOf(viewerFrames.received, "session_snapshot")) {
      const payload = JSON.parse(raw).payload;
      expect(payload).not.toHaveProperty("keeper_material");
      expect(payload).not.toHaveProperty("keeper_assets");
      expect(payload).not.toHaveProperty("keeper_investigators");
      expect(payload.character).toBeNull();
    }
    for (const width of [1280, 939, 640]) {
      await viewer.setViewportSize({ width, height: 900 });
      await expect(viewer.locator(".boot-loader")).toHaveCount(0);
      await viewer.waitForTimeout(1000);
      await expect(viewer.getByTestId("online-room-dock")).toBeInViewport();
      expect(
        await viewer.evaluate(
          () => document.documentElement.scrollWidth <= window.innerWidth + 1,
        ),
      ).toBe(true);
      await viewer.screenshot({
        path: `/tmp/trpg-viewer-readonly-${width}.png`,
      });
    }

    // Ownership and secret-reading authorization remain separate. A new owner
    // can manage the room, but does not silently inherit keeper documents.
    const nextOwnerRow = keeper
      .locator(".member-row")
      .filter({ hasText: playerBName });
    await nextOwnerRow
      .getByRole("button", { name: "移交", exact: true })
      .click();
    await nextOwnerRow.getByRole("button", { name: "确认移交房主" }).click();
    await expect(
      keeper.getByRole("button", { name: "退出房间", exact: true }),
    ).toBeEnabled();
    await playerB.locator(".online-room-dock-toggle").click();
    await expect(
      playerB
        .locator(".online-room-dock-members .member-row")
        .filter({ hasText: playerBName }),
    ).toContainText("房主");
    await expect
      .poll(async () =>
        playerB.evaluate(async (worldId) => {
          const response = await fetch(
            `/api/worlds/${encodeURIComponent(worldId)}/keeper-guide`,
            { credentials: "include" },
          );
          return response.status;
        }, libraryWorldId),
      )
      .toBe(404);
    await keeper.getByRole("button", { name: "退出房间", exact: true }).click();
    await keeper.getByRole("button", { name: "确认退出房间" }).click();
    await expect(
      keeper.getByRole("heading", { name: "联机大厅" }),
    ).toBeVisible();
    await expect(keeper.locator(".keeper-library")).toHaveCount(0);
    await expect(playerB.locator(".header-scene-name")).toHaveText(sceneNow);

    // 全程没有模型调用（base url 指向关闭端口）。
    for (const page of [keeper, playerA, playerB, viewer]) {
      await expect(
        page.getByText(/无法连接配置的模型|模型.*不可用|connect.*fail/i),
      ).toHaveCount(0);
    }
  } finally {
    await keeperContext.close();
    await playerAContext.close();
    await playerBContext.close();
    await viewerContext.close();
  }
});
