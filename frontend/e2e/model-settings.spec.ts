/**
 * 模型设置 e2e（本地模式真实后端 + 可探测模型桩）。
 *
 * 闭环：表单填自定义服务（本机 http 桩，本地模式允许）→ 数据发送确认 →
 * 测试连接（连通性/生成/能力探针）→ 保存（下回合生效）→ 真实回合确实切到
 * 自定义模型 → 上下文摘要出现 → 刷新后配置回填且 Key 不回显 → 恢复默认。
 */
import { spawn, type ChildProcess } from "node:child_process";
import { createServer, type Server } from "node:http";
import { mkdtempSync, rmSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

import { expect, request, test, type Page } from "@playwright/test";

const port = 8778;
const baseUrl = `http://127.0.0.1:${port}`;
const repositoryRoot = resolve(import.meta.dirname, "../..");
let runtimeRoot = "";
let server: ChildProcess | null = null;
let serverOutput = "";
let modelServer: Server | null = null;
let stubUrl = "";
const modelRequests: Array<{ model?: string; tools?: boolean }> = [];

function narrativeChunk(content: string, model: string) {
  return {
    id: "chatcmpl-e2e-settings",
    object: "chat.completion.chunk",
    created: 1,
    model,
    choices: [
      { index: 0, delta: { role: "assistant", content }, finish_reason: null },
    ],
  };
}

async function startModelStub(): Promise<string> {
  modelServer = createServer((incoming, response) => {
    const url = incoming.url || "";
    if (incoming.method === "GET" && url.endsWith("/models")) {
      response.writeHead(200, { "Content-Type": "application/json" });
      response.end(JSON.stringify({ data: [{ id: "e2e-model" }] }));
      return;
    }
    if (incoming.method !== "POST" || !url.endsWith("/chat/completions")) {
      response.writeHead(404).end();
      return;
    }
    let body = "";
    incoming.on("data", (chunk) => {
      body += String(chunk);
    });
    incoming.once("end", () => {
      const parsed = JSON.parse(body || "{}");
      modelRequests.push({
        model: parsed.model,
        tools: Array.isArray(parsed.tools) && parsed.tools.length > 0,
      });
      // 能力探针：非流式 tool_call 应答
      if (parsed.stream === false) {
        const isToolProbe =
          Array.isArray(parsed.tools) &&
          parsed.tools.some((tool) => tool?.function?.name === "probe_noop");
        response.writeHead(200, { "Content-Type": "application/json" });
        response.end(
          JSON.stringify({
            id: "probe",
            choices: [
              {
                message: isToolProbe
                  ? {
                      role: "assistant",
                      content: null,
                      tool_calls: [
                        {
                          id: "call_probe",
                          type: "function",
                          function: {
                            name: "probe_noop",
                            arguments: '{"echo":"ok"}',
                          },
                        },
                      ],
                    }
                  : { role: "assistant", content: "好" },
              },
            ],
          }),
        );
        return;
      }
      const content =
        "雨幕笼罩着阿卡姆，你在约定的办公室里见到了等待已久的委托人。" +
        "\n\n**你可以——**\n1. 请他说明委托\n2. 观察办公室" +
        "\n3. 检查档案封面\n4. [自由行动] 你决定做什么？";
      const chunk = narrativeChunk(content, parsed.model || "e2e-model");
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
  stubUrl = `http://127.0.0.1:${address.port}/v1`;
  return stubUrl;
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
  runtimeRoot = mkdtempSync(join(tmpdir(), "trpg-settings-e2e-"));
  const modelBaseUrl = await startModelStub();
  const repositoryPython = resolve(repositoryRoot, ".venv/bin/python");
  const python =
    process.env.TRPG_E2E_PYTHON ??
    (existsSync(repositoryPython) ? repositoryPython : "python");
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

async function enterGame(page: Page) {
  await page.goto(`${baseUrl}/?mode=local`);
  await expect(page.locator(".boot-loader")).toBeHidden({ timeout: 30_000 });
  await page.locator(".module-select-trigger").click();
  await page.getByRole("option", { name: /猩红文档/ }).click();
  await page.locator("#btn-start").click();
  const confirmCharacter = page.locator("#btn-character-confirm");
  await expect(confirmCharacter).toBeVisible();
  await confirmCharacter.click();
  await expect(page.getByText("雨幕笼罩着阿卡姆").first()).toBeVisible({
    timeout: 60_000,
  });
}

test("本地模式：自定义服务全链路（测试→保存→回合切换→摘要→持久化→恢复默认）", async ({
  page,
}) => {
  test.setTimeout(240_000);
  await enterGame(page);

  // 打开模型设置：两页签
  await page.locator("#btn-model-settings").click();
  const dialog = page.getByRole("dialog", { name: "模型设置" });
  await expect(dialog).toBeVisible();
  await expect(page.getByRole("tab", { name: "模型配置" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "上下文" })).toBeVisible();
  // 作用域徽标 + 默认绑定
  await expect(dialog.getByText(/本地默认/)).toBeVisible();

  // 叙述角色切到自定义服务
  const narrativeCard = dialog.locator('[data-role="narrative"]');
  await narrativeCard.getByRole("button", { name: "自定义服务" }).click();
  await narrativeCard
    .getByPlaceholder("https://api.deepseek.com/v1")
    .fill(stubUrl);
  await narrativeCard.getByPlaceholder("sk-…").fill("sk-e2e-local");
  await narrativeCard.getByPlaceholder("deepseek-v4-flash").fill("e2e-model");
  await narrativeCard.getByPlaceholder("例如 65536").fill("65536");

  // 数据发送确认
  await dialog.getByRole("checkbox").check();

  // 测试连接（连通性 + 生成探针打到桩上）
  await narrativeCard.getByRole("button", { name: "测试连接" }).click();
  await expect(dialog.getByText(/✓ 测试通过/)).toBeVisible({ timeout: 30_000 });
  await expect(dialog.getByText(/连通|connectivity/i).first()).toBeVisible();

  // 保存：下回合生效
  await dialog.getByRole("button", { name: /保存配置/ }).click();
  await expect(dialog.getByText(/下一回合生效/)).toBeVisible({
    timeout: 10_000,
  });
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();

  // 新回合：请求应打到自定义服务（桩记录 model=e2e-model）
  const before = modelRequests.length;
  const input = page.locator("#user-input");
  await expect(input).toBeEnabled({ timeout: 60_000 });
  await input.fill("我检查档案封面。");
  await page.locator("#btn-send").click();
  await expect(page.getByText("雨幕笼罩着阿卡姆").nth(1)).toBeVisible({
    timeout: 60_000,
  });
  await expect
    .poll(() => modelRequests.length, { timeout: 30_000 })
    .toBeGreaterThan(before);
  const lastNarrative = modelRequests[modelRequests.length - 1];
  expect(lastNarrative.model).toBe("e2e-model");

  // 主界面摘要：桩不给 usage → 本地估算口径
  await expect(page.locator("#context-summary-btn")).toBeVisible({
    timeout: 15_000,
  });

  // 刷新持久化：回开局页开设置（配置是本地全局，与世界无关）
  await page.reload();
  await expect(page.locator(".boot-loader")).toBeHidden({ timeout: 30_000 });
  await page.locator("#btn-settings").click();
  const dialog2 = page.getByRole("dialog", { name: "模型设置" });
  await expect(dialog2).toBeVisible();
  const narrativeCard2 = dialog2.locator('[data-role="narrative"]');
  await expect(
    narrativeCard2.getByPlaceholder("https://api.deepseek.com/v1"),
  ).toHaveValue(stubUrl);
  // Key 不回显，只显示"已配置"
  await expect(narrativeCard2.getByPlaceholder("已配置，不回显")).toHaveValue(
    "",
  );
  // 刷新后新引擎尚未跑回合：显示"尚无回合调用"；配置已从本地文件回填
  await expect(
    dialog2.getByText(/尚无回合调用|已保存 v\d+/).first(),
  ).toBeVisible();

  // 恢复默认
  await dialog2.getByRole("button", { name: "恢复默认" }).click();
  await expect(dialog2.getByText(/已恢复平台默认配置/)).toBeVisible({
    timeout: 10_000,
  });
  await expect(
    narrativeCard2.getByRole("button", { name: "跟随默认" }),
  ).toHaveAttribute("aria-pressed", "true");
});
