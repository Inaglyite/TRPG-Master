/**
 * 角色库本地全流程 E2E（真实后端 + 模型桩，不是替身服务器）：
 * 主菜单进角色库 → 导入 JSON 角色卡 → 预览确认 → 列表可见 → 页面重载仍在
 * → 开局选角页出现「角色库」分组并自动选中 → 以该调查员开始游戏。
 */

import { spawn, type ChildProcess } from "node:child_process";
import { createServer, type Server } from "node:http";
import { existsSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

import { expect, request, test } from "@playwright/test";

import { openLocalStartScreen } from "./readiness";

const port = 8771;
const baseUrl = `http://127.0.0.1:${port}`;
const repositoryRoot = resolve(import.meta.dirname, "../..");
let runtimeRoot = "";
let server: ChildProcess | null = null;
let serverOutput = "";
let modelServer: Server | null = null;

const IMPORT_CARD = {
  format: "trpg-character-card",
  format_version: 1,
  card: {
    name: "E2E导入调查员",
    occupation: "古董商",
    age: 41,
    era: "1920年代",
    attributes: {
      STR: 45,
      DEX: 50,
      CON: 55,
      INT: 70,
      POW: 60,
      SIZ: 50,
      APP: 55,
      EDU: 70,
    },
    derived: { LUCK: 55 },
    skills: { appraise: 65, library_use: 60, history: 55 },
    credit_rating: 35,
    inventory: ["放大镜"],
    backstory: { description: "戴着旧呢帽的中年人。" },
  },
};

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
      const content = "雨幕笼罩着阿卡姆，你推开了古董店的木门。";
      const chunk = {
        id: "chatcmpl-e2e-library",
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
  runtimeRoot = mkdtempSync(join(tmpdir(), "trpg-character-library-"));
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

test("本地角色库：导入→预览→列表→重载持久化→开局选中并开始", async ({
  page,
}) => {
  test.setTimeout(180_000);
  await openLocalStartScreen(page, `${baseUrl}/?mode=local`);

  // 主菜单入口打开角色库
  await page
    .locator("#start-menu-view")
    .getByRole("button", { name: "角色库" })
    .click();
  const panel = page.locator(".character-library-panel");
  await expect(panel).toBeVisible();
  await expect(page.getByText("角色库还是空的")).toBeVisible();

  for (const width of [1280, 939, 640]) {
    await page.setViewportSize({ width, height: 900 });
    const importButton = panel.getByRole("button", { name: "导入角色卡" });
    await expect(importButton).toBeInViewport();
    await expect(importButton).toBeEnabled();
    await page.screenshot({
      path: test.info().outputPath(`character-library-${width}.png`),
    });
  }
  await page.setViewportSize({ width: 1440, height: 900 });

  // 导入角色卡：选择文件 → 校验预览 → 确认导入
  await panel.getByRole("button", { name: "导入角色卡" }).click();
  await panel.locator('input[type="file"]').setInputFiles({
    name: "e2e-card.json",
    mimeType: "application/json",
    buffer: Buffer.from(JSON.stringify(IMPORT_CARD), "utf-8"),
  });
  await expect(panel.getByText("戴着旧呢帽的中年人。")).toBeVisible({
    timeout: 15_000,
  });
  await panel.getByRole("button", { name: "确认导入" }).click();
  await expect(panel.getByText("E2E导入调查员").first()).toBeVisible({
    timeout: 15_000,
  });
  await panel.getByRole("button", { name: "关闭" }).click();

  // 页面重载后角色库仍在（服务端持久化）
  await openLocalStartScreen(page, `${baseUrl}/?mode=local`);
  await page
    .locator("#start-menu-view")
    .getByRole("button", { name: "角色库" })
    .click();
  await expect(
    page.locator(".character-library-panel").getByText("E2E导入调查员"),
  ).toBeVisible({ timeout: 15_000 });
  await page
    .locator(".character-library-panel")
    .getByRole("button", { name: "关闭" })
    .click();

  // 开局选角：角色库分组出现且导入角色已自动选中（导入即记录 pending 选择）
  await page.locator("#btn-start").click();
  const libraryGroup = page.locator(".character-group", {
    has: page.locator(".character-group-title", { hasText: "角色库" }),
  });
  await expect(libraryGroup).toBeVisible({ timeout: 15_000 });
  const libraryCard = libraryGroup.locator(".character-card", {
    hasText: "E2E导入调查员",
  });
  await expect(libraryCard).toBeVisible();
  await expect(libraryCard).toHaveClass(/selected/, { timeout: 15_000 });

  // 以该调查员开始游戏（模型桩出开场白）
  await page.locator("#btn-character-confirm").click();
  await expect(page.locator("#user-input")).toBeEnabled({ timeout: 90_000 });
  // 角色面板证明开局物化的就是库角色本人
  await page.locator("#btn-panel").click();
  await expect(page.locator("#char-content")).toContainText("E2E导入调查员", {
    timeout: 30_000,
  });
});

test("角色档案夹：四种视口管理与编辑可达，本地忽略保存的云端地址", async ({
  page,
  request,
}) => {
  test.setTimeout(180_000);
  const name = "档案夹布局调查员";
  const created = await request.post(`${baseUrl}/api/character-library`, {
    data: {
      ...IMPORT_CARD,
      card: {
        ...IMPORT_CARD.card,
        name,
        backstory: {
          description: "档案应当可读，按钮应当可达。",
          background: "调查笔记。".repeat(160),
        },
      },
    },
  });
  expect(created.ok()).toBe(true);
  await page.addInitScript(() =>
    localStorage.setItem("trpg-cloud-origin", "https://unused.example.test"),
  );
  const destinations: string[] = [];
  page.on("request", (req) => {
    if (req.url().includes("/api/character-library"))
      destinations.push(req.url());
  });
  await openLocalStartScreen(page, `${baseUrl}/?mode=local`);
  const trigger = page
    .locator("#start-menu-view")
    .getByRole("button", { name: "角色库" });
  await trigger.click();
  const panel = page.getByRole("dialog", { name: "角色库" });
  await panel.locator(".library-row-main", { hasText: name }).click();
  const widths = [
    { width: 1280, height: 900 },
    { width: 939, height: 900 },
    { width: 640, height: 480 },
    { width: 390, height: 360 },
  ];
  for (const size of widths) {
    await page.setViewportSize(size);
    await page.waitForTimeout(1000);
    await expect(
      panel.getByRole("searchbox", { name: "查找档案" }),
    ).toBeVisible();
    await expect(panel.getByRole("heading", { name })).toBeAttached();
    for (const label of [
      "关闭",
      "新建角色",
      "导入角色卡",
      "编辑",
      "复制",
      "导出",
      "删除",
    ]) {
      const button = panel.getByRole("button", { name: label, exact: true });
      await expect(button).toBeInViewport();
      expect(
        await button.evaluate((node) => {
          const rect = node.getBoundingClientRect();
          const style = getComputedStyle(node);
          const hit = document.elementFromPoint(
            rect.x + rect.width / 2,
            rect.y + rect.height / 2,
          );
          return {
            height: rect.height,
            padding: parseFloat(style.paddingLeft),
            nowrap: style.whiteSpace,
            hit: hit === node || node.contains(hit),
          };
        }),
      ).toMatchObject({
        height: 44,
        padding: expect.any(Number),
        nowrap: "nowrap",
        hit: true,
      });
    }
    expect(
      await panel.evaluate((node) => node.scrollWidth <= node.clientWidth),
    ).toBe(true);
    await page.screenshot({
      path: `/tmp/trpg-library-dossier-${size.width}.png`,
    });
    await panel.getByRole("button", { name: "编辑", exact: true }).click();
    const save = panel.getByRole("button", { name: "保存修改" });
    await expect(save).toBeInViewport();
    await expect(
      panel.getByRole("button", { name: "取消", exact: true }),
    ).toBeInViewport();
    await page.screenshot({
      path: `/tmp/trpg-library-editor-${size.width}.png`,
    });
    await panel.getByRole("button", { name: "取消", exact: true }).click();
  }
  await page.setViewportSize({ width: 939, height: 900 });
  await panel.getByLabel("查找档案").fill("查无此人");
  await expect(panel.getByText(/没有匹配的档案/)).toBeVisible();
  await expect(panel.getByRole("heading", { name })).toBeVisible();
  await panel.getByLabel("查找档案").fill("古董商");
  await expect(
    panel.locator(".library-row-main", { hasText: name }),
  ).toBeVisible();
  // Hold an actual successful save response. The server mutation is not
  // cancelled by Escape; the busy UI must keep focus inside the dialog.
  let release!: () => void;
  const gate = new Promise<void>((resolveGate) => {
    release = resolveGate;
  });
  let received!: () => void;
  const ready = new Promise<void>((resolveReady) => {
    received = resolveReady;
  });
  await page.route(`${baseUrl}/api/character-library/*`, async (route) => {
    if (route.request().method() !== "PUT") {
      await route.continue();
      return;
    }
    const response = await route.fetch();
    expect(response.ok()).toBe(true);
    received();
    await gate;
    await route.fulfill({ response });
  });
  try {
    await panel.getByRole("button", { name: "编辑", exact: true }).click();
    await panel.getByRole("button", { name: "保存修改" }).click();
    await ready;
    await expect(panel).toBeFocused();
    await page.keyboard.press("Tab");
    await expect(panel).toBeFocused();
    await page.keyboard.press("Escape");
    await expect(panel).toBeVisible();
    release();
    await expect(
      panel.getByRole("button", { name: "编辑", exact: true }),
    ).toBeVisible();
  } finally {
    release();
    await page.unroute(`${baseUrl}/api/character-library/*`);
  }
  await panel.getByRole("button", { name: "关闭" }).focus();
  await page.keyboard.press("Shift+Tab");
  await expect(
    panel.getByRole("button", { name: "删除", exact: true }),
  ).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(panel.getByRole("button", { name: "关闭" })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(panel).not.toBeAttached();
  await expect(trigger).toBeFocused();
  expect(destinations.length).toBeGreaterThan(0);
  expect(destinations.every((url) => url.startsWith(baseUrl))).toBe(true);
});
