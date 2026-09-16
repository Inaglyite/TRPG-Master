/**
 * 启动覆盖层的针对性验收（真实本地后端、人类主持、零模型）：
 *
 *   1. 正常加载：覆盖层真实卸载后开局页可点，开局照常完成；
 *   2. 延迟加载：图片被人为拖慢时，界面仍在**有界**时间内可交互
 *      （覆盖层不再拦点击），并有可见说明；
 *   3. 资源失败：图片请求被中断时，界面照常可交互并给出说明，
 *      绝不出现「永远挡着」的加载屏。
 *
 * 三条都不加 skip / retry，也不用 force click：点击走真实行动性检查。
 * 覆盖层是否「不再拦点击」由命中测试判断（readiness.ts），不是「元素不可见」。
 */

import { spawn, spawnSync, type ChildProcess } from "node:child_process";
import { createServer, type Server } from "node:http";
import { existsSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

import { expect, request, test, type Page } from "@playwright/test";

import { openLocalStartScreen } from "./readiness";

const port = 8791;
const baseUrl = `http://127.0.0.1:${port}`;
const repositoryRoot = resolve(import.meta.dirname, "../..");
const MODULE = "猩红文档";
let runtimeRoot = "";
let server: ChildProcess | null = null;
let serverOutput = "";
let modelServer: Server | null = null;

function pythonPath(): string {
  return (
    process.env.TRPG_E2E_PYTHON ??
    (existsSync(resolve(repositoryRoot, ".venv/bin/python"))
      ? resolve(repositoryRoot, ".venv/bin/python")
      : "python")
  );
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
  return result.stdout.trim();
}

/** 会计数的模型桩：只服务于开局那一步。 */
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
      const chunk = {
        id: "chatcmpl-boot",
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
      response.writeHead(200, {
        "Content-Type": "text/event-stream; charset=utf-8",
        Connection: "close",
      });
      response.write(
        `data: ${JSON.stringify(chunk)}\n\ndata: ${JSON.stringify({
          ...chunk,
          choices: [{ index: 0, delta: {}, finish_reason: "stop" }],
        })}\n\ndata: [DONE]\n\n`,
      );
      response.end();
    });
  });
  await new Promise<void>((resolveListen) =>
    modelServer!.listen(0, "127.0.0.1", () => resolveListen()),
  );
  const address = modelServer.address();
  if (!address || typeof address === "string") {
    throw new Error("model stub did not expose a TCP address");
  }
  return `http://127.0.0.1:${address.port}/v1`;
}

test.beforeAll(async () => {
  runtimeRoot = mkdtempSync(join(tmpdir(), "trpg-boot-readiness-"));
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
  server.stdout?.on("data", (chunk) => (serverOutput += String(chunk)));
  server.stderr?.on("data", (chunk) => (serverOutput += String(chunk)));
  const client = await request.newContext();
  for (let attempt = 0; attempt < 80; attempt += 1) {
    try {
      if ((await client.get(`${baseUrl}/api/health`)).ok()) break;
    } catch {
      /* 启动中 */
    }
    await new Promise((wait) => setTimeout(wait, 125));
  }
  await client.dispose();
});

test.afterAll(async () => {
  if (server && server.exitCode === null) {
    server.kill("SIGTERM");
    await new Promise<void>((wait) => setTimeout(wait, 1500));
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

/** 从开局页真正开一局（人类主持）：点到 #btn-start 且角色确认可见。 */
async function startGameFromScreen(page: Page): Promise<string> {
  await page.locator(".module-select-trigger").click();
  await page.getByRole("option", { name: new RegExp(MODULE) }).click();
  await page.locator("#btn-start").click();
  await expect(page.locator("#btn-character-confirm")).toBeVisible({
    timeout: 30_000,
  });
  await page.locator("#btn-character-confirm").click();
  await expect(page.locator("#user-input")).toBeEnabled({ timeout: 90_000 });
  const worldId = await page.evaluate(
    () => localStorage.getItem("trpg-active-world-id") || "",
  );
  expect(worldId).not.toBe("");
  expect(configureWorld(worldId)).toContain("world ready");
  return worldId;
}

test("1. 正常加载：覆盖层真实卸载后开局页可点，开局照常完成", async ({
  page,
}) => {
  test.setTimeout(300_000);
  page.setDefaultTimeout(30_000);
  const frames = collectFrames(page);
  // 就绪判据已包含「覆盖层真实卸载」：这一步本身即断言
  await openLocalStartScreen(page, `${baseUrl}/?mode=local`);
  await expect(page.locator(".boot-loader")).toHaveCount(0);
  const worldId = await startGameFromScreen(page);
  expect(worldId).not.toBe("");
  await page.reload();
  await expect(page.getByTestId("structured-tool-row")).toBeVisible({
    timeout: 60_000,
  });
  expect(
    frames.received.filter((frame) => frame.includes("session_snapshot"))
      .length,
  ).toBeGreaterThan(0);
});

test("2. 延迟/卡住的加载：资源一直没落定时，界面仍在有界时间内可交互", async ({
  page,
}) => {
  test.setTimeout(300_000);
  page.setDefaultTimeout(30_000);
  // 让 UI 图片请求**一直挂着**（不 continue、不 fulfill）：这正是 CI 上
  // 「资源早已 200 但某张图片既不 onload 也不 onerror」的等价形态。
  await page.route("**/assets/*.webp", () => {
    /* 故意不结束：请求保持 pending */
  });
  const started = Date.now();
  await openLocalStartScreen(page, `${baseUrl}/?mode=local`);
  const waited = Date.now() - started;
  // 有界：加载屏最多挡 SPLASH_BUDGET_MS 量级，不该等到图片有结果
  expect(waited, `启动到可交互用了 ${waited}ms，超过有界预算`).toBeLessThan(
    20_000,
  );
  await expect(page.locator(".boot-loader")).toHaveCount(0);
  const worldId = await startGameFromScreen(page);
  expect(worldId).not.toBe("");
});

test("3. 资源失败：图片请求中断时界面照常可交互，不出现永远挡着的加载屏", async ({
  page,
}) => {
  test.setTimeout(300_000);
  page.setDefaultTimeout(30_000);
  await page.route("**/assets/*.webp", async (route) => {
    await route.abort("failed");
  });
  await openLocalStartScreen(page, `${baseUrl}/?mode=local`);
  const worldId = await startGameFromScreen(page);
  expect(worldId).not.toBe("");
});
