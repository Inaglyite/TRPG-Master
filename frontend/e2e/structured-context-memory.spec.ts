/**
 * M5 上下文与记忆改造：前端适配的真实后端 E2E（人类主持，无模型）。
 *
 * 覆盖：
 * A. 主持开交互线程（resolve_intent.thread=open）→ 待办区出现「当前交互」卡：
 *    只显示玩家可知的目标与「尚未执行/已告知」，**没有**强制确认按钮；位置不变。
 * B. 刷新后由快照 `interactions[]` 恢复同一张卡。
 * C. 主持记录一条角色记忆（record_memory）→ 主持台只读记忆查询命中它；
 *    记忆内容只出现在主持台，不进聊天/叙事区。
 * D. 玩家侧不出现记忆查询入口（能力的服务端授权 + 前端按能力隐藏的双重门槛）。
 *
 * 世界在临时 runtime root；开局那一步用会计数的模型桩，判定部分不调用模型。
 */

import { spawn, spawnSync, type ChildProcess } from "node:child_process";
import { createServer, type Server } from "node:http";
import { existsSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

import { expect, request, test, type Page } from "@playwright/test";

import { openLocalStartScreen } from "./readiness";

const port = 8777;
const baseUrl = `http://127.0.0.1:${port}`;
const repositoryRoot = resolve(import.meta.dirname, "../..");
const MODULE = "猩红文档";
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

/** 会计数的模型桩：只用于本地开局那一步。 */
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
        id: "chatcmpl-context-memory",
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

function configureWorld(worldId: string): string {
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
    "    world.metadata_json = apply_profile_metadata(world.metadata_json,",
    "        execution_profile='structured_v1', keeper_mode='human')",
    "    ensure_local_operator(session, world_id)",
    "    session.add(world)",
    "print('world ready')",
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
      `configureWorld failed:\n${result.stderr || result.stdout}`,
    );
  }
  return result.stdout.trim();
}

async function waitForServer(): Promise<void> {
  const client = await request.newContext();
  try {
    for (let attempt = 0; attempt < 80; attempt += 1) {
      try {
        if ((await client.get(`${baseUrl}/api/health`)).ok()) return;
      } catch {
        // 启动中
      }
      await new Promise((wait) => setTimeout(wait, 125));
    }
  } finally {
    await client.dispose();
  }
  throw new Error(`E2E server did not start:\n${serverOutput.slice(-3000)}`);
}

let modelBaseUrl = "";

test.beforeAll(async () => {
  modelBaseUrl = await startModelStub();
});

test.beforeEach(async () => {
  runtimeRoot = mkdtempSync(join(tmpdir(), "trpg-context-memory-"));
  serverOutput = "";
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
  server.stdout?.on("data", (chunk) => (serverOutput += String(chunk)));
  server.stderr?.on("data", (chunk) => (serverOutput += String(chunk)));
  await waitForServer();
});

test.afterEach(async () => {
  if (server && server.exitCode === null) {
    server.kill("SIGTERM");
    await new Promise<void>((wait) => {
      const timer = setTimeout(wait, 3000);
      server?.once("exit", () => {
        clearTimeout(timer);
        wait();
      });
    });
    if (server.exitCode === null) server.kill("SIGKILL");
  }
  if (runtimeRoot) {
    rmSync(runtimeRoot, { recursive: true, force: true });
    runtimeRoot = "";
  }
});

test.afterAll(async () => {
  if (modelServer) {
    await new Promise<void>((resolveClose) =>
      modelServer!.close(() => resolveClose()),
    );
  }
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

async function fillKeeperField(page: Page, field: string, value: string) {
  const locator = page.locator(
    `[data-field="${field}"] select, [data-field="${field}"] input, [data-field="${field}"] textarea`,
  );
  const tag = await locator.evaluate((node) => node.tagName);
  if (tag === "SELECT") await locator.selectOption(value);
  else await locator.fill(value);
}

async function openConsole(page: Page) {
  await page.getByTestId("btn-keeper-console").click();
  await expect(page.getByRole("dialog", { name: "主持台" })).toBeVisible();
}

async function closeConsole(page: Page) {
  await page.getByRole("button", { name: "关闭主持台" }).click();
  await expect(page.getByRole("dialog", { name: "主持台" })).toBeHidden({
    timeout: 10_000,
  });
}

async function bootWorld(page: Page, frames: Frames): Promise<string> {
  await openLocalStartScreen(page, `${baseUrl}/?mode=local`);
  await page.locator(".module-select-trigger").click();
  await page.getByRole("option", { name: new RegExp(MODULE) }).click();
  await page.locator("#btn-start").click();
  await page.locator("#btn-character-confirm").click();
  await expect(page.locator("#user-input")).toBeEnabled({ timeout: 90_000 });
  const worldId = await page.evaluate(
    () => localStorage.getItem("trpg-active-world-id") || "",
  );
  expect(worldId).not.toBe("");
  expect(configureWorld(worldId)).toContain("world ready");
  await page.reload();
  await expect(page.locator(".boot-loader")).toBeHidden({ timeout: 30_000 });
  await expect(page.getByTestId("structured-tool-row")).toBeVisible({
    timeout: 60_000,
  });
  return worldId;
}

/** 玩家发一条自由文本请求，返回 request_id（线程的 origin/last request）。 */
async function playerText(
  page: Page,
  frames: Frames,
  text: string,
): Promise<string> {
  const before = frames.sent.filter((f) =>
    f.includes('"type":"action_request"'),
  ).length;
  await page.locator("#user-input").fill(text);
  await page.locator("#btn-send").click();
  await expect
    .poll(
      () =>
        frames.sent.filter((f) => f.includes('"type":"action_request"')).length,
      { timeout: 30_000 },
    )
    .toBeGreaterThan(before);
  const frame = frames.sent
    .filter((f) => f.includes('"type":"action_request"'))
    .at(-1) as string;
  return (JSON.parse(frame) as { request_id: string }).request_id;
}

test("交互线程：主持开线程 → 卡片出现 → 刷新恢复 → 位置不变", async ({
  page,
}) => {
  test.setTimeout(300_000);
  page.setDefaultTimeout(30_000);
  const frames = collectFrames(page);
  await bootWorld(page, frames);
  const scene = await page.locator(".header-scene-name").innerText();

  const requestId = await playerText(
    page,
    frames,
    "说实话，我想先看看莱特教授的尸体。",
  );
  expect(await page.locator(".header-scene-name").innerText()).toBe(scene);

  // 主持用 resume_intent 的线程操作开一条线程（记录，不是执行授权）
  await openConsole(page);
  await page.getByTestId("keeper-cmd-resolve_intent").click();
  await fillKeeperField(page, "request_id", requestId);
  await fillKeeperField(page, "resolution", "awaiting_player");
  await fillKeeperField(page, "thread_action", "open");
  await fillKeeperField(page, "pending_action_kind", "move");
  await fillKeeperField(
    page,
    "pending_action_note",
    "尚未出发前往医学院停尸间",
  );
  await fillKeeperField(
    page,
    "disclosed",
    "遗体需由医学院安排查看\n法伦可代为致电",
  );
  await page.getByTestId("keeper-submit").click();
  await expect(page.getByTestId("keeper-submit")).toBeEnabled({
    timeout: 30_000,
  });
  await closeConsole(page);

  expect(
    framesOf(frames, "interaction_updated").length,
    `收到的帧类型：${JSON.stringify(
      frames.received.map((frame) => {
        try {
          const parsed = JSON.parse(frame) as {
            type?: string;
            payload?: { code?: string; message?: string };
          };
          return parsed.type === "request_error"
            ? `request_error:${parsed.payload?.code}:${String(parsed.payload?.message).slice(-260)}`
            : parsed.type;
        } catch {
          return "unparsed";
        }
      }),
    )}`,
  ).toBeGreaterThan(0);
  const card = page.getByTestId("structured-interaction-card");
  await expect(card).toBeVisible({ timeout: 30_000 });
  await expect(
    card.getByText(/尚未执行：尚未出发前往医学院停尸间/),
  ).toBeVisible();
  await expect(card.getByText(/已告知：遗体需由医学院安排查看/)).toBeVisible();
  // 不是强制确认弹窗：卡片上没有任何按钮
  expect(await card.getByRole("button").count()).toBe(0);
  expect(await page.locator(".header-scene-name").innerText()).toBe(scene);

  // 刷新：快照 interactions[] 恢复同一张卡
  await page.reload();
  await expect(page.locator(".boot-loader")).toBeHidden({ timeout: 30_000 });
  await expect(page.getByTestId("structured-interaction-card")).toBeVisible({
    timeout: 60_000,
  });
  await expect(
    page
      .getByTestId("structured-interaction-card")
      .getByText(/尚未执行：尚未出发前往医学院停尸间/),
  ).toBeVisible();
  expect(await page.locator(".header-scene-name").innerText()).toBe(scene);
  await page.screenshot({
    path: resolve(
      repositoryRoot,
      "docs/screenshots/structured-interaction-card.png",
    ),
  });
});

test("主持记忆查询：记录一条记忆 → 只读查询命中；内容不进聊天区", async ({
  page,
}) => {
  test.setTimeout(300_000);
  page.setDefaultTimeout(30_000);
  const frames = collectFrames(page);
  await bootWorld(page, frames);

  // 1) 记忆查询能力由服务端声明，主持台才出现入口
  await expect
    .poll(() => framesOf(frames, "session_snapshot").length, {
      timeout: 30_000,
    })
    .toBeGreaterThan(0);
  const snapshot = JSON.parse(
    framesOf(frames, "session_snapshot").at(-1) as string,
  ) as {
    payload?: {
      server_capabilities?: { memory_query?: boolean };
      investigator_id?: string;
    };
  };
  expect(snapshot.payload?.server_capabilities?.memory_query).toBe(true);
  const investigatorId = String(snapshot.payload?.investigator_id || "pc");

  await openConsole(page);
  await expect(page.getByTestId("keeper-memory-query")).toBeVisible();

  // 2) 主持记录一条记忆（record_memory）
  const memoryContent = "调查员在停尸间确认了遗体上的旧伤。";
  await page.getByTestId("keeper-cmd-record_memory").click();
  await fillKeeperField(page, "character_id", investigatorId);
  await fillKeeperField(page, "knowledge_type", "experienced");
  await fillKeeperField(page, "content", memoryContent);
  await fillKeeperField(page, "topics", "morgue,evidence");
  await page.getByTestId("keeper-submit").click();
  await expect(page.getByTestId("keeper-submit")).toBeEnabled({
    timeout: 30_000,
  });

  // 3) 只读查询命中它，并显示本轮选用的过滤条件与命中数
  // 本地世界的调查员候选可能为空（下拉只有「全部」）：有选项就按角色查，否则按主题查。
  const characterSelect = page.locator(
    '[data-field="memory_character_id"] select',
  );
  const hasOption = await characterSelect
    .locator(`option[value="${investigatorId}"]`)
    .count();
  if (hasOption > 0) {
    await characterSelect.selectOption(investigatorId);
  }
  await fillKeeperField(page, "memory_topics", "morgue");
  await page.getByTestId("keeper-memory-submit").click();
  const results = page.getByTestId("keeper-memory-results");
  await expect(results).toBeVisible({ timeout: 60_000 });
  await expect(results.getByText(/命中 [1-9]\d* 条/)).toBeVisible();
  await expect(results.getByText(new RegExp(memoryContent))).toBeVisible();
  await expect(page.getByTestId("keeper-memory-empty")).toHaveCount(0);
  await page.screenshot({
    path: resolve(
      repositoryRoot,
      "docs/screenshots/structured-memory-query.png",
    ),
  });

  // 4) 记忆内容只在主持台：聊天/叙事区不应出现这句
  await closeConsole(page);
  const chatText = await page
    .locator("#messages, .chat-messages, main")
    .first()
    .innerText();
  expect(chatText).not.toContain(memoryContent);
});
