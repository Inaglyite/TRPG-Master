/**
 * 后端缺口 #1 的独立、可重复验收：**请求状态与已提交世界事实的同步**。
 *
 * 背景（Kimi 负责修，见交付记录 §9.4.1）：移动命令把目标一致的开放线程
 * `auto_complete_move_threads` 收尾为 completed，但那条 `awaiting_player` 请求
 * 没有被同步收尾 —— 玩家卡片继续显示「尚未执行：尚未出发前往X」，而队伍已经
 * 站在 X。世界状态与待办自相矛盾。
 *
 * 本文件是这两个行为的**可执行验收**（不是 fixme）：
 *   A. 移动完成后旧「尚未出发」消失，且实时与刷新一致；
 *   B. 复合请求抵达后仍保留未完成调查，只清掉已由世界事实满足的那部分。
 *
 * 在后端修复落地前，这两条会**真的失败**（失败即缺口证据，不隐藏、不用前端
 * 遮掩）；后端修复后应自动转绿，无需改动本文件。
 *
 * 世界在临时 runtime root；人类主持。开局那一步要用模型（世界创建时的开场回合），
 * 用会计数的模型桩；**开局之后不再允许任何模型调用**（每个用例末尾断言计数不变）。
 */

import { spawn, spawnSync, type ChildProcess } from "node:child_process";
import { createServer, type Server } from "node:http";
import { existsSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

import { expect, request, test, type Page } from "@playwright/test";

const port = 8781;
const baseUrl = `http://127.0.0.1:${port}`;
const repositoryRoot = resolve(import.meta.dirname, "../..");
const screenshotsDir = resolve(repositoryRoot, "docs/screenshots");
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
      modelRequests.push(body.slice(0, 200));
      const chunk = {
        id: "chatcmpl-pending-sync",
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

test.beforeAll(async () => {
  runtimeRoot = mkdtempSync(join(tmpdir(), "trpg-pending-sync-"));
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
  await waitForServer();
});

test.afterAll(async () => {
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

/** 事件类帧（含 outbox 事件）里按 type 取 payload。 */
function eventPayload(frames: string[], type: string) {
  return frames
    .filter((frame) => frame.includes(`"type":"${type}"`))
    .map(
      (frame) =>
        (JSON.parse(frame) as { payload?: Record<string, unknown> }).payload ??
        {},
    );
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
  await expect(page.getByRole("dialog", { name: "主持台" })).toBeHidden({
    timeout: 10_000,
  });
  await page.getByTestId("btn-keeper-console").click();
  await expect(page.getByRole("dialog", { name: "主持台" })).toBeVisible();
}

async function closeConsole(page: Page) {
  await page.getByRole("button", { name: "关闭主持台" }).click();
  await expect(page.getByRole("dialog", { name: "主持台" })).toBeHidden({
    timeout: 10_000,
  });
}

async function bootWorld(
  page: Page,
  frames: Frames,
): Promise<{ worldId: string; modelCallsAfterBoot: number }> {
  await page.goto(`${baseUrl}/?mode=local`);
  await expect(page.locator(".boot-loader")).toBeHidden({ timeout: 30_000 });
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
  return { worldId, modelCallsAfterBoot: modelRequests.length };
}

async function playerText(
  page: Page,
  frames: Frames,
  text: string,
): Promise<string> {
  const count = () =>
    frames.sent.filter((frame) => frame.includes('"type":"action_request"'))
      .length;
  const before = count();
  await page.locator("#user-input").fill(text);
  await page.locator("#btn-send").click();
  await expect.poll(count, { timeout: 30_000 }).toBeGreaterThan(before);
  const frame = frames.sent
    .filter((item) => item.includes('"type":"action_request"'))
    .at(-1) as string;
  return (JSON.parse(frame) as { request_id: string }).request_id;
}

/** 主持记录「尚未执行」的待办（awaiting_player + 自动落线程）。 */
async function keeperPark(
  page: Page,
  requestId: string,
  options: {
    kind?: string;
    note: string;
    destinationId?: string;
  },
) {
  await openConsole(page);
  await page.getByTestId("keeper-cmd-resolve_intent").click();
  await fillKeeperField(page, "request_id", requestId);
  await fillKeeperField(page, "resolution", "awaiting_player");
  await fillKeeperField(page, "pending_action_kind", options.kind ?? "move");
  await fillKeeperField(page, "pending_action_note", options.note);
  if (options.destinationId) {
    await fillKeeperField(
      page,
      "pending_action_destination",
      options.destinationId,
    );
  }
  await page.getByTestId("keeper-submit").click();
  await expect(page.getByTestId("keeper-submit")).toBeEnabled({
    timeout: 30_000,
  });
  await closeConsole(page);
}

async function keeperMove(page: Page, destinationId: string) {
  await openConsole(page);
  await page.getByTestId("keeper-cmd-move_party").click();
  await fillKeeperField(page, "destination_scene_id", destinationId);
  await page.getByTestId("keeper-submit").click();
  await expect(page.getByTestId("keeper-submit")).toBeEnabled({
    timeout: 30_000,
  });
  await closeConsole(page);
}

async function openMoveDialog(
  page: Page,
): Promise<Array<{ id: string; name: string }>> {
  await page.getByTestId("btn-move").click();
  await expect(page.getByRole("dialog", { name: /前往/ })).toBeVisible({
    timeout: 10_000,
  });
  return page.locator(".structured-destination").evaluateAll((nodes) =>
    nodes.map((node) => ({
      name: (
        node.querySelector(".structured-destination-name")?.textContent ?? ""
      ).trim(),
      id: (
        node.querySelector(".structured-destination-id")?.textContent ?? ""
      ).trim(),
    })),
  );
}

/** 所有玩家可见的「尚未执行」文案（线程卡与请求级 awaiting 明细都在内）。 */
function pendingTexts(page: Page) {
  return page.getByText(/尚未执行：/);
}

test("A. 移动请求完成后旧「尚未出发」消失，实时与刷新一致", async ({
  page,
}) => {
  test.setTimeout(420_000);
  page.setDefaultTimeout(30_000);
  const frames = collectFrames(page);
  const boot = await bootWorld(page, frames);
  const sceneBefore = await page.locator(".header-scene-name").innerText();
  const destinations = await openMoveDialog(page);
  const target = destinations.find((item) => item.name !== sceneBefore)!;
  expect(target, "前往列表里应有不是当前场景的目的地").toBeTruthy();
  await page.getByRole("button", { name: "取消" }).click();

  const wishId = await playerText(
    page,
    frames,
    `我想去${target.name}看看遗体。`,
  );
  await keeperPark(page, wishId, {
    note: `尚未出发前往${target.name}`,
    destinationId: target.id,
  });
  await expect(pendingTexts(page)).toHaveCount(1);
  expect(await page.locator(".header-scene-name").innerText()).toBe(
    sceneBefore,
  );

  // 主持执行移动：世界事实已经变了，那条「尚未出发」必须随之一并消失
  await keeperMove(page, target.id);
  await expect
    .poll(() => page.locator(".header-scene-name").innerText(), {
      timeout: 30_000,
    })
    .toBe(target.name);

  // 实时：不再有任何「尚未执行：尚未出发…」
  await expect(
    pendingTexts(page),
    "人已经到达，卡片上不能还写着「尚未出发」",
  ).toHaveCount(0, { timeout: 30_000 });
  // 刷新后同样不能复活（实时与刷新一致）
  await page.reload();
  await expect(page.locator(".boot-loader")).toBeHidden({ timeout: 30_000 });
  await expect
    .poll(() => page.locator(".header-scene-name").innerText(), {
      timeout: 60_000,
    })
    .toBe(target.name);
  await expect(
    pendingTexts(page),
    "刷新后也不该出现已被世界事实满足的旧待办",
  ).toHaveCount(0, { timeout: 30_000 });
  // 崩溃点证据：请求侧状态（决定刷新时会不会复活）
  const snapshot = JSON.parse(
    framesOf(frames, "session_snapshot").at(-1) as string,
  ) as { payload?: { requests?: Array<Record<string, unknown>> } };
  expect(
    snapshot.payload?.requests ?? [],
    "快照 requests[] 里不应再有这条未收尾的移动请求",
  ).not.toEqual(
    expect.arrayContaining([
      expect.objectContaining({
        request_id: wishId,
        status: "awaiting_player",
      }),
    ]),
  );
  await page.screenshot({
    path: `${screenshotsDir}/pending-sync-after-move.png`,
  });
  // 开局之后零模型调用（人类主持的判定不该碰模型）
  expect(modelRequests.length).toBe(boot.modelCallsAfterBoot);
});

test("B. 复合请求抵达后仍保留未完成调查，只清掉已完成的那部分", async ({
  page,
}) => {
  test.setTimeout(420_000);
  page.setDefaultTimeout(30_000);
  const frames = collectFrames(page);
  const boot = await bootWorld(page, frames);
  const sceneBefore = await page.locator(".header-scene-name").innerText();
  const destinations = await openMoveDialog(page);
  const target = destinations.find((item) => item.name !== sceneBefore)!;
  await page.getByRole("button", { name: "取消" }).click();

  // 复合意图：过去看遗体 + 到了还要翻值班记录
  const compoundId = await playerText(
    page,
    frames,
    `我想去${target.name}看遗体，到了还要翻一下值班记录。`,
  );
  await keeperPark(page, compoundId, {
    note: `尚未出发前往${target.name}（到了还要查值班记录）`,
    destinationId: target.id,
  });
  // 未完成的那一半：另开一条调查待办（玩家的补充请求）
  const rosterId = await playerText(page, frames, "值班记录那件事也别忘了。");
  await keeperPark(page, rosterId, {
    kind: "other",
    note: "尚未查看值班记录",
  });
  await expect(pendingTexts(page)).toHaveCount(2);
  await page.screenshot({
    path: `${screenshotsDir}/pending-sync-compound-before.png`,
  });

  // 抵达：移动那一半由世界事实满足，调查那一半仍未完成
  await keeperMove(page, target.id);
  await expect
    .poll(() => page.locator(".header-scene-name").innerText(), {
      timeout: 30_000,
    })
    .toBe(target.name);

  await expect(
    page.getByText(/尚未查看值班记录/),
    "尚未完成的调查必须留在台面上",
  ).toBeVisible({ timeout: 30_000 });
  await expect(
    page.getByText(/尚未出发前往/),
    "已由抵达满足的移动待办必须消失",
  ).toHaveCount(0, { timeout: 30_000 });

  // 刷新后同样：调查还在、移动待办不复活
  await page.reload();
  await expect(page.locator(".boot-loader")).toBeHidden({ timeout: 30_000 });
  await expect(
    page.getByText(/尚未查看值班记录/),
    "刷新后未完成的调查仍在",
  ).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText(/尚未出发前往/)).toHaveCount(0);
  expect(await page.locator(".header-scene-name").innerText()).toBe(
    target.name,
  );
  await page.screenshot({
    path: `${screenshotsDir}/pending-sync-compound-after.png`,
  });
  expect(modelRequests.length).toBe(boot.modelCallsAfterBoot);
});
