/**
 * 生命周期（读档 / 重连 / 分支）在结构化世界里的真实后端 E2E（人类主持，无模型）。
 *
 * 覆盖：
 * A. 主动读档：存档点之后的进度不复活——场景回滚、被回滚的交互线程不再显示、
 *    非终态请求收成 failed（不是继续「等你回应」）、存档点之后的记忆查不到。
 * B. 重连（刷新/重连）不等于读档：当前进度保留，不重放已提交事件。
 * C. 分支：结构化世界从「当前已提交状态」分叉（没有旧回合也必须有入口）；
 *    分支共享分叉点的记忆，但检索不到原世界分叉之后新增的记忆（按 world_id 隔离）。
 *
 * 记忆隔离的最后一步用同一真实库做断言（分支世界与原世界是两个 world_id，
 * 前端界面一次只显示一个世界，所以这一条不通过 UI 展示，注释里写明）。
 */

import { spawn, spawnSync, type ChildProcess } from "node:child_process";
import { createServer, type Server } from "node:http";
import { existsSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

import { expect, request, test, type Page } from "@playwright/test";

import { openLocalStartScreen } from "./readiness";

const port = 8780;
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

function databasePath(): string {
  return `sqlite:///${join(runtimeRoot, "e2e.db")}`;
}

/** 把隔离世界切成 structured_v1（人类主持）。 */
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
      TRPG_DATABASE_URL: databasePath(),
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

/**
 * 分支世界的记忆可见性（真实库断言）。
 *
 * 界面一次只显示一个世界，而这条要比较「原世界新增的记忆」在分支里是否可查，
 * 所以直接读同一个真实库的 character_memories：不算 UI 用例，但结论来自真实数据。
 */
function runBackend(script: string, args: string[]): string {
  const result = spawnSync(pythonPath(), ["-c", script, ...args], {
    cwd: repositoryRoot,
    env: {
      ...process.env,
      TRPG_RUNTIME_ROOT: runtimeRoot,
      TRPG_DATABASE_URL: databasePath(),
      TRPG_WRITE_COMPAT_EXPORTS: "0",
    },
    encoding: "utf-8",
  });
  if (result.status !== 0) {
    throw new Error(
      `backend script failed:\n${result.stderr || result.stdout}`,
    );
  }
  return result.stdout.trim();
}

/** 会计数的模型桩：只服务于本地开局那一步，本组用例的业务步骤不调用模型。 */
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
        id: "chatcmpl-load-branch",
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
  runtimeRoot = mkdtempSync(join(tmpdir(), "trpg-load-branch-"));
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
        TRPG_DATABASE_URL: databasePath(),
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
  await expect
    .poll(() => framesOf(frames, "session_snapshot").length, {
      timeout: 30_000,
    })
    .toBeGreaterThan(0);
  return worldId;
}

/** 玩家发一条自由文本请求，返回 request_id。 */
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

/** 主持记录「尚未执行」的待办（开一条交互线程）。 */
async function keeperPark(
  page: Page,
  requestId: string,
  destinationId: string,
  note: string,
) {
  await openConsole(page);
  await page.getByTestId("keeper-cmd-resolve_intent").click();
  await fillKeeperField(page, "request_id", requestId);
  await fillKeeperField(page, "resolution", "awaiting_player");
  await fillKeeperField(page, "pending_action_kind", "move");
  await fillKeeperField(page, "pending_action_note", note);
  await fillKeeperField(page, "pending_action_destination", destinationId);
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

/** 主持记录一条角色记忆（保持主持台打开，便于接着查询）。 */
async function recordMemory(
  page: Page,
  investigatorId: string,
  content: string,
  topics: string,
) {
  await expect(page.getByTestId("keeper-memory-query")).toBeVisible();
  await page.getByTestId("keeper-cmd-record_memory").click();
  await fillKeeperField(page, "character_id", investigatorId);
  await fillKeeperField(page, "knowledge_type", "experienced");
  await fillKeeperField(page, "content", content);
  await fillKeeperField(page, "topics", topics);
  await page.getByTestId("keeper-submit").click();
  await expect(page.getByTestId("keeper-submit")).toBeEnabled({
    timeout: 30_000,
  });
}

/** 主持只读记忆查询；返回结果文本（无命中时返回空字符串）。 */
async function queryMemories(page: Page, topic: string): Promise<string> {
  await fillKeeperField(page, "memory_topics", topic);
  await page.getByTestId("keeper-memory-submit").click();
  const results = page.getByTestId("keeper-memory-results");
  const empty = page.getByTestId("keeper-memory-empty");
  await expect
    .poll(
      async () => {
        if (await results.isVisible().catch(() => false)) return "results";
        if (await empty.isVisible().catch(() => false)) return "empty";
        return "pending";
      },
      { timeout: 60_000 },
    )
    .not.toBe("pending");
  if (await results.isVisible().catch(() => false)) {
    return results.innerText();
  }
  return "";
}

/** 打开「前往」对话框并读取实时目的地列表。 */
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

/** 主持快速存档，等到服务端回执 ok。 */
async function quickSave(page: Page, frames: Frames) {
  const before = framesOf(frames, "saved").length;
  await openConsole(page);
  await page.getByTestId("keeper-save").click();
  await expect
    .poll(() => framesOf(frames, "saved").length, { timeout: 60_000 })
    .toBeGreaterThan(before);
  const saved = JSON.parse(framesOf(frames, "saved").at(-1) as string) as {
    ok?: boolean;
    slot_id?: string;
    reason?: string;
  };
  expect(saved.ok, `存档失败：${JSON.stringify(saved)}`).toBe(true);
  await closeConsole(page);
}

/** 主持读档（服务端回 loaded 回执 + 全新快照）。 */
async function loadAutoSave(page: Page, frames: Frames) {
  const before = framesOf(frames, "loaded").length;
  const snapshotsBefore = framesOf(frames, "session_snapshot").length;
  await openConsole(page);
  await page.getByTestId("keeper-load").click();
  await expect
    .poll(() => framesOf(frames, "loaded").length, { timeout: 60_000 })
    .toBeGreaterThan(before);
  await closeConsole(page);
  // 读档后必须以新快照重同步（世界 revision 已回退）
  await expect
    .poll(() => framesOf(frames, "session_snapshot").length, {
      timeout: 60_000,
    })
    .toBeGreaterThan(snapshotsBefore);
}

test("主动读档：存档点之后的进度不复活；重连不等于读档", async ({ page }) => {
  test.setTimeout(600_000);
  page.setDefaultTimeout(30_000);
  const frames = collectFrames(page);
  await bootWorld(page, frames);
  const sceneBefore = await page.locator(".header-scene-name").innerText();
  const snapshot = JSON.parse(
    framesOf(frames, "session_snapshot").at(-1) as string,
  ) as { payload?: { investigator_id?: string } };
  const investigatorId = String(snapshot.payload?.investigator_id || "pc");
  const modelCallsAfterBoot = modelRequests.length;
  const preMemory = "存档点之前：在停尸间确认了遗体上的旧伤。";
  await openConsole(page);
  await recordMemory(page, investigatorId, preMemory, "e2e-readsave");
  expect(await queryMemories(page, "e2e-readsave")).toContain(preMemory);

  // 1) 存档：此后的一切都属于「被回滚的未来」
  await closeConsole(page);
  await quickSave(page, frames);

  // 2) 存档点之后：一条记忆、一条未执行待办、一次真实移动、又一条未执行待办
  const postMemory = "存档点之后：在码头找到了失窃的木箱。";
  await openConsole(page);
  await recordMemory(page, investigatorId, postMemory, "e2e-readsave");
  expect(
    await queryMemories(page, "e2e-readsave"),
    "读档前：存档点之后的记忆应当可见（作为对照）",
  ).toContain(postMemory);
  await closeConsole(page);

  const destinations = await openMoveDialog(page);
  const target = destinations[0];
  await page.getByRole("button", { name: "取消" }).click();

  const pendingId = await playerText(
    page,
    frames,
    `我想去${target.name}看看。`,
  );
  await keeperPark(page, pendingId, target.id, `尚未出发前往${target.name}`);
  await keeperMove(page, target.id);
  await expect
    .poll(() => page.locator(".header-scene-name").innerText(), {
      timeout: 30_000,
    })
    .toBe(target.name);
  const staleId = await playerText(page, frames, "那我还想去别的地方查档案。");
  await keeperPark(page, staleId, target.id, "尚未出发查档案");
  const interactionCard = page.getByTestId("structured-interaction-card");
  await expect(interactionCard).toBeVisible({ timeout: 30_000 });
  await page.screenshot({
    path: `${screenshotsDir}/structured-load-before.png`,
  });

  // 3) 重连（刷新）不是读档：当前进度保留，不重放已提交事件
  const sceneEventsBeforeReload = eventPayload(
    frames.received,
    "scene_changed",
  ).length;
  await page.reload();
  await expect(page.locator(".boot-loader")).toBeHidden({ timeout: 30_000 });
  await expect
    .poll(() => page.locator(".header-scene-name").innerText(), {
      timeout: 60_000,
    })
    .toBe(target.name);
  await expect(page.getByTestId("structured-interaction-card")).toBeVisible({
    timeout: 60_000,
  });
  expect(
    eventPayload(frames.received, "scene_changed").length,
    "重连只能靠快照恢复，不能重放事件",
  ).toBe(sceneEventsBeforeReload);

  // 4) 主动读档：回到存档点
  await loadAutoSave(page, frames);
  await expect
    .poll(() => page.locator(".header-scene-name").innerText(), {
      timeout: 60_000,
    })
    .toBe(sceneBefore);
  // 被回滚的交互线程不再显示（失效待办不能挂着）
  await expect(page.getByTestId("structured-interaction-card")).toHaveCount(0);
  await expect(page.getByTestId("structured-awaiting")).toHaveCount(0);
  await expect(page.getByText("等你回应")).toHaveCount(0);
  // 存档点之后的记忆查不到，存档点之前的仍在
  await openConsole(page);
  const afterLoad = await queryMemories(page, "e2e-readsave");
  expect(afterLoad).toContain(preMemory);
  expect(
    afterLoad,
    "存档点之后的记忆必须随回滚删除，不能在检索里泄漏未来",
  ).not.toContain(postMemory);
  await closeConsole(page);
  await page.screenshot({
    path: `${screenshotsDir}/structured-load-after.png`,
  });
  // 读档/重连过程零模型调用（人类主持）
  expect(modelRequests.length).toBe(modelCallsAfterBoot);
});

test("分支：结构化世界可从当前进度分叉，且检索不到原世界分叉后的记忆", async ({
  page,
}) => {
  test.setTimeout(600_000);
  page.setDefaultTimeout(30_000);
  const frames = collectFrames(page);
  const sourceWorldId = await bootWorld(page, frames);
  const snapshot = JSON.parse(
    framesOf(frames, "session_snapshot").at(-1) as string,
  ) as { payload?: { investigator_id?: string } };
  const investigatorId = String(snapshot.payload?.investigator_id || "pc");

  // 分叉点之前的共同经历
  const sharedMemory = "分叉点之前的共同经历：在图书馆查到了报纸缩微胶卷。";
  await openConsole(page);
  await recordMemory(page, investigatorId, sharedMemory, "e2e-fork");
  expect(await queryMemories(page, "e2e-fork")).toContain(sharedMemory);
  await closeConsole(page);

  // 从当前进度创建分支（结构化世界没有旧回合，入口依然必须在）
  await openConsole(page);
  // 存档面板会覆盖并顶掉主持台：点开后不能再按「关闭主持台」
  await page.getByTestId("keeper-save-panel").click();
  await expect(page.locator("#save-panel-overlay")).toBeVisible({
    timeout: 30_000,
  });
  // 当前世界那一张卡（同库可能有别的世界）→ 管理时间线
  await page
    .locator(".adventure-card.current .adventure-manage")
    .first()
    .click();
  await expect(page.getByTestId("save-panel-timelines")).toBeVisible({
    timeout: 30_000,
  });
  const branchButton = page.locator(".timeline-branch-create");
  await expect(
    branchButton,
    "结构化世界的分支入口应当可见（分支点是当前已提交状态）",
  ).toBeVisible({ timeout: 30_000 });
  await page.locator(".timeline-branch-input").fill("E2E 分支");
  const branchBefore = framesOf(frames, "turn_branched").length;
  await branchButton.click();
  await expect
    .poll(() => framesOf(frames, "turn_branched").length, { timeout: 90_000 })
    .toBeGreaterThan(branchBefore);
  const branched = JSON.parse(
    framesOf(frames, "turn_branched").at(-1) as string,
  ) as { world_id?: string; source_turn_id?: string; label?: string };
  const branchWorldId = String(branched.world_id || "");
  expect(branchWorldId).not.toBe("");
  expect(branchWorldId).not.toBe(sourceWorldId);
  expect(branched.source_turn_id ?? "").toBe("");
  // 分支创建后存档面板可能已自行关闭；没关就手动关掉，避免遮挡主界面
  if (
    await page
      .locator("#save-panel-close")
      .isVisible()
      .catch(() => false)
  ) {
    await page.locator("#save-panel-close").click();
  }
  await expect(page.locator("#save-panel-overlay")).toBeHidden({
    timeout: 30_000,
  });
  await expect(page.getByTestId("structured-tool-row")).toBeVisible({
    timeout: 60_000,
  });

  // 回到原世界再新增一条记忆（分叉之后才发生）：界面此时在分支世界，
  // 所以这一步直接调用同一个真实后端的命令服务写源世界。
  const laterMemory = "分叉之后才知道的事：木箱里是空的。";
  runBackend(
    [
      "import os, sys",
      "from pathlib import Path",
      "from sqlalchemy import select",
      "from src.storage.database import WorldMember, session_scope, database_url",
      "from src.structured.service import StructuredPlayService",
      "from src.structured.principal import Principal",
      "root = Path(os.environ['TRPG_RUNTIME_ROOT'])",
      "world_id, character_id, content = sys.argv[1], sys.argv[2], sys.argv[3]",
      "with session_scope(database_url(root)) as session:",
      "    member = session.execute(select(WorldMember).where(",
      "        WorldMember.world_id == world_id,",
      "        WorldMember.can_keeper.is_(True))).scalars().first()",
      "    if member is None: raise SystemExit('keeper member missing')",
      "    keeper_id = member.user_id",
      "service = StructuredPlayService(database_url(root))",
      "service.execute_command(",
      "    world_id=world_id,",
      "    principal=Principal(kind='keeper', user_id=keeper_id),",
      "    kind='record_memory',",
      "    payload={",
      "        'character_id': character_id,",
      "        'knowledge_type': 'experienced',",
      "        'content': content,",
      "        'topics': ['e2e-fork'],",
      "    },",
      "    command_id='e2e-later-memory',",
      "    expected_revision=None,",
      ")",
      "print('later memory written')",
    ].join("\n"),
    [sourceWorldId, investigatorId, laterMemory],
  );

  // 真实库断言：分支共享分叉点的记忆，但看不到原世界分叉之后新增的记忆
  const visibility = runBackend(
    [
      "import os, sys",
      "from pathlib import Path",
      "from sqlalchemy import select",
      "from src.storage.database import CharacterMemory, session_scope, database_url",
      "root = Path(os.environ['TRPG_RUNTIME_ROOT'])",
      "source, branch, topic = sys.argv[1], sys.argv[2], sys.argv[3]",
      "with session_scope(database_url(root)) as session:",
      "    def contents(world_id):",
      "        rows = session.execute(select(CharacterMemory).where(",
      "            CharacterMemory.world_id == world_id)).scalars().all()",
      "        return sorted(row.content for row in rows if topic in (row.topics or []))",
      "    print('SOURCE=' + ' | '.join(contents(source)))",
      "    print('BRANCH=' + ' | '.join(contents(branch)))",
    ].join("\n"),
    [sourceWorldId, branchWorldId, "e2e-fork"],
  );
  const sourceLine = visibility
    .split("\n")
    .find((line) => line.startsWith("SOURCE=")) as string;
  const branchLine = visibility
    .split("\n")
    .find((line) => line.startsWith("BRANCH=")) as string;
  expect(sourceLine).toContain(sharedMemory);
  expect(sourceLine).toContain(laterMemory);
  expect(branchLine, "分支必须继承分叉点的记忆").toContain(sharedMemory);
  expect(branchLine, "分支不得检索到原世界分叉之后新增的记忆").not.toContain(
    laterMemory,
  );
  await page.screenshot({
    path: `${screenshotsDir}/structured-branch-created.png`,
  });
});
