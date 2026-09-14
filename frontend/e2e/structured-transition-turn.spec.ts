/**
 * 守秘人主持的叙事型过渡回合：真实本地后端 + 真实前端。
 *
 * 覆盖产品目标那条链路：**表达意愿 → 正常叙事过渡 → 追问 → 决定 → 执行**，
 * 并且反向性质也成立——意愿不移动、等待真的停住、待办不自动执行、
 * 只有主持执行命令后位置才改变。
 *
 * 人类主持用主持台操作。模型方向放了一个**会计数的桩**：本地开局那一步会用到它，
 * 而切换成 structured_v1 + human 之后的整条过渡流程必须一次都不调用（断言计数不变）。
 * 世界只存在于临时 runtime root，不触碰任何真实存档。
 */

import { spawn, spawnSync, type ChildProcess } from "node:child_process";
import { createServer, type Server } from "node:http";
import { existsSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

import { expect, request, test, type Page } from "@playwright/test";

const port = 8771;
const baseUrl = `http://127.0.0.1:${port}`;
const repositoryRoot = resolve(import.meta.dirname, "../..");
const screenshotsDir = resolve(import.meta.dirname, "../../docs/screenshots");
let runtimeRoot = "";
let server: ChildProcess | null = null;
let serverOutput = "";
let modelServer: Server | null = null;
const modelRequests: string[] = [];

function pythonPath(): string {
  return (
    process.env.TRPG_E2E_PYTHON ??
    (existsSync(resolve(repositoryRoot, ".venv/bin/python"))
      ? resolve(repositoryRoot, ".venv/bin/python")
      : "python")
  );
}

function enableStructuredWorld(worldId: string): string {
  const script = [
    "import os, sys",
    "from pathlib import Path",
    "from src.storage.database import World, session_scope, database_url",
    "from src.structured.bootstrap import apply_profile_metadata, ensure_local_operator",
    "root = Path(os.environ['TRPG_RUNTIME_ROOT'])",
    "world_id = sys.argv[1]",
    "with session_scope(database_url(root)) as session:",
    "    world = session.get(World, world_id)",
    "    if world is None: raise SystemExit('world not found')",
    "    world.metadata_json = apply_profile_metadata(world.metadata_json, execution_profile='structured_v1', keeper_mode='human')",
    "    ensure_local_operator(session, world_id)",
    "    session.add(world)",
    "print('structured world ready')",
  ].join("\n");
  const result = spawnSync(pythonPath(), ["-c", script, worldId], {
    cwd: repositoryRoot,
    env: {
      ...process.env,
      TRPG_RUNTIME_ROOT: runtimeRoot,
      TRPG_DATABASE_URL: `sqlite:///${join(runtimeRoot, "e2e.db")}`,
      TRPG_WRITE_COMPAT_EXPORTS: "0",
    },
    encoding: "utf-8",
  });
  if (result.status !== 0) {
    throw new Error(
      `enableStructuredWorld failed:\n${result.stderr || result.stdout}`,
    );
  }
  return result.stdout.trim();
}

/** 会计数的模型桩：用于证明切换成 human 之后不再有模型调用。 */
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
        id: "chatcmpl-transition",
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
  runtimeRoot = mkdtempSync(join(tmpdir(), "trpg-structured-transition-"));
  const modelBaseUrl = await startModelStub();
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
        TRPG_WRITE_COMPAT_EXPORTS: "0",
        OPENAI_API_KEY: "e2e-placeholder",
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

function collectFrames(page: Page): { sent: string[]; received: string[] } {
  const sent: string[] = [];
  const received: string[] = [];
  page.on("websocket", (socket) => {
    socket.on("framesent", (event) => sent.push(String(event.payload)));
    socket.on("framereceived", (event) => received.push(String(event.payload)));
  });
  return { sent, received };
}

function framePayload(frames: string[], type: string): Record<string, unknown> {
  const found = frames.find((frame) => frame.includes(`"type":"${type}"`));
  if (!found) return {};
  const parsed = JSON.parse(found) as Record<string, unknown>;
  return (parsed.payload as Record<string, unknown>) ?? {};
}

/** 主持台字段：可能是 select 也可能是 input（候选列表存在与否）。 */
async function fillKeeperField(
  page: Page,
  field: string,
  value: string,
): Promise<void> {
  const locator = page.locator(
    `[data-field="${field}"] select, [data-field="${field}"] input, [data-field="${field}"] textarea`,
  );
  const tag = await locator.evaluate((node) => node.tagName);
  if (tag === "SELECT") await locator.selectOption(value);
  else await locator.fill(value);
}

/**
 * 等待中的待办卡：M5 之后由「当前交互」卡承担（interaction_updated / 快照
 * interactions[]）；旧路径的 awaiting 明细只在没有对应线程时才显示。
 */
function waitingCard(page: Page) {
  return page
    .locator(
      '[data-testid="structured-interaction-card"], [data-testid="structured-awaiting"]',
    )
    .first();
}

async function openConsole(page: Page): Promise<void> {
  await page.getByTestId("btn-keeper-console").click();
  await expect(page.getByRole("dialog", { name: "主持台" })).toBeVisible();
}

async function runKeeperCommand(
  page: Page,
  kind: string,
  fields: Record<string, string>,
): Promise<void> {
  await page.getByTestId(`keeper-cmd-${kind}`).click();
  for (const [field, value] of Object.entries(fields)) {
    await fillKeeperField(page, field, value);
  }
  await page.getByTestId("keeper-submit").click();
  await expect(page.locator('[data-testid="keeper-submit"]')).toBeEnabled({
    timeout: 30_000,
  });
}

test("过渡回合：意愿→叙事等待→追问→决定→执行（人类主持，零模型）", async ({
  page,
}) => {
  test.setTimeout(300_000);
  const frames = collectFrames(page);
  page.setDefaultTimeout(30_000);

  // 1) 先用旧路径开局拿到世界 ID，再切成 structured_v1 + human 主持。
  await page.goto(`${baseUrl}/?mode=local`);
  await expect(page.locator(".boot-loader")).toBeHidden({ timeout: 30_000 });
  await page.locator(".module-select-trigger").click();
  await page.getByRole("option", { name: /猩红文档/ }).click();
  await page.locator("#btn-start").click();
  await page.locator("#btn-character-confirm").click();
  await expect(page.locator("#user-input")).toBeEnabled({ timeout: 90_000 });
  const worldId = await page.evaluate(
    () => localStorage.getItem("trpg-active-world-id") || "",
  );
  expect(worldId).not.toBe("");
  expect(enableStructuredWorld(worldId)).toContain("structured world ready");
  // 从这一刻起（human 主持的结构化世界）不允许再有任何模型调用。
  const modelCallsBefore = modelRequests.length;
  await page.reload();
  await expect(page.locator(".boot-loader")).toBeHidden({ timeout: 30_000 });
  await expect(page.getByTestId("structured-tool-row")).toBeVisible({
    timeout: 60_000,
  });

  // 起点场景与一个已知目的地（都来自服务端快照）。
  const sceneAtStart = await page.locator(".header-scene-name").innerText();
  const snapshot = framePayload(frames.received, "session_snapshot");
  const destinations = (snapshot.destinations ?? []) as Array<{
    id: string;
    name: string;
  }>;
  expect(destinations.length).toBeGreaterThan(0);
  const destination = destinations[0];

  // 2) 玩家表达意愿：只是说话，不是出发。
  const wish = "说实话，我想先看看莱特教授的尸体。";
  await page.locator("#user-input").fill(wish);
  await page.locator("#btn-send").click();
  await expect
    .poll(
      () =>
        frames.sent.filter((frame) => frame.includes('"type":"action_request"'))
          .length,
      { timeout: 30_000 },
    )
    .toBeGreaterThan(0);
  const wishFrame = frames.sent.find(
    (frame) =>
      frame.includes('"type":"action_request"') && frame.includes("freeform"),
  );
  expect(wishFrame).toBeTruthy();
  const wishRequest = JSON.parse(wishFrame as string) as {
    request_id: string;
    action: { kind: string; text: string };
  };
  expect(wishRequest.action.text).toContain("尸体");
  expect(await page.locator(".header-scene-name").innerText()).toBe(
    sceneAtStart,
  );

  // 3) 主持：正常叙事 + 用正式等待能力挂起请求（不执行任何移动）。
  await openConsole(page);
  await runKeeperCommand(page, "publish_message", {
    speaker_kind: "keeper",
    text: "法伦放下雪茄：“停尸房得先跟值班医生打招呼，否则他们不会放人进去。”",
  });
  await runKeeperCommand(page, "resolve_intent", {
    request_id: wishRequest.request_id,
    resolution: "awaiting_player",
    pending_action_kind: "move",
    pending_action_note: "尚未出发前往停尸房",
    pending_action_destination: destination.id,
    disclosed: "停尸房需要值班医生放行",
    note: "等玩家决定是否现在联系医生",
  });
  await page.getByRole("button", { name: "关闭主持台" }).click();

  // 4) 玩家侧：看到「等你回应」与尚未执行的待办；位置没有变；输入框可用。
  await expect(waitingCard(page)).toBeVisible({
    timeout: 30_000,
  });
  await expect(page.getByText(/尚未执行：尚未出发前往停尸房/)).toBeVisible();
  await expect(page.getByText(/已告知：停尸房需要值班医生放行/)).toBeVisible();
  expect(await page.locator(".header-scene-name").innerText()).toBe(
    sceneAtStart,
  );
  await expect(page.locator("#user-input")).toBeEnabled();
  await page.screenshot({
    path: `${screenshotsDir}/structured-transition-awaiting.png`,
  });
  // 窄屏：等待块与输入区都不被挤压、不产生横向溢出（沿用 ui-button-check 的验收意图）。
  await page.setViewportSize({ width: 390, height: 780 });
  await expect(waitingCard(page)).toBeVisible();
  const overflow = await page.evaluate(
    () =>
      document.documentElement.scrollWidth -
      document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(1);
  await page.screenshot({
    path: `${screenshotsDir}/structured-transition-awaiting-narrow.png`,
  });
  await page.setViewportSize({ width: 1440, height: 900 });

  // 5) 追问：继续对话，不移动；待办仍在等玩家。
  await page.locator("#user-input").fill("那位医生和我们熟吗？");
  await page.locator("#btn-send").click();
  await expect
    .poll(
      () =>
        frames.sent.filter((frame) => frame.includes('"type":"action_request"'))
          .length,
      { timeout: 30_000 },
    )
    .toBeGreaterThan(1);
  await openConsole(page);
  await runKeeperCommand(page, "publish_message", {
    speaker_kind: "keeper",
    text: "“不算熟，不过他认我的名片。”",
  });
  await page.getByRole("button", { name: "关闭主持台" }).click();
  await expect(page.getByText(/不算熟/)).toBeVisible({ timeout: 30_000 });
  await expect(waitingCard(page)).toBeVisible();
  expect(await page.locator(".header-scene-name").innerText()).toBe(
    sceneAtStart,
  );

  // 6) 决定：玩家明确要现在过去 → 主持执行联系与移动，位置才改变。
  const decide = "那麻烦你联系一下，我现在过去。";
  await page.locator("#user-input").fill(decide);
  await page.locator("#btn-send").click();
  await expect
    .poll(
      () =>
        frames.sent.filter((frame) => frame.includes('"type":"action_request"'))
          .length,
      { timeout: 30_000 },
    )
    .toBeGreaterThan(2);
  const decideFrame = frames.sent
    .filter((frame) => frame.includes('"type":"action_request"'))
    .at(-1) as string;
  const decideRequest = JSON.parse(decideFrame) as { request_id: string };

  await openConsole(page);
  await runKeeperCommand(page, "publish_message", {
    speaker_kind: "keeper",
    text: "法伦打了电话：“医生在等你们。”",
  });
  await runKeeperCommand(page, "move_party", {
    destination_scene_id: destination.id,
    travel_minutes: "15",
  });
  await runKeeperCommand(page, "resolve_intent", {
    request_id: wishRequest.request_id,
    resolution: "completed",
    outcome: "success",
    note: "玩家决定现在前往",
  });
  await runKeeperCommand(page, "resolve_intent", {
    request_id: decideRequest.request_id,
    resolution: "completed",
    outcome: "success",
    note: "联系医生并出发",
  });
  await page.getByRole("button", { name: "关闭主持台" }).click();

  // 只有真正执行之后，当前位置才改变。
  await expect
    .poll(() => page.locator(".header-scene-name").innerText(), {
      timeout: 30_000,
    })
    .toBe(destination.name);
  // 待办收尾后不再显示等待块。
  await page.screenshot({
    path: `${screenshotsDir}/structured-transition-decided.png`,
  });
  await expect(waitingCard(page)).toBeHidden({
    timeout: 30_000,
  });

  // 7) 刷新：位置是已提交的事实，待办不再恢复（已收尾）。
  await page.reload();
  await expect(page.locator(".boot-loader")).toBeHidden({ timeout: 30_000 });
  await expect
    .poll(() => page.locator(".header-scene-name").innerText(), {
      timeout: 30_000,
    })
    .toBe(destination.name);
  await expect(waitingCard(page)).toBeHidden();
  // 全程零模型调用：切换成 human 主持后一次都没碰模型。
  expect(modelRequests.length).toBe(modelCallsBefore);
});
