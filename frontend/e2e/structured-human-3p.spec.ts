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

import { spawn, spawnSync, type ChildProcess } from "node:child_process";
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

/** Scripted producer, not a real-model claim; all state lives in this test's runtime root. */
function produceAssistedCombatDraft(
  worldId: string,
  requestId: string,
  action: Record<string, unknown>,
) {
  const produced = spawnSync(
    pythonPath(),
    [
      "-c",
      [
        "import asyncio, json, sys",
        "from sqlalchemy import select",
        "from src.storage.database import PlayerRequest, session_scope",
        "from src.structured.agent import KeeperAgentRunner",
        "world_id, request_id, action = sys.argv[1], sys.argv[2], json.loads(sys.argv[3])",
        "from src.storage.database import database_url",
        "url = database_url()",
        "async def caller(system, context):",
        "    return json.dumps({'assessment':'验收：准备对抗，等待双方响应。', 'narration':'', 'commands':[{'kind':'combat_action','payload':action},{'kind':'resolve_intent','payload':{'request_id':request_id,'resolution':'awaiting_player','outcome':'not_executed','pending_action':{'kind':'freeform','note':'准备对抗，等待双方参与、选择防御并确认掷骰'}}}]}, ensure_ascii=False)",
        "result = asyncio.run(KeeperAgentRunner(url, caller=caller).run_assisted(world_id=world_id, trigger_request_id=request_id))",
        "assert result.stop_reason == 'draft_ready', result.stop_reason",
        "with session_scope(url) as session:",
        "    draft = session.scalar(select(PlayerRequest).where(PlayerRequest.world_id == world_id, PlayerRequest.request_type == 'keeper_draft').order_by(PlayerRequest.created_at.desc()))",
        "    print(json.dumps({'draft_id':draft.request_id}))",
      ].join("\n"),
      worldId,
      requestId,
      JSON.stringify(action),
    ],
    {
      cwd: repositoryRoot,
      env: {
        ...process.env,
        TRPG_RUNTIME_ROOT: runtimeRoot,
        TRPG_DATABASE_URL: `sqlite:///${join(runtimeRoot, "e2e.db")}`,
        TRPG_WRITE_COMPAT_EXPORTS: "0",
        OPENAI_API_KEY: "e2e-placeholder",
        OPENAI_BASE_URL: "http://127.0.0.1:9/v1",
      },
      encoding: "utf-8",
    },
  );
  if (produced.status !== 0)
    throw new Error(produced.stderr || produced.stdout);
  return JSON.parse(produced.stdout.trim());
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

    // Actor-free keeper dice: actual commands, server privacy, no model or game effects.
    const diceStartSnapshot = JSON.parse(
      framesOf(keeperFrames.received, "session_snapshot").at(-1)!,
    ).payload;
    expect(diceStartSnapshot.investigator_id).toBeNull();
    await expect(keeper.getByTestId("btn-keeper-dice")).toBeEnabled();
    for (const player of [playerA, playerB])
      await expect(player.getByTestId("btn-keeper-dice")).toHaveCount(0);
    await keeper.getByTestId("btn-keeper-dice").click();
    const diceDialog = keeper.getByRole("dialog", {
      name: "主持普通骰",
      exact: true,
    });
    await expect(
      diceDialog.getByRole("combobox", { name: "接收范围" }),
    ).toHaveValue("keeper");
    for (const [width, height] of [
      [1280, 900],
      [939, 900],
      [640, 900],
      [390, 900],
      [390, 360],
    ]) {
      await keeper.setViewportSize({ width, height });
      for (const control of [
        diceDialog.getByRole("textbox", { name: "骰式" }),
        diceDialog.getByRole("combobox", { name: "接收范围" }),
        diceDialog.getByTestId("keeper-dice-submit"),
        diceDialog.getByRole("button", { name: "关闭", exact: true }),
      ]) {
        await control.scrollIntoViewIfNeeded();
        const shape = await control.evaluate((node) => {
          const r = node.getBoundingClientRect(),
            s = getComputedStyle(node);
          const label = node
            .closest("label")
            ?.querySelector("span")
            ?.getBoundingClientRect();
          const body = node
            .closest(".panel-action-body")
            ?.getBoundingClientRect();
          return {
            height: r.height,
            padding: parseFloat(s.paddingLeft),
            nowrap: s.whiteSpace,
            within:
              r.left >= 0 &&
              r.right <= innerWidth &&
              r.top >= 0 &&
              r.bottom <= innerHeight,
            hit: node.contains(
              document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2),
            ),
            labelVisible:
              !label ||
              !body ||
              (label.top >= Math.max(body.top, 0) &&
                label.bottom <= Math.min(body.bottom, innerHeight)),
          };
        });
        expect(shape.height).toBeGreaterThanOrEqual(44);
        expect(shape.within).toBe(true);
        expect(shape.hit).toBe(true);
        expect(shape.labelVisible).toBe(true);
        if (await control.evaluate((node) => node.tagName === "BUTTON")) {
          expect(shape.padding).toBeGreaterThanOrEqual(13);
          expect(shape.nowrap).toBe("nowrap");
        }
      }
      expect(
        await keeper.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      ).toBe(true);
      if (height === 360) {
        const bothLabels = await diceDialog.evaluate((dialog) => {
          const body = dialog
            .querySelector(".panel-action-body")!
            .getBoundingClientRect();
          return [
            ...dialog.querySelectorAll(".panel-action-field > span"),
          ].every((span) => {
            const r = span.getBoundingClientRect();
            return r.top >= body.top && r.bottom <= body.bottom;
          });
        });
        expect(bothLabels).toBe(true);
      }
      await keeper.screenshot({
        path: resolve(
          repositoryRoot,
          `docs/design/platform-ui/keeper-dice-${width}${height === 360 ? "-short" : ""}.png`,
        ),
      });
    }
    await keeper.getByTestId("keeper-dice-submit").click();
    const privateDice = JSON.parse(
      framesOf(keeperFrames.sent, "command_request").at(-1)!,
    );
    expect(privateDice.kind).toBe("keeper_roll");
    expect(privateDice.payload).toEqual({
      spec: "1d100",
      visibility: "keeper",
    });
    await expect
      .poll(() =>
        framesOf(keeperFrames.received, "keeper_roll_resolved").some(
          (raw) =>
            JSON.parse(raw).payload.command_id === privateDice.command_id,
        ),
      )
      .toBe(true);
    for (const frames of [playerAFrames, playerBFrames])
      expect(
        frames.received.some((raw) => raw.includes(privateDice.command_id)),
      ).toBe(false);
    await expect(
      keeper.locator(
        `[data-testid="keeper-dice-receipt"][data-command-id="${privateDice.command_id}"]`,
      ),
    ).toContainText("仅主持可见");
    await keeper.setViewportSize({ width: 1440, height: 900 });
    await keeper.getByTestId("btn-keeper-dice").click();
    await diceDialog.getByRole("textbox", { name: "骰式" }).fill("2d6+3");
    await diceDialog
      .getByRole("combobox", { name: "接收范围" })
      .selectOption("public");
    await keeper.getByTestId("keeper-dice-submit").click();
    const publicDice = JSON.parse(
      framesOf(keeperFrames.sent, "command_request").at(-1)!,
    );
    for (const [page, frames] of [
      [keeper, keeperFrames],
      [playerA, playerAFrames],
      [playerB, playerBFrames],
    ] as const) {
      await expect
        .poll(() =>
          framesOf(frames.received, "keeper_roll_resolved").some(
            (raw) =>
              JSON.parse(raw).payload.command_id === publicDice.command_id,
          ),
        )
        .toBe(true);
      await expect(
        page.locator(
          `[data-testid="keeper-dice-receipt"][data-command-id="${publicDice.command_id}"]`,
        ),
      ).toContainText("公开");
    }
    const resultBodies = [keeperFrames, playerAFrames, playerBFrames].map(
      (frames) =>
        JSON.parse(
          framesOf(frames.received, "keeper_roll_resolved").find(
            (raw) =>
              JSON.parse(raw).payload.command_id === publicDice.command_id,
          )!,
        ).payload,
    );
    expect(resultBodies[0]).toEqual(resultBodies[1]);
    expect(resultBodies[1]).toEqual(resultBodies[2]);
    expect(resultBodies[0].total).toBeGreaterThanOrEqual(5);
    expect(resultBodies[0].total).toBeLessThanOrEqual(15);
    const diceEvents = framesOf(
      keeperFrames.received,
      "keeper_roll_resolved",
    ).map((raw) => JSON.parse(raw));
    expect(
      diceEvents.every((e) => e.revision === diceStartSnapshot.revision),
    ).toBe(true);
    await keeper.screenshot({
      path: resolve(
        repositoryRoot,
        "docs/design/platform-ui/keeper-dice-receipts.png",
      ),
    });
    const commandsAfterDice = framesOf(
      keeperFrames.sent,
      "command_request",
    ).length;
    expect(commandsAfterDice).toBe(2);

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
      .toBe(commandsAfterDice + 3); // 两次普通骰之外，仍恰好三条原命令。
    const grantFrame = JSON.parse(
      framesOf(keeperFrames.sent, "command_request")[commandsAfterDice + 2],
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
    await expect(useRequest.locator(".keeper-pending-label")).toHaveText(
      `申请使用：${item.label} ×1`,
    );
    await useRequest.getByText("查看完整请求", { exact: true }).click();
    await expect(useRequest.locator(".keeper-pending-body")).toContainText(
      "本次申请数量：1",
    );
    await expect(useRequest.locator(".keeper-pending-body")).toContainText(
      `${item.label}（${item.id}）`,
    );
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
    for (const width of [1280, 939, 640, 390]) {
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
    await setKeeperField(
      keeper,
      "from_holder",
      `investigator/${playerAInvestigator}`,
    );
    await setKeeperField(keeper, "to_holder", `investigator/${bInvestigator}`);
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
    // Real human UI custody circuit: PC -> NPC -> scene -> PC. The same
    // existing object survives; no DB injection or fabricated inventory.
    const privateProgress = JSON.parse(
      framesOf(keeperFrames.received, "keeper_progress_updated").at(-1)!,
    ).payload;
    const custodians = privateProgress.holdings.holders as {
      kind: string;
      id: string;
      name: string;
    }[];
    const npcCustodian = custodians.find((h) => h.kind === "npc");
    const sceneCustodian = custodians.find((h) => h.kind === "scene");
    expect(npcCustodian).toBeTruthy();
    expect(sceneCustodian).toBeTruthy();
    let fromHolder = `investigator/${bInvestigator}`;
    const aInventoryBeforeCircuit = framesOf(
      playerAFrames.received,
      "inventory_changed",
    ).length;
    for (const toHolder of [
      `npc/${npcCustodian!.id}`,
      `scene/${sceneCustodian!.id}`,
      `investigator/${bInvestigator}`,
    ]) {
      await keeper.getByTestId("keeper-cmd-transfer_item").click();
      await setKeeperField(keeper, "item_id", otherItem.id);
      await expect(keeper.getByLabel("来源", { exact: true })).toHaveValue(
        fromHolder,
      );
      await setKeeperField(keeper, "quantity", String(otherItem.quantity));
      await setKeeperField(keeper, "to_holder", toHolder);
      const moved = await submitKeeperCommand(keeper, keeperFrames);
      expect(moved.accepted, moved.lastError).toBe(true);
      const progress = JSON.parse(
        framesOf(keeperFrames.received, "keeper_progress_updated").at(-1)!,
      ).payload;
      expect(
        progress.holdings.items.find(
          (i: { id: string }) => i.id === otherItem.id,
        ).holder,
      ).toEqual({
        kind: toHolder.split("/")[0],
        id: toHolder.slice(toHolder.indexOf("/") + 1),
      });
      fromHolder = toHolder;
    }
    expect(framesOf(playerAFrames.received, "inventory_changed")).toHaveLength(
      aInventoryBeforeCircuit,
    );
    expect(
      framesOf(playerAFrames.received, "keeper_progress_updated"),
    ).toHaveLength(0);
    expect(
      framesOf(playerBFrames.received, "keeper_progress_updated"),
    ).toHaveLength(0);
    await keeper.reload();
    await expect(keeper.getByTestId("btn-keeper-console")).toBeVisible({
      timeout: 90_000,
    });
    await keeper.getByTestId("btn-keeper-console").click();
    await keeper.getByTestId("keeper-cmd-transfer_item").click();
    await setKeeperField(keeper, "item_id", otherItem.id);
    await expect(keeper.getByLabel("来源", { exact: true })).toHaveValue(
      `investigator/${bInvestigator}`,
    );
    await playerA.reload();
    await expect(playerA.getByTestId("structured-tool-row")).toBeVisible({
      timeout: 90_000,
    });
    await expect(
      playerA.locator(
        `[data-testid="keeper-dice-receipt"][data-command-id="${publicDice.command_id}"]`,
      ),
    ).toBeVisible();
    const restoredRolls = JSON.parse(
      framesOf(playerAFrames.received, "session_snapshot").at(-1)!,
    ).payload.keeper_rolls;
    expect(
      restoredRolls.some(
        (r: { command_id: string }) => r.command_id === publicDice.command_id,
      ),
    ).toBe(true);
    expect(
      restoredRolls.some(
        (r: { command_id: string }) => r.command_id === privateDice.command_id,
      ),
    ).toBe(false);
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
    const latestClock =
      framesOf(keeperFrames.received, "state_changed")
        .map((raw) => JSON.parse(raw).payload.clock?.elapsed_minutes)
        .filter((value) => typeof value === "number")
        .at(-1) ??
      JSON.parse(framesOf(keeperFrames.received, "session_snapshot").at(-1)!)
        .payload.clock.elapsed_minutes;
    expect(Number.isSafeInteger(latestClock)).toBe(true);
    expect(latestClock).toBeLessThanOrEqual(200);
    await keeper.getByTestId("keeper-cmd-advance_time").click();
    await setKeeperField(keeper, "minutes", String(200 - latestClock));
    await setKeeperField(keeper, "activity", "wait");
    await setKeeperField(
      keeper,
      "reason",
      "主持明确推进时间；并非对白里提到某个日期。",
    );
    const timeSubmit = await submitKeeperCommand(keeper, keeperFrames);
    expect(timeSubmit.accepted, timeSubmit.lastError).toBe(true);
    expect(
      JSON.parse(framesOf(keeperFrames.sent, "command_request").at(-1)!).payload
        .activity,
    ).toBe("wait");
    await expect(keeper.getByTestId("reference-game-clock")).toContainText(
      "已过3小时20分钟",
    );
    for (const page of [keeper, playerA, playerB]) {
      await expect(page.getByTestId("header-game-clock")).toContainText(
        "已过3小时20分钟",
      );
    }
    await expect(keeper.getByTestId("keeper-save-panel")).toBeVisible();
    // Actual owner + human keeper in a multiplayer room: save/management remain
    // usable, but restoring this shared world is not an available UI action.
    await expect(keeper.getByTestId("keeper-load")).toBeDisabled();
    await expect(keeper.getByTestId("keeper-save")).toBeEnabled();
    await expect(keeper.getByTestId("keeper-save-panel")).toBeEnabled();
    await expect(keeper.getByTestId("keeper-save-reason")).toContainText(
      "当前多人房间不支持读档",
    );
    for (const width of [1280, 939, 640, 390]) {
      await keeper.setViewportSize({ width, height: 900 });
      const saveRegion = keeper.getByRole("region", { name: "存档与续团" });
      await saveRegion.scrollIntoViewIfNeeded();
      await expect(keeper.getByTestId("keeper-save-reason")).toBeVisible();
      await keeper.screenshot({
        path: resolve(
          repositoryRoot,
          `docs/design/platform-ui/keeper-save-multiplayer-${width}.png`,
        ),
      });
    }
    await keeper.setViewportSize({ width: 1280, height: 900 });
    await keeper.getByRole("button", { name: "关闭主持台" }).click();

    const sceneNow = await keeper.locator(".header-scene-name").innerText();
    await playerA.reload();
    await expect(playerA.getByTestId("structured-tool-row")).toBeVisible({
      timeout: 90_000,
    });
    await expect(playerA.locator(".header-scene-name")).toHaveText(sceneNow, {
      timeout: 30_000,
    });
    await expect(playerA.getByTestId("header-game-clock")).toContainText(
      "已过3小时20分钟",
    );

    // Real human battle flow, not a scripted model/stub projection. Use only
    // authored in-scene NPCs and the actual investigator cards; no DB seeding.
    await keeper.getByTestId("btn-keeper-console").click();
    await keeper.getByTestId("keeper-cmd-combat_start").click();
    const latestTargets = framesOf(keeperFrames.received, "state_changed")
      .map((raw) => JSON.parse(raw).payload.targets)
      .filter(Array.isArray)
      .at(-1);
    expect(latestTargets, "移动后没有刷新在场目标").toBeTruthy();
    const combatNpc = latestTargets.find(
      (target: { kind: string; id: string }) => target.kind === "npc",
    );
    expect(combatNpc, "当前真实场景缺少参战NPC").toBeTruthy();
    await setKeeperField(keeper, "participants", combatNpc.id);
    const startedCombat = await submitKeeperCommand(keeper, keeperFrames);
    expect(startedCombat.accepted, startedCombat.lastError).toBe(true);
    const latestCombat = () =>
      JSON.parse(framesOf(keeperFrames.received, "combat_updated").at(-1)!)
        .payload;
    // A faster NPC can have initiative. Its non-dice movement is an ordinary
    // keeper command, not an artificial rewrite of initiative.
    for (
      let turn = 0;
      latestCombat().current_actor === combatNpc.id && turn < 3;
      turn += 1
    ) {
      await keeper.getByTestId("keeper-cmd-combat_action").click();
      await setKeeperField(keeper, "actor_id", combatNpc.id);
      await setKeeperField(keeper, "action_type", "move");
      await setKeeperField(keeper, "description", "退到房间另一侧。");
      const movedNpc = await submitKeeperCommand(keeper, keeperFrames);
      expect(movedNpc.accepted, movedNpc.lastError).toBe(true);
    }
    const actor = latestCombat().current_actor;
    const actingPage = actor === playerAInvestigator ? playerA : playerB;
    const otherPage = actingPage === playerA ? playerB : playerA;
    const actingFrames = actingPage === playerA ? playerAFrames : playerBFrames;
    const otherFrames = actingPage === playerA ? playerBFrames : playerAFrames;
    expect(
      latestCombat().participants.find(
        (p: { id: string; kind: string }) => p.id === actor,
      )?.kind,
    ).toBe("pc");
    const privateBefore =
      framesOf(otherFrames.received, "combat_decision_required").length +
      framesOf(otherFrames.received, "combat_roll_required").length;
    await keeper.getByTestId("keeper-cmd-combat_action").click();
    await setKeeperField(keeper, "actor_id", actor);
    await setKeeperField(keeper, "target_id", combatNpc.id);
    await setKeeperField(keeper, "action_type", "melee");
    await setKeeperField(keeper, "damage_spec", "1d3");
    const preparedCombat = await submitKeeperCommand(keeper, keeperFrames);
    expect(preparedCombat.accepted, preparedCombat.lastError).toBe(true);
    await keeper.getByRole("button", { name: "关闭主持台" }).click();
    const battleCard = actingPage.getByTestId("combat-field-record");
    await expect(battleCard).toBeVisible();
    // Non-hostile authored NPCs require the player's explicit violence choice.
    const decisionFrames = framesOf(
      actingFrames.received,
      "combat_decision_required",
    );
    if (latestCombat().awaiting_decision) {
      const decision = JSON.parse(decisionFrames.at(-1)!).payload;
      expect(decision.kind).toBe("irreversible_violence");
      const proceed = decision.options.find(
        (option: { id: string }) => option.id === "confirm_violence",
      );
      expect(proceed).toBeTruthy();
      await battleCard
        .getByRole("button", { name: proceed.label, exact: true })
        .click();
    }
    await expect(
      battleCard.getByRole("button", { name: "掷骰", exact: true }),
    ).toBeEnabled();
    await expect(
      otherPage.getByTestId("combat-field-record").getByRole("button"),
    ).toHaveCount(0);
    await expect(
      keeper.getByTestId("combat-field-record").getByRole("button"),
    ).toHaveCount(0);
    await actingPage.reload();
    await expect(
      actingPage
        .getByTestId("combat-field-record")
        .getByRole("button", { name: "掷骰", exact: true }),
    ).toBeEnabled({ timeout: 90_000 });
    const rollsBefore = framesOf(
      actingFrames.received,
      "combat_roll_resolved",
    ).length;
    await actingPage
      .getByTestId("combat-field-record")
      .getByRole("button", { name: "掷骰", exact: true })
      .click();
    await expect
      .poll(
        () => framesOf(actingFrames.received, "combat_roll_resolved").length,
      )
      .toBeGreaterThan(rollsBefore);
    const receipt = JSON.parse(
      framesOf(actingFrames.received, "combat_roll_resolved").at(-1)!,
    ).payload.result;
    expect(receipt.rolls.length).toBeGreaterThan(0);
    for (const page of [keeper, actingPage]) {
      const history = page.getByTestId("combat-result-history");
      await expect(history).toBeVisible();
      await history.locator("summary").click();
      await expect(history).toContainText(`d100=${receipt.rolls[0].roll}`);
    }
    await expect(otherPage.getByTestId("combat-result-history")).toHaveCount(0);
    await actingPage.reload();
    await expect(actingPage.getByTestId("combat-result-history")).toBeVisible({
      timeout: 90_000,
    });
    await actingPage
      .getByTestId("combat-result-history")
      .locator("summary")
      .click();
    await expect(actingPage.getByTestId("combat-result-history")).toContainText(
      `d100=${receipt.rolls[0].roll}`,
    );
    await expect(
      actingPage.getByTestId("combat-field-record").getByRole("button"),
    ).toHaveCount(0);
    expect(
      framesOf(otherFrames.received, "combat_decision_required").length +
        framesOf(otherFrames.received, "combat_roll_required").length,
    ).toBe(privateBefore);
    // Actual authored .38 revolver, selected by the owner's button and stable
    // registry ID. Advance initiative only through ordinary keeper commands;
    // no DB rewrites, fake inventory or model calls establish this premise.
    await keeper.getByTestId("btn-keeper-console").click();
    let shooter = "",
      gunId = "";
    for (let step = 0; step < 8; step++) {
      const currentActor = latestCombat().current_actor;
      await keeper.getByTestId("keeper-cmd-combat_action").click();
      await setKeeperField(keeper, "actor_id", currentActor);
      const kind = latestCombat().participants.find(
        (p: { id: string }) => p.id === currentActor,
      )?.kind;
      if (kind === "pc") {
        const choices = await keeper
          .locator('[data-field="weapon_item_id"] option')
          .evaluateAll((nodes) =>
            nodes.map((node) => ({
              id: (node as HTMLOptionElement).value,
              label: node.textContent || "",
            })),
          );
        const gun = choices.find((choice) =>
          choice.label.startsWith(".38口径左轮手枪（6发）"),
        );
        if (gun) {
          shooter = currentActor;
          gunId = gun.id;
          break;
        }
      }
      await setKeeperField(keeper, "action_type", "other");
      await setKeeperField(
        keeper,
        "description",
        "观察现场，等待下一位调查员准备动作。",
      );
      const waited = await submitKeeperCommand(keeper, keeperFrames);
      expect(waited.accepted, waited.lastError).toBe(true);
    }
    expect(gunId, "没有轮到真实持有模组左轮的调查员").not.toBe("");
    const gunPage = shooter === playerAInvestigator ? playerA : playerB;
    const gunFrames = gunPage === playerA ? playerAFrames : playerBFrames;
    const otherGunFrames = gunPage === playerA ? playerBFrames : playerAFrames;
    const otherInventoryEvents = framesOf(
      otherGunFrames.received,
      "inventory_changed",
    ).length;
    await keeper.getByRole("button", { name: "关闭主持台" }).click();
    await gunPage
      .getByRole("button", { name: "申报战斗动作", exact: true })
      .click();
    const shotDialog = gunPage.getByRole("dialog", { name: "申报战斗动作" });
    await shotDialog
      .getByLabel("动作", { exact: true })
      .selectOption("firearm");
    await shotDialog
      .getByLabel("目标", { exact: true })
      .selectOption(combatNpc.id);
    await shotDialog
      .getByLabel("使用的持有物品", { exact: true })
      .selectOption(gunId);
    await shotDialog.getByRole("button", { name: "提交申报" }).click();
    const shotRequest = JSON.parse(
      framesOf(gunFrames.sent, "action_request").at(-1)!,
    );
    expect(shotRequest.action).toMatchObject({
      kind: "combat",
      action_type: "firearm",
      weapon_item_id: gunId,
    });
    await expect
      .poll(() =>
        framesOf(gunFrames.received, "action_ack").some(
          (raw) =>
            JSON.parse(raw).payload.request_id === shotRequest.request_id,
        ),
      )
      .toBe(true);
    await keeper.getByTestId("btn-keeper-console").click();
    const shotPending = keeper
      .getByTestId("keeper-pending-request")
      .filter({ hasText: shotRequest.request_id });
    await shotPending.getByRole("button", { name: "准备战斗动作" }).click();
    await expect(keeper.getByLabel("武器物品")).toHaveValue(gunId);
    await setKeeperField(keeper, "damage_spec", "1d2");
    const preparedShot = await submitKeeperCommand(keeper, keeperFrames);
    expect(preparedShot.accepted, preparedShot.lastError).toBe(true);
    await keeper.getByRole("button", { name: "关闭主持台" }).click();
    const gunCard = gunPage.getByTestId("combat-field-record");
    if (latestCombat().awaiting_decision) {
      const decision = JSON.parse(
        framesOf(gunFrames.received, "combat_decision_required").at(-1)!,
      ).payload;
      const proceed = decision.options.find(
        (o: { id: string }) => o.id === "confirm_violence",
      );
      await gunCard
        .getByRole("button", { name: proceed.label, exact: true })
        .click();
    }
    await expect(gunCard).toContainText("批准时选定：.38口径左轮手枪（6发）");
    // A committed keeper injury must update the exact character and encounter,
    // retire old dice consent live and after reconnect, and not leak the full
    // private stat projection to the other player. No shot has occurred yet.
    const shooterBefore = latestCombat().participants.find(
      (p: { id: string }) => p.id === shooter,
    );
    const oldShotRoll = JSON.parse(
      framesOf(gunFrames.received, "combat_roll_required").at(-1)!,
    ).payload.roll_id;
    const otherStatEvents = framesOf(
      otherGunFrames.received,
      "state_changed",
    ).length;
    const shotReceiptsBefore = framesOf(
      gunFrames.received,
      "combat_roll_resolved",
    ).length;
    await keeper.getByTestId("btn-keeper-console").click();
    await keeper.getByTestId("keeper-cmd-adjust_stat").click();
    await setKeeperField(keeper, "investigator_id", shooter);
    await setKeeperField(keeper, "field", "hp");
    await setKeeperField(keeper, "delta", "-1");
    await setKeeperField(
      keeper,
      "reason",
      "主持确认旧伤恶化，需要重新批准本次动作。",
    );
    const injured = await submitKeeperCommand(keeper, keeperFrames);
    expect(injured.accepted, injured.lastError).toBe(true);
    await expect(
      gunCard.getByRole("button", { name: "掷骰", exact: true }),
    ).toHaveCount(0);
    await expect
      .poll(
        () =>
          latestCombat().participants.find(
            (p: { id: string }) => p.id === shooter,
          ).hp,
      )
      .toBe(shooterBefore.hp - 1);
    expect(framesOf(otherGunFrames.received, "state_changed")).toHaveLength(
      otherStatEvents,
    );
    expect(framesOf(gunFrames.received, "combat_roll_resolved")).toHaveLength(
      shotReceiptsBefore,
    );
    expect(framesOf(otherGunFrames.received, "inventory_changed")).toHaveLength(
      otherInventoryEvents,
    );
    await gunPage.reload();
    await expect(gunPage.getByTestId("structured-tool-row")).toBeVisible({
      timeout: 90_000,
    });
    await expect
      .poll(
        () =>
          JSON.parse(framesOf(gunFrames.received, "session_snapshot").at(-1)!)
            .payload.character.hp,
      )
      .toBe(shooterBefore.hp - 1);
    const injurySnapshot = JSON.parse(
      framesOf(gunFrames.received, "session_snapshot").at(-1)!,
    ).payload;
    expect(injurySnapshot.combat_roll).toBeNull();
    expect(
      injurySnapshot.items.find((item: { id: string }) => item.id === gunId)
        .label,
    ).toBe(".38口径左轮手枪（6发）");
    if (
      (await gunPage.locator("#char-panel").getAttribute("class"))?.includes(
        "collapsed",
      )
    ) {
      await gunPage.locator("#btn-panel").click();
    }
    await expect(gunPage.locator("#hp-bar")).toContainText(
      `${shooterBefore.hp - 1} / ${shooterBefore.max_hp}`,
    );
    await gunPage.screenshot({
      path: "../docs/design/platform-ui/combat-vitals-synced.png",
      fullPage: true,
    });
    // Human clinical records use real sheets and commands, not database seeding
    // or narrative inference. Adding/removing prone never heals or fires a gun.
    await expect(
      keeper.getByRole("dialog", { name: "主持工作台" }),
    ).toBeVisible();
    await keeper.getByTestId("keeper-cmd-record_condition").click();
    await setKeeperField(keeper, "investigator_id", shooter);
    await setKeeperField(keeper, "condition", "prone");
    await setKeeperField(keeper, "operation", "add");
    const clinicalBasis = `主持确认调查员暂时倒地，未改变生命值。clinical-private-${runId}`;
    await setKeeperField(keeper, "basis", clinicalBasis);
    const clinicalForm = keeper.getByRole("region", { name: "命令表单" });
    await expect(keeper.getByLabel("本次核对的状态")).toHaveText("未记录");
    const privateStatsBefore = framesOf(
      otherGunFrames.received,
      "state_changed",
    ).length;
    const rollsBeforeRecord = framesOf(
      gunFrames.received,
      "combat_roll_resolved",
    ).length;
    for (const width of [1280, 939, 640, 390]) {
      await keeper.setViewportSize({ width, height: 900 });
      for (const field of ["investigator_id", "condition", "operation"]) {
        const select = keeper.locator(`[data-field="${field}"] select`);
        await select.scrollIntoViewIfNeeded();
        expect((await select.boundingBox())!.height).toBeGreaterThanOrEqual(44);
      }
      await keeper.getByLabel("人物状态参考").scrollIntoViewIfNeeded();
      await expect(keeper.getByLabel("人物状态参考")).toContainText(
        `HP ${shooterBefore.hp - 1}`,
      );
      await keeper.screenshot({
        path: `../docs/design/platform-ui/condition-record-reading-${width}.png`,
      });
      for (const control of [
        keeper.getByRole("button", { name: "核对当前记录" }),
        keeper.getByRole("button", { name: "记录变更" }),
      ]) {
        await control.scrollIntoViewIfNeeded();
        const geometry = await control.evaluate((el) => {
          const rect = el.getBoundingClientRect(),
            style = getComputedStyle(el);
          return {
            height: rect.height,
            padding: parseFloat(style.paddingLeft),
            nowrap: style.whiteSpace,
            hit: el.contains(
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
        expect(geometry.hit).toBe(true);
      }
      expect(
        await keeper.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      ).toBe(true);
      await keeper.screenshot({
        path: `../docs/design/platform-ui/condition-record-${width}.png`,
      });
    }
    await keeper.setViewportSize({ width: 390, height: 360 });
    await keeper
      .getByRole("button", { name: "记录变更" })
      .scrollIntoViewIfNeeded();
    const shortButton = await keeper
      .getByRole("button", { name: "记录变更" })
      .boundingBox();
    expect(shortButton!.y).toBeGreaterThanOrEqual(0);
    expect(shortButton!.y + shortButton!.height).toBeLessThanOrEqual(360);
    await keeper.screenshot({
      path: "../docs/design/platform-ui/condition-record-390-short.png",
    });
    await keeper.setViewportSize({ width: 1280, height: 900 });
    expect((await submitKeeperCommand(keeper, keeperFrames)).accepted).toBe(
      true,
    );
    await expect
      .poll(
        () =>
          latestCombat().participants.find(
            (p: { id: string }) => p.id === shooter,
          ).conditions,
      )
      .toContain("prone");
    expect(
      latestCombat().participants.find((p: { id: string }) => p.id === shooter)
        .hp,
    ).toBe(shooterBefore.hp - 1);
    // The captured false remains frozen after the live true arrives.
    await expect(keeper.getByLabel("本次核对的状态")).toHaveText("未记录");
    await expect(clinicalForm).toContainText("记录已变化");
    await keeper.getByRole("button", { name: "核对当前记录" }).click();
    await setKeeperField(keeper, "operation", "remove");
    await setKeeperField(
      keeper,
      "basis",
      "调查员已站起，主持只移除倒地标记，不补生命。",
    );
    expect((await submitKeeperCommand(keeper, keeperFrames)).accepted).toBe(
      true,
    );
    await expect
      .poll(
        () =>
          latestCombat().participants.find(
            (p: { id: string }) => p.id === shooter,
          ).conditions,
      )
      .not.toContain("prone");
    expect(framesOf(gunFrames.received, "combat_roll_resolved")).toHaveLength(
      rollsBeforeRecord,
    );
    expect(framesOf(otherGunFrames.received, "state_changed")).toHaveLength(
      privateStatsBefore,
    );
    expect(framesOf(otherGunFrames.received, "inventory_changed")).toHaveLength(
      otherInventoryEvents,
    );
    expect(otherGunFrames.received.join(" ")).not.toContain(clinicalBasis);
    await keeper.getByRole("button", { name: "关闭主持台" }).click();
    const snapshotsBeforeClinicalReload = framesOf(
      gunFrames.received,
      "session_snapshot",
    ).length;
    await gunPage.reload();
    await expect
      .poll(() => framesOf(gunFrames.received, "session_snapshot").length)
      .toBeGreaterThan(snapshotsBeforeClinicalReload);
    await expect
      .poll(() => {
        const character = JSON.parse(
          framesOf(gunFrames.received, "session_snapshot").at(-1)!,
        ).payload.character;
        return {
          hp: character.hp,
          prone: character.conditions.includes("prone"),
        };
      })
      .toEqual({ hp: shooterBefore.hp - 1, prone: false });
    // The original request is still a task, not implicitly marked successful.
    // Re-approval is explicit and creates fresh consent bound to the same gun.
    await keeper.getByTestId("btn-keeper-console").click();
    await shotPending.getByRole("button", { name: "准备战斗动作" }).click();
    await setKeeperField(keeper, "damage_spec", "1d2");
    const approvedAgain = await submitKeeperCommand(keeper, keeperFrames);
    expect(approvedAgain.accepted, approvedAgain.lastError).toBe(true);
    await keeper.getByRole("button", { name: "关闭主持台" }).click();
    if (latestCombat().awaiting_decision) {
      const freshDecision = JSON.parse(
        framesOf(gunFrames.received, "combat_decision_required").at(-1)!,
      ).payload;
      const proceed = freshDecision.options.find(
        (o: { id: string }) => o.id === "confirm_violence",
      );
      await gunCard
        .getByRole("button", { name: proceed.label, exact: true })
        .click();
    }
    await expect(gunCard).toContainText("批准时选定：.38口径左轮手枪（6发）");
    const freshShotRoll = JSON.parse(
      framesOf(gunFrames.received, "combat_roll_required").at(-1)!,
    ).payload.roll_id;
    expect(freshShotRoll).not.toBe(oldShotRoll);
    await gunCard.getByRole("button", { name: "掷骰", exact: true }).click();
    await expect
      .poll(() =>
        framesOf(gunFrames.received, "inventory_changed").some((raw) =>
          JSON.parse(raw).payload.items?.some(
            (item: { id: string; label: string }) =>
              item.id === gunId && item.label === ".38口径左轮手枪（5发）",
          ),
        ),
      )
      .toBe(true);
    expect(framesOf(otherGunFrames.received, "inventory_changed")).toHaveLength(
      otherInventoryEvents,
    );
    const shotResult = JSON.parse(
      framesOf(gunFrames.received, "combat_roll_resolved").at(-1)!,
    ).payload.result;
    expect(shotResult.action_type).toBe("firearm");
    await keeper.getByTestId("btn-keeper-console").click();
    await keeper.getByTestId("keeper-cmd-resolve_intent").click();
    await setKeeperField(keeper, "request_id", shotRequest.request_id);
    await setKeeperField(keeper, "resolution", "completed");
    await setKeeperField(
      keeper,
      "outcome",
      shotResult.outcome === "attacker_hit" ? "success" : "failure",
    );
    const resolvedShot = await submitKeeperCommand(keeper, keeperFrames);
    expect(resolvedShot.accepted, resolvedShot.lastError).toBe(true);
    await keeper.getByRole("button", { name: "关闭主持台" }).click();
    await keeper.getByTestId("btn-keeper-console").click();
    await keeper.getByTestId("keeper-cmd-combat_end").click();
    await setKeeperField(keeper, "reason", "双方停手，由主持结束本次遭遇。");
    const stoppedCombat = await submitKeeperCommand(keeper, keeperFrames);
    expect(stoppedCombat.accepted, stoppedCombat.lastError).toBe(true);
    await keeper.getByRole("button", { name: "关闭主持台" }).click();
    for (const page of [keeper, playerA, playerB]) {
      await expect(page.getByTestId("combat-field-record")).toHaveCount(0);
    }

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

    // Player-to-player confrontation: both controllers participate, the target
    // chooses defence, then separately readies dice before the attacker's roll.
    // No model and no DB rewriting; use the actual cards/initiative.
    await keeper.getByRole("button", { name: "返回游戏", exact: true }).click();
    await keeper.getByTestId("btn-keeper-console").click();
    await keeper.getByTestId("keeper-cmd-combat_start").click();
    await setKeeperField(keeper, "participants", playerAInvestigator);
    const startedPvp = await submitKeeperCommand(keeper, keeperFrames);
    expect(startedPvp.accepted, startedPvp.lastError).toBe(true);
    const pvpActorId = latestCombat().current_actor;
    const pvpActorPage = pvpActorId === playerAInvestigator ? playerA : playerB;
    const pvpTargetPage = pvpActorPage === playerA ? playerB : playerA;
    const pvpTargetId = latestCombat().participants.find(
      (p: { id: string; kind: string }) =>
        p.kind === "pc" && p.id !== pvpActorId,
    )?.id;
    expect(pvpTargetId, "真实遭遇里没有另一名调查员").toBeTruthy();
    const pvpTargetFrames =
      pvpTargetPage === playerA ? playerAFrames : playerBFrames;
    const pvpActorFrames =
      pvpActorPage === playerA ? playerAFrames : playerBFrames;
    const pvpHpBefore = latestCombat().participants.map(
      (p: { id: string; hp: number }) => [p.id, p.hp],
    );
    await pvpActorPage
      .getByRole("button", { name: "申报战斗动作", exact: true })
      .click();
    const combatDeclaration = pvpActorPage.getByRole("dialog", {
      name: "申报战斗动作",
    });
    await combatDeclaration
      .getByLabel("目标", { exact: true })
      .selectOption(pvpTargetId);
    await combatDeclaration
      .getByLabel("补充做法（选填）")
      .fill("我提出与同伴进行对抗，先等双方确认。");
    const beforeDraftIntent = framesOf(
      pvpActorFrames.sent,
      "action_request",
    ).length;
    await combatDeclaration
      .getByRole("button", { name: "提交申报", exact: true })
      .click();
    await expect
      .poll(() => framesOf(pvpActorFrames.sent, "action_request").length)
      .toBeGreaterThan(beforeDraftIntent);
    const declarationFrame = JSON.parse(
      framesOf(pvpActorFrames.sent, "action_request").at(-1)!,
    );
    expect(declarationFrame.action).toMatchObject({
      kind: "combat",
      action_type: "melee",
      target_id: pvpTargetId,
      encounter_id: latestCombat().encounter_id,
    });
    const draftRequest = declarationFrame.request_id;
    await expect
      .poll(() =>
        framesOf(pvpActorFrames.received, "action_ack").some(
          (raw) => JSON.parse(raw).payload.request_id === draftRequest,
        ),
      )
      .toBe(true);
    const draftWorldId = JSON.parse(
      framesOf(keeperFrames.received, "session_snapshot").at(-1)!,
    ).world_id;
    const scriptedDraft = produceAssistedCombatDraft(
      draftWorldId,
      draftRequest,
      {
        actor_id: pvpActorId,
        target_id: pvpTargetId,
        action_type: "melee",
        damage_spec: "1d3",
      },
    );
    // Reconnect restores the real persisted draft. No production model is used.
    await keeper.reload();
    const draftReview = keeper.getByTestId("keeper-draft-card");
    await expect(draftReview).toContainText("验收：准备对抗，等待双方响应。", {
      timeout: 90000,
    });
    await expect(draftReview).toContainText("批准战斗动作");
    expect(
      latestCombat().participants.map((p: { id: string; hp: number }) => [
        p.id,
        p.hp,
      ]),
    ).toEqual(pvpHpBefore);
    await expect(
      pvpActorPage.getByRole("button", { name: "申报战斗动作", exact: true }),
    ).toBeDisabled();
    await draftReview.getByTestId("draft-approve").click();
    await expect(draftReview).toHaveCount(0);
    expect(
      JSON.parse(framesOf(keeperFrames.sent, "command_request").at(-1)!),
    ).toMatchObject({
      kind: "resolve_draft",
      payload: { draft_id: scriptedDraft.draft_id, decision: "approved" },
    });
    const pvpActorCard = pvpActorPage.getByTestId("combat-field-record");
    const pvpTargetCard = pvpTargetPage.getByTestId("combat-field-record");
    await expect(
      pvpActorCard.getByRole("button", { name: "同意参与对抗" }),
    ).toBeEnabled();
    await expect(pvpTargetCard.getByRole("button")).toHaveCount(0);
    await expect(
      keeper.getByTestId("combat-field-record").getByRole("button"),
    ).toHaveCount(0);
    await pvpActorCard.getByRole("button", { name: "同意参与对抗" }).click();
    await pvpTargetCard.getByRole("button", { name: "同意参与对抗" }).click();
    await pvpTargetCard
      .getByRole("button", { name: "闪避", exact: true })
      .click();
    await expect(pvpTargetCard).toContainText("双方确认前不会产生骰点");
    const pvpTargetResultCount = framesOf(
      pvpTargetFrames.received,
      "combat_roll_resolved",
    ).length;
    await pvpTargetCard
      .getByRole("button", { name: "确认掷骰", exact: true })
      .click();
    await expect(
      pvpActorCard.getByRole("button", { name: "确认掷骰", exact: true }),
    ).toBeEnabled();
    expect(
      latestCombat().participants.map((p: { id: string; hp: number }) => [
        p.id,
        p.hp,
      ]),
    ).toEqual(pvpHpBefore);
    const readyFrames = () =>
      framesOf(pvpTargetFrames.received, "combat_roll_resolved")
        .slice(pvpTargetResultCount)
        .map((raw) => JSON.parse(raw).payload);
    // The actor's socket can receive its waiting card before the target's
    // independent socket delivers this receipt. Wait for the target's OWN
    // committed event; do not retry an action or weaken the exact-count check.
    await expect.poll(readyFrames).toHaveLength(1);
    const readyReceipt = readyFrames()[0];
    expect(readyReceipt).not.toHaveProperty("result");
    expect(readyReceipt.roll_id).toBe(
      JSON.parse(framesOf(pvpTargetFrames.sent, "command_request").at(-1)!)
        .payload.roll_id,
    );
    await pvpActorPage.reload();
    await expect(
      pvpActorPage
        .getByTestId("combat-field-record")
        .getByRole("button", { name: "确认掷骰", exact: true }),
    ).toBeEnabled({ timeout: 90000 });
    await pvpActorPage
      .getByTestId("combat-field-record")
      .getByRole("button", { name: "确认掷骰", exact: true })
      .click();
    for (const [page, frames, id] of [
      [pvpActorPage, pvpActorFrames, pvpActorId],
      [pvpTargetPage, pvpTargetFrames, pvpTargetId],
    ] as const) {
      await expect
        .poll(() =>
          framesOf(frames.received, "combat_roll_resolved").some((raw) => {
            const result = JSON.parse(raw).payload.result;
            return (
              result?.investigator_id === id &&
              result?.target_id === pvpTargetId
            );
          }),
        )
        .toBe(true);
      await expect(
        page
          .getByTestId("combat-field-record")
          .getByRole("button")
          .filter({ hasNotText: "申报战斗动作" }),
      ).toHaveCount(0);
    }
    await keeper.getByTestId("btn-keeper-console").click();
    await keeper.getByTestId("keeper-cmd-combat_end").click();
    await setKeeperField(keeper, "reason", "双方结束这次对抗，转入案件收尾。");
    const closedPvp = await submitKeeperCommand(keeper, keeperFrames);
    expect(closedPvp.accepted, closedPvp.lastError).toBe(true);
    // A combat result does not infer completion of arbitrary freeform intent.
    // Here the human explicitly finishes the stated exchange after ending it.
    await keeper.getByTestId("keeper-cmd-resolve_intent").click();
    await setKeeperField(keeper, "request_id", draftRequest);
    await setKeeperField(keeper, "resolution", "completed");
    await setKeeperField(keeper, "outcome", "success");
    await setKeeperField(
      keeper,
      "note",
      "双方已完成这次对抗并停手，原申请由主持明确收尾。",
    );
    const resolvedDraftIntent = await submitKeeperCommand(keeper, keeperFrames);
    expect(resolvedDraftIntent.accepted, resolvedDraftIntent.lastError).toBe(
      true,
    );
    await expect(
      pvpActorPage.getByTestId("structured-interaction-card"),
    ).toHaveCount(0);
    await keeper.getByRole("button", { name: "关闭主持台" }).click();

    // A real authored neutral ending: both players explicitly abandon the case,
    // the human keeper narrates/rules the fact, then settles it through end_game.
    // This is not a claim of completing the full scarlet story/harness.
    const departureRequests: string[] = [];
    for (const [page, frames] of [
      [playerA, playerAFrames],
      [playerB, playerBFrames],
    ] as const) {
      await page
        .locator("#user-input")
        .fill("我确认放弃这次调查，与同伴一起离开阿卡姆。");
      await page.locator("#btn-send").click();
      await expect
        .poll(() => framesOf(frames.sent, "action_request").length)
        .toBeGreaterThan(0);
      departureRequests.push(
        JSON.parse(framesOf(frames.sent, "action_request").at(-1)!).request_id,
      );
    }
    await keeper.getByTestId("btn-keeper-console").click();
    await keeper.getByTestId("keeper-cmd-publish_message").click();
    await setKeeperField(
      keeper,
      "text",
      "你们一致决定收手，离开阿卡姆，把未解之谜留在身后。",
    );
    const closingNarration = await submitKeeperCommand(keeper, keeperFrames);
    expect(closingNarration.accepted, closingNarration.lastError).toBe(true);
    for (const requestId of departureRequests) {
      await keeper.getByTestId("keeper-cmd-resolve_intent").click();
      await setKeeperField(keeper, "request_id", requestId);
      await setKeeperField(keeper, "resolution", "completed");
      await setKeeperField(keeper, "outcome", "success");
      const closedIntent = await submitKeeperCommand(keeper, keeperFrames);
      expect(closedIntent.accepted, closedIntent.lastError).toBe(true);
    }
    // Real outstanding work remains when the group decides to end. It must be
    // cancelled transactionally, not falsely reported as a successful check.
    await playerA.locator("#user-input").fill("离开前我还想核对刚才的记录。");
    const actionsBeforeClosure = framesOf(
      playerAFrames.sent,
      "action_request",
    ).length;
    await playerA.locator("#btn-send").click();
    await expect
      .poll(() => framesOf(playerAFrames.sent, "action_request").length)
      .toBeGreaterThan(actionsBeforeClosure);
    const remainingRequest = JSON.parse(
      framesOf(playerAFrames.sent, "action_request").at(-1)!,
    ).request_id;
    await keeper.getByTestId("keeper-cmd-resolve_intent").click();
    await setKeeperField(keeper, "request_id", remainingRequest);
    await setKeeperField(keeper, "resolution", "awaiting_player");
    await setKeeperField(keeper, "pending_action_kind", "freeform");
    await setKeeperField(keeper, "pending_action_note", "核对记录，尚未执行");
    const awaitingClosure = await submitKeeperCommand(keeper, keeperFrames);
    expect(awaitingClosure.accepted, awaitingClosure.lastError).toBe(true);
    await expect(
      playerA.getByTestId("structured-interaction-card"),
    ).toBeVisible();
    await keeper.getByTestId("keeper-cmd-request_check").click();
    await setKeeperField(keeper, "investigator_id", playerAInvestigator);
    await setKeeperField(keeper, "skill", skillOptions[0]);
    await setKeeperField(keeper, "attempt", "尚未落实的核对");
    await setKeeperField(keeper, "visibility", "public");
    const pendingClosureCheck = await submitKeeperCommand(keeper, keeperFrames);
    expect(pendingClosureCheck.accepted, pendingClosureCheck.lastError).toBe(
      true,
    );
    await expect(
      playerA.locator('.check-request-card[data-status="pending"]'),
    ).toBeVisible();
    const resolvedChecksBeforeClosure = framesOf(
      playerAFrames.received,
      "check_resolved",
    ).length;
    const audit = keeper.getByTestId("keeper-ruling-audit");
    await audit.locator(":scope > summary").click();
    const authoredLeave = audit.locator('[data-ending-id="leave_arkham"]');
    await expect(authoredLeave).toContainText("条件未齐");
    await expect(
      authoredLeave.getByRole("button", { name: "准备结算" }),
    ).toBeDisabled();
    await authoredLeave.locator(".ending-condition-details > summary").click();
    await expect(authoredLeave).toContainText("investigation_abandoned");
    await keeper.getByTestId("keeper-cmd-record_ruling").click();
    await setKeeperField(keeper, "flag_id", "investigation_abandoned");
    await keeper.getByRole("checkbox", { name: /裁定后的状态/ }).check();
    const privateRulingBasis = `两位玩家明确同意离开；主持已叙述落实。ruling-private-${runId}`;
    await setKeeperField(keeper, "basis", privateRulingBasis);
    const ruledDeparture = await submitKeeperCommand(keeper, keeperFrames);
    expect(ruledDeparture.accepted, ruledDeparture.lastError).toBe(true);
    await expect(audit).toContainText("逃离阿卡姆");
    await expect(authoredLeave).toContainText("条件已齐");
    const beforeEndingPreparation = framesOf(
      keeperFrames.sent,
      "command_request",
    ).length;
    await authoredLeave.getByRole("button", { name: "准备结算" }).click();
    expect(framesOf(keeperFrames.sent, "command_request")).toHaveLength(
      beforeEndingPreparation,
    );
    await expect(keeper.getByLabel("模组结局 ID")).toHaveValue("leave_arkham");
    const endedCase = await submitKeeperCommand(keeper, keeperFrames);
    expect(endedCase.accepted, endedCase.lastError).toBe(true);
    for (const frames of [playerAFrames, playerBFrames]) {
      expect(framesOf(frames.received, "ending_catalog_updated")).toHaveLength(
        0,
      );
      expect(framesOf(frames.received, "ruling_recorded")).toHaveLength(0);
      for (const raw of framesOf(frames.received, "session_snapshot")) {
        expect(JSON.parse(raw).payload).not.toHaveProperty("keeper_rulings");
      }
    }
    await keeper.getByRole("button", { name: "关闭主持台" }).click();
    for (const page of [keeper, playerA, playerB]) {
      await expect(
        page.locator('.check-request-card[data-status="pending"]'),
      ).toHaveCount(0);
      await expect(page.getByTestId("structured-interaction-card")).toHaveCount(
        0,
      );
    }
    await expect(
      playerA.locator(
        '.action-status-card[data-request-id="' + remainingRequest + '"]',
      ),
    ).toContainText("剩余事项不再执行");
    expect(framesOf(playerAFrames.received, "check_resolved")).toHaveLength(
      resolvedChecksBeforeClosure,
    );
    for (const page of [keeper, playerA, playerB]) {
      await expect(page.getByTestId("combat-ending-record")).toContainText(
        "逃离阿卡姆",
      );
    }
    for (const page of [playerA, playerB]) {
      await expect(page.getByTestId("combat-ending-record")).toContainText(
        "本案声望变化",
      );
      await page.reload();
      await expect(page.getByTestId("combat-ending-record")).toContainText(
        "逃离阿卡姆",
        { timeout: 90_000 },
      );
    }
    // Each player's explicit save creates their own library copy. A keeper
    // without a character never gets a substitute save button for the party.
    await expect(keeper.getByTestId("case-character-actions")).toHaveCount(0);
    for (const [page, name] of [
      [playerA, "玩家甲的结案副本"],
      [playerB, "玩家乙的结案副本"],
    ] as const) {
      const card = page.getByTestId("case-character-actions");
      await expect(card.getByLabel("新角色名")).toBeEnabled({ timeout: 30000 });
      await card.getByLabel("新角色名").fill(name);
      await card.getByRole("button", { name: "保存为新角色" }).click();
      await expect(
        card.getByRole("button", { name: "已保存", exact: true }),
      ).toBeDisabled({ timeout: 30000 });
      const ownEntries = await page.evaluate(
        async () =>
          (
            await (
              await fetch("/api/character-library", { credentials: "include" })
            ).json()
          ).entries,
      );
      expect(
        ownEntries.filter((entry: { name: string }) => entry.name === name),
      ).toHaveLength(1);
      const otherName =
        name === "玩家甲的结案副本" ? "玩家乙的结案副本" : "玩家甲的结案副本";
      expect(
        ownEntries.some((entry: { name: string }) => entry.name === otherName),
      ).toBe(false);
      await page.reload();
      await expect(
        page
          .getByTestId("case-character-actions")
          .getByRole("button", { name: "已保存", exact: true }),
      ).toBeDisabled({ timeout: 90000 });
    }
    for (const frames of [playerAFrames, playerBFrames, viewerFrames]) {
      expect(framesOf(frames.received, "ruling_recorded")).toHaveLength(0);
      expect(
        frames.received.some((frame) => frame.includes(privateRulingBasis)),
      ).toBe(false);
    }
    // The earlier reconnect deliberately resets this optional dock to collapsed.
    const roomDockToggle = keeper.locator(".online-room-dock-toggle");
    if ((await roomDockToggle.getAttribute("aria-expanded")) !== "true")
      await roomDockToggle.click();
    await expect(roomDockToggle).toHaveAttribute("aria-expanded", "true");
    await keeper.getByRole("button", { name: "房间管理" }).click();

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
