/**
 * 结构化战斗/结案生命周期 —— 真实后端浏览器验收（人类主持，零模型调用）。
 *
 * 范围与边界：
 * - 真实后端：每个用例在 127.0.0.1:8876 起独立 uvicorn（mkdtemp runtime + SQLite），
 *   用完即杀即删；不碰 Pi/正式环境、不碰真实存档。
 * - 零模型：OPENAI_BASE_URL 指向本文件的「模型陷阱」TCP 监听（收到连接即断开并计数），
 *   每个用例结束断言计数为零——任何误调用都会立刻暴露为连接错误。
 * - 真实 UI 驱动：建房/选角/战斗/存读档/分支/结案全部走界面；只有「重放旧帧」这类
 *   界面不可能产生的输入用同一账号的原始 WebSocket 补发（协议级重放，属于真实客户端
 *   能力，不改数据库、不注入状态）。
 * - 多人房间不支持读档/分支是真实禁止边界，单独断言，不绕过。
 *
 * 用例映射（任务书 A–E）：
 *   A 战斗中保存与刷新重连（云端单人）
 *   B 本地主动读档：决定/掷骰授权失效 + 资源回滚 + 重新准备重新响应
 *   C 云端单人战斗中分支：已提交状态复制、旧授权不复制、分支不改父世界
 *   D 云端单人结案：合法结局/奖励恢复/另存角色/重放与重复结算
 *   E 本地读档后历史结案凭证不混入（读档侧对偶）
 *   F 多人房间：掷骰/决定/骰点/奖励/控制权限隔离 + PvP 双向授权失效 + 房内禁止边界
 */

import { spawn, type ChildProcess } from "node:child_process";
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import {
  createServer as createTcpServer,
  type Server as TcpServer,
} from "node:net";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { createRequire } from "node:module";

import {
  expect,
  request,
  test,
  type BrowserContext,
  type Page,
} from "@playwright/test";

const require = createRequire(import.meta.url);
// ws 随 playwright-core 传递安装（frontend/node_modules/ws），e2e 目录不在 tsc 范围内。
const WsClient = require("ws") as new (
  url: string,
  options?: { headers?: Record<string, string> },
) => {
  on(event: string, listener: (data: unknown) => void): void;
  once(event: string, listener: (arg?: unknown) => void): void;
  send(data: string): void;
  close(): void;
};

const port = 8876;
const baseUrl = `http://127.0.0.1:${port}`;
const repositoryRoot = resolve(import.meta.dirname, "../..");
const evidenceDir = resolve(
  repositoryRoot,
  "docs/evidence/keeper-combat-lifecycle",
);
const MODULE = "猩红文档";
const PASSWORD = "combat lifecycle e2e";
const runId = Math.random().toString(36).slice(2, 8);

let runtimeRoot = "";
let server: ChildProcess | null = null;
let serverOutput = "";
let modelTrap: TcpServer | null = null;
let modelCalls = 0;
let modelTrapUrl = "";
let modelCallsAtStart = 0;

function pythonPath(): string {
  return (
    process.env.TRPG_E2E_PYTHON ??
    (existsSync(resolve(repositoryRoot, ".venv/bin/python"))
      ? resolve(repositoryRoot, ".venv/bin/python")
      : "python")
  );
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
  throw new Error(`E2E server did not start:\n${serverOutput.slice(-4000)}`);
}

/** 起独立后端；auth=true 时开启注册/登录（云端用例），false 为本地模式。 */
async function bootServer(auth: boolean): Promise<void> {
  runtimeRoot = mkdtempSync(join(tmpdir(), "trpg-combat-lifecycle-"));
  serverOutput = "";
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
        ...(auth
          ? { TRPG_REQUIRE_AUTH: "1", TRPG_ALLOW_REGISTRATION: "1" }
          : {}),
        TRPG_WRITE_COMPAT_EXPORTS: "0",
        OPENAI_API_KEY: "e2e-placeholder",
        OPENAI_BASE_URL: modelTrapUrl,
        TRPG_STREAM_USAGE: "0",
      },
      stdio: ["ignore", "pipe", "pipe"],
    },
  );
  server.stdout?.on("data", (chunk) => (serverOutput += String(chunk)));
  server.stderr?.on("data", (chunk) => (serverOutput += String(chunk)));
  await waitForServer();
}

async function stopServer(): Promise<void> {
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
  server = null;
  if (runtimeRoot) {
    rmSync(runtimeRoot, { recursive: true, force: true });
    runtimeRoot = "";
  }
}

test.beforeAll(async () => {
  mkdirSync(evidenceDir, { recursive: true });
  // 模型陷阱：任何到 OPENAI_BASE_URL 的连接都被计数并立即断开。
  modelTrap = createTcpServer((socket) => {
    modelCalls += 1;
    socket.destroy();
  });
  await new Promise<void>((resolveListen, rejectListen) => {
    modelTrap!.once("error", rejectListen);
    modelTrap!.listen(0, "127.0.0.1", () => resolveListen());
  });
  const address = modelTrap.address();
  if (!address || typeof address === "string")
    throw new Error("model trap did not expose a TCP port");
  modelTrapUrl = `http://127.0.0.1:${address.port}/v1`;
});

test.beforeEach(() => {
  modelCallsAtStart = modelCalls;
});

test.afterEach(async ({}, testInfo) => {
  // 零模型调用是本验收的硬条件，先于清理断言，失败直接落在当前用例上。
  expect(
    modelCalls - modelCallsAtStart,
    `本用例发生了 ${modelCalls - modelCallsAtStart} 次模型调用（应为 0）`,
  ).toBe(0);
  if (serverOutput) {
    const safe = testInfo.title.replace(/[^\w一-鿿]+/g, "_").slice(0, 40);
    writeFileSync(join(evidenceDir, `server-${safe}.log`), serverOutput);
  }
  await stopServer();
});

test.afterAll(async () => {
  if (modelTrap) {
    await new Promise<void>((resolveClose) =>
      modelTrap!.close(() => resolveClose()),
    );
  }
});

// ---------------------------------------------------------------- 通用助手

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

function framesOf(received: string[], type: string): string[] {
  return received.filter((frame) => frame.includes(`"type":"${type}"`));
}

function lastPayload(frames: string[], type: string): any {
  const list = framesOf(frames, type);
  if (!list.length) return undefined;
  return (JSON.parse(list.at(-1)!) as { payload?: unknown }).payload;
}

/** 最近一次 session_snapshot 的 payload（重连/读档/分支后须先等到新快照再用）。 */
function latestSnapshot(frames: Frames): any {
  return lastPayload(frames.received, "session_snapshot");
}

/** 快照信封顶层 revision（expected_revision 必填整数；payload.identity 没有该字段）。 */
function snapshotRevision(received: string[]): number {
  const list = framesOf(received, "session_snapshot");
  if (!list.length) throw new Error("尚未收到 session_snapshot");
  const revision = (JSON.parse(list.at(-1)!) as { revision?: unknown })
    .revision;
  if (typeof revision !== "number")
    throw new Error("session_snapshot 缺少顶层 revision");
  return revision;
}

/** 最新战况：取时间上更靠后的 combat_updated / session_snapshot.combat。 */
function latestCombat(frames: Frames): any {
  const received = frames.received;
  const snapshotIdx = received.findLastIndex((frame) =>
    frame.includes('"type":"session_snapshot"'),
  );
  const combatIdx = received.findLastIndex((frame) =>
    frame.includes('"type":"combat_updated"'),
  );
  if (snapshotIdx > combatIdx) {
    return (JSON.parse(received[snapshotIdx]) as { payload?: any }).payload
      ?.combat;
  }
  if (combatIdx >= 0) {
    return (JSON.parse(received[combatIdx]) as { payload?: unknown }).payload;
  }
  return undefined;
}

/** 当前物品清单：库存变更走 inventory_changed 事件（快照只在重连时刷新）。 */
function latestItems(frames: Frames): any[] {
  const changed = framesOf(frames.received, "inventory_changed");
  if (changed.length) {
    return (
      (JSON.parse(changed.at(-1)!) as { payload: { items?: unknown[] } })
        .payload.items ?? []
    );
  }
  return latestSnapshot(frames)?.items ?? [];
}

function snapshotCount(frames: Frames): number {
  return framesOf(frames.received, "session_snapshot").length;
}

async function waitForNewSnapshot(frames: Frames, before: number) {
  await expect
    .poll(() => snapshotCount(frames), { timeout: 60_000 })
    .toBeGreaterThan(before);
}

async function cookieHeader(context: BrowserContext): Promise<string> {
  // 不带 URL 过滤：会话 cookie 带 Secure 属性，Playwright 按 RFC 不会把它
  // 匹配到 http:// 地址（Chromium 对 loopback 有例外，实际会发送）。
  const cookies = await context.cookies();
  return cookies.map((cookie) => `${cookie.name}=${cookie.value}`).join("; ");
}

/**
 * 同一账号的原始 WS 连接（协议级重放通道）。用于发送「界面已经不可能产生」的
 * 旧授权帧/重复命令，验证服务端拒绝或幂等；不改数据库、不注入状态。
 */
async function openRawSocket(
  path: string,
  cookie?: string,
): Promise<{
  frames: string[];
  send: (frame: unknown) => void;
  close: () => void;
}> {
  const socket = new WsClient(`ws://127.0.0.1:${port}${path}`, {
    // ws 客户端默认不带 Origin；云端模式服务端按白名单校验，缺 Origin 直接 403。
    headers: { Origin: baseUrl, ...(cookie ? { Cookie: cookie } : {}) },
  });
  const frames: string[] = [];
  socket.on("message", (data: unknown) => frames.push(String(data)));
  await new Promise<void>((resolveOpen, rejectOpen) => {
    socket.once("open", () => resolveOpen());
    socket.once("error", (err) => rejectOpen(err));
  });
  return {
    frames,
    send: (frame) => socket.send(JSON.stringify(frame)),
    close: () => socket.close(),
  };
}

async function register(page: Page, username: string): Promise<void> {
  await page.goto(`${baseUrl}/?mode=online`);
  await page.getByRole("tab", { name: "注册" }).click();
  await page.getByLabel("用户名").fill(username);
  await page.getByLabel("密码", { exact: true }).fill(PASSWORD);
  await page.getByLabel("确认密码").fill(PASSWORD);
  await page.getByRole("button", { name: "注册并登录" }).click();
  await expect(page.getByRole("heading", { name: "联机大厅" })).toBeVisible();
}

/** 经模式选择页进入云端单人大厅并完成注册（真实入口路径）。 */
async function registerIntoSoloLobby(
  page: Page,
  username: string,
): Promise<void> {
  await page.goto(baseUrl);
  await page.waitForSelector('[data-testid="mode-select"]', {
    timeout: 30_000,
  });
  await page.getByRole("button", { name: /云端单人/ }).click();
  await page.getByRole("tab", { name: "注册" }).click();
  await page.getByLabel("用户名").fill(username);
  await page.getByLabel("密码", { exact: true }).fill(PASSWORD);
  await page.getByLabel("确认密码").fill(PASSWORD);
  await page.getByRole("button", { name: "注册并登录" }).click();
  await expect(page.getByRole("heading", { name: "我的冒险" })).toBeVisible();
}

/** 云端单人：创建结构化（人类主持）世界并用指定角色开局，返回 world_id。 */
async function createSoloStructuredWorld(
  page: Page,
  frames: Frames,
  name: string,
  characterName: string,
): Promise<string> {
  await page.getByRole("button", { name: "开始新冒险" }).click();
  await page.getByLabel("冒险名称").fill(name);
  await page.locator(".module-select-trigger").click();
  await page.getByRole("option", { name: new RegExp(MODULE) }).click();
  await page.getByRole("radio", { name: "人类主持" }).click();
  await page.getByRole("button", { name: "创建冒险" }).click();
  // 选角 → 进入游戏。同名角色可能同时有「默认调查员」与「模组特色」两张卡，
  // 固定选模组名册那张（模组编写路径）。先等卡出现再数，避免竞态。
  const cards = page.locator(".character-card", { hasText: characterName });
  await expect(cards.first()).toBeVisible({ timeout: 60_000 });
  const card =
    (await cards.count()) > 1
      ? cards.filter({ hasText: "模组特色" })
      : cards.first();
  await card.click();
  await page.locator("#btn-character-confirm").click();
  await expect(page.locator("#user-input")).toBeEnabled({ timeout: 90_000 });
  await expect(page.getByTestId("structured-tool-row")).toBeVisible({
    timeout: 60_000,
  });
  const worldId = await page.evaluate(
    () => localStorage.getItem("trpg-active-world-id") || "",
  );
  expect(worldId).not.toBe("");
  await expect
    .poll(() => snapshotCount(frames), { timeout: 60_000 })
    .toBeGreaterThan(0);
  return worldId;
}

/** 本地模式：真实 UI 选「人类主持」开局（LocalPlayStylePanel），返回 world_id。 */
async function createLocalStructuredWorld(
  page: Page,
  frames: Frames,
): Promise<string> {
  await page.goto(`${baseUrl}/?mode=local`);
  await expect(page.locator(".boot-loader")).toHaveCount(0, {
    timeout: 30_000,
  });
  await expect(page.locator("#btn-start")).toBeVisible({ timeout: 30_000 });
  await page.locator(".module-select-trigger").click();
  await page.getByRole("option", { name: new RegExp(MODULE) }).click();
  await page.locator("#btn-start").click();
  // 游玩方式面板在选角页底部（StartScreen 的 character-select-footer）。
  await expect(page.locator("#btn-character-confirm")).toBeVisible({
    timeout: 60_000,
  });
  await page.locator('button[aria-label^="游玩方式："]').click();
  const dialog = page.getByRole("dialog", { name: "游玩方式" });
  await expect(dialog).toBeVisible();
  await dialog.getByRole("radio", { name: "人类主持" }).click();
  await dialog.getByRole("button", { name: "关闭", exact: true }).click();
  await expect(dialog).toBeHidden();
  await page.locator("#btn-character-confirm").click();
  await expect(page.locator("#user-input")).toBeEnabled({ timeout: 90_000 });
  await expect(page.getByTestId("structured-tool-row")).toBeVisible({
    timeout: 60_000,
  });
  const worldId = await page.evaluate(
    () => localStorage.getItem("trpg-active-world-id") || "",
  );
  expect(worldId).not.toBe("");
  await expect
    .poll(() => snapshotCount(frames), { timeout: 60_000 })
    .toBeGreaterThan(0);
  return worldId;
}

// ---------------------------------------------------------------- 主持台助手

async function openConsole(page: Page) {
  const dialog = page.getByRole("dialog", { name: "主持工作台" });
  if (!(await dialog.isVisible().catch(() => false))) {
    await page.getByTestId("btn-keeper-console").click();
    await expect(dialog).toBeVisible();
  }
}

async function closeConsole(page: Page) {
  const dialog = page.getByRole("dialog", { name: "主持工作台" });
  if (await dialog.isVisible().catch(() => false)) {
    await page.getByRole("button", { name: "关闭主持台" }).click();
    await expect(dialog).toBeHidden({ timeout: 10_000 });
  }
}

async function setKeeperField(page: Page, field: string, value: string) {
  const control = page.locator(
    `[data-field="${field}"] select, [data-field="${field}"] input:not([type="checkbox"]), [data-field="${field}"] textarea`,
  );
  const tag = await control.evaluate((node) => node.tagName);
  if (tag === "SELECT") await control.selectOption(value);
  else await control.fill(value);
}

/** 提交主持命令并等确定结局；revision_conflict 走界面自己的恢复动作（重发）。 */
async function submitKeeperCommand(
  page: Page,
  frames: Frames,
): Promise<{ accepted: boolean; lastError: string }> {
  for (let attempt = 0; attempt < 3; attempt += 1) {
    const settled = () =>
      framesOf(frames.received, "request_error").length +
      framesOf(frames.received, "action_status").length +
      framesOf(frames.received, "action_ack").length;
    const before = settled();
    const errorsBefore = framesOf(frames.received, "request_error").length;
    await page.getByTestId("keeper-submit").click();
    await expect.poll(settled, { timeout: 20_000 }).toBeGreaterThan(before);
    const errors = framesOf(frames.received, "request_error");
    if (errors.length === errorsBefore)
      return { accepted: true, lastError: "" };
    const code =
      (JSON.parse(errors.at(-1)!) as { payload?: { code?: string } }).payload
        ?.code ?? "";
    if (code !== "revision_conflict")
      return { accepted: false, lastError: errors.at(-1)! };
    await page.waitForTimeout(500);
  }
  return { accepted: false, lastError: "revision_conflict 多次未能提交" };
}

/** 本地/单人世界只有一个调查员：从主持台候选列表读其真实 ID（不猜字段名）。 */
async function soleInvestigatorId(page: Page): Promise<string> {
  const options = await page
    .locator('[data-field="investigator_id"] select option')
    .evaluateAll((nodes) =>
      nodes
        .map((node) => (node as HTMLOptionElement).value)
        .filter((value) => value),
    );
  expect(options, "单人世界应恰好一名可选调查员").toHaveLength(1);
  return options[0];
}

/** 主持快速存档（slot_000），等到 saved 回执 ok。 */
async function quickSave(page: Page, frames: Frames) {
  const before = framesOf(frames.received, "saved").length;
  await openConsole(page);
  await page.getByTestId("keeper-save").click();
  await expect
    .poll(() => framesOf(frames.received, "saved").length, { timeout: 60_000 })
    .toBeGreaterThan(before);
  const saved = JSON.parse(framesOf(frames.received, "saved").at(-1)!) as {
    ok?: boolean;
    slot_id?: string;
  };
  expect(saved.ok, `存档失败：${JSON.stringify(saved)}`).toBe(true);
  await closeConsole(page);
}

/** 主持读自动存档（本地模式专属入口；房间内读档是被拒的，另案断言）。 */
async function loadAutoSave(page: Page, frames: Frames) {
  const before = framesOf(frames.received, "loaded").length;
  const snapshots = snapshotCount(frames);
  await openConsole(page);
  await page.getByTestId("keeper-load").click();
  await expect
    .poll(() => framesOf(frames.received, "loaded").length, { timeout: 60_000 })
    .toBeGreaterThan(before);
  const loaded = JSON.parse(framesOf(frames.received, "loaded").at(-1)!) as {
    ok?: boolean;
  };
  expect(loaded.ok, `读档失败：${JSON.stringify(loaded)}`).toBe(true);
  await closeConsole(page);
  await waitForNewSnapshot(frames, snapshots);
}

// ---------------------------------------------------------------- 战斗助手

/** 移到医学院并返回在场 NPC 的 id（模组编写的真实场景目标）。 */
async function moveToNpcScene(page: Page, frames: Frames): Promise<string> {
  await openConsole(page);
  await page.getByTestId("keeper-cmd-move_party").click();
  await setKeeperField(page, "destination_scene_id", "miskatonic_medical");
  const moved = await submitKeeperCommand(page, frames);
  expect(moved.accepted, moved.lastError).toBe(true);
  await closeConsole(page);
  const targets = framesOf(frames.received, "state_changed")
    .map(
      (raw) =>
        (JSON.parse(raw) as { payload: { targets?: unknown } }).payload.targets,
    )
    .filter(Array.isArray)
    .at(-1) as Array<{ kind: string; id: string }> | undefined;
  const npc = targets?.find((target) => target.kind === "npc");
  expect(npc, "医学院场景缺少模组编写的在场 NPC").toBeTruthy();
  return npc!.id;
}

/** 开始遭遇并把先手 NPC 的普通行动过掉，直到轮到调查员。 */
async function startCombatWithNpc(page: Page, frames: Frames, npcId: string) {
  await openConsole(page);
  await page.getByTestId("keeper-cmd-combat_start").click();
  await setKeeperField(page, "participants", npcId);
  const started = await submitKeeperCommand(page, frames);
  expect(started.accepted, started.lastError).toBe(true);
  for (
    let turn = 0;
    latestCombat(frames).current_actor === npcId && turn < 3;
    turn += 1
  ) {
    await page.getByTestId("keeper-cmd-combat_action").click();
    await setKeeperField(page, "actor_id", npcId);
    await setKeeperField(page, "action_type", "move");
    await setKeeperField(page, "description", "退到房间另一侧。");
    const moved = await submitKeeperCommand(page, frames);
    expect(moved.accepted, moved.lastError).toBe(true);
  }
  const actor = latestCombat(frames).current_actor;
  expect(
    latestCombat(frames).participants.find(
      (p: { id: string }) => p.id === actor,
    )?.kind,
    "先手过完仍轮不到调查员",
  ).toBe("pc");
  return actor;
}

/** 云端单人刷新后落在模式选择页：走真实导航重新进入当前冒险。 */
async function reenterSoloWorld(page: Page, frames: Frames) {
  await page.reload();
  await page.waitForSelector('[data-testid="mode-select"]', {
    timeout: 30_000,
  });
  await page.getByRole("button", { name: /云端单人/ }).click();
  await expect(page.getByRole("heading", { name: "我的冒险" })).toBeVisible({
    timeout: 30_000,
  });
  const before = snapshotCount(frames);
  await page.getByRole("button", { name: "继续冒险" }).first().click();
  await expect(page.getByTestId("structured-tool-row")).toBeVisible({
    timeout: 90_000,
  });
  await waitForNewSnapshot(frames, before);
}

/** 用普通的主持行动把先手过到调查员（不伪造行动顺序），返回该调查员的参战 ID。 */
async function advanceToPc(page: Page, frames: Frames): Promise<string> {
  for (let turn = 0; turn < 4; turn += 1) {
    const combat = latestCombat(frames);
    const current = combat.current_actor;
    const kind = combat.participants.find(
      (p: { id: string }) => p.id === current,
    )?.kind;
    if (kind === "pc") return current;
    await openConsole(page);
    await page.getByTestId("keeper-cmd-combat_action").click();
    await setKeeperField(page, "actor_id", current);
    await setKeeperField(page, "action_type", "move");
    await setKeeperField(page, "description", "换位。");
    const moved = await submitKeeperCommand(page, frames);
    expect(moved.accepted, moved.lastError).toBe(true);
    await closeConsole(page);
  }
  throw new Error("没能轮到调查员行动");
}

/** 若当前挂着「不可逆暴力」等待决定，点掉它（确认动手）。 */
async function confirmViolenceIfAsked(page: Page, frames: Frames) {
  if (!latestCombat(frames)?.awaiting_decision) return;
  const decision = lastPayload(frames.received, "combat_decision_required");
  const proceed = decision.options.find(
    (option: { id: string }) => option.id === "confirm_violence",
  );
  // 点完后等这颗按钮消失（决定被消费），不赌 combat_updated 的字段刷新。
  const button = page
    .getByTestId("combat-field-record")
    .getByRole("button", { name: proceed.label, exact: true });
  await button.click();
  await expect(button).toHaveCount(0, { timeout: 20_000 });
}

/** 等「掷骰」按钮出现并点击，返回结算回执 payload.result。 */
async function rollPendingDice(page: Page, frames: Frames): Promise<any> {
  const before = framesOf(frames.received, "combat_roll_resolved").length;
  await page
    .getByTestId("combat-field-record")
    .getByRole("button", { name: "掷骰", exact: true })
    .click();
  await expect
    .poll(() => framesOf(frames.received, "combat_roll_resolved").length, {
      timeout: 30_000,
    })
    .toBeGreaterThan(before);
  return lastPayload(frames.received, "combat_roll_resolved").result;
}

/** 从页面收集的 sent 帧里抓最后一个 command_request 作信封模板。 */
function commandEnvelope(frames: Frames, kind: string): any {
  const raw = frames.sent
    .filter((frame) => frame.includes(`"kind":"${kind}"`))
    .at(-1);
  expect(raw, `没有抓到 ${kind} 的已发送帧作重放模板`).toBeTruthy();
  return JSON.parse(raw!);
}

// ---------------------------------------------------------------- 用例

test("A 战斗中保存：已结算 HP/弹药/物品 ID 落盘，刷新重连不回滚不重复扣", async ({
  browser,
}) => {
  test.setTimeout(300_000);
  await bootServer(true);
  const context = await browser.newContext();
  const page = await context.newPage();
  page.setDefaultTimeout(20_000);
  const frames = collectFrames(page);

  await registerIntoSoloLobby(page, `solo-a-${runId}`);
  const worldId = await createSoloStructuredWorld(
    page,
    frames,
    `战斗存档验收${runId}`,
    "黄千陆",
  );
  expect(worldId).not.toContain("-branch-");

  // 真实开战：医学院的模组 NPC + 黄千陆的 .38 左轮（6 发）。
  const npcId = await moveToNpcScene(page, frames);
  await startCombatWithNpc(page, frames, npcId);
  await closeConsole(page);

  // 玩家经真实申报对话框提交「开枪」，主持从待办卡里批准（cause_id 关联）。
  await page.getByRole("button", { name: "申报战斗动作", exact: true }).click();
  const declareDialog = page.getByRole("dialog", { name: "申报战斗动作" });
  await declareDialog
    .getByLabel("动作", { exact: true })
    .selectOption("firearm");
  await declareDialog.getByLabel("目标", { exact: true }).selectOption(npcId);
  const gunOptions = await declareDialog
    .getByLabel("使用的持有物品", { exact: true })
    .locator("option")
    .evaluateAll((nodes) =>
      nodes.map((node) => ({
        value: (node as HTMLOptionElement).value,
        label: node.textContent || "",
      })),
    );
  const gun = gunOptions.find((option) =>
    option.label.startsWith(".38口径左轮手枪（6发）"),
  );
  expect(gun, "黄千陆应当持有模组编写的 .38 左轮（6 发）").toBeTruthy();
  await declareDialog
    .getByLabel("使用的持有物品", { exact: true })
    .selectOption(gun!.value);
  await declareDialog.getByRole("button", { name: "提交申报" }).click();
  const declaration = JSON.parse(
    framesOf(frames.sent, "action_request").at(-1)!,
  ) as { request_id: string };
  await openConsole(page);
  const pending = page
    .getByTestId("keeper-pending-request")
    .filter({ hasText: declaration.request_id });
  await pending.getByRole("button", { name: "准备战斗动作" }).click();
  await expect(page.getByLabel("武器物品")).toHaveValue(gun!.value);
  await setKeeperField(page, "damage_spec", "1d2");
  const prepared = await submitKeeperCommand(page, frames);
  expect(prepared.accepted, prepared.lastError).toBe(true);
  await closeConsole(page);
  await confirmViolenceIfAsked(page, frames);
  const npcBefore = latestCombat(frames).participants.find(
    (p: { id: string }) => p.id === npcId,
  );

  // 掷骰结算：弹药 6→5，NPC HP 下降（命中与否由真实骰点决定，先记录）。
  const receipt = await rollPendingDice(page, frames);
  expect(receipt.action_type).toBe("firearm");
  await expect
    .poll(() => {
      const items = latestItems(frames);
      return items.find((item: { id: string }) => item.id === gun!.value)
        ?.label;
    })
    .toBe(".38口径左轮手枪（5发）");
  const npcAfter = latestCombat(frames).participants.find(
    (p: { id: string }) => p.id === npcId,
  );
  const pcHpAtSave = latestSnapshot(frames).character.hp;
  await page.screenshot({ path: join(evidenceDir, "a-shot-settled.png") });

  // 战斗中保存（主持台快速存档 → slot_000）。
  await quickSave(page, frames);

  // 刷新重连 ≠ 读档：世界必须保持已结算状态，不回滚、不重复扣弹/掷骰。
  const resolvedBefore = framesOf(
    frames.received,
    "combat_roll_resolved",
  ).length;
  await reenterSoloWorld(page, frames);
  const restored = latestSnapshot(frames);
  expect(restored.character.hp).toBe(pcHpAtSave);
  expect(
    restored.items.find((item: { id: string }) => item.id === gun!.value)
      ?.label,
    "重连后弹药标签必须保持 5 发，不得回滚或二次扣减",
  ).toBe(".38口径左轮手枪（5发）");
  expect(restored.combat?.active).toBe(true);
  const restoredNpc =
    latestCombat(frames)?.participants?.find(
      (p: { id: string }) => p.id === npcId,
    ) ??
    restored.combat?.participants?.find((p: { id: string }) => p.id === npcId);
  expect(restoredNpc?.hp).toBe(npcAfter.hp);
  // 战斗记录里恰好一条结算（没有因重连多掷一次）。
  const history = page.getByTestId("combat-result-history");
  await expect(history).toBeVisible();
  await history.locator("summary").click();
  await expect(history).toContainText(`d100=${receipt.rolls[0].roll}`);
  await expect(history.locator("li")).toHaveCount(1);
  expect(framesOf(frames.received, "combat_roll_resolved").length).toBe(
    resolvedBefore,
  );
  await page.screenshot({ path: join(evidenceDir, "a-after-reconnect.png") });
  await context.close();
});

test("B 本地主动读档：保存过的决定/待掷骰授权失效，旧响应被拒，重新准备才能继续", async ({
  browser,
}) => {
  test.setTimeout(300_000);
  await bootServer(false);
  const context = await browser.newContext();
  const page = await context.newPage();
  page.setDefaultTimeout(20_000);
  const frames = collectFrames(page);

  const worldId = await createLocalStructuredWorld(page, frames);
  const npcId = await moveToNpcScene(page, frames);
  await startCombatWithNpc(page, frames, npcId);

  // 主持批准一次近身攻击 → 非敌对 NPC 触发「不可逆暴力」决定 D1（挂起状态）。
  await page.getByTestId("keeper-cmd-combat_action").click();
  await setKeeperField(page, "actor_id", latestCombat(frames).current_actor);
  await setKeeperField(page, "target_id", npcId);
  await setKeeperField(page, "action_type", "melee");
  await setKeeperField(page, "damage_spec", "1d3");
  const prepared = await submitKeeperCommand(page, frames);
  expect(prepared.accepted, prepared.lastError).toBe(true);
  await closeConsole(page);
  await expect
    .poll(() => latestCombat(frames)?.awaiting_decision, { timeout: 20_000 })
    .toBeTruthy();
  const decision1 = lastPayload(frames.received, "combat_decision_required");
  expect(decision1.kind).toBe("irreversible_violence");
  const pcHpAtSave = latestSnapshot(frames).character.hp;
  const npcAtSave = latestCombat(frames).participants.find(
    (p: { id: string }) => p.id === npcId,
  );

  // 存档点：决定 D1 处于挂起状态时被一起存进快照。
  await quickSave(page, frames);

  // 存档点之后的进度：确认 D1 → 掷骰 R1 结算（真实骰点，命中与否都可能）→
  // 主持调整 PC HP -2（确定性的存档点后状态变更，用于回滚断言）。
  await confirmViolenceIfAsked(page, frames);
  const roll1 = lastPayload(frames.received, "combat_roll_required");
  const receipt1 = await rollPendingDice(page, frames);
  expect(receipt1.rolls.length, "掷骰必须真实结算").toBeGreaterThan(0);
  await openConsole(page);
  await page.getByTestId("keeper-cmd-adjust_stat").click();
  await setKeeperField(page, "investigator_id", await soleInvestigatorId(page));
  await setKeeperField(page, "field", "hp");
  await setKeeperField(page, "delta", "-2");
  await setKeeperField(page, "reason", "验收：旧伤恶化。");
  const adjusted = await submitKeeperCommand(page, frames);
  expect(adjusted.accepted, adjusted.lastError).toBe(true);

  // 再给玩家制造一份「活跃」的待掷骰授权 R2（不掷），用于读档后重放。
  // 第一枪结算后先手已让给 NPC，先用普通主持行动轮回到调查员。
  const pcActor = await advanceToPc(page, frames);
  await openConsole(page);
  await page.getByTestId("keeper-cmd-combat_action").click();
  await setKeeperField(page, "actor_id", pcActor);
  await setKeeperField(page, "target_id", npcId);
  await setKeeperField(page, "action_type", "melee");
  await setKeeperField(page, "damage_spec", "1d3");
  const prepared2 = await submitKeeperCommand(page, frames);
  expect(prepared2.accepted, prepared2.lastError).toBe(true);
  await closeConsole(page);
  await confirmViolenceIfAsked(page, frames);
  const roll2 = lastPayload(frames.received, "combat_roll_required");
  expect(roll2.roll_id).not.toBe(roll1.roll_id);
  await expect(
    page
      .getByTestId("combat-field-record")
      .getByRole("button", { name: "掷骰", exact: true }),
  ).toBeEnabled();

  // 主动读档（slot_000）：进度与授权都回到存档点。
  await loadAutoSave(page, frames);
  const restored = latestSnapshot(frames);
  expect(restored.character.hp, "读档必须回滚存档点之后的 HP 调整").toBe(
    pcHpAtSave,
  );
  expect(restored.combat?.active).toBe(true);
  expect(restored.combat?.phase).toBe("awaiting_action");
  expect(restored.combat_roll, "读档后不得残留待掷骰授权").toBeNull();
  expect(restored.combat_decision, "保存过的决定必须被读档失效").toBeNull();
  const restoredNpc = (
    latestCombat(frames)?.participants ??
    restored.combat?.participants ??
    []
  ).find((p: { id: string }) => p.id === npcId);
  expect(restoredNpc?.hp, "读档必须回滚存档点之后的战斗伤害").toBe(
    npcAtSave.hp,
  );
  // 存档点之后结算的掷骰记录也必须随读档消失。
  expect(
    (restored.combat_results ?? []).length,
    "存档点之后的结算记录不得在读档后复活",
  ).toBe(0);
  // UI 层：掷骰/取消动作/决定选项按钮都不在了（「申报战斗动作」是 awaiting_action
  // 的正常入口，不是授权残留）。
  const staleButtons = page
    .getByTestId("combat-field-record")
    .getByRole("button");
  await expect(staleButtons.filter({ hasText: "掷骰" })).toHaveCount(0);
  await expect(staleButtons.filter({ hasText: "取消动作" })).toHaveCount(0);
  await page.screenshot({ path: join(evidenceDir, "b-after-load.png") });

  // 旧响应重放（协议级，同一台本地会话的真实客户端输入）：全部拒绝且世界不动。
  const raw = await openRawSocket(`/ws?world_id=${worldId}`);
  await expect
    .poll(() => framesOf(raw.frames, "session_snapshot").length, {
      timeout: 30_000,
    })
    .toBeGreaterThan(0);
  // expected_revision 是 schema 必填整数：从快照信封顶层取（payload.identity 没有该字段，
  // 读错会得到 null → 网关 invalid_action，掩盖真正要验的「授权失效」拒绝）。
  const rawSnapshotFrame = JSON.parse(
    framesOf(raw.frames, "session_snapshot").at(-1)!,
  ) as { revision: number };
  const revision = rawSnapshotFrame.revision;
  expect(typeof revision).toBe("number");
  const decideTemplate = commandEnvelope(frames, "combat_decide");
  const replay = (kind: string, payload: unknown, tag: string) => ({
    ...decideTemplate,
    command_id: `replay-${tag}-${runId}`,
    expected_revision: revision,
    kind,
    payload,
  });
  raw.send(
    replay("combat_roll", { roll_id: roll2.roll_id, response: "roll" }, "r2"),
  );
  raw.send(
    replay("combat_roll", { roll_id: roll1.roll_id, response: "roll" }, "r1"),
  );
  raw.send(
    replay(
      "combat_decide",
      {
        decision_id: decision1.id,
        option_id:
          decision1.options.find(
            (option: { id: string }) => option.id === "confirm_violence",
          )?.id ?? decision1.options[0].id,
      },
      "d1",
    ),
  );
  await expect
    .poll(() => framesOf(raw.frames, "request_error").length, {
      timeout: 30_000,
    })
    .toBe(3);
  for (const rawError of framesOf(raw.frames, "request_error")) {
    const error = JSON.parse(rawError) as {
      payload: { code: string; message: string };
    };
    expect(error.payload.code).toBe("request_not_found");
    expect(error.payload.message).toContain("已失效");
  }
  expect(
    framesOf(raw.frames, "combat_roll_resolved"),
    "旧授权重放不得产生任何结算",
  ).toHaveLength(0);
  expect(latestSnapshot(frames).combat?.phase).toBe("awaiting_action");
  raw.close();

  // 主持重新准备、玩家重新响应：新决定/新掷骰授权与旧的全然不同，流程能继续。
  const pcActor3 = await advanceToPc(page, frames);
  await openConsole(page);
  await page.getByTestId("keeper-cmd-combat_action").click();
  await setKeeperField(page, "actor_id", pcActor3);
  await setKeeperField(page, "target_id", npcId);
  await setKeeperField(page, "action_type", "melee");
  await setKeeperField(page, "damage_spec", "1d3");
  const prepared3 = await submitKeeperCommand(page, frames);
  expect(prepared3.accepted, prepared3.lastError).toBe(true);
  await closeConsole(page);
  await confirmViolenceIfAsked(page, frames);
  const roll3 = lastPayload(frames.received, "combat_roll_required");
  expect(roll3.roll_id).not.toBe(roll2.roll_id);
  expect(roll3.roll_id).not.toBe(roll1.roll_id);
  const receipt3 = await rollPendingDice(page, frames);
  expect(
    receipt3.rolls.length,
    "重新准备+重新响应后必须能正常结算",
  ).toBeGreaterThan(0);
  expect(latestCombat(frames)?.active).toBe(true);
  await context.close();
});

test("C 云端单人战斗中分支：已提交状态复制、旧授权不复制、分支不改父世界", async ({
  browser,
}) => {
  test.setTimeout(300_000);
  await bootServer(true);
  const context = await browser.newContext();
  const page = await context.newPage();
  page.setDefaultTimeout(20_000);
  const frames = collectFrames(page);

  await registerIntoSoloLobby(page, `solo-c-${runId}`);
  const parentWorldId = await createSoloStructuredWorld(
    page,
    frames,
    `战斗分支验收${runId}`,
    "黄千陆",
  );

  // 已提交状态：真实开枪一次（弹药 6→5、NPC 掉 HP）。
  const npcId = await moveToNpcScene(page, frames);
  await startCombatWithNpc(page, frames, npcId);
  await closeConsole(page);
  await page.getByRole("button", { name: "申报战斗动作", exact: true }).click();
  const declareDialog = page.getByRole("dialog", { name: "申报战斗动作" });
  await declareDialog
    .getByLabel("动作", { exact: true })
    .selectOption("firearm");
  await declareDialog.getByLabel("目标", { exact: true }).selectOption(npcId);
  const gunOptions = await declareDialog
    .getByLabel("使用的持有物品", { exact: true })
    .locator("option")
    .evaluateAll((nodes) =>
      nodes.map((node) => ({
        value: (node as HTMLOptionElement).value,
        label: node.textContent || "",
      })),
    );
  const gun = gunOptions.find((option) =>
    option.label.startsWith(".38口径左轮手枪（6发）"),
  );
  expect(gun).toBeTruthy();
  await declareDialog
    .getByLabel("使用的持有物品", { exact: true })
    .selectOption(gun!.value);
  await declareDialog.getByRole("button", { name: "提交申报" }).click();
  const declaration = JSON.parse(
    framesOf(frames.sent, "action_request").at(-1)!,
  ) as { request_id: string };
  await openConsole(page);
  await page
    .getByTestId("keeper-pending-request")
    .filter({ hasText: declaration.request_id })
    .getByRole("button", { name: "准备战斗动作" })
    .click();
  await setKeeperField(page, "damage_spec", "1d2");
  const prepared = await submitKeeperCommand(page, frames);
  expect(prepared.accepted, prepared.lastError).toBe(true);
  await closeConsole(page);
  await confirmViolenceIfAsked(page, frames);
  await rollPendingDice(page, frames);
  await expect
    .poll(() => {
      const items = latestItems(frames);
      return items.find((item: { id: string }) => item.id === gun!.value)
        ?.label;
    })
    .toBe(".38口径左轮手枪（5发）");
  const forkNpcHp = latestCombat(frames).participants.find(
    (p: { id: string }) => p.id === npcId,
  ).hp;
  const forkPcHp = latestSnapshot(frames).character.hp;

  // 分叉点再挂一份「活跃」的待掷骰授权 R2（分支不得复制它）。
  // 开枪结算后先手已让给 NPC，先轮回到调查员。
  const pcActor2 = await advanceToPc(page, frames);
  await openConsole(page);
  // 第一次准备是从待办卡片进入的，控制台仍挂着原申报的 cause 关联；
  // 同 kind 再点不会重置（KeeperConsole 的既有行为），先切到别的命令再切回来。
  await page.getByTestId("keeper-cmd-adjust_stat").click();
  await page.getByTestId("keeper-cmd-combat_action").click();
  await setKeeperField(page, "actor_id", pcActor2);
  await setKeeperField(page, "target_id", npcId);
  await setKeeperField(page, "action_type", "melee");
  await setKeeperField(page, "damage_spec", "1d3");
  const prepared2 = await submitKeeperCommand(page, frames);
  expect(prepared2.accepted, prepared2.lastError).toBe(true);
  await closeConsole(page);
  await confirmViolenceIfAsked(page, frames);
  const roll2 = lastPayload(frames.received, "combat_roll_required");
  expect(roll2?.roll_id).toBeTruthy();

  // 真实 UI 分叉：主持台 → 存档管理 → 时间线 → 创建分支。
  const branchFramesBefore = framesOf(frames.sent, "solo_branch_create").length;
  await openConsole(page);
  await page.getByTestId("keeper-save-panel").click();
  const savePanel = page.locator("#save-panel-overlay");
  await expect(savePanel).toBeVisible();
  await page.locator(".adventure-card.current .adventure-manage").click();
  await expect(page.getByTestId("save-panel-timelines")).toBeVisible();
  await page.locator(".timeline-branch-input").fill(`战斗分支${runId}`);
  await page.locator(".timeline-branch-create").click();
  // 契约：结构化分支帧不得伪造 legacy turn_id。
  await expect
    .poll(() => framesOf(frames.sent, "solo_branch_create").length)
    .toBeGreaterThan(branchFramesBefore);
  const branchFrame = JSON.parse(
    framesOf(frames.sent, "solo_branch_create").at(-1)!,
  ) as Record<string, unknown>;
  expect(
    "turn_id" in branchFrame && branchFrame.turn_id,
    "结构化分支帧不允许携带 legacy turn_id",
  ).toBeFalsy();
  // 服务端回执：solo_world_switched + 断线重连进分支世界。
  await expect
    .poll(() => framesOf(frames.received, "solo_world_switched").length, {
      timeout: 30_000,
    })
    .toBeGreaterThan(0);
  await expect(page.getByTestId("structured-tool-row")).toBeVisible({
    timeout: 90_000,
  });
  const branchWorldId = await page.evaluate(
    () => localStorage.getItem("trpg-active-world-id") || "",
  );
  expect(branchWorldId).toContain("-branch-");
  expect(branchWorldId).not.toBe(parentWorldId);
  await waitForNewSnapshot(frames, 0);

  // 分支复制「已提交状态」：战斗仍在、NPC 掉过的 HP、弹药 5 发、物品 ID 不变。
  const branchSnapshot = latestSnapshot(frames);
  expect(branchSnapshot.combat?.active).toBe(true);
  expect(branchSnapshot.combat?.phase).toBe("awaiting_action");
  expect(
    branchSnapshot.items.find((item: { id: string }) => item.id === gun!.value)
      ?.label,
  ).toBe(".38口径左轮手枪（5发）");
  const branchNpc = (
    latestCombat(frames)?.participants ??
    branchSnapshot.combat?.participants ??
    []
  ).find((p: { id: string }) => p.id === npcId);
  expect(branchNpc?.hp).toBe(forkNpcHp);
  expect(branchSnapshot.character.hp).toBe(forkPcHp);
  // 旧授权不复制：分叉点挂起的 R2 在分支里不存在。
  expect(
    branchSnapshot.combat_roll,
    "分支不得复制父世界的待掷骰授权",
  ).toBeNull();
  const branchButtons = page
    .getByTestId("combat-field-record")
    .getByRole("button");
  await expect(branchButtons.filter({ hasText: "掷骰" })).toHaveCount(0);
  await expect(branchButtons.filter({ hasText: "取消动作" })).toHaveCount(0);
  await page.screenshot({ path: join(evidenceDir, "c-branch-world.png") });

  // 旧授权在分支里协议级重放 → 拒绝。
  const cookie = await cookieHeader(context);
  const raw = await openRawSocket(`/ws/room?world_id=${branchWorldId}`, cookie);
  await expect
    .poll(() => framesOf(raw.frames, "session_snapshot").length, {
      timeout: 30_000,
    })
    .toBeGreaterThan(0);
  const rawRevision = snapshotRevision(raw.frames);
  const envelope = commandEnvelope(frames, "combat_action");
  raw.send({
    ...envelope,
    command_id: `replay-branch-${runId}`,
    world_id: branchWorldId,
    expected_revision: rawRevision,
    kind: "combat_roll",
    payload: { roll_id: roll2.roll_id, response: "roll" },
  });
  await expect
    .poll(() => framesOf(raw.frames, "request_error").length, {
      timeout: 30_000,
    })
    .toBe(1);
  const replayError = JSON.parse(framesOf(raw.frames, "request_error").at(-1)!);
  expect(replayError.payload.code).toBe("request_not_found");
  expect(replayError.payload.message).toContain("已失效");
  raw.close();

  // 分叉用的存档面板还开着，先关掉再操作主持台。
  const savePanelStillOpen = page.locator("#save-panel-overlay");
  if (await savePanelStillOpen.isVisible().catch(() => false)) {
    await page.getByRole("button", { name: "关闭存档管理" }).click();
    await expect(savePanelStillOpen).toBeHidden();
  }

  // 分支中的行动（HP -3）不改变父世界。
  // 云端候选列表同时给出 module 键与 wi_ 认领行两种值；以战斗参与者里的
  // pc ID 为准（与战斗结算使用的是同一个标识）。
  const branchPcId = latestCombat(frames).participants.find(
    (p: { kind: string }) => p.kind === "pc",
  )?.id;
  expect(branchPcId).toBeTruthy();
  await openConsole(page);
  await page.getByTestId("keeper-cmd-adjust_stat").click();
  await setKeeperField(page, "investigator_id", branchPcId);
  await setKeeperField(page, "field", "hp");
  await setKeeperField(page, "delta", "-3");
  await setKeeperField(page, "reason", "验收：分支世界内的独立伤害。");
  const hurt = await submitKeeperCommand(page, frames);
  expect(hurt.accepted, hurt.lastError).toBe(true);
  await closeConsole(page);
  // 快照只在重连时刷新；已结算的 HP 变化看 state_changed 投影与人物面板。
  await expect
    .poll(() => {
      const change = framesOf(frames.received, "state_changed")
        .map((frame) => JSON.parse(frame).payload)
        .filter(
          (payload) =>
            payload &&
            typeof payload.hp === "number" &&
            payload.investigator_id,
        )
        .at(-1);
      return change?.hp;
    })
    .toBe(forkPcHp - 3);

  // 回大厅切到父世界（真实界面路径），父世界保持分叉点状态、等待按钮仍在。
  await page.goto(baseUrl);
  await page.waitForSelector('[data-testid="mode-select"]', {
    timeout: 30_000,
  });
  await page.getByRole("button", { name: /云端单人/ }).click();
  await expect(page.getByRole("heading", { name: "我的冒险" })).toBeVisible();
  await page
    .locator(".adventure-card")
    .first()
    .getByRole("button", { name: "管理时间线", exact: true })
    .click();
  const timelinePanel = page.locator(".solo-timeline-dialog");
  await expect(timelinePanel).toBeVisible();
  const parentRow = timelinePanel.locator(`[data-world="${parentWorldId}"]`);
  await expect(parentRow).toBeVisible();
  await parentRow.getByRole("button", { name: "继续游戏" }).click();
  await expect(page.getByTestId("structured-tool-row")).toBeVisible({
    timeout: 90_000,
  });
  const parentWorldAgain = await page.evaluate(
    () => localStorage.getItem("trpg-active-world-id") || "",
  );
  expect(parentWorldAgain).toBe(parentWorldId);
  await waitForNewSnapshot(frames, 0);
  const parentSnapshot = latestSnapshot(frames);
  expect(parentSnapshot.character.hp, "分支内的 HP 调整不得回流父世界").toBe(
    forkPcHp,
  );
  const parentNpc = (
    latestCombat(frames)?.participants ??
    parentSnapshot.combat?.participants ??
    []
  ).find((p: { id: string }) => p.id === npcId);
  expect(parentNpc?.hp).toBe(forkNpcHp);
  expect(
    parentSnapshot.combat_roll?.roll_id,
    "父世界自己的待掷骰授权必须原样保留",
  ).toBe(roll2.roll_id);
  await page.screenshot({ path: join(evidenceDir, "c-parent-untouched.png") });
  await context.close();
});

test("D 云端单人结案：非法结局被拒、合法结局与奖励可恢复、另存不覆盖、重放不重奖", async ({
  browser,
}) => {
  test.setTimeout(300_000);
  await bootServer(true);
  const context = await browser.newContext();
  const page = await context.newPage();
  page.setDefaultTimeout(20_000);
  const frames = collectFrames(page);

  await registerIntoSoloLobby(page, `solo-d-${runId}`);
  const worldId = await createSoloStructuredWorld(
    page,
    frames,
    `结案验收${runId}`,
    "黄千陆",
  );

  // 非法结局：truth_and_seal 的前置事实未满足，必须被拒。
  await openConsole(page);
  await page.getByTestId("keeper-cmd-end_game").click();
  await setKeeperField(page, "ending_id", "truth_and_seal");
  const illegal = await submitKeeperCommand(page, frames);
  expect(illegal.accepted, "非法结局必须被拒绝").toBe(false);
  expect(illegal.lastError).toContain("前置条件");

  // 合法结局：裁定 investigation_abandoned → leave_arkham 条件齐 → 结算。
  await page.getByTestId("keeper-cmd-record_ruling").click();
  await setKeeperField(page, "flag_id", "investigation_abandoned");
  await page.getByRole("checkbox", { name: /裁定后的状态/ }).check();
  await setKeeperField(page, "basis", "验收：调查员明确放弃调查。");
  const ruled = await submitKeeperCommand(page, frames);
  expect(ruled.accepted, ruled.lastError).toBe(true);
  const audit = page.getByTestId("keeper-ruling-audit");
  await audit.locator(":scope > summary").click();
  const leaveCard = audit.locator('[data-ending-id="leave_arkham"]');
  await expect(leaveCard).toContainText("条件已齐");
  const sentBefore = framesOf(frames.sent, "command_request").length;
  await leaveCard.getByRole("button", { name: "准备结算" }).click();
  expect(
    framesOf(frames.sent, "command_request"),
    "「准备结算」只能填表，不得直接发送命令",
  ).toHaveLength(sentBefore);
  await expect(page.getByLabel("模组结局 ID")).toHaveValue("leave_arkham");
  const settled = await submitKeeperCommand(page, frames);
  expect(settled.accepted, settled.lastError).toBe(true);
  await closeConsole(page);

  const endedFrame = JSON.parse(
    framesOf(frames.received, "game_ended").at(-1)!,
  ) as { payload: { title: string; type: string } };
  expect(endedFrame.payload.title).toBe("逃离阿卡姆");
  const settledFrame = JSON.parse(
    framesOf(frames.received, "case_settled").at(-1)!,
  );
  const receipt = settledFrame.payload;
  expect(receipt.case.case_id).toBe(`${worldId}:leave_arkham`);
  expect(receipt.case.reputation_delta).toBe(1); // neutral
  const investigatorId = receipt.investigator_id as string;
  const caseId = receipt.case.case_id as string;
  const reputationAfterSettle = receipt.career.reputation as number;
  expect(typeof reputationAfterSettle).toBe("number");

  // 结局与本人奖励可见，刷新重连后仍恢复（快照语义，不靠事件重放）。
  await expect(page.getByTestId("combat-ending-record")).toContainText(
    "逃离阿卡姆",
  );
  await expect(page.getByTestId("combat-ending-record")).toContainText(
    "本案声望变化",
  );
  await reenterSoloWorld(page, frames);
  await expect(page.getByTestId("combat-ending-record")).toContainText(
    "逃离阿卡姆",
    { timeout: 90_000 },
  );
  await page.screenshot({ path: join(evidenceDir, "d-ending-record.png") });

  // 另存角色：生成角色库新条目，不覆盖任何既有卡。
  const libraryBefore = await page.evaluate(async () => {
    const response = await fetch("/api/character-library", {
      credentials: "include",
    });
    return (await response.json()).entries as Array<{
      id: string;
      name: string;
    }>;
  });
  const saveCard = page.getByTestId("case-character-actions");
  await expect(saveCard.getByLabel("新角色名")).toBeEnabled({
    timeout: 30_000,
  });
  await saveCard.getByLabel("新角色名").fill(`结案副本${runId}`);
  await saveCard.getByRole("button", { name: "保存为新角色" }).click();
  await expect(
    saveCard.getByRole("button", { name: "已保存", exact: true }),
  ).toBeDisabled({ timeout: 30_000 });
  const libraryAfter = await page.evaluate(async () => {
    const response = await fetch("/api/character-library", {
      credentials: "include",
    });
    return (await response.json()).entries as Array<{
      id: string;
      name: string;
    }>;
  });
  expect(libraryAfter.length).toBe(libraryBefore.length + 1);
  for (const before of libraryBefore) {
    const still = libraryAfter.find((entry) => entry.id === before.id);
    expect(still, "既有角色库条目不得被另存改写").toBeTruthy();
    expect(still!.name).toBe(before.name);
  }
  const newEntry = libraryAfter.find(
    (entry) => entry.name === `结案副本${runId}`,
  );
  expect(newEntry).toBeTruthy();
  expect(newEntry!.id).toMatch(/^chcase_/);

  // 重复保存（真实 HTTP，同一份结案凭证）→ 幂等去重，不新增条目。
  const dedup = await page.evaluate(
    async ({ worldId: wid, investigatorId: inv, caseId: cid }) => {
      const preview = await (
        await fetch("/api/character-library/from-case/preview", {
          method: "POST",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            world_id: wid,
            investigator_id: inv,
            case_id: cid,
            expected_revision: 1,
          }),
        })
      ).json();
      const save = await fetch("/api/character-library/from-case", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          world_id: wid,
          investigator_id: inv,
          case_id: cid,
          expected_revision: preview.revision,
          receipt_digest: preview.receipt_digest,
          name: "重复保存应当被去重",
        }),
      });
      return { status: save.status, body: await save.json() };
    },
    { worldId, investigatorId, caseId },
  );
  expect(dedup.status).toBe(200);
  expect(dedup.body.deduplicated).toBe(true);
  const libraryFinal = await page.evaluate(async () => {
    const response = await fetch("/api/character-library", {
      credentials: "include",
    });
    return (await response.json()).entries as Array<{ name: string }>;
  });
  expect(libraryFinal.length).toBe(libraryAfter.length);
  expect(
    libraryFinal.some((entry) => entry.name === "重复保存应当被去重"),
  ).toBe(false);

  // 请求重放（协议级）：同一帧原样重发 → 幂等账本命中。契约：调用方会收到
  // 从 outbox 重读的**原始事件**（同 event_id，前端按 event_id 去重），
  // 但不得产生任何新事件、新 revision 或二次奖励。
  const originalGameEndedIds = framesOf(frames.received, "game_ended").map(
    (frame) => (JSON.parse(frame) as { event_id: number }).event_id,
  );
  // case_settled 按观众各发一条（调查员 + 主持），payload 是同一张收据；
  // 「奖励只有一份」的判据是不同 payload 恰为一张，event_id 集合用于重放比对。
  const originalSettledFrames = framesOf(frames.received, "case_settled").map(
    (frame) => JSON.parse(frame) as { event_id: number; payload: unknown },
  );
  const originalSettledIds = originalSettledFrames
    .map((frame) => frame.event_id)
    .sort();
  expect(originalGameEndedIds).toHaveLength(1);
  expect(originalSettledIds.length).toBeGreaterThan(0);
  expect(
    new Set(originalSettledFrames.map((frame) => JSON.stringify(frame.payload)))
      .size,
    "结案奖励凭证必须唯一（观众副本同 payload）",
  ).toBe(1);
  const cookie = await cookieHeader(context);
  const raw = await openRawSocket(`/ws/room?world_id=${worldId}`, cookie);
  await expect
    .poll(() => framesOf(raw.frames, "session_snapshot").length, {
      timeout: 30_000,
    })
    .toBeGreaterThan(0);
  const revisionBeforeReplay = snapshotRevision(raw.frames);
  const endGameFrame = commandEnvelope(frames, "end_game");
  raw.send(endGameFrame);
  await expect
    .poll(() => framesOf(raw.frames, "case_settled").length, {
      timeout: 30_000,
    })
    .toBeGreaterThan(0);
  const replayedEndedIds = framesOf(raw.frames, "game_ended").map(
    (frame) => (JSON.parse(frame) as { event_id: number }).event_id,
  );
  const replayedSettledIds = framesOf(raw.frames, "case_settled")
    .map((frame) => (JSON.parse(frame) as { event_id: number }).event_id)
    .sort();
  expect(
    replayedEndedIds,
    "重放只允许重投原始 game_ended（同 event_id），不得产生新事件",
  ).toEqual(originalGameEndedIds);
  expect(
    replayedSettledIds,
    "重放只允许重投原始 case_settled（同 event_id），不得产生新事件",
  ).toEqual(originalSettledIds);
  expect(snapshotRevision(raw.frames), "幂等重放不得推进世界 revision").toBe(
    revisionBeforeReplay,
  );

  // 换 command_id 的「新」结案请求 → 已结算拒绝；两种路径奖励都只有一份。
  const freshRevision = snapshotRevision(raw.frames);
  raw.send({
    ...endGameFrame,
    command_id: `settle-again-${runId}`,
    expected_revision: freshRevision,
  });
  await expect
    .poll(() => framesOf(raw.frames, "request_error").length, {
      timeout: 30_000,
    })
    .toBeGreaterThan(0);
  // game_over 门禁先于结案查重：新 command_id 的结案以 invalid_action 拒绝，
  // 关键是不得产生第二次结算。
  const settleAgainError = JSON.parse(
    framesOf(raw.frames, "request_error").at(-1)!,
  ) as { payload: { code: string; message: string } };
  expect(settleAgainError.payload.code).toBe("invalid_action");
  expect(settleAgainError.payload.message).toContain("游戏已结束");
  raw.close();
  await reenterSoloWorld(page, frames);
  const finalSnapshot = latestSnapshot(frames);
  // case_settlements 是收据数组（不是按 case_id 键控的字典）。
  const settlements = finalSnapshot.case_settlements ?? [];
  const settledCaseIds = (settlements as Array<{ case?: { case_id?: string } }>)
    .map((entry) => entry?.case?.case_id)
    .filter(Boolean);
  expect(settledCaseIds, "重放与重复结案后，结案账本必须恰好一条记录").toEqual([
    caseId,
  ]);
  // 页面连接可能收到同 event_id 的重投副本（幂等重放的契约行为）；
  // 「只发一次奖」的正确判据是不同收据 payload 恰为一张（观众副本同 payload）。
  const distinctSettledPayloads = new Set(
    framesOf(frames.received, "case_settled").map((frame) =>
      JSON.stringify((JSON.parse(frame) as { payload: unknown }).payload),
    ),
  );
  expect(
    distinctSettledPayloads.size,
    "全程只应有一张结案奖励收据（观众副本同 payload）",
  ).toBe(1);
  await context.close();
});

test("E 本地读档后历史结案凭证不混入：回滚抹掉旧结案，新卡只认新凭证", async ({
  browser,
}) => {
  test.setTimeout(300_000);
  await bootServer(false);
  const context = await browser.newContext();
  const page = await context.newPage();
  page.setDefaultTimeout(20_000);
  const frames = collectFrames(page);

  const worldId = await createLocalStructuredWorld(page, frames);

  // 存档点：结案之前。
  await quickSave(page, frames);

  // 第一次结案（leave_arkham / 逃离阿卡姆）。
  await openConsole(page);
  await page.getByTestId("keeper-cmd-record_ruling").click();
  await setKeeperField(page, "flag_id", "investigation_abandoned");
  await page.getByRole("checkbox", { name: /裁定后的状态/ }).check();
  await setKeeperField(page, "basis", "验收：第一次结案（将被读档回滚）。");
  const ruled = await submitKeeperCommand(page, frames);
  expect(ruled.accepted, ruled.lastError).toBe(true);
  await page.getByTestId("keeper-cmd-end_game").click();
  await setKeeperField(page, "ending_id", "leave_arkham");
  const settledA = await submitKeeperCommand(page, frames);
  expect(settledA.accepted, settledA.lastError).toBe(true);
  await closeConsole(page);
  await expect(page.getByTestId("combat-ending-record")).toContainText(
    "逃离阿卡姆",
  );
  const caseA = `${worldId}:leave_arkham`;
  const receiptA = JSON.parse(
    framesOf(frames.received, "case_settled").at(-1)!,
  ).payload;
  const investigatorId = receiptA.investigator_id as string;

  // 主动读档：结案连同裁定一起回滚。
  await loadAutoSave(page, frames);
  const restored = latestSnapshot(frames);
  expect(restored.game_over, "读档后不得残留 game_over").toBeFalsy();
  // case_settlements 是收据数组；空数组也是 truthy，必须按长度断言。
  expect(
    (restored.case_settlements ?? []).length,
    "读档后不得残留历史结案凭证",
  ).toBe(0);
  await expect(page.getByTestId("combat-ending-record")).toHaveCount(0);
  await openConsole(page);
  const audit = page.getByTestId("keeper-ruling-audit");
  await audit.locator(":scope > summary").click();
  await expect(
    audit.locator('[data-ending-id="leave_arkham"]'),
    "读档必须回滚裁定：leave_arkham 回到条件未齐",
  ).toContainText("条件未齐");

  // 第二次结案（devoured_by_ink / 被怪物吞噬）：另一条合法路径。
  await page.getByTestId("keeper-cmd-record_ruling").click();
  await setKeeperField(page, "flag_id", "monster_manifested");
  await page.getByRole("checkbox", { name: /裁定后的状态/ }).check();
  await setKeeperField(page, "basis", "验收：怪物完全显形。");
  const ruled2 = await submitKeeperCommand(page, frames);
  expect(ruled2.accepted, ruled2.lastError).toBe(true);
  await page.getByTestId("keeper-cmd-end_game").click();
  await setKeeperField(page, "ending_id", "devoured_by_ink");
  const settledB = await submitKeeperCommand(page, frames);
  expect(settledB.accepted, settledB.lastError).toBe(true);
  await closeConsole(page);
  await expect(page.getByTestId("combat-ending-record")).toContainText(
    "被怪物吞噬",
  );

  // 账本上只有第二次结案的凭证，没有第一次的残留。
  // 快照只在重连时刷新：本地世界刷新页面直接回游戏，拿到权威快照再断言。
  const snapshotBefore2 = framesOf(frames.received, "session_snapshot").length;
  await page.reload();
  await expect(page.getByTestId("structured-tool-row")).toBeVisible({
    timeout: 90_000,
  });
  await expect
    .poll(() => framesOf(frames.received, "session_snapshot").length, {
      timeout: 30_000,
    })
    .toBeGreaterThan(snapshotBefore2);
  const settlements = latestSnapshot(frames).case_settlements;
  const settlementsJson = JSON.stringify(settlements ?? {});
  expect(settlementsJson).toContain(`${worldId}:devoured_by_ink`);
  expect(settlementsJson).not.toContain("leave_arkham");

  // 历史凭证（case A）不能用于预览/另存；新凭证（case B）可以，且卡面只认 B。
  const revision = snapshotRevision(frames.received);
  const api = async (path: string, body: unknown) =>
    page.evaluate(
      async ({ path: p, body: b }) => {
        const response = await fetch(p, {
          method: "POST",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(b),
        });
        return {
          ok: response.ok,
          status: response.status,
          body: await response.json(),
        };
      },
      { path, body },
    );
  const previewA = await api("/api/character-library/from-case/preview", {
    world_id: worldId,
    investigator_id: investigatorId,
    case_id: caseA,
    expected_revision: revision,
  });
  expect(previewA.ok, "读档回滚后的历史结案凭证不得再用于另存角色").toBe(false);
  const caseB = `${worldId}:devoured_by_ink`;
  const previewB = await api("/api/character-library/from-case/preview", {
    world_id: worldId,
    investigator_id: investigatorId,
    case_id: caseB,
    expected_revision: revision,
  });
  expect(previewB.ok, JSON.stringify(previewB.body)).toBe(true);
  const exportB = await api("/api/character-library/from-case/export", {
    world_id: worldId,
    investigator_id: investigatorId,
    case_id: caseB,
    expected_revision: previewB.body.revision,
    receipt_digest: previewB.body.receipt_digest,
  });
  expect(exportB.ok, JSON.stringify(exportB.body)).toBe(true);
  expect(exportB.body.format).toBe("trpg-character-card");
  const cardJson = JSON.stringify(exportB.body.card);
  expect(cardJson).toContain("被怪物吞噬");
  expect(cardJson).not.toContain("逃离阿卡姆");
  expect(cardJson).not.toContain("leave_arkham");
  await page.screenshot({ path: join(evidenceDir, "e-second-ending.png") });
  await context.close();
});

test("F 多人房间：掷骰/决定/骰点/奖励/控制权限隔离，PvP 授权失效，房内禁止边界", async ({
  browser,
}) => {
  test.setTimeout(420_000);
  await bootServer(true);
  const keeper = await (await browser.newContext()).newPage();
  const playerA = await (await browser.newContext()).newPage();
  const playerB = await (await browser.newContext()).newPage();
  const viewer = await (await browser.newContext()).newPage();
  for (const page of [keeper, playerA, playerB, viewer])
    page.setDefaultTimeout(20_000);
  const keeperFrames = collectFrames(keeper);
  const playerAFrames = collectFrames(playerA);
  const playerBFrames = collectFrames(playerB);
  const viewerFrames = collectFrames(viewer);
  const roomName = `隔离验收房${runId}`;

  // ---- 建房（人类主持）+ 两名玩家认领调查员 ----
  await register(keeper, `keeper${runId}`);
  await keeper.getByLabel("房间名称").fill(roomName);
  await keeper.getByRole("radio", { name: "人类主持" }).click();
  await keeper.getByRole("button", { name: "创建房间" }).click();
  await expect(keeper.getByRole("heading", { name: roomName })).toBeVisible();
  const worldId = await keeper.evaluate(
    () => localStorage.getItem("trpg-online-world-id") || "",
  );
  expect(worldId).not.toBe("");
  await keeper.getByLabel("有效期（小时）").fill("72");
  await keeper.getByLabel("使用次数").fill("5");
  await keeper.getByRole("button", { name: "生成邀请码" }).click();
  const invite = (await keeper.locator(".invite-token").textContent())!.trim();
  expect(invite).toBeTruthy();

  for (const [page, name] of [
    [playerA, `alice${runId}`],
    [playerB, `bob${runId}`],
  ] as const) {
    await register(page, name);
    await page
      .getByRole("textbox", { name: "邀请码", exact: true })
      .fill(invite);
    await page.getByRole("button", { name: "加入房间" }).click();
    await expect(page.getByRole("heading", { name: roomName })).toBeVisible();
    await page.getByRole("button", { name: "选择" }).first().click();
    await expect(page.getByRole("button", { name: "释放" })).toBeVisible();
  }
  for (const page of [keeper, playerA, playerB]) {
    await expect(page.locator(".member-row").first()).toBeVisible();
    await page.getByRole("button", { name: "准备" }).click();
  }
  await expect
    .poll(
      () => {
        const states = framesOf(keeperFrames.received, "room_state");
        return (
          (JSON.parse(states.at(-1) ?? "{}") as { ready_user_ids?: string[] })
            .ready_user_ids?.length ?? 0
        );
      },
      { timeout: 30_000 },
    )
    .toBe(3);
  await keeper.getByRole("button", { name: "开始游戏" }).click();
  for (const page of [playerA, playerB]) {
    await expect(page.locator("#user-input")).toBeEnabled({ timeout: 90_000 });
    await expect(page.getByTestId("structured-tool-row")).toBeVisible({
      timeout: 60_000,
    });
  }

  // 调查员归属（真实成员投影，不猜顺序）。
  const members = await keeper.evaluate(async (wid) => {
    const response = await fetch(`/api/worlds/${wid}/members`, {
      credentials: "include",
    });
    return (await response.json()) as {
      members: Array<{
        username: string;
        investigator?: { character_key?: string };
      }>;
    };
  }, worldId);
  const investigatorOf = (username: string) =>
    members.members.find((member) => member.username === username)?.investigator
      ?.character_key ?? "";
  const invA = investigatorOf(`alice${runId}`);
  const invB = investigatorOf(`bob${runId}`);
  expect(invA).not.toBe("");
  expect(invB).not.toBe("");

  // ---- 旁观者开局后加入：只读，无私密帧 ----
  // 开局后邀请入口在「房间管理」：先展开房间坞（OnlineRoomDock 收起态是入口条）。
  await keeper
    .getByTestId("online-room-dock")
    .locator("button")
    .first()
    .click();
  await keeper.getByRole("button", { name: "房间管理" }).click();
  // 已有玩家邀请码时面板显示邀请卡而非创建表单；撤销旧码（不影响已入房玩家）
  // 才能创建旁观者邀请。
  await keeper
    .getByRole("button", { name: "撤销", exact: true })
    .first()
    .click();
  await keeper.getByLabel("邀请角色").selectOption("viewer");
  await keeper.getByRole("button", { name: "生成邀请码" }).click();
  const viewerInvite = (await keeper
    .locator(".invite-token")
    .textContent())!.trim();
  await keeper.getByRole("button", { name: "返回游戏" }).click();
  await register(viewer, `viewer${runId}`);
  await viewer
    .getByRole("textbox", { name: "邀请码", exact: true })
    .fill(viewerInvite);
  await viewer.getByRole("button", { name: "加入房间" }).click();
  await expect(viewer.locator("#user-input")).toBeDisabled({ timeout: 90_000 });
  await expect(viewer.getByTestId("btn-keeper-console")).toHaveCount(0);

  // ---- 战斗：决定/掷骰授权只发给当事人与主持 ----
  const npcId = await moveToNpcScene(keeper, keeperFrames);
  await openConsole(keeper);
  await keeper.getByTestId("keeper-cmd-combat_start").click();
  await setKeeperField(keeper, "participants", npcId);
  const started = await submitKeeperCommand(keeper, keeperFrames);
  expect(started.accepted, started.lastError).toBe(true);
  for (
    let turn = 0;
    latestCombat(keeperFrames).current_actor === npcId && turn < 3;
    turn += 1
  ) {
    await keeper.getByTestId("keeper-cmd-combat_action").click();
    await setKeeperField(keeper, "actor_id", npcId);
    await setKeeperField(keeper, "action_type", "move");
    await setKeeperField(keeper, "description", "退到房间另一侧。");
    const moved = await submitKeeperCommand(keeper, keeperFrames);
    expect(moved.accepted, moved.lastError).toBe(true);
  }
  await closeConsole(keeper);
  const actorId = latestCombat(keeperFrames).current_actor;
  const actorPage = actorId === invA ? playerA : playerB;
  const actorFrames = actorId === invA ? playerAFrames : playerBFrames;
  const otherPage = actorId === invA ? playerB : playerA;
  const otherFrames = actorId === invA ? playerBFrames : playerAFrames;
  const otherId = actorId === invA ? invB : invA;
  expect([invA, invB]).toContain(actorId);

  await openConsole(keeper);
  await keeper.getByTestId("keeper-cmd-combat_action").click();
  await setKeeperField(keeper, "actor_id", actorId);
  await setKeeperField(keeper, "target_id", npcId);
  await setKeeperField(keeper, "action_type", "melee");
  await setKeeperField(keeper, "damage_spec", "1d3");
  const prepared = await submitKeeperCommand(keeper, keeperFrames);
  expect(prepared.accepted, prepared.lastError).toBe(true);
  await closeConsole(keeper);

  // 决定（不可逆暴力）只进当事人与主持的帧流；其他人连帧都收不到。
  await expect
    .poll(() => latestCombat(keeperFrames)?.awaiting_decision, {
      timeout: 20_000,
    })
    .toBeTruthy();
  expect(
    framesOf(actorFrames.received, "combat_decision_required").length,
  ).toBeGreaterThan(0);
  expect(
    framesOf(keeperFrames.received, "combat_decision_required").length,
  ).toBeGreaterThan(0);
  expect(
    framesOf(otherFrames.received, "combat_decision_required"),
  ).toHaveLength(0);
  expect(
    framesOf(viewerFrames.received, "combat_decision_required"),
  ).toHaveLength(0);
  await expect(
    otherPage.getByTestId("combat-field-record").getByRole("button"),
  ).toHaveCount(0);
  await expect(
    viewer.getByTestId("combat-field-record").getByRole("button"),
  ).toHaveCount(0);
  await confirmViolenceIfAsked(actorPage, actorFrames);
  // 决定结算与 combat_roll_required 是两帧，等帧到达再取（消除竞态）。
  await expect
    .poll(() => framesOf(actorFrames.received, "combat_roll_required").length, {
      timeout: 20_000,
    })
    .toBeGreaterThan(0);
  const roll1 = lastPayload(actorFrames.received, "combat_roll_required");
  expect(roll1?.roll_id).toBeTruthy();
  expect(framesOf(otherFrames.received, "combat_roll_required")).toHaveLength(
    0,
  );
  expect(framesOf(viewerFrames.received, "combat_roll_required")).toHaveLength(
    0,
  );
  await expect(
    actorPage
      .getByTestId("combat-field-record")
      .getByRole("button", { name: "掷骰", exact: true }),
  ).toBeEnabled();

  // 协议级越权：另一名玩家/旁观者拿当事人的 roll_id 直接发帧 → 拒绝。
  const decideTemplate = commandEnvelope(actorFrames, "combat_decide");
  const otherCookie = await cookieHeader(otherPage.context());
  const rawOther = await openRawSocket(
    `/ws/room?world_id=${worldId}`,
    otherCookie,
  );
  await expect
    .poll(() => framesOf(rawOther.frames, "session_snapshot").length, {
      timeout: 30_000,
    })
    .toBeGreaterThan(0);
  rawOther.send({
    ...decideTemplate,
    command_id: `steal-roll-${runId}`,
    world_id: worldId,
    expected_revision: snapshotRevision(rawOther.frames),
    kind: "combat_roll",
    payload: { roll_id: roll1.roll_id, response: "roll" },
  });
  const viewerCookie = await cookieHeader(viewer.context());
  const rawViewer = await openRawSocket(
    `/ws/room?world_id=${worldId}`,
    viewerCookie,
  );
  await expect
    .poll(() => framesOf(rawViewer.frames, "session_snapshot").length, {
      timeout: 30_000,
    })
    .toBeGreaterThan(0);
  rawViewer.send({
    ...decideTemplate,
    command_id: `viewer-roll-${runId}`,
    world_id: worldId,
    expected_revision: snapshotRevision(rawViewer.frames),
    kind: "combat_roll",
    payload: { roll_id: roll1.roll_id, response: "roll" },
  });
  await expect
    .poll(() => framesOf(rawOther.frames, "request_error").length, {
      timeout: 30_000,
    })
    .toBe(1);
  await expect
    .poll(() => framesOf(rawViewer.frames, "request_error").length, {
      timeout: 30_000,
    })
    .toBe(1);
  for (const raw of [rawOther, rawViewer]) {
    const error = JSON.parse(framesOf(raw.frames, "request_error").at(-1)!);
    expect(["not_authorized", "request_not_found"]).toContain(
      error.payload.code,
    );
    raw.close();
  }

  // 当事人掷骰：骰点回执只进当事人与主持；公开战况（HP）对所有人可见。
  const receipt = await rollPendingDice(actorPage, actorFrames);
  expect(receipt.rolls.length).toBeGreaterThan(0);
  // 主持按协议 §5.2 同时可见 investigator 向与 keeper 向副本（同一份收据，
  // 两个 event_id）；判据是收据 payload 唯一且 roll_id 一致，而非帧数。
  const keeperReceipts = framesOf(keeperFrames.received, "combat_roll_resolved")
    .map(
      (frame) =>
        JSON.parse(frame) as { payload: { roll_id: string; result: unknown } },
    )
    .filter((frame) => frame.payload.roll_id === roll1.roll_id);
  expect(
    keeperReceipts.length,
    "主持必须收到本次掷骰回执（允许观众副本各一）",
  ).toBeGreaterThan(0);
  expect(
    new Set(keeperReceipts.map((frame) => JSON.stringify(frame.payload.result)))
      .size,
    "主持收到的各副本必须是同一份骰点收据",
  ).toBe(1);
  expect(framesOf(otherFrames.received, "combat_roll_resolved")).toHaveLength(
    0,
  );
  expect(framesOf(viewerFrames.received, "combat_roll_resolved")).toHaveLength(
    0,
  );
  await expect(otherPage.getByTestId("combat-field-record")).toBeVisible();

  // ---- PvP 双向参与授权：另一玩家成为目标，随后被 adjust_stat 整体失效 ----
  // 上次掷骰结算后先手已让给下一位；先用普通 move 行动轮回到 actorId。
  await openConsole(keeper);
  for (
    let turn = 0;
    latestCombat(keeperFrames).current_actor !== actorId && turn < 6;
    turn += 1
  ) {
    const current = latestCombat(keeperFrames).current_actor;
    await keeper.getByTestId("keeper-cmd-combat_action").click();
    await setKeeperField(keeper, "actor_id", current);
    await setKeeperField(keeper, "action_type", "move");
    await setKeeperField(keeper, "description", "调整站位。");
    const moved = await submitKeeperCommand(keeper, keeperFrames);
    expect(moved.accepted, moved.lastError).toBe(true);
  }
  expect(latestCombat(keeperFrames).current_actor).toBe(actorId);
  await keeper.getByTestId("keeper-cmd-combat_action").click();
  await setKeeperField(keeper, "actor_id", actorId);
  await setKeeperField(keeper, "target_id", otherId);
  await setKeeperField(keeper, "action_type", "melee");
  await setKeeperField(keeper, "damage_spec", "1d3");
  const preparedPvp = await submitKeeperCommand(keeper, keeperFrames);
  expect(preparedPvp.accepted, preparedPvp.lastError).toBe(true);
  await closeConsole(keeper);
  // PvP 之前可能先问「不可逆暴力」；逐层确认，直到进入双向参与同意链。
  let pvpDecision: any = null;
  let pvpOwnerPage = actorPage;
  for (let step = 0; step < 4 && !pvpDecision; step += 1) {
    await expect
      .poll(
        () =>
          framesOf(actorFrames.received, "combat_decision_required").length +
          framesOf(otherFrames.received, "combat_decision_required").length,
        { timeout: 20_000 },
      )
      .toBeGreaterThan(0);
    const actorDecisions = framesOf(
      actorFrames.received,
      "combat_decision_required",
    );
    const otherDecisions = framesOf(
      otherFrames.received,
      "combat_decision_required",
    );
    const latestRaw = [...actorDecisions, ...otherDecisions].at(-1)!;
    const candidate = JSON.parse(latestRaw).payload;
    pvpOwnerPage = actorDecisions.at(-1) === latestRaw ? actorPage : otherPage;
    if (candidate.kind === "pvp_consent") {
      pvpDecision = candidate;
      break;
    }
    // 前置决定（如不可逆暴力）由当事玩家确认，授权链才继续。
    const proceed = candidate.options.find(
      (option: { id: string }) => option.id === "confirm_violence",
    );
    expect(proceed, `未预期的决定类型：${candidate.kind}`).toBeTruthy();
    const decisionButton = pvpOwnerPage
      .getByTestId("combat-field-record")
      .getByRole("button", { name: proceed.label, exact: true });
    await decisionButton.click();
    await expect(decisionButton).toHaveCount(0, { timeout: 20_000 });
  }
  expect(pvpDecision, "PvP 必须进入双向参与同意（pvp_consent）").toBeTruthy();
  // 同意请求只发给当事玩家（+主持），旁观者收不到。
  expect(
    framesOf(viewerFrames.received, "combat_decision_required"),
  ).toHaveLength(0);

  // 主持提交事实修正（adjust_stat）→ PvP 双向授权整体失效，双方按钮消失。

  // 主持提交事实修正（adjust_stat）→ PvP 双向授权整体失效，双方按钮消失。
  await openConsole(keeper);
  await keeper.getByTestId("keeper-cmd-adjust_stat").click();
  await setKeeperField(keeper, "investigator_id", actorId);
  await setKeeperField(keeper, "field", "hp");
  await setKeeperField(keeper, "delta", "-1");
  await setKeeperField(keeper, "reason", "验收：事实修正使 PvP 授权失效。");
  const adjusted = await submitKeeperCommand(keeper, keeperFrames);
  expect(adjusted.accepted, adjusted.lastError).toBe(true);
  await closeConsole(keeper);
  for (const page of [playerA, playerB]) {
    const buttons = page.getByTestId("combat-field-record").getByRole("button");
    const labels = (await buttons.allTextContents())
      .map((text) => text.trim())
      .filter(Boolean);
    expect(
      labels.filter((text) => text !== "申报战斗动作"),
      "PvP 授权失效后卡片上不得残留任何授权按钮（掷骰/同意/取消等）",
    ).toEqual([]);
  }
  // 失效后的 PvP 同意帧重放 → 拒绝。
  const pvpCookie = await cookieHeader(pvpOwnerPage.context());
  const rawPvp = await openRawSocket(`/ws/room?world_id=${worldId}`, pvpCookie);
  await expect
    .poll(() => framesOf(rawPvp.frames, "session_snapshot").length, {
      timeout: 30_000,
    })
    .toBeGreaterThan(0);
  rawPvp.send({
    ...decideTemplate,
    command_id: `pvp-stale-${runId}`,
    world_id: worldId,
    expected_revision: snapshotRevision(rawPvp.frames),
    kind: "combat_decide",
    payload: {
      decision_id: pvpDecision.id,
      option_id: pvpDecision.options[0].id,
    },
  });
  await expect
    .poll(() => framesOf(rawPvp.frames, "request_error").length, {
      timeout: 30_000,
    })
    .toBe(1);
  const pvpError = JSON.parse(framesOf(rawPvp.frames, "request_error").at(-1)!);
  expect(pvpError.payload.code).toBe("request_not_found");
  expect(pvpError.payload.message).toContain("已失效");
  rawPvp.close();

  // ---- 结束遭遇 + 房内禁止边界（真实服务端拒绝，不绕过） ----
  await openConsole(keeper);
  await keeper.getByTestId("keeper-cmd-combat_end").click();
  await setKeeperField(keeper, "reason", "验收：隔离断言完毕，结束遭遇。");
  const endedCombat = await submitKeeperCommand(keeper, keeperFrames);
  expect(endedCombat.accepted, endedCombat.lastError).toBe(true);
  await closeConsole(keeper);
  for (const page of [keeper, playerA, playerB, viewer]) {
    await expect(page.getByTestId("combat-field-record")).toHaveCount(0);
  }
  // 多人房间：读档/分支是真实禁止边界。
  const keeperCookie = await cookieHeader(keeper.context());
  const rawKeeper = await openRawSocket(
    `/ws/room?world_id=${worldId}`,
    keeperCookie,
  );
  await expect
    .poll(() => framesOf(rawKeeper.frames, "session_snapshot").length, {
      timeout: 30_000,
    })
    .toBeGreaterThan(0);
  const boundaryBefore = framesOf(
    rawKeeper.frames,
    "room_action_rejected",
  ).length;
  rawKeeper.send({ type: "save_load", slot_id: "slot_000" });
  rawKeeper.send({ type: "solo_branch_create", label: "越界分支" });
  rawKeeper.send({ type: "turn_branch_create", label: "越界分支" });
  await expect
    .poll(() => framesOf(rawKeeper.frames, "room_action_rejected").length, {
      timeout: 30_000,
    })
    .toBe(boundaryBefore + 3);
  rawKeeper.close();
  // 前端同一边界：多人房间的存档面板不渲染分支入口。
  await openConsole(keeper);
  await keeper.getByTestId("keeper-save-panel").click();
  await expect(keeper.locator("#save-panel-overlay")).toBeVisible();
  await expect(keeper.locator(".timeline-branch-create")).toHaveCount(0);
  await keeper.getByRole("button", { name: "关闭存档管理" }).click();
  await expect(keeper.locator("#save-panel-overlay")).toBeHidden();
  await closeConsole(keeper);

  // ---- 结案：奖励只进本人帧流；keeper/旁观者没有另存入口 ----
  await openConsole(keeper);
  await keeper.getByTestId("keeper-cmd-record_ruling").click();
  await setKeeperField(keeper, "flag_id", "investigation_abandoned");
  await keeper.getByRole("checkbox", { name: /裁定后的状态/ }).check();
  await setKeeperField(keeper, "basis", "验收：全桌同意离开阿卡姆。");
  const ruled = await submitKeeperCommand(keeper, keeperFrames);
  expect(ruled.accepted, ruled.lastError).toBe(true);
  await keeper.getByTestId("keeper-cmd-end_game").click();
  await setKeeperField(keeper, "ending_id", "leave_arkham");
  const settled = await submitKeeperCommand(keeper, keeperFrames);
  expect(settled.accepted, settled.lastError).toBe(true);
  await closeConsole(keeper);

  const settledIdsOf = (frames: Frames) =>
    framesOf(frames.received, "case_settled").map(
      (raw) =>
        (JSON.parse(raw) as { payload: { investigator_id: string } }).payload
          .investigator_id,
    );
  expect(settledIdsOf(playerAFrames)).toEqual([invA]);
  expect(settledIdsOf(playerBFrames)).toEqual([invB]);
  expect(settledIdsOf(viewerFrames)).toEqual([]);
  expect(new Set(settledIdsOf(keeperFrames))).toEqual(new Set([invA, invB]));
  await expect(playerA.getByTestId("case-character-actions")).toBeVisible();
  await expect(playerB.getByTestId("case-character-actions")).toBeVisible();
  await expect(keeper.getByTestId("case-character-actions")).toHaveCount(0);
  await expect(viewer.getByTestId("case-character-actions")).toHaveCount(0);
  await expect(viewer.getByTestId("combat-ending-record")).toContainText(
    "逃离阿卡姆",
  );
  await viewer.screenshot({ path: join(evidenceDir, "f-viewer-readonly.png") });

  // 控制权限：玩家/旁观者直接发主持命令 → 拒绝。
  const rawB = await openRawSocket(`/ws/room?world_id=${worldId}`, otherCookie);
  await expect
    .poll(() => framesOf(rawB.frames, "session_snapshot").length, {
      timeout: 30_000,
    })
    .toBeGreaterThan(0);
  rawB.send({
    ...decideTemplate,
    command_id: `usurp-keeper-${runId}`,
    world_id: worldId,
    expected_revision: snapshotRevision(rawB.frames),
    kind: "end_game",
    payload: { ending_id: "leave_arkham" },
  });
  const rawViewer2 = await openRawSocket(
    `/ws/room?world_id=${worldId}`,
    viewerCookie,
  );
  await expect
    .poll(() => framesOf(rawViewer2.frames, "session_snapshot").length, {
      timeout: 30_000,
    })
    .toBeGreaterThan(0);
  rawViewer2.send({
    ...decideTemplate,
    command_id: `viewer-publish-${runId}`,
    world_id: worldId,
    expected_revision: snapshotRevision(rawViewer2.frames),
    kind: "publish_message",
    payload: {
      speaker_kind: "keeper",
      audience_kind: "public",
      text: "旁观者不应能发布主持叙述。",
    },
  });
  await expect
    .poll(() => framesOf(rawB.frames, "request_error").length, {
      timeout: 30_000,
    })
    .toBe(1);
  await expect
    .poll(() => framesOf(rawViewer2.frames, "request_error").length, {
      timeout: 30_000,
    })
    .toBe(1);
  const usurpError = JSON.parse(framesOf(rawB.frames, "request_error").at(-1)!);
  // 玩家伪装主持命令：命令层按 keeper 授权复核（keeper_required），
  // 或成员权限层拦截（not_authorized）——两者都是真实拒绝契约。
  expect(["keeper_required", "not_authorized"]).toContain(
    usurpError.payload.code,
  );
  const viewerError = JSON.parse(
    framesOf(rawViewer2.frames, "request_error").at(-1)!,
  );
  // 旁观者伪造主持叙述：schema/命令层（invalid_action）或权限层
  // （keeper_required/not_authorized）拒绝均可，关键是不得产生发布事件。
  expect(["keeper_required", "not_authorized", "invalid_action"]).toContain(
    viewerError.payload.code,
  );
  expect(
    framesOf(keeperFrames.received, "message_completed").filter((frame) =>
      frame.includes("旁观者不应能发布主持叙述"),
    ),
    "旁观者伪造的主持叙述不得出现在任何帧流",
  ).toHaveLength(0);
  rawB.close();
  rawViewer2.close();
  await keeper.context().close();
  await playerA.context().close();
  await playerB.context().close();
  await viewer.context().close();
});
