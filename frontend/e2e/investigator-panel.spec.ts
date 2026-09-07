/**
 * 调查员侧栏三卡片 e2e：本地模式真实后端 + 模型桩。
 *
 * 覆盖：三卡片渲染/折叠、线索筛选与详情、出示编辑器（取消/空值禁用/提交）、
 * 使用编辑器（提交后进聊天回合）、Escape 关闭、以及提交只走普通 action 入口。
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

const port = 8768;
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
        "雨幕笼罩着阿卡姆，你在约定的办公室里见到了等待已久的委托人。" +
        "他把一份尚未拆封的档案推到桌边，示意你先听完事情的来龙去脉。" +
        "\n\n**你可以——**\n1. 请他说明委托\n2. 观察办公室" +
        "\n3. 检查档案封面\n4. [自由行动] 你决定做什么？";
      const chunk = {
        id: "chatcmpl-e2e-panel",
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
        // Uvicorn 仍在启动。
      }
      await new Promise((resolveWait) => setTimeout(resolveWait, 125));
    }
  } finally {
    await client.dispose();
  }
  throw new Error(`E2E server did not start:\n${serverOutput.slice(-4000)}`);
}

test.beforeAll(async () => {
  runtimeRoot = mkdtempSync(join(tmpdir(), "trpg-panel-e2e-"));
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
        ...process.env,
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

test("三卡片布局、折叠、出示与使用行动编辑器全流程", async ({ page }) => {
  test.setTimeout(180_000);
  await page.goto(`${baseUrl}/?mode=local`);
  await expect(page.locator(".boot-loader")).toBeHidden({ timeout: 30_000 });
  await expect(page.locator("#btn-start")).toBeVisible();
  // 使用猩红文档模组：开局即带线索与道具，覆盖三卡片全部数据路径。
  await page.locator(".module-select-trigger").click();
  await page.getByRole("option", { name: /猩红文档/ }).click();
  await page.locator("#btn-start").click();
  const confirmCharacter = page.locator("#btn-character-confirm");
  await expect(confirmCharacter).toBeVisible();
  await confirmCharacter.click();
  await expect(page.getByText("雨幕笼罩着阿卡姆").first()).toBeVisible({
    timeout: 60_000,
  });
  const input = page.locator("#user-input");
  await waitForInputEnabled(page, input, 60_000);

  // 打开调查员侧栏：三张独立卡片。
  await page.locator("#btn-panel").click();
  const statusToggle = page.locator("#inv-card-toggle-status");
  const cluesToggle = page.locator("#inv-card-toggle-clues");
  const itemsToggle = page.locator("#inv-card-toggle-items");
  await expect(statusToggle).toBeVisible();
  await expect(cluesToggle).toBeVisible();
  await expect(itemsToggle).toBeVisible();
  await expect(statusToggle).toHaveAttribute("aria-expanded", "true");

  // 线索卡：猩红文档开局的死亡通告线索在“探案”分组；筛选可切换。
  await expect(
    page.locator('[data-clue^="investigation:"]').first(),
  ).toBeVisible();
  await page.getByRole("tab", { name: "事件" }).click();
  await expect(page.getByText("该分类暂无线索")).toBeVisible();
  await page.getByRole("tab", { name: "全部" }).click();

  // 折叠不发送回合：折叠道具卡后聊天区无新消息。
  const messagesBefore = await page.locator(".msg").count();
  await itemsToggle.click();
  await expect(itemsToggle).toHaveAttribute("aria-expanded", "false");
  await itemsToggle.click();
  await expect(page.locator(".msg")).toHaveCount(messagesBefore);

  // 出示：取消不产生任何行动。
  const firstPresent = page
    .locator('[data-clue^="investigation:"] .inv-present-btn')
    .first();
  await firstPresent.click();
  const dialog = page.getByRole("dialog", { name: "出示线索" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("button", { name: "确认出示" })).toBeDisabled();
  await dialog.getByRole("button", { name: "取消", exact: true }).click();
  await expect(dialog).toBeHidden();
  await expect(page.locator(".msg")).toHaveCount(messagesBefore);

  // 出示：填对象后提交，进入普通行动回合。
  await firstPresent.click();
  await expect(dialog).toBeVisible();
  await dialog
    .getByPlaceholder("例如：惠特克罗夫特医生")
    .fill("惠特克罗夫特医生");
  await expect(dialog.getByRole("button", { name: "确认出示" })).toBeEnabled();
  await dialog.getByRole("button", { name: "确认出示" }).click();
  await expect(dialog).toBeHidden();
  await expect(
    page.getByText(/我向惠特克罗夫特医生说明我已知的线索：/).first(),
  ).toBeVisible();
  await waitForInputEnabled(page, input, 60_000);

  // 使用：开局道具行的原始标签直接从 DOM 取（不同角色自带物品不同）。
  const firstItemLabel = await page
    .locator(".inv-item-row")
    .first()
    .getAttribute("data-item");
  expect(firstItemLabel).toBeTruthy();
  const useButtons = page.locator(".inv-use-btn");
  await expect(useButtons.first()).toBeVisible();
  await useButtons.first().click();
  const useDialog = page.getByRole("dialog", { name: "使用道具" });
  await expect(
    useDialog.getByRole("button", { name: "确认使用" }),
  ).toBeDisabled();
  await useDialog
    .getByPlaceholder("例如：照亮床底，检查是否有可见物品")
    .fill("试着打开办公室的门锁");
  await useDialog.getByRole("button", { name: "确认使用" }).click();
  await expect(useDialog).toBeHidden();
  await expect(
    page
      .getByText(`我尝试用「${firstItemLabel}」试着打开办公室的门锁。`)
      .first(),
  ).toBeVisible();
  await waitForInputEnabled(page, input, 60_000);

  // Escape 关闭编辑器，不产生行动。
  const bubblesBefore = await page.locator(".msg.player").count();
  await useButtons.first().click();
  await expect(useDialog).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(useDialog).toBeHidden();
  await expect(page.locator(".msg.player")).toHaveCount(bubblesBefore);
});
