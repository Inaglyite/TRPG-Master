/**
 * 后端缺口 #2 的独立、可重复验收：**云端（账号化单人房间）的结构化世界分支**。
 *
 * 背景（Kimi 负责修，见交付记录 §9.4.2）：`solo_branch_create` 要求非空 `turn_id`
 * （`src/multiplayer/solo_timeline_ws.py`），而结构化世界没有 Turn 记录 —— 云端
 * 结构化世界因此无法创建分支（本地路径已通：`world_timeline_ws.py` 从当前已提交
 * 状态分叉）。
 *
 * 本文件是可执行验收（不是 fixme）：房主在云端单人结构化世界里点「从当前进度创建
 * 分支」→ **真的**创建出分支（`turn_branched`，新 world_id，位置与记忆继承）。
 * 前端不伪造 turn_id：没有回合就不带这个字段（见 panels-owner-guard 的单测）。
 *
 * 后端修复落地前这条会真的失败，并且失败信息里带服务端的实际拒绝码与文案；
 * 修复后应自动转绿，无需改动本文件。
 *
 * 权限契约不变：多人房间不出现该入口（服务端也会拒 solo_* 消息）；时间线能力
 * 仍由 `timelineCapabilities()`（solo + 房主）决定。
 */

import { spawn, spawnSync, type ChildProcess } from "node:child_process";
import { randomUUID } from "node:crypto";
import { createServer, type Server } from "node:http";
import { existsSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

import { expect, request, test, type Page } from "@playwright/test";

const port = 8782;
const baseUrl = `https://127.0.0.1:${port}`;
const repositoryRoot = resolve(import.meta.dirname, "../..");
const screenshotsDir = resolve(repositoryRoot, "docs/screenshots");
const runId = `${Date.now()}-${randomUUID().slice(0, 8)}`;
let runtimeRoot = "";
let server: ChildProcess | null = null;
let serverOutput = "";
let modelServer: Server | null = null;
let modelBaseUrl = "";
const modelRequests: string[] = [];

/** 会计数的模型桩：legacy 世界需要它产出开场回合；结构化 human 世界不调用。 */
async function startModelStub(): Promise<string> {
  modelServer = createServer((incoming, response) => {
    if (incoming.method !== "POST" || incoming.url !== "/v1/chat/completions") {
      response.writeHead(404).end();
      return;
    }
    let body = "";
    incoming.on("data", (chunk) => {
      body += String(chunk);
    });
    incoming.once("end", () => {
      modelRequests.push(body.slice(0, 200));
      const chunk = {
        id: "chatcmpl-branch-online",
        object: "chat.completion.chunk",
        created: 1,
        model: "e2e-model",
        choices: [
          {
            index: 0,
            delta: { role: "assistant", content: "雨幕笼罩着阿卡姆。" },
            finish_reason: null,
          },
        ],
      };
      const terminal = {
        ...chunk,
        choices: [{ index: 0, delta: {}, finish_reason: "stop" }],
      };
      response.writeHead(200, {
        "Content-Type": "text/event-stream; charset=utf-8",
        "Cache-Control": "no-cache",
        Connection: "close",
      });
      response.write(`data: ${JSON.stringify(chunk)}\n\n`);
      response.write(`data: ${JSON.stringify(terminal)}\n\n`);
      response.end("data: [DONE]\n\n");
    });
  });
  await new Promise<void>((resolveListen, rejectListen) => {
    modelServer!.once("error", rejectListen);
    modelServer!.listen(0, "127.0.0.1", () => resolveListen());
  });
  const address = modelServer.address();
  if (!address || typeof address === "string") {
    throw new Error("model stub did not expose a TCP address");
  }
  return `http://127.0.0.1:${address.port}/v1`;
}

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
  runtimeRoot = mkdtempSync(join(tmpdir(), "trpg-branch-online-"));
  modelBaseUrl = await startModelStub();
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
        // 云端 legay 世界的开源转需要 BYOK；桩监听在回环地址，白名单放行它。
        TRPG_EGRESS_ALLOWED_PRIVATE_HOSTS: "127.0.0.1",
        OPENAI_API_KEY: "e2e-placeholder",
        // legacy 云端世界用模型桩产出开场回合；结构化 human 世界不调用模型
        // （结构化那条用例末尾断言开局之后零调用）。
        OPENAI_BASE_URL: modelBaseUrl,
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
  if (modelServer) {
    await new Promise<void>((resolveClose) =>
      modelServer!.close(() => resolveClose()),
    );
  }
  if (runtimeRoot) rmSync(runtimeRoot, { recursive: true, force: true });
});

type Frames = { sent: string[]; received: string[] };

function collectFrames(page: Page): Frames {
  const sent: string[] = [];
  const received: string[] = [];
  page.on("websocket", (socket) => {
    socket.on("framesent", (event) => sent.push(String(event.payload)));
    socket.on("framereceived", (event) => received.push(String(event.payload)));
  });
  return { sent, received };
}

function framesOf(frames: Frames, type: string): string[] {
  return frames.received.filter((frame) => frame.includes(`"type":"${type}"`));
}

async function registerAndOpenAdventures(page: Page, username: string) {
  await page.goto(`${baseUrl}/`);
  await page.getByRole("button", { name: /云端单人/ }).click();
  await page.getByRole("tab", { name: "注册" }).click();
  await page.getByLabel("用户名").fill(username);
  await page
    .getByLabel("密码", { exact: true })
    .fill("structured branch password");
  await page.getByLabel("确认密码").fill("structured branch password");
  await page.getByRole("button", { name: "注册并登录" }).click();
  await expect(page.getByRole("heading", { name: "我的冒险" })).toBeVisible({
    timeout: 30_000,
  });
}

/** 云端单人结构化世界：注册 → 建冒险（勾选结构化）→ 开局。 */
async function bootCloudStructured(page: Page): Promise<string> {
  await registerAndOpenAdventures(page, `branch_owner_${runId}`);

  await page.getByText("开始新冒险", { exact: true }).first().click();
  await page.locator(".module-select-trigger").click();
  await page.getByRole("option", { name: /猩红文档/ }).click();
  await expect(page.getByLabel(/结构化操作模式/)).toBeVisible();
  await page.getByLabel(/结构化操作模式/).check();
  await page.getByRole("button", { name: /创建冒险/ }).click();

  const confirmCharacter = page.locator("#btn-character-confirm");
  await expect(confirmCharacter).toBeVisible({ timeout: 30_000 });
  await page.locator(".character-card").first().click();
  await expect(confirmCharacter).toBeEnabled();
  await confirmCharacter.click();
  await expect(page.getByTestId("structured-tool-row")).toBeVisible({
    timeout: 90_000,
  });
  return page.url();
}

test("云端单人结构化：从当前进度创建分支真的成功（不依赖 legacy turn_id）", async ({
  page,
}) => {
  test.setTimeout(420_000);
  page.setDefaultTimeout(30_000);
  const frames = collectFrames(page);
  await bootCloudStructured(page);
  const sourceWorldId = await page.evaluate(
    () => localStorage.getItem("trpg-active-world-id") || "",
  );
  expect(sourceWorldId).not.toBe("");
  const modelCallsAfterBoot = modelRequests.length;
  const sceneBefore = await page.locator(".header-scene-name").innerText();

  // 房主（solo）：先存一次档（云端的存档管理面板在没有存档位时是空态，
  // 时间线/分支管理挂在存档条目下），再打开存档管理 → 管理时间线。
  await page.getByTestId("btn-keeper-console").click();
  await expect(page.getByRole("dialog", { name: "主持台" })).toBeVisible();
  const savedBefore = framesOf(frames, "saved").length;
  await page.getByTestId("keeper-save").click();
  await expect
    .poll(() => framesOf(frames, "saved").length, { timeout: 60_000 })
    .toBeGreaterThan(savedBefore);
  const saved = JSON.parse(framesOf(frames, "saved").at(-1) as string) as {
    ok?: boolean;
    slot_id?: string;
  };
  expect(saved.ok, `云端单人存档失败：${JSON.stringify(saved)}`).toBe(true);

  await page.getByTestId("keeper-save-panel").click();
  await expect(page.locator("#save-panel-overlay")).toBeVisible({
    timeout: 30_000,
  });
  await page
    .locator(".adventure-card.current .adventure-manage")
    .first()
    .click();
  await expect(page.getByTestId("save-panel-timelines")).toBeVisible({
    timeout: 30_000,
  });
  const branchButton = page.locator(".timeline-branch-create");
  await expect(
    branchButton,
    "云端单人结构化世界必须能点到「从当前进度创建分支」",
  ).toBeVisible({ timeout: 30_000 });
  await page.locator(".timeline-branch-input").fill("云端分支");
  await page.screenshot({
    path: `${screenshotsDir}/branch-online-entry.png`,
  });

  const rejectedBefore = framesOf(frames, "room_action_rejected").length;
  const switchedBefore = framesOf(frames, "solo_world_switched").length;
  await branchButton.click();

  // 云端单人分支的契约：服务端建分支 → 拆除房间 → 连接以 4412 重连到新分支世界。
  // 所以这里等的是 solo_world_switched（本地路径才是 turn_branched），并确认没被拒。
  await expect
    .poll(() => framesOf(frames, "solo_world_switched").length, {
      timeout: 120_000,
    })
    .toBeGreaterThan(switchedBefore);
  expect(
    framesOf(frames, "room_action_rejected").length,
    `云端结构化分支被拒（服务端响应）：${
      framesOf(frames, "room_action_rejected").at(-1) ?? ""
    }`,
  ).toBe(rejectedBefore);

  // 真的进入了新分支世界：会话记录的当前世界带 -branch- 标记且不等于原世界
  await expect
    .poll(
      () =>
        page.evaluate(() => localStorage.getItem("trpg-active-world-id") || ""),
      { timeout: 120_000 },
    )
    .toContain("-branch-");
  const branchWorldId = await page.evaluate(
    () => localStorage.getItem("trpg-active-world-id") || "",
  );
  expect(branchWorldId).not.toBe(sourceWorldId);

  // 分支里仍是结构化世界（能力协商随新连接重来），并继承分叉点的世界状态
  await expect(page.getByTestId("structured-tool-row")).toBeVisible({
    timeout: 90_000,
  });
  await expect
    .poll(() => page.locator(".header-scene-name").innerText(), {
      timeout: 90_000,
    })
    .toBe(sceneBefore);
  // 客户端始终没有伪造 turn_id
  const branchFrames = frames.sent.filter((frame) =>
    frame.includes("solo_branch_create"),
  );
  expect(branchFrames).toHaveLength(1);
  expect(branchFrames[0]).not.toContain("turn_id");
  await page.screenshot({
    path: `${screenshotsDir}/branch-online-created.png`,
  });
  // 结构化 human 世界：开局之后一次模型调用都不该有
  expect(modelRequests.length).toBe(modelCallsAfterBoot);
});

/**
 * legacy 不回归：云端单人**未勾选结构化**的世界，分支点仍然是「最近完成回合」，
 * 帧里必须带真实 turn_id（结构化那条才不带）。这条守住两边共用同一入口时
 * 服务端的 `if not is_structured and not turn_id: reject` 分支语义没被改坏。
 */
/** 选角页的模型设置里，把两个角色都指向 E2E 桩（云端 BYOK-only 的 legacy 世界入口）。 */
async function configureByokToStub(page: Page) {
  await page
    .getByTestId("solo-character-select")
    .getByRole("button", { name: "模型设置" })
    .click();
  const dialog = page.getByRole("dialog", { name: "模型设置" });
  await expect(dialog).toBeVisible({ timeout: 30_000 });
  for (const role of ["narrative", "judgement"] as const) {
    const card = dialog.locator(`[data-role="${role}"]`);
    await card.getByRole("button", { name: "自定义服务" }).click();
    await card
      .getByPlaceholder("https://api.deepseek.com/v1")
      .fill(modelBaseUrl);
    await card.getByPlaceholder("sk-…").fill("sk-e2e-byok");
    await card.getByPlaceholder("deepseek-flash").fill("e2e-model");
  }
  await dialog.getByRole("checkbox").check();
  await dialog.getByRole("button", { name: /保存配置/ }).click();
  await expect(dialog.getByText(/下一回合生效/)).toBeVisible({
    timeout: 30_000,
  });
  await dialog.getByRole("button", { name: "取消" }).click();
  await expect(dialog).toBeHidden({ timeout: 15_000 });
}

async function bootCloudLegacy(page: Page): Promise<string> {
  await registerAndOpenAdventures(page, `branch_legacy_${runId}`);
  await page.getByText("开始新冒险", { exact: true }).first().click();
  await page.locator(".module-select-trigger").click();
  await page.getByRole("option", { name: /猩红文档/ }).click();
  // 不勾「结构化操作模式」→ legacy 世界（开场回合走模型桩）
  await page.getByRole("button", { name: /创建冒险/ }).click();
  const confirmCharacter = page.locator("#btn-character-confirm");
  await expect(confirmCharacter).toBeVisible({ timeout: 30_000 });
  await page.locator(".character-card").first().click();
  await expect(confirmCharacter).toBeEnabled();
  // 云端是 BYOK-only：legacy 世界的开场回合必须先配好自定义模型服务
  await configureByokToStub(page);
  await confirmCharacter.click();
  await expect(page.locator("#user-input")).toBeEnabled({ timeout: 120_000 });
  await expect(page.getByText("雨幕笼罩着阿卡姆。")).toBeVisible({
    timeout: 120_000,
  });
  await expect(page.getByTestId("structured-tool-row")).toHaveCount(0);
  const worldId = await page.evaluate(
    () => localStorage.getItem("trpg-active-world-id") || "",
  );
  expect(worldId).not.toBe("");
  return worldId;
}

test("legacy 不回归：云端单人旧世界仍从最近完成回合分叉（帧里带真实 turn_id）", async ({
  page,
}) => {
  test.setTimeout(420_000);
  page.setDefaultTimeout(30_000);
  const frames = collectFrames(page);
  const sourceWorldId = await bootCloudLegacy(page);

  // 先存一次档：云端存档管理面板在没有存档位时是空态，时间线管理挂在存档条目下
  const savedBefore = framesOf(frames, "saved").length;
  await page
    .getByRole("button", { name: /快速存档/ })
    .first()
    .click();
  await expect
    .poll(() => framesOf(frames, "saved").length, { timeout: 60_000 })
    .toBeGreaterThan(savedBefore);

  await page
    .getByRole("button", { name: /存档管理/ })
    .first()
    .click();
  await expect(page.locator("#save-panel-overlay")).toBeVisible({
    timeout: 30_000,
  });
  await page.locator(".adventure-card.current").first().click();
  await expect(page.getByTestId("save-panel-timelines")).toBeVisible({
    timeout: 30_000,
  });
  const branchButton = page.locator(".timeline-branch-create");
  await expect(
    branchButton,
    "legacy 世界的分支入口应当可见（基于最近完成回合）",
  ).toBeVisible({ timeout: 30_000 });
  await page.locator(".timeline-branch-input").fill("legacy 分支");

  const switchedBefore = framesOf(frames, "solo_world_switched").length;
  const rejectedBefore = framesOf(frames, "room_action_rejected").length;
  await branchButton.click();
  await expect
    .poll(() => framesOf(frames, "solo_world_switched").length, {
      timeout: 120_000,
    })
    .toBeGreaterThan(switchedBefore);
  expect(
    framesOf(frames, "room_action_rejected").length,
    `legacy 云端分支被拒：${framesOf(frames, "room_action_rejected").at(-1) ?? ""}`,
  ).toBe(rejectedBefore);

  // legacy 帧必须带真实 turn_id（不能因为结构化分支的新路径而丢掉）
  const branchFrames = frames.sent.filter((frame) =>
    frame.includes("solo_branch_create"),
  );
  expect(branchFrames).toHaveLength(1);
  const sentFrame = JSON.parse(branchFrames[0]) as { turn_id?: string };
  expect(
    String(sentFrame.turn_id ?? ""),
    "legacy 分支必须带真实的回合 ID",
  ).not.toBe("");
  await expect
    .poll(
      () =>
        page.evaluate(() => localStorage.getItem("trpg-active-world-id") || ""),
      { timeout: 120_000 },
    )
    .not.toBe(sourceWorldId);
  await expect(page.locator("#user-input")).toBeEnabled({ timeout: 90_000 });
});
