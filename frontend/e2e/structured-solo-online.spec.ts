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

import { expect, request, test, type Page } from "@playwright/test";

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
    page.getByLabel(/结构化操作模式/),
    "云端单人创建卡没有结构化模式开关",
  ).toBeVisible();
  await page.getByLabel(/结构化操作模式/).check();
  await page.getByRole("button", { name: /创建冒险/ }).click();

  // ---- 选角页：human 结构化世界不需要 BYOK，直接开局 ----
  const confirmCharacter = page.locator("#btn-character-confirm");
  await expect(confirmCharacter).toBeVisible({ timeout: 30_000 });
  await page.locator(".character-card").first().click();
  await expect(confirmCharacter).toBeEnabled();
  await confirmCharacter.click();

  // 结构化入口出现即说明服务端给出了 structured_v1 能力协商。
  await expect(page.getByTestId("structured-tool-row")).toBeVisible({
    timeout: 60_000,
  });
  await expect(page.getByTestId("btn-move")).toBeVisible();
  await expect(page.getByTestId("btn-keeper-console")).toBeVisible();
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

  await page.getByTestId("btn-keeper-console").click();
  const console_ = page.getByRole("dialog", { name: "主持台" });
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
  await expect(page.getByRole("dialog", { name: "主持台" })).toBeVisible();
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
});
