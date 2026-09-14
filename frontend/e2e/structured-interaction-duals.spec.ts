/**
 * 交互可靠性的本地真实后端 E2E（人类主持，无模型）：
 *
 * A. 对偶：普通直接移动（前往按钮 → 主持执行，不额外多问一轮）、换目的地、取消。
 * B. 刷新不丢公开待办、且不会重复执行（同一动作只落账一次）。
 * C. agent 世界缺 BYOK 时的失败体验：请求明确暂停、输入可用、无永久转圈、
 *    主机可接管（本地操作者具备 can_keeper）。
 *
 * 世界在临时 runtime root；模型 base URL 指向关闭端口，且这些用例都不需要模型。
 */

import { spawn, spawnSync, type ChildProcess } from "node:child_process";
import { createServer, type Server } from "node:http";
import { existsSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

import { expect, request, test, type Page } from "@playwright/test";

const port = 8775;
const baseUrl = `http://127.0.0.1:${port}`;
const repositoryRoot = resolve(import.meta.dirname, "../..");
const screenshotsDir = resolve(import.meta.dirname, "../../docs/screenshots");
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

/** 把隔离世界切成 structured_v1，并按需设置 keeper_mode。 */
function configureWorld(
  worldId: string,
  keeperMode: "human" | "agent",
): string {
  const script = [
    "import os, sys",
    "from pathlib import Path",
    "from src.storage.database import World, session_scope, database_url",
    "from src.structured.bootstrap import apply_profile_metadata, ensure_local_operator",
    "root = Path(os.environ['TRPG_RUNTIME_ROOT'])",
    "world_id, keeper_mode = sys.argv[1], sys.argv[2]",
    "with session_scope(database_url(root)) as session:",
    "    world = session.get(World, world_id)",
    "    if world is None: raise SystemExit('world not found')",
    "    world.metadata_json = apply_profile_metadata(world.metadata_json,",
    "        execution_profile='structured_v1', keeper_mode=keeper_mode)",
    "    ensure_local_operator(session, world_id)",
    "    session.add(world)",
    "print('world ready')",
  ].join("\n");
  const result = spawnSync(pythonPath(), ["-c", script, worldId, keeperMode], {
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

/** 会计数的模型桩：用于证明切换成 human 之后不再有模型调用。 */
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
        id: "chatcmpl-transition",
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
  runtimeRoot = mkdtempSync(join(tmpdir(), "trpg-interaction-duals-"));
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
        // 开局那一步会用到模型；本组用例的判定部分不该再调用它（用完清点计数）。
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

function eventPayload(frames: string[], type: string) {
  return frames
    .filter((frame) => frame.includes(`"type":"${type}"`))
    .map(
      (frame) =>
        (JSON.parse(frame) as { payload?: Record<string, unknown> }).payload ??
        {},
    );
}

async function bootStructuredWorld(
  page: Page,
  frames: Frames,
  keeperMode: "human" | "agent",
): Promise<{
  scene: string;
  destinations: Array<{ id: string; name: string }>;
}> {
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
  expect(configureWorld(worldId, keeperMode)).toContain("world ready");
  await page.reload();
  await expect(page.locator(".boot-loader")).toBeHidden({ timeout: 30_000 });
  await expect(page.getByTestId("structured-tool-row")).toBeVisible({
    timeout: 60_000,
  });
  const snapshot =
    eventPayload(frames.received, "session_snapshot").at(-1) ?? {};
  return {
    scene: await page.locator(".header-scene-name").innerText(),
    destinations: (snapshot.destinations ?? []) as Array<{
      id: string;
      name: string;
    }>,
    modelCallsAfterBoot: modelRequests.length,
  };
}

async function fillKeeperField(page: Page, field: string, value: string) {
  const locator = page.locator(
    `[data-field="${field}"] select, [data-field="${field}"] input, [data-field="${field}"] textarea`,
  );
  const tag = await locator.evaluate((node) => node.tagName);
  if (tag === "SELECT") await locator.selectOption(value);
  else await locator.fill(value);
}

async function waitNoDialog(page: Page) {
  await expect(
    page.getByRole("dialog", { name: /前往|出示线索|使用道具/ }),
  ).toBeHidden({
    timeout: 10_000,
  });
}

async function keeperPark(
  page: Page,
  requestId: string,
  destinationId: string,
  note: string,
) {
  await waitNoDialog(page);
  await page.getByTestId("btn-keeper-console").click();
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
  await page.getByRole("button", { name: "关闭主持台" }).click();
}

async function keeperMove(page: Page, destinationId: string) {
  await waitNoDialog(page);
  await page.getByTestId("btn-keeper-console").click();
  await page.getByTestId("keeper-cmd-move_party").click();
  await fillKeeperField(page, "destination_scene_id", destinationId);
  await page.getByTestId("keeper-submit").click();
  await expect(page.getByTestId("keeper-submit")).toBeEnabled({
    timeout: 30_000,
  });
  await page.getByRole("button", { name: "关闭主持台" }).click();
}

/** 从最近的结构化帧里取当前 open 线程 id（关闭线程需要它）。 */
function openThreadId(frames: Frames): string {
  const fromEvents = frames.received
    .filter((frame) => frame.includes('"type":"interaction_updated"'))
    .map(
      (frame) =>
        JSON.parse(frame) as {
          payload?: { thread_id?: string; status?: string };
        },
    )
    .filter((event) => event.payload?.status === "open");
  const last = fromEvents.at(-1);
  expect(
    last?.payload?.thread_id,
    "应能从 interaction_updated 取到线程 id",
  ).toBeTruthy();
  return String(last!.payload!.thread_id);
}

async function keeperResolve(
  page: Page,
  requestId: string,
  resolution: string,
  closeThread = false,
  threadId = "",
) {
  await waitNoDialog(page);
  await page.getByTestId("btn-keeper-console").click();
  await page.getByTestId("keeper-cmd-resolve_intent").click();
  await fillKeeperField(page, "request_id", requestId);
  await fillKeeperField(page, "resolution", resolution);
  if (closeThread && threadId) {
    await fillKeeperField(page, "thread_id", threadId);
  }
  if (closeThread) {
    // 契约里的显式线程收尾（thread.action=close）。注意：仅把请求置终态
    // 不会关闭它的交互线程——后端缺口见交付记录「给后端的清单」。
    await fillKeeperField(page, "thread_action", "close");
  }
  await page.getByTestId("keeper-submit").click();
  await expect(page.getByTestId("keeper-submit")).toBeEnabled({
    timeout: 30_000,
  });
  await page.getByRole("button", { name: "关闭主持台" }).click();
}

/** 打开「前往」对话框并读取实时目的地（快照列表在移动后会滞后，不能拿它当准）。 */
/**
 * 等待中的待办卡：M5 之后由「当前交互」卡承担（interaction_updated / 快照
 * interactions[]）；旧路径的 awaiting 明细只在没有对应线程时才显示。
 */
function waitingCard(page: Page) {
  return page
    .locator(
      '[data-testid="structured-interaction-card"], [data-testid="structured-awaiting"]',
    )
    .first();
}

async function openMoveDialog(
  page: Page,
): Promise<Array<{ id: string; name: string }>> {
  await expect(page.getByRole("dialog", { name: "主持台" })).toBeHidden({
    timeout: 10_000,
  });
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

function countSentRequests(frames: Frames): number {
  return frames.sent.filter((frame) =>
    frame.includes('"type":"action_request"'),
  ).length;
}

async function lastRequestId(frames: Frames): Promise<string> {
  const frame = frames.sent
    .filter((item) => item.includes('"type":"action_request"'))
    .at(-1) as string;
  return (JSON.parse(frame) as { request_id: string }).request_id;
}

/** 玩家用「前往」按钮发一条结构化移动请求（目的地由对话框实时列表挑，避免原地移动）。 */
async function playerMoveToNewScene(
  page: Page,
  frames: Frames,
): Promise<{
  requestId: string;
  target: { id: string; name: string };
  sceneBefore: string;
}> {
  const sceneBefore = await page.locator(".header-scene-name").innerText();
  const destinations = await openMoveDialog(page);
  const target = destinations.find((item) => item.name !== sceneBefore);
  expect(target, "前往列表里应有不是当前场景的目的地").toBeTruthy();
  const before = countSentRequests(frames);
  await page
    .locator(".structured-destination", { hasText: target!.id })
    .click();
  await expect
    .poll(() => countSentRequests(frames), { timeout: 30_000 })
    .toBeGreaterThan(before);
  return {
    requestId: await lastRequestId(frames),
    target: target!,
    sceneBefore,
  };
}

/** 玩家用自由文本发一条请求，返回 request_id。 */
async function playerTextRequest(
  page: Page,
  frames: Frames,
  text: string,
): Promise<string> {
  const before = countSentRequests(frames);
  await expect(page.getByRole("dialog", { name: "主持台" })).toBeHidden({
    timeout: 10_000,
  });
  await page.locator("#user-input").fill(text);
  await page.locator("#btn-send").click();
  await expect
    .poll(() => countSentRequests(frames), { timeout: 30_000 })
    .toBeGreaterThan(before);
  return lastRequestId(frames);
}

/** 玩家用「前往」按钮发一条结构化移动请求，返回 request_id。 */
async function playerMoveRequest(
  page: Page,
  frames: Frames,
  destinationName: string,
): Promise<string> {
  const before = frames.sent.filter((frame) =>
    frame.includes('"type":"action_request"'),
  ).length;
  // 主持台刚关过：等它真的消失再点玩家入口，避免点击落在残留浮层上。
  await expect(page.getByRole("dialog", { name: "主持台" })).toBeHidden({
    timeout: 10_000,
  });
  await page.getByTestId("btn-move").click();
  await expect(page.getByRole("dialog", { name: /前往/ })).toBeVisible({
    timeout: 10_000,
  });
  await page.getByRole("button", { name: new RegExp(destinationName) }).click();
  await expect
    .poll(
      () =>
        frames.sent.filter((frame) => frame.includes('"type":"action_request"'))
          .length,
      { timeout: 30_000 },
    )
    .toBeGreaterThan(before);
  const frame = frames.sent
    .filter((item) => item.includes('"type":"action_request"'))
    .at(-1) as string;
  return (JSON.parse(frame) as { request_id: string }).request_id;
}

test("对偶：换目的地 / 普通直接移动 / 取消", async ({ page }) => {
  test.setTimeout(300_000);
  page.setDefaultTimeout(30_000);
  const frames = collectFrames(page);
  const boot = await bootStructuredWorld(page, frames, "human");

  // 1) 换目的地：先按旧计划挂一个待办（去医学院），玩家改主意后用「前往」选了别处；
  //    主持取消旧待办并执行新目的地 —— 旧待办不会随后执行。
  const firstMove = await playerMoveToNewScene(page, frames);
  const wishId = await playerTextRequest(
    page,
    frames,
    `我想去${firstMove.target.name}看看。`,
  );
  await keeperPark(
    page,
    wishId,
    firstMove.target.id,
    `尚未出发前往${firstMove.target.name}`,
  );
  await expect(waitingCard(page)).toBeVisible();
  expect(await page.locator(".header-scene-name").innerText()).toBe(
    firstMove.sceneBefore,
  );

  const secondMove = await playerMoveToNewScene(page, frames);
  expect(await page.locator(".header-scene-name").innerText()).toBe(
    firstMove.sceneBefore,
  );
  await keeperResolve(page, wishId, "cancelled", true, openThreadId(frames));
  await keeperMove(page, secondMove.target.id);
  await keeperResolve(page, secondMove.requestId, "completed");
  await expect
    .poll(() => page.locator(".header-scene-name").innerText(), {
      timeout: 30_000,
    })
    .toBe(secondMove.target.name);
  await expect(waitingCard(page)).toBeHidden({
    timeout: 30_000,
  });
  await page.screenshot({
    path: `${screenshotsDir}/structured-dual-replace-destination.png`,
  });

  // 2) 普通直接移动：新起点 ≠ 新目标（由对话框实时列表挑选），主持执行后位置才变。
  const plainMove = await playerMoveToNewScene(page, frames);
  expect(await page.locator(".header-scene-name").innerText()).toBe(
    plainMove.sceneBefore,
  );
  await keeperMove(page, plainMove.target.id);
  await keeperResolve(page, plainMove.requestId, "completed");
  await expect
    .poll(() => page.locator(".header-scene-name").innerText(), {
      timeout: 30_000,
    })
    .toBe(plainMove.target.name);
  await page.screenshot({
    path: `${screenshotsDir}/structured-dual-plain-move.png`,
  });

  // 3) 取消：意愿 + 待办 → 主持取消 → 不移动，且旧卡片不再可执行
  const sceneBeforeCancel = await page
    .locator(".header-scene-name")
    .innerText();
  const cancelId = await playerTextRequest(page, frames, "我想再去别处看看。");
  const destinations = await openMoveDialog(page);
  const cancelTarget = destinations.find(
    (item) => item.name !== sceneBeforeCancel,
  );
  await expect(page.getByRole("dialog", { name: /前往/ })).toBeVisible();
  await page.getByRole("button", { name: "取消" }).click();
  await keeperPark(page, cancelId, cancelTarget!.id, "尚未出发");
  await expect(waitingCard(page)).toBeVisible();
  await keeperResolve(page, cancelId, "cancelled", true, openThreadId(frames));
  await expect(waitingCard(page)).toBeHidden({
    timeout: 30_000,
  });
  expect(await page.locator(".header-scene-name").innerText()).toBe(
    sceneBeforeCancel,
  );
  await expect(page.getByText("已取消")).toBeVisible();
  // 判定过程零模型调用：开局那一步之外不再碰模型
  expect(modelRequests.length).toBe(boot.modelCallsAfterBoot);
});

test("追问：正常回答并等待是合法完成，原交互保持存活，直到原动作真的执行", async ({
  page,
}) => {
  test.setTimeout(300_000);
  page.setDefaultTimeout(30_000);
  const frames = collectFrames(page);
  const boot = await bootStructuredWorld(page, frames, "human");
  // 目的地从「前往」对话框实时挑（快照列表在移动后会滞后）
  const dialogDestinations = await openMoveDialog(page);
  const target = dialogDestinations[0];
  await page.getByRole("button", { name: "取消" }).click();
  const sceneBefore = await page.locator(".header-scene-name").innerText();
  const movesBefore = eventPayload(frames.received, "scene_changed").length;

  // 1) 玩家表达意愿 → 主持记录「尚未出发」（线程 open，位置不变）
  const wishId = await playerTextRequest(
    page,
    frames,
    `我想去${target.name}看看。`,
  );
  await keeperPark(page, wishId, target.id, `尚未出发前往${target.name}`);
  await expect(waitingCard(page)).toBeVisible();
  const threadBefore = openThreadId(frames);
  expect(await page.locator(".header-scene-name").innerText()).toBe(
    sceneBefore,
  );

  // 2) 玩家追问细节 —— 这只是普通对话，不是「执行原动作」
  const followUpId = await playerTextRequest(
    page,
    frames,
    "那边现在有人值班吗？会不会吃闭门羹？",
  );
  expect(followUpId).not.toBe(wishId);

  // 3) 主持正常回答并等待：只给叙事，不移动，也不动线程
  await keeperResolve(page, followUpId, "completed");
  await expect(waitingCard(page)).toBeVisible();
  await expect(waitingCard(page)).toContainText("尚未执行");
  await expect(waitingCard(page)).toContainText(target.name);
  // 同一线程仍然存活：位置没变、线程 id 没换、没有新的场景事件
  expect(await page.locator(".header-scene-name").innerText()).toBe(
    sceneBefore,
  );
  expect(openThreadId(frames)).toBe(threadBefore);
  expect(
    eventPayload(frames.received, "scene_changed").length,
    "回答追问不应改变场景",
  ).toBe(movesBefore);
  await page.screenshot({
    path: `${screenshotsDir}/structured-dual-followup-alive.png`,
  });

  // 4) 原动作真的执行（换场景）→ 目标一致的开放线程自动收尾
  await keeperMove(page, target.id);
  await expect
    .poll(() => page.locator(".header-scene-name").innerText(), {
      timeout: 30_000,
    })
    .toBe(target.name);
  await expect(page.getByTestId("structured-interaction-card")).toHaveCount(0);

  // 全流程零模型调用（人类主持）
  expect(modelRequests.length).toBe(boot.modelCallsAfterBoot);
});

/**
 * 抵达收尾的请求侧同步（原 fixme，后端修复后由 Kimi 改回真测试）：
 * `auto_complete_move_threads` 收尾线程时，必须一并同步那条 `awaiting_player`
 * 请求 —— 否则人已经到达，卡片上还写着「尚未执行：尚未出发前往X」，世界状态与
 * 待办自相矛盾。
 *
 * 这条覆盖最简单的情形（纯移动请求）；复合意图（抵达后仍有未完成调查）与
 * 实时/刷新一致性在专用验收文件 `structured-pending-sync.spec.ts` 里。
 */
test("移动已抵达后：关联请求的「尚未执行」明细必须同时消失", async ({
  page,
}) => {
  test.setTimeout(300_000);
  page.setDefaultTimeout(30_000);
  const frames = collectFrames(page);
  await bootStructuredWorld(page, frames, "human");
  const dialogDestinations = await openMoveDialog(page);
  const target = dialogDestinations[0];
  await page.getByRole("button", { name: "取消" }).click();

  const wishId = await playerTextRequest(
    page,
    frames,
    `我想去${target.name}看看。`,
  );
  await keeperPark(page, wishId, target.id, `尚未出发前往${target.name}`);
  await expect(waitingCard(page)).toBeVisible();

  await keeperMove(page, target.id);
  await expect
    .poll(() => page.locator(".header-scene-name").innerText(), {
      timeout: 30_000,
    })
    .toBe(target.name);
  expect(await page.locator(".header-scene-name").innerText()).toBe(
    target.name,
  );
  // 人已经到达：任何「尚未执行：尚未出发」都不该还在（当前会失败）
  await expect(waitingCard(page)).toBeHidden({ timeout: 30_000 });
  await expect(page.getByText(/尚未执行：尚未出发/)).toHaveCount(0);
});

test("刷新不丢公开待办，且同一动作只落账一次", async ({ page }) => {
  test.setTimeout(300_000);
  page.setDefaultTimeout(30_000);
  const frames = collectFrames(page);
  const boot = await bootStructuredWorld(page, frames, "human");
  const { scene } = boot;
  // 目的地要从「前往」对话框实时挑（快照列表在移动后会滞后）
  const dialogDestinations = await openMoveDialog(page);
  const target = dialogDestinations[0];
  await page.getByRole("button", { name: "取消" }).click();

  // 挂一个待办 → 刷新 → 卡片应恢复（快照里的公开待办）
  const wishId = await playerTextRequest(
    page,
    frames,
    `我想去${target.name}。`,
  );
  await keeperPark(page, wishId, target.id, `尚未出发前往${target.name}`);
  await expect(waitingCard(page)).toBeVisible();

  await page.reload();
  await expect(page.locator(".boot-loader")).toBeHidden({ timeout: 30_000 });
  await expect(waitingCard(page)).toBeVisible({
    timeout: 60_000,
  });
  await expect(
    page.getByText(new RegExp(`尚未执行：尚未出发前往${target.name}`)),
  ).toBeVisible();
  expect(await page.locator(".header-scene-name").innerText()).toBe(scene);

  // 主持执行一次 → 恰好一次 scene_changed；再刷新不会重放
  await keeperMove(page, target.id);
  await expect
    .poll(() => page.locator(".header-scene-name").innerText(), {
      timeout: 30_000,
    })
    .toBe(target.name);
  const movedEvents = eventPayload(frames.received, "scene_changed").length;
  expect(movedEvents).toBe(1);
  await page.reload();
  await expect(page.locator(".boot-loader")).toBeHidden({ timeout: 30_000 });
  await expect
    .poll(() => page.locator(".header-scene-name").innerText(), {
      timeout: 30_000,
    })
    .toBe(target.name);
  expect(eventPayload(frames.received, "scene_changed").length).toBe(
    movedEvents,
  );
  await page.screenshot({
    path: `${screenshotsDir}/structured-refresh-pending.png`,
  });
  expect(modelRequests.length).toBe(boot.modelCallsAfterBoot);
});

test("agent 缺 BYOK：请求明确暂停、输入可用、无永久转圈、可接管", async ({
  page,
}) => {
  test.setTimeout(300_000);
  page.setDefaultTimeout(30_000);
  const frames = collectFrames(page);
  const boot = await bootStructuredWorld(page, frames, "agent");

  await page.locator("#user-input").fill("说实话，我想先看看莱特教授的尸体。");
  await page.locator("#btn-send").click();

  // 缺 BYOK 的暂停在 Kimi 的 552672c 之后有实时帧与快照 detail；这条用例负责
  // 「刷新/重连」那一半：暂停态 + 可操作原因都要能从快照恢复出来，输入仍可用。
  await expect(page.locator("#user-input")).toBeEnabled();
  await expect(page.getByTestId("btn-keeper-console")).toBeVisible();
  await page.reload();
  await expect(page.locator(".boot-loader")).toBeHidden({ timeout: 30_000 });
  await expect(page.getByText("已暂停（可恢复）")).toBeVisible({
    timeout: 60_000,
  });
  // 刷新后不能只知道「暂停了」，还要知道为什么、能不能接管（快照 detail）
  await expect(page.getByText(/BYOK|未配置模型服务/)).toBeVisible({
    timeout: 30_000,
  });
  await expect(page.locator("#user-input")).toBeEnabled();
  await page.screenshot({
    path: `${screenshotsDir}/structured-agent-paused.png`,
  });
  // 缺 BYOK 时是 fail-closed：一次模型调用都不该发生
  expect(modelRequests.length).toBe(boot.modelCallsAfterBoot);
});

// Kimi 在途修复（`agent_runtime._run_keeper_agent` 的 BYOK 分支此前只提交命令、
// 丢弃 resolve_intent 事件，玩家卡片永久停在「已提交，等待服务端确认」）：
// 该修复把暂停事件按 deliver/broadcast 投递出去。此用例即原来的 fixme，
// 后端修复落地后改回真测试；若所在版本尚无该修复，这里会红——那就是回归信号。
test("agent 缺 BYOK：实时帧必须让请求离开「处理中」", async ({ page }) => {
  test.setTimeout(300_000);
  page.setDefaultTimeout(30_000);
  const frames = collectFrames(page);
  const boot = await bootStructuredWorld(page, frames, "agent");
  await page.locator("#user-input").fill("说实话，我想先看看莱特教授的尸体。");
  await page.locator("#btn-send").click();
  await expect
    .poll(
      async () => {
        const card = page.locator(".action-status-card").last();
        if (!(await card.count())) return "no-card";
        return (await card.getAttribute("data-status")) ?? "";
      },
      { timeout: 120_000 },
    )
    .toBe("paused");
  // 暂停是实时到达的：不需要刷新就能看到可读原因，输入仍可用
  await expect(page.locator("#user-input")).toBeEnabled();
  await expect(page.getByText("已暂停（可恢复）")).toBeVisible();
  expect(modelRequests.length).toBe(boot.modelCallsAfterBoot);
});
