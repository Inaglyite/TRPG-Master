/**
 * 真实本地后端的结构化回归（不是替身）。
 *
 * 目的有二：
 * 1. 证明当前真实后端**没有**下发 `session_snapshot` / `server_capabilities`，
 *    并向它证明前端在这种服务端上不会出现任何结构化入口、也不会把提交
 *    静默改走自然语言——协议不支持时只保留旧模式。
 * 2. 证明旧模式在这条路径上完全不受影响（旧出示编辑器仍可用）。
 *
 * 这不算“结构化联调成功”；它是联调的前置条件验证。真正的三客户端 human
 * 闭环要等后端把 M1 接进 WebSocket 之后另跑（见交付报告与联调清单）。
 */

import { spawn, type ChildProcess } from "node:child_process";
import { createServer, type Server } from "node:http";
import { existsSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

import { expect, request, test, type Page } from "@playwright/test";

const port = 8770;
const baseUrl = `http://127.0.0.1:${port}`;
const repositoryRoot = resolve(import.meta.dirname, "../..");
let runtimeRoot = "";
let server: ChildProcess | null = null;
let serverOutput = "";
let modelServer: Server | null = null;

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
      const content =
        "雨幕笼罩着阿卡姆，你在约定的办公室里见到了等待已久的委托人。";
      const chunk = {
        id: "chatcmpl-e2e-real",
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
  runtimeRoot = mkdtempSync(join(tmpdir(), "trpg-structured-real-"));
  const modelBaseUrl = await startModelStub();
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

/** 记录这个页面发出的所有 WS 帧文本。 */
function collectFrames(page: Page): string[] {
  const frames: string[] = [];
  page.on("websocket", (socket) => {
    socket.on("framesent", (event) => frames.push(String(event.payload)));
    socket.on("framereceived", (event) => frames.push(String(event.payload)));
  });
  return frames;
}

test("真实后端：未下发结构化能力 → 不出现新入口、不发结构请求、旧模式可用", async ({
  page,
}) => {
  test.setTimeout(180_000);
  const frames = collectFrames(page);

  await page.goto(`${baseUrl}/?mode=local`);
  await expect(page.locator(".boot-loader")).toBeHidden({ timeout: 30_000 });
  await page.locator(".module-select-trigger").click();
  await page.getByRole("option", { name: /猩红文档/ }).click();
  await page.locator("#btn-start").click();
  await page.locator("#btn-character-confirm").click();
  await expect(page.locator("#user-input")).toBeEnabled({ timeout: 90_000 });
  await page.locator("#btn-panel").click();
  await expect(page.locator("#char-content .inv-card-clues")).toBeVisible({
    timeout: 30_000,
  });

  // 服务端没有声明 structured_v1：结构化入口一个都不该出现。
  await expect(page.getByTestId("structured-tool-row")).toHaveCount(0);
  await expect(page.getByTestId("btn-keeper-console")).toHaveCount(0);
  await expect(page.getByTestId("btn-move")).toHaveCount(0);
  await expect(page.getByText(/结构化模式/)).toHaveCount(0);

  // 服务端的确没有下发结构化帧（这是“M1 未接入 WS”的直接证据）。
  const received = frames.filter((frame) => frame.startsWith("{"));
  expect(received.some((frame) => frame.includes("session_snapshot"))).toBe(
    false,
  );
  expect(received.some((frame) => frame.includes("server_capabilities"))).toBe(
    false,
  );

  // 旧模式仍然可用：出示编辑器照旧打开、照旧只发文字行动。
  await page.getByRole("button", { name: "出示" }).first().click();
  const legacyDialog = page.getByRole("dialog", { name: "出示线索" });
  await expect(legacyDialog).toBeVisible();
  // 旧编辑器以文字预览为权威，不是结构 JSON。
  await expect(
    legacyDialog.locator(".panel-action-preview-text"),
  ).toContainText("我向");
  // 旧编辑器的字段文案是“向谁出示/说明（必填）”：按输入框定位，不按 label 文本。
  await legacyDialog
    .locator("input[type=text]")
    .first()
    .fill("惠特克罗夫特医生");
  await legacyDialog.getByRole("button", { name: "确认出示" }).click();

  await expect
    .poll(
      () =>
        frames.filter(
          (frame) => frame.startsWith("{") && frame.includes('"type":"action"'),
        ).length,
      { timeout: 30_000 },
    )
    .toBeGreaterThan(0);
  // 整场没有发出任何结构化请求；也没有出现“无法识别的协议消息”的错误提示。
  expect(frames.some((frame) => frame.includes("action_request"))).toBe(false);
  expect(frames.some((frame) => frame.includes("check_response"))).toBe(false);
  await expect(page.getByText(/无法识别的协议消息/)).toHaveCount(0);
});
