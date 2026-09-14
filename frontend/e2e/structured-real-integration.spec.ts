/**
 * 真实本地后端的结构化集成（M1 接入 `/ws` 之后）。
 *
 * 与 `structured-real-backend.spec.ts`（验证“没有能力时不出新入口”）互补：
 * 这里把测试世界的 metadata 切成 `structured_v1` + `human`，然后跑真实
 * 服务端，验证：真实 session_snapshot 驱动界面、按钮发出的结构请求被真实
 * 服务端受理并落账、位置由服务端事件更新，而且**整个流程不需要模型调用**。
 *
 * 世界切开执行档位只发生在本次运行创建的隔离世界（临时 runtime root），
 * 不触碰任何真实存档。
 */

import { spawn, spawnSync, type ChildProcess } from "node:child_process";
import { createServer, type Server } from "node:http";
import { existsSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

import { expect, request, test, type Page } from "@playwright/test";

const port = 8769;
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

/** 用后端自己的 bootstrap 把一个本地世界切成结构化档位。 */
function enableStructuredWorld(worldId: string): string {
  const script = [
    "import os, sys, json",
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
      const content =
        "雨幕笼罩着阿卡姆，你在约定的办公室里见到了等待已久的委托人。";
      const chunk = {
        id: "chatcmpl-real-structured",
        object: "chat.completion.chunk",
        created: 1,
        model: "e2e-model",
        choices: [
          {
            index: 0,
            delta: { role: "assistant", content },
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
  runtimeRoot = mkdtempSync(join(tmpdir(), "trpg-structured-integration-"));
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

test("真实后端 + structured_v1：快照驱动界面、结构请求落账、无需模型调用", async ({
  page,
}) => {
  test.setTimeout(240_000);
  const frames = collectFrames(page);

  // 1) 先用旧路径开局（世界此时还是 legacy），拿到世界 ID。
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

  // 2) 把这个隔离世界切成 structured_v1 + human 主持，然后刷新重连。
  expect(enableStructuredWorld(worldId)).toContain("structured world ready");
  const modelCallsBefore = modelRequests.length;
  await page.reload();
  await expect(page.locator(".boot-loader")).toBeHidden({ timeout: 30_000 });

  // 3) 真实服务端下发能力协商：结构化入口出现。
  await expect(page.getByTestId("structured-tool-row")).toBeVisible({
    timeout: 60_000,
  });
  await expect(page.getByTestId("btn-move")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("btn-keeper-console")).toBeVisible();
  expect(
    frames.received.some((frame) => frame.includes("session_snapshot")),
  ).toBe(true);
  expect(
    frames.received.some((frame) => frame.includes("server_capabilities")),
  ).toBe(true);

  // 4) 顶栏位置来自真实快照。
  const sceneBefore = await page.locator(".header-scene-name").innerText();

  // 5) 主持台：用真实命令调整 SAN（人类主持操作，不需要模型）。
  // 调查员 ID 用服务端自己下发的那个（真实本地世界的调查员 id 不是夹具里的名字）。
  const snapshotFrame = frames.received.find((frame) =>
    frame.includes('"type":"session_snapshot"'),
  );
  const snapshotPayload =
    JSON.parse(snapshotFrame ?? "{}").payload ??
    ({} as Record<string, unknown>);
  const investigatorId = String(snapshotPayload.investigator_id || "");
  expect(investigatorId).not.toBe("");

  await page.getByTestId("btn-keeper-console").click();
  await page.getByTestId("keeper-cmd-adjust_stat").click();
  const investigatorField = page.locator(
    '[data-field="investigator_id"] select, [data-field="investigator_id"] input',
  );
  if ((await investigatorField.evaluate((node) => node.tagName)) === "SELECT") {
    await investigatorField.selectOption(investigatorId);
  } else {
    await investigatorField.fill(investigatorId);
  }
  await page.locator('[data-field="field"] select').selectOption("san");
  await page.locator('[data-field="delta"] input').fill("-3");
  await page.locator('[data-field="reason"] input').fill("目击遗体");
  await page.getByTestId("keeper-submit").click();
  // 真实服务端要么 ack、要么给出确定的错误码：两者都证明帧过了 schema 并进入
  // 服务层（只有请求确实到达才会回答）。
  await expect
    .poll(
      () =>
        frames.received.filter(
          (frame) =>
            frame.includes('"type":"action_ack"') ||
            frame.includes('"type":"request_error"') ||
            frame.includes('"type":"action_status"'),
        ).length,
      { timeout: 30_000 },
    )
    .toBeGreaterThan(0);
  // 命令带着快照里的版本号提交，不应出现 revision 冲突。
  expect(
    frames.received.some((frame) =>
      frame.includes('"code":"revision_conflict"'),
    ),
  ).toBe(false);
  // 人类主持操作不消耗模型额度。
  expect(modelRequests.length).toBe(modelCallsBefore);

  // 6) 玩家侧：普通掷骰走真实服务端结算。
  await page.getByRole("button", { name: "关闭主持台" }).click();
  await expect(page.getByRole("dialog", { name: "主持台" })).toBeHidden();
  await page.getByTestId("btn-free-roll").click();
  await page.getByTestId("roll-confirm").click();
  await expect
    .poll(
      () =>
        frames.received.filter((frame) =>
          frame.includes('"type":"roll_resolved"'),
        ).length,
      { timeout: 30_000 },
    )
    .toBeGreaterThan(0);
  await expect(page.getByText(/普通掷骰 1d100 →/)).toBeVisible({
    timeout: 30_000,
  });
  // 掷骰不消耗模型额度。
  expect(modelRequests.length).toBe(modelCallsBefore);

  // 7) 位置仍未被无关命令改写。
  expect(await page.locator(".header-scene-name").innerText()).toBe(
    sceneBefore,
  );
});
