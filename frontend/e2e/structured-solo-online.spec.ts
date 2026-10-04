/**
 * 云端单人（账号化私密世界）的结构化模式验收。
 *
 * 覆盖需求「本地、云端单人和多人均暴露相应模式和按钮，human 不被模型设置门禁挡住」：
 * - 云端单人创建冒险时可勾选“结构化操作模式（人类主持）”；
 * - human 结构化世界**不需要 BYOK**：不配置任何模型也能直接开局（对比：
 *   `model-settings-online.spec.ts` 证明 legacy/BYOK 世界未配置会被拒）；
 * - 按钮发出的是 M0 结构请求（前往 → action_request、掷骰 → free_roll_request、
 *   主持台 → command_request），位置只由已提交的 scene_changed 更新；
 * - 全程零模型调用（模型 base URL 指向关闭端口）。
 */

import { spawn, spawnSync, type ChildProcess } from "node:child_process";
import { randomUUID } from "node:crypto";
import { existsSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

import {
  expect,
  request,
  test,
  type Page,
  type WebSocketRoute,
} from "@playwright/test";

import { assertGameHeaderFits } from "./header-layout";

const port = 8757;
const baseUrl = `https://127.0.0.1:${port}`;
const repositoryRoot = resolve(import.meta.dirname, "../..");
const runId = `${Date.now()}-${randomUUID().slice(0, 8)}`;
let runtimeRoot = "";
let server: ChildProcess | null = null;
let serverOutput = "";

async function waitForServer(): Promise<void> {
  const client = await request.newContext();
  try {
    for (let attempt = 0; attempt < 100; attempt += 1) {
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
  runtimeRoot = mkdtempSync(join(tmpdir(), "trpg-structured-solo-online-"));
  const certificate = join(runtimeRoot, "certificate.pem");
  const privateKey = join(runtimeRoot, "private-key.pem");
  const generated = spawnSync(
    "openssl",
    [
      "req",
      "-x509",
      "-newkey",
      "rsa:2048",
      "-nodes",
      "-days",
      "1",
      "-subj",
      "/CN=127.0.0.1",
      "-keyout",
      privateKey,
      "-out",
      certificate,
    ],
    { stdio: "ignore" },
  );
  if (generated.status !== 0) {
    throw new Error("Failed to generate the temporary E2E TLS certificate");
  }
  const python =
    process.env.TRPG_E2E_PYTHON ??
    (existsSync(resolve(repositoryRoot, ".venv/bin/python"))
      ? resolve(repositoryRoot, ".venv/bin/python")
      : "python");
  server = spawn(
    python,
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
      "--ssl-certfile",
      certificate,
      "--ssl-keyfile",
      privateKey,
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
        TRPG_REQUIRE_AUTH: "1",
        TRPG_ALLOW_REGISTRATION: "1",
        TRPG_ALLOWED_ORIGINS: baseUrl,
        TRPG_WRITE_COMPAT_EXPORTS: "0",
        // 结构化 human 世界不建模型会话；指到必死地址，被调用即测试失败。
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

function framesOf(frames: string[], type: string): string[] {
  return frames.filter((frame) => frame.includes(`"type":"${type}"`));
}

test("云端单人结构化：勾选后无 Key 开局，按钮发结构请求且零模型调用", async ({
  page,
}) => {
  test.setTimeout(300_000);
  page.setDefaultTimeout(20_000);
  const frames = collectFrames(page);
  let roomSocket: WebSocketRoute | null = null;
  let holdSnapshot = false;
  const heldSnapshots: (string | Buffer)[] = [];
  await page.routeWebSocket(/\/ws\/room\?/, (client) => {
    roomSocket = client;
    const upstream = client.connectToServer();
    upstream.onMessage((message) => {
      if (
        holdSnapshot &&
        JSON.parse(String(message)).type === "session_snapshot"
      ) {
        heldSnapshots.push(message);
      } else {
        client.send(message);
      }
    });
  });

  // ---- 注册并进入“我的冒险”（云端单人意图从模式选择页进入） ----
  await page.goto(`${baseUrl}/`);
  await page.getByRole("button", { name: /云端单人/ }).click();
  await page.getByRole("tab", { name: "注册" }).click();
  const username = `solo_struct_${runId}`;
  await page.getByLabel("用户名").fill(username);
  await page
    .getByLabel("密码", { exact: true })
    .fill("structured solo password");
  await page.getByLabel("确认密码").fill("structured solo password");
  await page.getByRole("button", { name: "注册并登录" }).click();
  await expect(page.getByRole("heading", { name: "我的冒险" })).toBeVisible({
    timeout: 30_000,
  });

  // ---- 创建冒险：勾选结构化操作模式（人类主持） ----
  await page.getByText("开始新冒险", { exact: true }).first().click();
  await page.locator(".module-select-trigger").click();
  await page.getByRole("option", { name: /猩红文档/ }).click();
  await expect(
    page.getByRole("radio", { name: "人类主持" }),
    "云端单人创建卡没有人类主持入口",
  ).toBeVisible();
  await page.getByRole("radio", { name: "人类主持" }).click();
  for (const width of [1280, 939, 640]) {
    await page.setViewportSize({ width, height: 900 });
    const heights = await page
      .locator(".lobby-form > input, .lobby-form .module-select-trigger")
      .evaluateAll((nodes) =>
        nodes.map((node) => node.getBoundingClientRect().height),
      );
    for (const height of heights) expect(height).toBeLessThan(65);
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth + 1,
      ),
    ).toBe(true);
    await page.screenshot({
      path: `/tmp/trpg-flow-ui-solo-create-${width}.png`,
      fullPage: true,
    });
  }
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.getByRole("button", { name: /创建冒险/ }).click();

  // ---- 选角页：human 结构化世界不需要 BYOK，直接开局 ----
  const confirmCharacter = page.locator("#btn-character-confirm");
  await expect(confirmCharacter).toBeVisible({ timeout: 30_000 });
  for (const viewport of [
    { width: 1280, height: 720 },
    { width: 939, height: 640 },
    { width: 640, height: 480 },
    { width: 390, height: 360 },
  ]) {
    await page.setViewportSize(viewport);
    await page.waitForTimeout(500);
    const first = page.locator(".character-card").first();
    const bounds = await first.evaluate((node) => {
      const box = node.getBoundingClientRect();
      const list = node
        .closest("#character-choice-list")!
        .getBoundingClientRect();
      return {
        top: box.top,
        bottom: box.bottom,
        listTop: list.top,
        listBottom: list.bottom,
        height: box.height,
        hit: node.contains(
          document.elementFromPoint(
            box.x + box.width / 2,
            box.y + box.height / 2,
          ),
        ),
      };
    });
    expect(bounds.top).toBeGreaterThanOrEqual(bounds.listTop);
    expect(bounds.bottom).toBeLessThanOrEqual(bounds.listBottom);
    expect(bounds.height).toBeGreaterThanOrEqual(44);
    expect(bounds.hit).toBe(true);
    const chooser = page.getByTestId("solo-character-select");
    expect(
      await chooser
        .locator("#character-detail")
        .evaluate((node) => node.clientHeight),
    ).toBeGreaterThanOrEqual(72);
    expect(
      await chooser
        .locator(".online-conn-hint")
        .evaluate((node) => getComputedStyle(node).whiteSpace),
    ).toBe("nowrap");
    for (const label of ["角色库", "模型设置"]) {
      const geometry = await chooser
        .getByRole("button", { name: label, exact: true })
        .evaluate((node) => {
          const box = node.getBoundingClientRect();
          return {
            height: box.height,
            top: box.top,
            bottom: box.bottom,
            right: box.right,
            hit: node.contains(
              document.elementFromPoint(
                box.x + box.width / 2,
                box.y + box.height / 2,
              ),
            ),
          };
        });
      expect(geometry.height).toBeGreaterThanOrEqual(44);
      expect(geometry.top).toBeGreaterThanOrEqual(0);
      expect(geometry.bottom).toBeLessThanOrEqual(viewport.height);
      expect(geometry.right).toBeLessThanOrEqual(viewport.width);
      expect(geometry.hit).toBe(true);
    }
    await page.screenshot({
      path: `/tmp/trpg-cloud-character-choice-${viewport.width}.png`,
    });
  }
  await page.locator(".character-card").first().click();
  await expect(confirmCharacter).toBeEnabled();
  const confirmBounds = await confirmCharacter.evaluate((node) => {
    const box = node.getBoundingClientRect();
    return {
      height: box.height,
      bottom: box.bottom,
      hit: node.contains(
        document.elementFromPoint(
          box.x + box.width / 2,
          box.y + box.height / 2,
        ),
      ),
    };
  });
  expect(confirmBounds.height).toBeGreaterThanOrEqual(44);
  expect(confirmBounds.bottom).toBeLessThanOrEqual(360);
  expect(confirmBounds.hit).toBe(true);
  await page.screenshot({ path: "/tmp/trpg-cloud-character-claimed-390.png" });
  await confirmCharacter.click();

  // 结构化入口出现即说明服务端给出了 structured_v1 能力协商。
  await expect(page.getByTestId("structured-tool-row")).toBeVisible({
    timeout: 60_000,
  });
  await expect(page.getByTestId("btn-move")).toBeVisible();
  await expect(page.getByTestId("btn-keeper-console")).toBeVisible();
  // Being the keeper must not erase the cloud solo player's claimed card.
  await expect(page.locator("#hp-bar")).not.toContainText("--");
  await expect(page.getByTestId("btn-free-roll")).toBeEnabled();
  // 没有被模型门禁挡住：不出现门禁引导按钮（.model-gate-cta）与“尚未配置”提示。
  // 顶栏的“模型设置”按钮是通用入口，任何模式都在，不算门禁。
  await expect(page.getByText(/尚未配置/)).toHaveCount(0);
  await expect(page.locator(".model-gate-cta")).toHaveCount(0);

  // 服务端确实下发了快照与能力协商。
  expect(
    frames.received.filter((frame) => frame.includes("session_snapshot")),
  ).not.toHaveLength(0);
  expect(
    frames.received.filter((frame) => frame.includes("server_capabilities")),
  ).not.toHaveLength(0);

  // Real server reconnection, with only the authoritative member snapshot delayed.
  // The existing character/scene must not make the new socket actionable early.
  const recoveredScene = await page.locator(".header-scene-name").innerText();
  holdSnapshot = true;
  expect(roomSocket).not.toBeNull();
  await roomSocket!.close({
    code: 1012,
    reason: "isolated reconnect verification",
  });
  await expect.poll(() => heldSnapshots.length).toBeGreaterThan(0);
  await expect(page.getByTestId("btn-free-roll")).toBeDisabled();
  await expect(page.getByTestId("btn-move")).toBeDisabled();
  expect(framesOf(frames.sent, "action_request")).toHaveLength(0);
  await expect(page.locator(".header-scene-name")).toHaveText(recoveredScene);
  for (const width of [1280, 939, 640, 390]) {
    await page.setViewportSize({ width, height: 900 });
    await assertGameHeaderFits(page);
    await expect(
      page.getByText("正在连接并同步权威状态，完成后可提交。", { exact: true }),
    ).toBeVisible();
    const rollGeometry = await page
      .getByTestId("btn-free-roll")
      .evaluate((node) => {
        const rect = node.getBoundingClientRect();
        const style = getComputedStyle(node);
        return {
          width: rect.width,
          height: rect.height,
          padding: parseFloat(style.paddingLeft),
          whiteSpace: style.whiteSpace,
        };
      });
    expect(rollGeometry.width).toBeGreaterThan(50);
    expect(rollGeometry.height).toBeGreaterThan(25);
    expect(rollGeometry.padding).toBeGreaterThan(0);
    expect(rollGeometry.whiteSpace).toBe("nowrap");
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth + 1,
      ),
    ).toBe(true);
    await page.screenshot({ path: `/tmp/trpg-room-sync-waiting-${width}.png` });
  }
  await page.setViewportSize({ width: 1440, height: 900 });
  holdSnapshot = false;
  for (const snapshot of heldSnapshots.splice(0)) roomSocket!.send(snapshot);
  await expect(page.getByTestId("btn-free-roll")).toBeEnabled();
  await expect(page.getByTestId("btn-move")).toBeEnabled();
  expect(framesOf(frames.sent, "action_request")).toHaveLength(0);

  // ---- 前往：action_request{kind:move}，位置由已提交场景事件更新 ----
  const sceneBefore = await page.locator(".header-scene-name").innerText();
  await page.getByTestId("btn-move").click();
  const moveDialog = page.getByRole("dialog", { name: "前往…" });
  await expect(moveDialog).toBeVisible();
  await moveDialog.locator(".structured-destination").first().click();
  await expect
    .poll(() => framesOf(frames.sent, "action_request").length, {
      timeout: 30_000,
      message: "前往按钮没有发出 action_request",
    })
    .toBe(1);
  const moveFrame = JSON.parse(framesOf(frames.sent, "action_request")[0]) as {
    type: string;
    protocol_version: number;
    request_id: string;
    action: Record<string, unknown>;
  };
  expect(moveFrame.type).toBe("action_request");
  expect(moveFrame.protocol_version).toBe(1);
  expect(moveFrame.action.kind).toBe("move");
  // 权威通道里没有自然语言行动句。
  expect(JSON.stringify(moveFrame)).not.toContain("我前往");
  // human 主持世界里，玩家的结构请求进入**主持待办**，不自动执行：
  // 位置必须由主持的 move_party 命令结算后才变化。
  await expect
    .poll(async () => page.locator(".header-scene-name").innerText(), {
      timeout: 5_000,
    })
    .toBe(sceneBefore);
  expect(framesOf(frames.received, "scene_changed")).toHaveLength(0);
  const queuedMove = page.locator(
    `[data-request-id="${moveFrame.request_id}"]`,
  );
  await expect(queuedMove).toContainText("已收件，待守秘人处理");
  await expect(queuedMove).toContainText("还未执行行动，不需要重复提交");
  await expect(
    queuedMove.getByRole("button", { name: "申请取消" }),
  ).toBeEnabled();
  await expect(queuedMove.getByRole("button")).toHaveCount(1);
  await expect(queuedMove).not.toContainText("结果：成功");
  for (const width of [1280, 939, 640]) {
    await page.setViewportSize({ width, height: 900 });
    await queuedMove.scrollIntoViewIfNeeded();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    await page.screenshot({ path: `/tmp/trpg-request-received-${width}.png` });
  }
  await page.setViewportSize({ width: 1440, height: 900 });

  await page.getByTestId("btn-keeper-console").click();
  const console_ = page.getByRole("dialog", { name: "主持工作台" });
  await expect(console_).toBeVisible();
  // 待处理行动里能看到玩家刚才的移动请求（主持要处理它）。
  await expect(console_).toContainText("前往");
  await page.getByTestId("keeper-cmd-move_party").click();
  const sceneOptions = await page
    .locator('[data-field="destination_scene_id"] select option')
    .evaluateAll((nodes) =>
      nodes
        .map((node) => (node as HTMLOptionElement).value)
        .filter((value) => value.length > 0),
    );
  expect(sceneOptions, "主持台没有可选目的地").toContain("miskatonic_medical");
  await page
    .locator('[data-field="destination_scene_id"] select')
    .selectOption("miskatonic_medical");
  await page.getByTestId("keeper-submit").click();
  await expect
    .poll(async () => page.locator(".header-scene-name").innerText(), {
      timeout: 30_000,
      message: "主持的整队移动没有更新公开场景",
    })
    .not.toBe(sceneBefore);
  await expect(page.locator(".header-scene-name")).toHaveText(/医学院/, {
    timeout: 15_000,
  });
  // 抵达不等于调查：移动本身不发线索、不结算 SAN。
  expect(framesOf(frames.received, "clue_granted")).toHaveLength(0);

  // ---- 掷骰：free_roll_request + 服务端结算 ----
  // 主持台是模态浮层，先收起再操作工具栏。
  await page.getByRole("button", { name: "关闭主持台" }).click();
  await page.getByTestId("btn-free-roll").click();
  await page.getByTestId("roll-confirm").click();
  await expect
    .poll(() => framesOf(frames.sent, "free_roll_request").length, {
      timeout: 30_000,
      message: "掷骰入口没有发出 free_roll_request",
    })
    .toBe(1);
  await expect
    .poll(() => framesOf(frames.received, "roll_resolved").length, {
      timeout: 30_000,
    })
    .toBeGreaterThan(0);
  await expect(page.getByText(/普通掷骰 1d100 →/)).toBeVisible({
    timeout: 30_000,
  });

  // ---- 主持台：单人房主即主持，命令走 command_request ----
  await page.getByTestId("btn-keeper-console").click();
  await expect(page.getByRole("dialog", { name: "主持工作台" })).toBeVisible();
  // 本用例此前已发过一条 move_party：按增量断言，避免绝对计数互串。
  const commandsBefore = framesOf(frames.sent, "command_request").length;
  await page.getByTestId("keeper-cmd-advance_time").click();
  await page.locator('[data-field="minutes"] input').fill("30");
  await page.locator('[data-field="reason"] input').fill("驱车前往医学院");
  await page.getByTestId("keeper-submit").click();
  await expect
    .poll(() => framesOf(frames.sent, "command_request").length, {
      timeout: 30_000,
      message: "主持命令没有发出 command_request",
    })
    .toBeGreaterThan(commandsBefore);
  const commandFrame = JSON.parse(
    framesOf(frames.sent, "command_request").slice(-1)[0],
  ) as {
    kind: string;
    command_id: string;
    payload: Record<string, unknown>;
  };
  expect(commandFrame.kind).toBe("advance_time");
  expect(commandFrame.command_id).toMatch(/^cmd-/);
  expect(commandFrame.payload).toEqual({
    minutes: 30,
    reason: "驱车前往医学院",
  });

  // ---- 零模型调用：页面上没有模型/连通性错误 ----
  await expect(
    page.getByText(/无法连接配置的模型|模型.*不可用|connect.*fail/i),
  ).toHaveCount(0);
  // legacy 文字回合入口在结构化世界里被关闭，前端也不会退回它。
  expect(framesOf(frames.sent, "action")).toHaveLength(0);

  // Real UI + real server history recovery, not mocked event injection.
  // Public narrative exceeds one page; reloading must recover the newest 50
  // and older reads must neither resubmit commands nor replay dice/actions.
  await page.getByTestId("keeper-cmd-publish_message").click();
  await page
    .locator('[data-field="speaker_kind"] select')
    .selectOption("keeper");
  await page
    .locator('[data-field="audience_kind"] select')
    .selectOption("public");
  for (let index = 0; index < 53; index += 1) {
    const text = `历史验收记录 ${String(index).padStart(2, "0")}：钟声落在案卷之间。`;
    await page.locator('[data-field="text"] textarea').fill(text);
    const before = framesOf(frames.sent, "command_request").length;
    await page.getByTestId("keeper-submit").click();
    await expect
      .poll(() => framesOf(frames.sent, "command_request").length)
      .toBe(before + 1);
    const command = JSON.parse(
      framesOf(frames.sent, "command_request").at(-1)!,
    );
    await expect
      .poll(() =>
        framesOf(frames.received, "action_status").some((frame) => {
          const value = JSON.parse(frame);
          return (
            value.payload?.request_id === command.command_id &&
            value.payload?.status === "completed"
          );
        }),
      )
      .toBe(true);
    await expect(page.getByTestId("keeper-submit")).toBeEnabled();
  }
  await page.getByRole("button", { name: "关闭主持台" }).click();
  await expect(
    page.locator(".msg.gm").filter({ hasText: "历史验收记录" }),
  ).toHaveCount(53);
  await expect(page.locator(".msg.gm.streaming-cursor")).toHaveCount(0);
  await page.reload();
  await expect(page.locator(".boot-loader")).toHaveCount(0);
  // Refresh intentionally returns to mode selection. Re-enter through the
  // real private-adventure continuation path, not multiplayer auto-resume.
  await page.getByRole("button", { name: /云端单人/ }).click();
  await expect(page.getByTestId("solo-lobby")).toBeVisible();
  await page.getByRole("button", { name: "继续冒险", exact: true }).click();
  // Capture the actual settled game, not the shell's permitted exit animation.
  await expect(page.getByTestId("online-shell")).toHaveCount(0);
  await expect(page.locator("#messages")).toContainText("历史验收记录 52");
  await expect(page.locator("#messages")).not.toContainText("历史验收记录 00");
  const older = page.getByRole("button", { name: "载入更早叙事" });
  for (const width of [1280, 939, 640]) {
    await page.setViewportSize({ width, height: 900 });
    await older.scrollIntoViewIfNeeded();
    await expect(older).toBeVisible();
    const layout = await older.evaluate((button) => {
      const rect = button.getBoundingClientRect();
      const style = getComputedStyle(button);
      const hit = document.elementFromPoint(
        rect.x + rect.width / 2,
        rect.y + rect.height / 2,
      );
      return {
        height: rect.height,
        padding: parseFloat(style.paddingLeft),
        nowrap: style.whiteSpace,
        hit: hit === button || button.contains(hit),
        overflow: document.documentElement.scrollWidth > innerWidth,
      };
    });
    expect(layout.height).toBeGreaterThanOrEqual(42);
    expect(layout.padding).toBeGreaterThanOrEqual(13);
    expect(layout.nowrap).toBe("nowrap");
    expect(layout.hit).toBe(true);
    expect(layout.overflow).toBe(false);
    await page.screenshot({ path: `/tmp/trpg-history-control-${width}.png` });
  }
  const commandCount = framesOf(frames.sent, "command_request").length;
  const diceCount = framesOf(frames.received, "roll_resolved").length;
  await older.click();
  await expect(page.locator("#messages")).toContainText("历史验收记录 00");
  await expect
    .poll(() =>
      page.locator("#messages").evaluate((element) => element.scrollTop),
    )
    .toBe(0);
  await expect(older).toHaveCount(0);
  expect(framesOf(frames.sent, "command_request")).toHaveLength(commandCount);
  expect(framesOf(frames.received, "roll_resolved")).toHaveLength(diceCount);
  expect(await page.locator("#messages").innerText()).toMatch(
    /历史验收记录 00[\s\S]*历史验收记录 52/,
  );
});

async function checkArchiveLayout(page: Page, surface: string) {
  const confirmation = page.getByTestId("adventure-archive-confirm");
  for (const width of [1280, 939, 640, 390]) {
    await page.setViewportSize({
      width,
      height: surface === "game" ? (width === 390 ? 360 : 480) : 900,
    });
    await expect(page.locator(".boot-loader")).toHaveCount(0);
    await confirmation.scrollIntoViewIfNeeded();
    await expect(confirmation).toBeVisible();
    for (const button of await confirmation.getByRole("button").all()) {
      const layout = await button.evaluate((node) => {
        const rect = node.getBoundingClientRect();
        const style = getComputedStyle(node);
        const hit = document.elementFromPoint(
          rect.x + rect.width / 2,
          rect.y + rect.height / 2,
        );
        return {
          height: rect.height,
          padding: parseFloat(style.paddingLeft),
          whiteSpace: style.whiteSpace,
          hit: hit === node || node.contains(hit),
          overflow: document.documentElement.scrollWidth > innerWidth,
        };
      });
      expect(layout.height).toBeGreaterThanOrEqual(44);
      expect(layout.padding).toBeGreaterThanOrEqual(18);
      expect(layout.whiteSpace).toBe("nowrap");
      expect(layout.hit).toBe(true);
      expect(layout.overflow).toBe(false);
    }
    if (surface === "game") {
      const keep = confirmation.getByRole("button", {
        name: "继续保留",
        exact: true,
      });
      const archive = confirmation.getByRole("button", {
        name: "确认归档",
        exact: true,
      });
      await archive.focus();
      await page.keyboard.press("Tab");
      await expect(keep).toBeFocused();
      await page.keyboard.press("Shift+Tab");
      await expect(archive).toBeFocused();
      const before = await keep.boundingBox();
      await confirmation.locator(".adventure-archive-body").evaluate((node) => {
        node.scrollTop = node.scrollHeight;
      });
      expect((await keep.boundingBox())?.y).toBe(before?.y);
    }
    await page.waitForTimeout(1000); // screenshot the settled UI, not its exit/entry transition
    await page.screenshot({
      path: `/tmp/trpg-archive-${surface}-${width}.png`,
    });
  }
}

async function checkAccountLayout(page: Page) {
  const account = page.locator(".solo-lobby-user");
  for (const width of [1280, 939, 640, 390]) {
    await page.setViewportSize({ width, height: 900 });
    await account.scrollIntoViewIfNeeded();
    await expect(page.locator(".boot-loader")).toHaveCount(0);
    await page.waitForTimeout(1000); // settle the lobby's entry transform before measuring
    for (const button of await account.getByRole("button").all()) {
      const geometry = await button.evaluate((node) => {
        const rect = node.getBoundingClientRect();
        const style = getComputedStyle(node);
        return {
          height: rect.height,
          whiteSpace: style.whiteSpace,
          padding: parseFloat(style.paddingLeft),
          hit: node.contains(
            document.elementFromPoint(
              rect.x + rect.width / 2,
              rect.y + rect.height / 2,
            ),
          ),
        };
      });
      expect(geometry.height).toBeGreaterThanOrEqual(44);
      expect(geometry.padding).toBeGreaterThanOrEqual(12);
      expect(geometry.whiteSpace).toBe("nowrap");
      expect(geometry.hit).toBe(true);
    }
    const name = account.locator(".online-user");
    await expect(name).toHaveAttribute("title", await name.innerText());
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    await page.waitForTimeout(1000);
    await page.screenshot({ path: `/tmp/trpg-account-layout-${width}.png` });
  }
}

test("账号过期不泄漏旧资料；重新登录保留单人入口；归档失败可重试且归档整棵分支", async ({
  page,
}) => {
  test.setTimeout(300_000);
  page.setDefaultTimeout(20_000);
  const frames = collectFrames(page);
  const username = `privacy_${runId}`;
  const password = "isolated account test password";
  await page.goto(baseUrl);
  await page.getByRole("button", { name: /云端单人/ }).click();
  await page.getByRole("tab", { name: "注册" }).click();
  await page.getByLabel("用户名").fill(username);
  await page.getByLabel("密码", { exact: true }).fill(password);
  await page.getByLabel("确认密码").fill(password);
  await page.getByRole("button", { name: "注册并登录" }).click();
  await expect(page.getByTestId("solo-lobby")).toBeVisible();
  await checkAccountLayout(page);
  await page.getByText("开始新冒险", { exact: true }).first().click();
  await page.locator(".module-select-trigger").click();
  await page.getByRole("option", { name: /猩红文档/ }).click();
  await page.getByRole("radio", { name: "人类主持" }).click();
  await page.getByRole("button", { name: /创建冒险/ }).click();
  await expect(page.locator("#btn-character-confirm")).toBeVisible();
  await page.locator(".character-card").first().click();
  await page.locator("#btn-character-confirm").click();
  await expect(page.getByTestId("online-shell")).toHaveCount(0);
  await expect(page.getByTestId("btn-keeper-console")).toBeVisible();
  const rootWorldId = await page.evaluate(() =>
    localStorage.getItem("trpg-online-world-id"),
  );
  expect(rootWorldId).toBeTruthy();

  // Create an actual branch through the product UI; archive is not just a row removal.
  await page.getByTestId("btn-keeper-console").click();
  await page.getByTestId("keeper-save").click();
  await expect
    .poll(() =>
      framesOf(frames.received, "saved").some((frame) => JSON.parse(frame).ok),
    )
    .toBe(true);
  await page.getByTestId("keeper-save-panel").click();
  await expect(page.locator("#save-panel-overlay")).toBeVisible();
  await page
    .locator(".adventure-card.current .adventure-manage")
    .first()
    .click();
  await expect(page.getByTestId("save-panel-timelines")).toBeVisible();
  await page.locator(".timeline-branch-input").fill("隐私与归档验收分支");
  await page.locator(".timeline-branch-create").click();
  await expect
    .poll(() => framesOf(frames.received, "solo_world_switched").length)
    .toBeGreaterThan(0);
  await expect
    .poll(() =>
      page.evaluate(() => localStorage.getItem("trpg-online-world-id")),
    )
    .not.toBe(rootWorldId);
  const branchWorldId = await page.evaluate(() =>
    localStorage.getItem("trpg-online-world-id"),
  );
  // The world switch closes the old private console; only the save overlay reopens.
  await expect(page.getByTestId("save-panel-timelines")).toBeVisible();
  await page.locator("#save-panel-close").click();
  await expect(page.getByRole("dialog", { name: "主持工作台" })).toHaveCount(0);
  await page.getByTestId("btn-keeper-console").click();
  const library = page.getByTestId("keeper-library");
  await library.getByRole("button", { name: /打开资料库/ }).click();
  await expect(library).toContainText("仅主持可见");

  // Expire the real cookie, then let a real on-demand HTTP request notify the app.
  await page.context().clearCookies();
  await library.getByRole("button", { name: "模组手册", exact: true }).click();
  await expect(
    page.getByRole("alert").filter({ hasText: "登录已过期，请重新登录" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "云端单人", exact: true }),
  ).toBeVisible();
  await expect(page.getByTestId("keeper-library")).toHaveCount(0);
  await expect(page.getByRole("dialog", { name: "主持工作台" })).toHaveCount(0);
  await expect(page.locator("#messages .msg")).toHaveCount(0);
  for (const width of [1280, 939, 640]) {
    await page.setViewportSize({ width, height: 900 });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    await page.screenshot({ path: `/tmp/trpg-account-expired-${width}.png` });
  }
  await page.getByLabel("用户名").fill(username);
  await page.getByLabel("密码", { exact: true }).fill(password);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page.getByRole("heading", { name: "我的冒险" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "联机大厅" })).toHaveCount(0);
  const archiveInfo = page.locator(".solo-lobby .adventure-card-info");
  await archiveInfo.focus();
  await archiveInfo.press("Enter");
  await expect(
    page.getByRole("button", { name: "关闭时间线面板" }),
  ).toBeVisible();
  // Real backend timeline data: keyboard cancellation is not a rename/delete.
  const timeline = page.locator(".solo-timeline-dialog");
  const timelineClose = timeline.getByRole("button", {
    name: "关闭时间线面板",
  });
  let timelineMutations = 0;
  const countTimelineMutation = (req: import("@playwright/test").Request) => {
    if (
      req.method() !== "GET" &&
      /\/timelines\/(rename|archive|switch)$/.test(new URL(req.url()).pathname)
    )
      timelineMutations += 1;
  };
  page.on("request", countTimelineMutation);
  for (const width of [1280, 939, 640, 390]) {
    await page.setViewportSize({ width, height: 480 });
    await expect(timelineClose).toBeInViewport();
    const rename = timeline.locator(".timeline-rename").first();
    await rename.click();
    const input = timeline.getByLabel("时间线名称");
    await expect(input).toBeFocused();
    await input.fill("取消后不得保存的新名字");
    for (const button of [
      timelineClose,
      timeline.getByRole("button", { name: "确认重命名时间线" }),
      timeline.getByRole("button", { name: "取消重命名时间线" }),
    ]) {
      const box = await button.evaluate((node) => {
        const rect = node.getBoundingClientRect();
        const style = getComputedStyle(node);
        return {
          height: rect.height,
          width: rect.width,
          padding: parseFloat(style.paddingLeft),
          bottom: rect.bottom,
          right: rect.right,
          left: rect.left,
          hit: node.contains(
            document.elementFromPoint(
              rect.x + rect.width / 2,
              rect.y + rect.height / 2,
            ),
          ),
        };
      });
      expect(box.height).toBeGreaterThanOrEqual(44);
      expect(box.width).toBeGreaterThanOrEqual(44);
      expect(box.padding).toBeGreaterThanOrEqual(10);
      expect(box.bottom).toBeLessThanOrEqual(480);
      expect(box.left).toBeGreaterThanOrEqual(0);
      expect(box.right).toBeLessThanOrEqual(width);
      expect(box.hit).toBe(true);
    }
    await page.screenshot({ path: `/tmp/trpg-timeline-edit-${width}.png` });
    await input.press("Escape");
    await expect(input).toHaveCount(0);
    await expect(timeline).toBeVisible();
    await expect(rename).toBeFocused();
    const last = timeline.locator("button:enabled").last();
    await last.focus();
    await page.keyboard.press("Tab");
    await expect(timelineClose).toBeFocused();
    await page.keyboard.press("Shift+Tab");
    await expect(last).toBeFocused();
    await timeline.locator(".solo-timeline-body").evaluate((node) => {
      node.scrollTop = node.scrollHeight;
    });
    await expect(timelineClose).toBeInViewport();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    await page.screenshot({ path: `/tmp/trpg-timeline-list-${width}.png` });
    await timeline.locator(".solo-timeline-body").evaluate((node) => {
      node.scrollTop = 0;
    });
  }
  expect(timelineMutations).toBe(0);
  page.off("request", countTimelineMutation);
  await page.keyboard.press("Escape");
  await expect(timeline).toHaveCount(0);
  await expect(archiveInfo).toBeFocused();
  await page.setViewportSize({ width: 1440, height: 900 });
  await archiveInfo.press("Enter");
  await expect(timelineClose).toBeVisible();
  await page.getByRole("button", { name: "关闭时间线面板" }).click();
  await page.getByRole("button", { name: "继续冒险", exact: true }).click();
  await expect(page.getByTestId("online-shell")).toHaveCount(0);
  await expect(page.getByTestId("btn-keeper-console")).toBeVisible();

  let archiveRequests = 0;
  page.on("request", (req) => {
    if (req.url().endsWith("/abandon")) archiveRequests += 1;
  });
  await page.getByRole("button", { name: "离开当前冒险", exact: true }).click();
  await page
    .getByRole("button", { name: "放弃并归档冒险", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "继续保留", exact: true }),
  ).toBeFocused();
  await checkArchiveLayout(page, "game");
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("adventure-archive-confirm")).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "继续调查", exact: true }),
  ).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(page.locator(".solo-adventure-exit-dialog")).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "离开当前冒险", exact: true }),
  ).toBeFocused();
  expect(archiveRequests).toBe(0);
  await page.getByRole("button", { name: "离开当前冒险", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "继续调查", exact: true }),
  ).toBeFocused();
  await page.getByRole("button", { name: "返回我的冒险（保留进度）" }).click();
  await expect(page.getByTestId("solo-lobby")).toBeVisible();
  const treeResponse = await page.request.get(
    `${baseUrl}/api/worlds/${rootWorldId}/timelines`,
  );
  expect(treeResponse.ok()).toBe(true);
  const tree = await treeResponse.json();
  expect(
    tree.worlds.map((world: { world_id: string }) => world.world_id),
  ).toEqual(expect.arrayContaining([rootWorldId, branchWorldId]));
  await page.getByRole("button", { name: "归档冒险", exact: true }).click();
  await checkArchiveLayout(page, "lobby");
  await page.getByRole("button", { name: "继续保留", exact: true }).click();
  expect(archiveRequests).toBe(0);

  // A real rejected HTTP operation must not remove the card or close confirmation.
  await page.route("**/api/worlds/*/abandon", (route) =>
    route.fulfill({
      status: 409,
      contentType: "application/json",
      body: JSON.stringify({
        error: "归档验收：操作被拒，请重试",
        error_code: "test_conflict",
      }),
    }),
  );
  await page.getByRole("button", { name: "归档冒险", exact: true }).click();
  await page.getByRole("button", { name: "确认归档", exact: true }).click();
  await expect(
    page.getByTestId("adventure-archive-confirm").getByRole("alert"),
  ).toHaveText("归档验收：操作被拒，请重试");
  await expect(
    page.getByRole("button", { name: "继续冒险", exact: true }),
  ).toBeEnabled();
  await page.unroute("**/api/worlds/*/abandon");
  await page.getByRole("button", { name: "确认归档", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "继续冒险", exact: true }),
  ).toHaveCount(0);
  await expect(page.getByTestId("adventure-archive-confirm")).toHaveCount(0);
  expect(archiveRequests).toBe(2);
  // Server evidence: neither root nor child remains accessible after archive.
  for (const id of [rootWorldId, branchWorldId]) {
    const response = await page.request.get(
      `${baseUrl}/api/worlds/${id}/timelines`,
    );
    expect(response.status()).toBe(404);
    expect((await response.json()).code).toBe("world_not_found");
  }
  // Read only the isolated fixture database: both records survive as archived.
  // A missing route/physical deletion cannot masquerade as successful archival.
  const archivedRows = spawnSync(
    process.env.TRPG_E2E_PYTHON ?? resolve(repositoryRoot, ".venv/bin/python"),
    [
      "-c",
      'import sqlite3,sys,json; db=sqlite3.connect("file:"+sys.argv[1]+"?mode=ro",uri=True); print(json.dumps(db.execute("select id,status from worlds where id in (?,?)",sys.argv[2:]).fetchall())); db.close()',
      join(runtimeRoot, "e2e.db"),
      rootWorldId!,
      branchWorldId!,
    ],
    { encoding: "utf8" },
  );
  expect(archivedRows.status).toBe(0);
  expect(JSON.parse(archivedRows.stdout)).toEqual(
    expect.arrayContaining([
      [rootWorldId, "archived"],
      [branchWorldId, "archived"],
    ]),
  );
  await page.route("**/api/auth/logout", (route) =>
    route.fulfill({
      status: 500,
      contentType: "application/json",
      body: JSON.stringify({ error: "退出验收：服务不可用" }),
    }),
  );
  await page.getByRole("button", { name: "退出登录", exact: true }).click();
  await expect(page.getByRole("heading", { name: "我的冒险" })).toBeVisible();
  await expect(page.getByRole("alert")).toContainText("退出验收：服务不可用");
  await page.unroute("**/api/auth/logout");
  await page.getByRole("button", { name: "退出登录", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "云端单人", exact: true }),
  ).toBeVisible();
  expect(framesOf(frames.sent, "action")).toHaveLength(0);
});
