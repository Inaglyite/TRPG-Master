/**
 * 顶栏“当前场景”e2e：本地模式真实后端 + 模型桩。
 *
 * 位置只认服务端已提交的世界状态：开局显示初始场景，提交移动后更新，
 * 参与正文与叙述无关（模型桩写的是同一段与地名无关的文本）。
 * 覆盖：开局前不显示、开局显示、提交移动后更新、长地名省略但可查完整名、
 * 窄屏布局，并把截图落到 docs/screenshots/ 供人工复核。
 */
import { spawn, spawnSync, type ChildProcess } from "node:child_process";
import { createServer, type Server } from "node:http";
import { mkdtempSync, rmSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

import {
  expect,
  request,
  test,
  type Locator,
  type Page,
} from "@playwright/test";

const port = 8776;
const baseUrl = `http://127.0.0.1:${port}`;
const repositoryRoot = resolve(import.meta.dirname, "../..");
const screenshotsDir = resolve(repositoryRoot, "docs/screenshots");
let runtimeRoot = "";
let server: ChildProcess | null = null;
let serverOutput = "";
let modelServer: Server | null = null;

const NARRATIVE =
  "雨幕笼罩着阿卡姆，你在约定的办公室里见到了等待已久的委托人。" +
  "他把一份尚未拆封的档案推到桌边，示意你先听完事情的来龙去脉。" +
  "\n\n**你可以——**\n1. 请他说明委托\n2. 观察办公室" +
  "\n3. 检查档案封面\n4. [自由行动] 你决定做什么？";

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
      const parsed = JSON.parse(body || "{}");
      if (parsed.stream === false) {
        response.writeHead(200, { "Content-Type": "application/json" });
        response.end(
          JSON.stringify({
            id: "stub-nonstream",
            choices: [{ message: { role: "assistant", content: "好" } }],
          }),
        );
        return;
      }
      const chunk = {
        id: "chatcmpl-e2e-scene",
        object: "chat.completion.chunk",
        created: 1,
        model: "e2e-model",
        choices: [
          {
            index: 0,
            delta: { role: "assistant", content: NARRATIVE },
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
        // Uvicorn 仍在启动。
      }
      await new Promise((resolveWait) => setTimeout(resolveWait, 125));
    }
  } finally {
    await client.dispose();
  }
  throw new Error(`E2E server did not start:\n${serverOutput.slice(-4000)}`);
}

/** 直接改写权威世界状态，模拟一次已被引擎结算的移动。 */
function commitScene(worldId: string, sceneId: string): void {
  const script = [
    "import os, sys",
    "from pathlib import Path",
    "from src.app.runtime import RuntimeContext",
    "root = Path(os.environ['TRPG_RUNTIME_ROOT'])",
    "ctx = RuntimeContext.create(sys.argv[1], '猩红文档', runtime_root=root)",
    "with ctx.world_store.transaction() as state:",
    "    scene = state['scene_catalog'][sys.argv[2]]",
    "    state['current_scene'] = {**scene, 'npcs_present': []}",
    "print('scene committed:', sys.argv[2])",
  ].join("\n");
  const python =
    process.env.TRPG_E2E_PYTHON ??
    (existsSync(resolve(repositoryRoot, ".venv/bin/python"))
      ? resolve(repositoryRoot, ".venv/bin/python")
      : "python");
  const result = spawnSync(python, ["-c", script, worldId, sceneId], {
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
    throw new Error(`commitScene failed:\n${result.stderr || result.stdout}`);
  }
}

test.beforeAll(async () => {
  runtimeRoot = mkdtempSync(join(tmpdir(), "trpg-scene-e2e-"));
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

async function waitForInputEnabled(
  page: Page,
  input: Locator,
  timeout: number,
) {
  const suggestConfirm = page.getByRole("button", { name: /确定尝试/ });
  await expect
    .poll(
      async () => {
        if (await suggestConfirm.isVisible().catch(() => false)) {
          await suggestConfirm.click().catch(() => undefined);
        }
        return await input.isEnabled();
      },
      { timeout },
    )
    .toBe(true);
}

/** 提交一次自由行动并等到该回合结束（输入框重新可用）。 */
async function submitAction(page: Page, text: string) {
  const input = page.locator("#user-input");
  await waitForInputEnabled(page, input, 60_000);
  await input.fill(text);
  await page.locator("#btn-send").click();
  await expect(input).toBeDisabled({ timeout: 15_000 });
  await waitForInputEnabled(page, input, 90_000);
}

async function sceneLineText(page: Page): Promise<string | null> {
  const line = page.locator(".header-scene");
  if ((await line.count()) === 0) return null;
  return (await line.first().innerText()).replace(/\s+/g, "");
}

test("顶栏当前场景：开局、移动、长地名与窄屏", async ({ page }) => {
  test.setTimeout(300_000);
  await page.goto(`${baseUrl}/?mode=local`);
  await expect(page.locator(".boot-loader")).toBeHidden({ timeout: 30_000 });

  // 开局前不显示位置行。
  await expect(page.locator("#btn-start")).toBeVisible();
  expect(await sceneLineText(page)).toBeNull();

  await page.locator(".module-select-trigger").click();
  await page.getByRole("option", { name: /猩红文档/ }).click();
  await page.locator("#btn-start").click();
  const confirmCharacter = page.locator("#btn-character-confirm");
  await expect(confirmCharacter).toBeVisible();
  await confirmCharacter.click();
  await expect(page.getByText("雨幕笼罩着阿卡姆").first()).toBeVisible({
    timeout: 90_000,
  });

  // 开局后显示服务端的初始场景。
  await expect(page.locator(".header-scene")).toBeVisible({ timeout: 30_000 });
  expect(await sceneLineText(page)).toBe("当前场景·密斯卡托尼克大学");
  await page.screenshot({
    path: join(screenshotsDir, "scene-indicator-desktop.png"),
  });

  const worldId = await page.evaluate(
    () => localStorage.getItem("trpg-active-world-id") || "",
  );
  expect(worldId).not.toBe("");

  // 提交一次移动：模型桩回的还是同一段不含任何地名的正文，
  // 位置必须来自已结算的世界状态，而不是正文里的地名。
  commitScene(worldId, "miskatonic_medical");
  await submitAction(page, "我起身前往医学院，去看看莱特的遗体。");
  expect(await sceneLineText(page)).toBe("当前场景·密斯卡托尼克大学医学院");
  await expect(page.getByText(NARRATIVE.slice(0, 12)).last()).toBeVisible();
  await page.screenshot({
    path: join(screenshotsDir, "scene-indicator-after-move.png"),
  });

  // 长地名：窄屏下允许省略，完整名称仍可查看。
  commitScene(worldId, "miskatonic_history");
  await submitAction(page, "我去历史系研究生自习室找人。");
  const line = page.locator(".header-scene");
  const longLabel = "当前场景 · 密斯卡托尼克大学历史系研究生自习室";
  expect(await sceneLineText(page)).toBe(longLabel.replace(/ /g, ""));

  // 常用窗口宽度：正文标题与位置行同处左上，工具栏不被挤压。
  await page.setViewportSize({ width: 939, height: 900 });
  await expect(line).toBeVisible();
  await expect(line).toHaveAttribute("title", longLabel);
  await page.screenshot({
    path: join(screenshotsDir, "scene-indicator-long-name.png"),
  });

  // 多宽度回归：位置行始终可见、不超出视口、不与工具栏重叠。
  for (const width of [1280, 760, 640, 560, 520, 430, 390]) {
    await page.setViewportSize({ width, height: 780 });
    await expect(line).toBeVisible();
    const lineBox = await line.boundingBox();
    const toolbarBox = await page.locator("#toolbar").boundingBox();
    expect(lineBox, `位置行在 ${width}px 不可见`).not.toBeNull();
    expect(lineBox!.width, `位置行在 ${width}px 超出视口`).toBeLessThanOrEqual(
      width,
    );
    expect(toolbarBox, `工具栏在 ${width}px 不可见`).not.toBeNull();
    const overlap =
      Math.min(lineBox!.x + lineBox!.width, toolbarBox!.x + toolbarBox!.width) -
        Math.max(lineBox!.x, toolbarBox!.x) >
        0 &&
      Math.min(
        lineBox!.y + lineBox!.height,
        toolbarBox!.y + toolbarBox!.height,
      ) -
        Math.max(lineBox!.y, toolbarBox!.y) >
        0;
    expect(overlap, `位置行与工具栏在 ${width}px 重叠`).toBe(false);
    // 位置行不挤压按钮：每个工具栏按钮都有可见宽度。
    const buttonBox = await page.locator("#btn-panel").boundingBox();
    expect(buttonBox?.width ?? 0).toBeGreaterThan(30);
  }

  await page.setViewportSize({ width: 390, height: 780 });
  await expect(line).toHaveAttribute("title", longLabel);
  await page.screenshot({
    path: join(screenshotsDir, "scene-indicator-narrow.png"),
  });

  // 回到开局选择：位置行随游戏一起收起。
  await page.locator("#btn-new").click();
  await expect(page.locator("#btn-start")).toBeVisible({ timeout: 30_000 });
  await expect(page.locator(".header-scene")).toHaveCount(0);
});
