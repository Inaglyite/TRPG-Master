/**
 * 猩红文档 · 全真主线验收 —— 一位人类主持 + 两位玩家，零模型调用。
 *
 * 验收目标：从建房到 truth_and_seal 结案，完整走完模组主线
 * （法伦办公室→停尸房→研究生→莱特办公室/小屋→精神病院→买家→监视古董店→
 * 搜店→伏击战→文档+徽章→封印→结案→各自保存结案角色），
 * 全部通过真实前端 UI 与真实后端完成，不调用任何模型。
 *
 * 边界：
 * - 独立副仓库（/tmp/trpg-scarlet-acc/repo）内运行；服务每跑一次起独立
 *   uvicorn（127.0.0.1:8879，mkdtemp runtime + SQLite）。不碰 Pi/正式环境。
 * - 零模型：OPENAI_BASE_URL 指向「模型陷阱」TCP 监听（收到连接即断开并计数），
 *   用例结束断言计数为零。
 * - 不修改数据库/世界 JSON、不加后门、不改模组；主持裁定一律走 record_ruling
 *   并在 basis 里写明事实依据。失败检定用真实重试（换人/耗时），不补状态。
 * - 越权探针（玩家代发主持命令、代掷他人检定、偷读主持手册）单独标注，
 *   用同一账号的原始 WebSocket/REST 发送，属于真实客户端能力。
 * - 发现缺口记录进 gaps[]（类别①-⑤），不擅自改产品代码。
 *
 * 证据：/tmp/trpg-scarlet-acc/evidence/（报告 JSON、截图、服务端日志、世界状态只读导出）。
 */

import { spawn, type ChildProcess } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
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
const WsClient = require("ws") as new (
  url: string,
  options?: { headers?: Record<string, string> },
) => {
  on(event: string, listener: (data: unknown) => void): void;
  once(event: string, listener: (arg?: unknown) => void): void;
  send(data: string): void;
  close(): void;
};

const port = 8879;
const baseUrl = `http://127.0.0.1:${port}`;
const repositoryRoot = resolve(import.meta.dirname, "../..");
const evidenceDir = resolve(
  repositoryRoot,
  "test-results/keeper-platform/scarlet-human-mainline",
  String(Date.now()),
);
const shotsDir = join(evidenceDir, "shots");
const MODULE = "猩红文档";
const PASSWORD = "scarlet playthrough e2e";
const runId = Math.random().toString(36).slice(2, 8);

let runtimeRoot = "";
let server: ChildProcess | null = null;
let serverOutput = "";
let modelTrap: TcpServer | null = null;
let modelCalls = 0;
let modelTrapUrl = "";

// ---- 验收记录（报告用，跨 helper 共享） ----
type Stage = {
  id: string;
  name: string;
  ok: boolean;
  detail: string;
  at: string;
};
type Gap = {
  category: string;
  title: string;
  detail: string;
};
const stages: Stage[] = [];
const gaps: Gap[] = [];
let worldId = "";
let invA = "";
let invB = "";
const pages: Record<string, Page> = {};
const frameDump: Record<string, Frames> = {};

function recordGap(category: string, title: string, detail: string) {
  gaps.push({ category, title, detail });
  // eslint-disable-next-line no-console
  console.log(`[GAP ${category}] ${title}: ${detail}`);
}

function pythonPath(): string {
  return (
    process.env.TRPG_E2E_PYTHON ??
    (existsSync(join(repositoryRoot, ".venv/bin/python"))
      ? join(repositoryRoot, ".venv/bin/python")
      : "python")
  );
}

async function waitForServer(): Promise<void> {
  const client = await request.newContext();
  try {
    for (let attempt = 0; attempt < 120; attempt += 1) {
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

async function bootServer(): Promise<void> {
  runtimeRoot = mkdtempSync(join(tmpdir(), "trpg-scarlet-acc-"));
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
        TRPG_REQUIRE_AUTH: "1",
        TRPG_ALLOW_REGISTRATION: "1",
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
}

test.beforeAll(async () => {
  console.log(`Scarlet UI evidence: ${evidenceDir}`);
  mkdirSync(shotsDir, { recursive: true });
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

test.afterEach(async () => {
  // 零模型调用是硬条件。
  expect(modelCalls, `全程发生了 ${modelCalls} 次模型调用（应为 0）`).toBe(0);
  if (serverOutput) {
    writeFileSync(join(evidenceDir, "server-playthrough.log"), serverOutput);
  }
  for (const [name, frames] of Object.entries(frameDump)) {
    writeFileSync(
      join(evidenceDir, `frames-${name}.jsonl`),
      frames.received.map((frame) => frame).join("\n"),
    );
  }
  await stopServer();
  // 世界状态只读导出（不改库）：核对 flags/时钟/结案落账。
  if (worldId && runtimeRoot) {
    const dumped = spawn(
      pythonPath(),
      [
        "-c",
        [
          "import json, sqlite3, sys",
          "db = sqlite3.connect(f'file:{sys.argv[1]}?mode=ro', uri=True)",
          "row = db.execute('select revision, state from world_states where world_id=?', [sys.argv[2]]).fetchone()",
          "out = {'revision': row[0], 'flags': row[1] and json.loads(row[1]).get('flags'), 'case_clocks': row[1] and json.loads(row[1]).get('case_clocks'), 'game_over': row[1] and json.loads(row[1]).get('game_over'), 'case_settlements': row[1] and json.loads(row[1]).get('case_settlements'), 'world_clock': row[1] and json.loads(row[1]).get('world_clock')}",
          "print(json.dumps(out, ensure_ascii=False, indent=1))",
        ].join("\n"),
        join(runtimeRoot, "e2e.db"),
        worldId,
      ],
      { encoding: "utf-8" },
    );
    let out = "";
    dumped.stdout?.on("data", (chunk) => (out += String(chunk)));
    await new Promise<void>((wait) => dumped.on("exit", () => wait()));
    if (out.trim()) {
      writeFileSync(join(evidenceDir, "world-state-final.json"), out);
    }
  }
  // 报告始终落盘（成功或失败）。
  writeFileSync(
    join(evidenceDir, "playthrough-report.json"),
    JSON.stringify(
      {
        runId,
        worldId,
        runtimeRoot,
        modelCalls,
        stages,
        gaps,
        at: new Date().toISOString(),
      },
      null,
      1,
    ),
  );
  // 世界保留在 runtimeRoot（报告记录路径），供缺口复查；不自动清理。
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

async function shot(page: Page, label: string): Promise<void> {
  try {
    await page.screenshot({ path: join(shotsDir, `${label}.png`) });
  } catch {
    // 页面已关闭时不阻断报告
  }
}

async function shotAll(label: string): Promise<void> {
  for (const [name, page] of Object.entries(pages)) {
    await shot(page, `${label}-${name}`);
  }
}

async function stage(
  id: string,
  name: string,
  fn: () => Promise<void>,
): Promise<void> {
  const at = new Date().toISOString();
  try {
    await fn();
    stages.push({ id, name, ok: true, detail: "", at });
    await shotAll(id);
  } catch (error) {
    const detail =
      error instanceof Error
        ? String(error.message).replace(/\s+/g, " ").slice(0, 800)
        : String(error);
    stages.push({ id, name, ok: false, detail, at });
    await shotAll(`${id}-FAIL`);
    throw error;
  }
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

/** 用当前选择器（input 或 select）给主持表单字段赋值。 */
async function setKeeperField(page: Page, field: string, value: string) {
  if (field === "target_kind") return; // composite selector, not a scalar DOM field
  if (
    field === "target_id" &&
    (await page.locator('[data-field="target"] select').count()) > 0
  ) {
    await page
      .locator('[data-field="target"] select')
      .selectOption(`scene_object:${value}`);
    return;
  }
  const control = page.locator(
    `[data-field="${field}"] select, [data-field="${field}"] input:not([type="checkbox"]), [data-field="${field}"] textarea`,
  );
  const tag = await control.evaluate((node) => node.tagName);
  if (tag === "SELECT") {
    await control.selectOption(value);
  } else {
    await control.fill(value);
  }
}

/** 提交主持命令并等确定结局（受理或拒绝）；revision_conflict 走界面重发。 */
async function submitKeeperCommand(
  page: Page,
  frames: Frames,
  options: { attempts?: number } = {},
): Promise<{ accepted: boolean; lastError: string }> {
  const attempts = options.attempts ?? 3;
  for (let attempt = 0; attempt < attempts; attempt += 1) {
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
    const last = JSON.parse(errors[errors.length - 1]) as {
      payload?: { code?: string };
    };
    const code = last.payload?.code ?? "";
    if (code !== "revision_conflict") {
      return { accepted: false, lastError: errors[errors.length - 1] };
    }
    await page.waitForTimeout(500);
  }
  return { accepted: false, lastError: "revision_conflict 多次未能提交" };
}

// ---------------------------------------------------------------- 主持操作助手

async function ensureConsole(keeper: Page): Promise<void> {
  const dialog = keeper.getByRole("dialog", { name: "主持工作台" });
  if (!(await dialog.isVisible().catch(() => false))) {
    await keeper.getByTestId("btn-keeper-console").click();
    await expect(dialog).toBeVisible();
  }
}

async function checkKeeperCatalogueLayout(keeper: Page) {
  await ensureConsole(keeper);
  const catalogue = keeper.getByTestId("keeper-progress-catalogue");
  await catalogue.locator(".keeper-authored-clues > summary").click();
  await catalogue.getByLabel("搜索线索").fill("witch_trial_documents");
  const clue = catalogue.locator('[data-clue-id="witch_trial_documents"]');
  await clue.locator(":scope > summary").click();
  for (const [width, height] of [
    [1280, 900],
    [939, 900],
    [640, 900],
    [390, 844],
    [390, 360],
  ]) {
    await keeper.setViewportSize({ width, height });
    const buttons = clue.locator(".keeper-catalogue-actions button");
    for (const button of await buttons.all()) {
      await button.scrollIntoViewIfNeeded();
      const geometry = await button.evaluate((el) => {
        const r = el.getBoundingClientRect(),
          style = getComputedStyle(el);
        return {
          height: r.height,
          left: r.left,
          right: r.right,
          width: innerWidth,
          padding: parseFloat(style.paddingLeft),
          nowrap: style.whiteSpace,
          hit: el.contains(
            document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2),
          ),
        };
      });
      expect(geometry.height).toBeGreaterThanOrEqual(44);
      expect(geometry.padding).toBeGreaterThanOrEqual(10);
      expect(geometry.nowrap).toBe("nowrap");
      expect(geometry.left).toBeGreaterThanOrEqual(0);
      expect(geometry.right).toBeLessThanOrEqual(geometry.width);
      expect(geometry.hit).toBe(true);
    }
    await keeper.screenshot({
      path: join(shotsDir, `keeper-progress-${width}x${height}.png`),
    });
  }
  await keeper.setViewportSize({ width: 1440, height: 900 });
  await catalogue.getByLabel("搜索线索").fill("");
  await catalogue.locator(".keeper-authored-clues > summary").click();
}

async function closeConsole(keeper: Page): Promise<void> {
  const dialog = keeper.getByRole("dialog", { name: "主持工作台" });
  if (await dialog.isVisible().catch(() => false)) {
    await keeper.getByRole("button", { name: "关闭主持台" }).click();
    await expect(dialog).toHaveCount(0);
  }
}

/** 打开主持台 → 切到指定命令 tab → 填字段 → 提交并断言受理。 */
async function keeperCmd(
  keeper: Page,
  keeperFrames: Frames,
  kind: string,
  fields: Record<string, string>,
  bools: Record<string, boolean> = {},
): Promise<void> {
  await ensureConsole(keeper);
  await keeper.getByTestId(`keeper-cmd-${kind}`).click();
  for (const [field, value] of Object.entries(fields)) {
    await setKeeperField(keeper, field, value);
  }
  for (const [label, checked] of Object.entries(bools)) {
    const box = keeper.getByRole("checkbox", { name: new RegExp(label) });
    if (checked) await box.check();
    else await box.uncheck();
  }
  const result = await submitKeeperCommand(keeper, keeperFrames);
  expect(result.accepted, `${kind} 被拒：${result.lastError}`).toBe(true);
}

/** 主持以 NPC 身份公开叙事。 */
async function publishNpc(
  keeper: Page,
  keeperFrames: Frames,
  npcId: string,
  text: string,
): Promise<void> {
  await keeperCmd(keeper, keeperFrames, "publish_message", {
    speaker_kind: "npc",
    speaker_id: npcId,
    audience_kind: "public",
    text,
  });
}

/** 主持旁白（keeper 身份）。 */
async function publishKeeper(
  keeper: Page,
  keeperFrames: Frames,
  text: string,
): Promise<void> {
  await keeperCmd(keeper, keeperFrames, "publish_message", {
    speaker_kind: "keeper",
    audience_kind: "public",
    text,
  });
}

const visitedScenes = new Set<string>(["miskatonic_university"]);

/** 整队移动并断言两名玩家页首场景名切换；目标不可达时经枢纽场景中转。 */
async function moveParty(
  keeper: Page,
  keeperFrames: Frames,
  playerA: Page,
  playerB: Page,
  sceneId: string,
  sceneName: string,
  travelMinutes = "15",
): Promise<void> {
  const readOptions = async () => {
    await ensureConsole(keeper);
    await keeper.getByTestId("keeper-cmd-move_party").click();
    return keeper
      .locator('[data-field="destination_scene_id"] select option')
      .evaluateAll((nodes) =>
        nodes.map((node) => (node as HTMLOptionElement).value),
      );
  };
  let options = await readOptions();
  if (!options.includes(sceneId)) {
    expect(
      visitedScenes.has(sceneId),
      "已访问场景必须直接可选，不能通过枢纽掩盖G4",
    ).toBe(false);
    // 先回枢纽（初始场景出口覆盖全图）。
    const sceneSelect = keeper.locator(
      '[data-field="destination_scene_id"] select',
    );
    await sceneSelect.selectOption("miskatonic_university");
    await setKeeperField(keeper, "travel_minutes", travelMinutes);
    const hub = await submitKeeperCommand(keeper, keeperFrames);
    expect(hub.accepted, `回枢纽被拒：${hub.lastError}`).toBe(true);
    await closeConsole(keeper);
    for (const page of [playerA, playerB]) {
      await expect(page.locator(".header-scene-name")).toHaveText(
        "密斯卡托尼克大学",
        { timeout: 30_000 },
      );
    }
    options = await readOptions();
  }
  expect(
    options,
    `目的地候选缺少 ${sceneId}（当前候选：${options.join(", ") || "空"}）`,
  ).toContain(sceneId);
  const sceneSelect = keeper.locator(
    '[data-field="destination_scene_id"] select',
  );
  await sceneSelect.selectOption(sceneId);
  await setKeeperField(keeper, "travel_minutes", travelMinutes);
  const moved = await submitKeeperCommand(keeper, keeperFrames);
  expect(moved.accepted, `move_party ${sceneId} 被拒：${moved.lastError}`).toBe(
    true,
  );
  visitedScenes.add(sceneId);
  await closeConsole(keeper);
  for (const page of [playerA, playerB]) {
    await expect(page.locator(".header-scene-name")).toHaveText(sceneName, {
      timeout: 30_000,
    });
  }
}

/**
 * 【诊断探针 · 非正常游玩路径】主持本人用自己的真实凭证在原始 WS 上发一条
 * 命令，仅用于把「UI 候选缺失」与「后端也不支持」区分开；每次使用都在报告
 * 里单独标注，主线操作一律走 UI。
 */
async function keeperRawCommand(
  context: BrowserContext,
  keeperFrames: Frames,
  kind: string,
  payload: Record<string, unknown>,
): Promise<{ ok: boolean; detail: string }> {
  const cookies = await context.cookies();
  const cookieHeader = cookies
    .map((cookie) => `${cookie.name}=${cookie.value}`)
    .join("; ");
  const probeFrames: string[] = [];
  const ws = new WsClient(
    `${baseUrl.replace("http", "ws")}/ws/room?world_id=${worldId}`,
    { headers: { Origin: baseUrl, Cookie: cookieHeader } },
  );
  ws.on("message", (data: unknown) => probeFrames.push(String(data)));
  await new Promise<void>((resolveOpen, rejectOpen) => {
    ws.once("open", () => resolveOpen());
    ws.once("error", rejectOpen);
  });
  try {
    await expect
      .poll(() => framesOf(probeFrames, "session_snapshot").length, {
        timeout: 15_000,
      })
      .toBeGreaterThan(0);
    const revision = JSON.parse(
      framesOf(probeFrames, "session_snapshot").at(-1)!,
    ).revision as number;
    const settled = () =>
      framesOf(probeFrames, "request_error").length +
      framesOf(probeFrames, "action_status").length +
      framesOf(probeFrames, "action_ack").length;
    const before = settled();
    ws.send(
      JSON.stringify({
        type: "command_request",
        protocol_version: 1,
        command_id: `probe-${runId}-${kind}`,
        world_id: worldId,
        expected_revision: revision,
        kind,
        payload,
      }),
    );
    await expect.poll(settled, { timeout: 15_000 }).toBeGreaterThan(before);
    const errors = framesOf(probeFrames, "request_error");
    if (errors.length)
      return { ok: false, detail: errors[errors.length - 1].slice(0, 300) };
    return { ok: true, detail: "accepted" };
  } finally {
    ws.close();
  }
}

/**
 * 定向发放线索；候选缺失（模组 catalog 里未被发现的线索）时记录缺口并返回
 * false，由调用方改用叙事+裁定继续——不假装线索已发。
 */
async function tryGrantClue(
  keeper: Page,
  keeperFrames: Frames,
  clueId: string,
  recipients: string[],
  basis: string,
  discovery?: {
    rule: number;
    actor: string;
    check?: string;
    acquire?: boolean;
  },
): Promise<boolean> {
  await ensureConsole(keeper);
  await keeper.getByTestId("keeper-cmd-grant_clue").click();
  const clueSelect = keeper.locator('[data-field="clue_id"] select');
  const option = clueSelect.locator(`option[value="${clueId}"]`);
  await expect(
    option,
    `必须能通过UI选择作者线索 ${clueId}，不允许原始帧兜底`,
  ).toHaveCount(1);
  await setKeeperField(
    keeper,
    "discovery_rule_index",
    discovery ? String(discovery.rule) : "",
  );
  await setKeeperField(
    keeper,
    "discovery_investigator_id",
    discovery?.actor || "",
  );
  await setKeeperField(keeper, "check_request_id", discovery?.check || "");
  await keeper
    .getByRole("checkbox", { name: /确认取得作者声明的实物/ })
    .setChecked(discovery?.acquire === true);
  await clueSelect.selectOption(clueId);
  await keeper
    .locator('[data-field="recipient_investigator_ids"] input[type="text"]')
    .fill(recipients.join(","));
  await keeper.locator('[data-field="basis"] input').fill(basis);
  const result = await submitKeeperCommand(keeper, keeperFrames);
  expect(
    result.accepted,
    `grant_clue ${clueId} 被拒：${result.lastError}`,
  ).toBe(true);
  return true;
}

/** 主持裁定 flag=true（record_ruling，附事实依据）。 */
async function ruleFlag(
  keeper: Page,
  keeperFrames: Frames,
  flagId: string,
  basis: string,
): Promise<void> {
  await ensureConsole(keeper);
  await keeper.getByTestId("keeper-cmd-record_ruling").click();
  await setKeeperField(keeper, "flag_id", flagId);
  await keeper.getByRole("checkbox", { name: /裁定后的状态/ }).check();
  await setKeeperField(keeper, "basis", basis);
  const result = await submitKeeperCommand(keeper, keeperFrames);
  expect(
    result.accepted,
    `record_ruling ${flagId} 被拒：${result.lastError}`,
  ).toBe(true);
}

/** 用资料库 UI 向指定调查员展示图片（按标题选素材）。 */
async function presentPhoto(
  keeper: Page,
  titleLike: string,
  recipientIds: string[],
): Promise<void> {
  await ensureConsole(keeper);
  const library = keeper.getByTestId("keeper-library");
  const toggle = library.getByRole("button", { name: /打开资料库|收起资料库/ });
  if ((await toggle.textContent())?.includes("打开")) await toggle.click();
  await library.getByRole("button", { name: "图片", exact: true }).click();
  await library
    .locator(".keeper-library-entry")
    .filter({ hasText: titleLike })
    .first()
    .click();
  const boxes = library.getByRole("checkbox");
  const values = await boxes.evaluateAll((nodes) =>
    nodes.map((node) => (node as HTMLInputElement).value),
  );
  // 先清空既有勾选（组件会记住上一次分发的接收者），再只勾本次目标。
  for (let index = 0; index < values.length; index += 1) {
    if (await boxes.nth(index).isChecked()) await boxes.nth(index).uncheck();
  }
  for (const id of recipientIds) {
    const index = values.indexOf(id);
    expect(index, `资料库接收者候选缺少 ${id}`).toBeGreaterThanOrEqual(0);
    await boxes.nth(index).check();
  }
  await library.getByRole("button", { name: "展示给所选调查员" }).click();
  await expect(library.getByRole("status")).toContainText(
    "服务端已确认图片分发",
    { timeout: 30_000 },
  );
}

/** 玩家确认收到图片后关掉浮层，避免遮挡后续操作。 */
async function ackHandout(player: Page): Promise<void> {
  await expect(player.locator("#handout-container img").first()).toBeVisible({
    timeout: 30_000,
  });
  await player.keyboard.press("Escape");
  await player.waitForTimeout(400);
}

/** 等玩家请求终态；revision 冲突走界面「用最新版本重新提交」（换新 ID，跟随之）。 */
async function settlePlayerRequest(
  page: Page,
  frames: Frames,
  requestId: string,
): Promise<string> {
  const waitOutcome = async (id: string): Promise<string> => {
    for (let tick = 0; tick < 75; tick += 1) {
      const errors = framesOf(frames.received, "request_error").filter((raw) =>
        raw.includes(id),
      );
      if (errors.some((raw) => raw.includes("revision_conflict")))
        return "conflict";
      if (errors.length) return "error";
      if (
        framesOf(frames.received, "action_ack").some((raw) => raw.includes(id))
      )
        return "ack";
      await page.waitForTimeout(200);
    }
    return "timeout";
  };
  let current = requestId;
  for (let attempt = 0; attempt < 3; attempt += 1) {
    const outcome = await waitOutcome(current);
    if (outcome === "ack") return current;
    if (outcome !== "conflict") {
      throw new Error(`玩家请求未被受理（${outcome}）：${current}`);
    }
    const sentBefore = framesOf(frames.sent, "action_request").length;
    const resubmit = page
      .getByRole("button", { name: "用最新版本重新提交" })
      .first();
    await expect(resubmit).toBeVisible({ timeout: 15_000 });
    await resubmit.click();
    await expect
      .poll(() => framesOf(frames.sent, "action_request").length)
      .toBeGreaterThan(sentBefore);
    current = JSON.parse(framesOf(frames.sent, "action_request").at(-1)!)
      .request_id as string;
  }
  throw new Error(`玩家请求多次 revision_conflict 未恢复：${requestId}`);
}

/** 玩家自由行动输入；等受理终态；返回最终 request_id。 */
async function freeform(
  page: Page,
  frames: Frames,
  text: string,
): Promise<string> {
  const before = framesOf(frames.sent, "action_request").length;
  await page.locator("#user-input").fill(text);
  await page.locator("#btn-send").click();
  await expect
    .poll(() => framesOf(frames.sent, "action_request").length)
    .toBeGreaterThan(before);
  const requestId = JSON.parse(framesOf(frames.sent, "action_request").at(-1)!)
    .request_id as string;
  return settlePlayerRequest(page, frames, requestId);
}

/** 主持在待办卡上收尾玩家请求。 */
async function resolvePending(
  keeper: Page,
  keeperFrames: Frames,
  requestId: string,
  note: string,
): Promise<void> {
  await ensureConsole(keeper);
  const card = keeper
    .getByTestId("keeper-pending-request")
    .filter({ hasText: requestId });
  await expect(card).toBeVisible();
  await card.getByRole("button", { name: "准备裁定", exact: true }).click();
  await expect(keeper.getByLabel("玩家请求", { exact: true })).toHaveValue(
    requestId,
  );
  await setKeeperField(keeper, "resolution", "completed");
  await setKeeperField(keeper, "outcome", "success");
  await setKeeperField(keeper, "note", note);
  const result = await submitKeeperCommand(keeper, keeperFrames);
  expect(result.accepted, result.lastError).toBe(true);
  await expect(card).toHaveCount(0);
}

/** 主持请求检定 → 指定玩家掷骰 → 返回 outcome（success/failure）。 */
async function requestCheckAndRoll(
  keeper: Page,
  keeperFrames: Frames,
  player: Page,
  playerFrames: Frames,
  investigatorId: string,
  skill: string,
  attempt: string,
  clueId?: string,
): Promise<string> {
  await keeperCmd(keeper, keeperFrames, "request_check", {
    investigator_id: investigatorId,
    skill,
    difficulty: "regular",
    attempt,
    visibility: "public",
    ...(clueId ? { target_kind: "scene_object", target_id: clueId } : {}),
  });
  await closeConsole(keeper);
  const card = player
    .locator('.check-request-card[data-status="pending"]')
    .last();
  await expect(card).toBeVisible({ timeout: 30_000 });
  const resolvedBefore = framesOf(
    playerFrames.received,
    "check_resolved",
  ).length;
  await card.getByRole("button", { name: "掷骰", exact: true }).click();
  await expect
    .poll(() => framesOf(playerFrames.received, "check_resolved").length, {
      timeout: 30_000,
    })
    .toBeGreaterThan(resolvedBefore);
  const payload = lastPayload(playerFrames.received, "check_resolved");
  return String(payload?.outcome ?? "unknown");
}

/** 主持普通骰（公开）→ 返回点数总额；随后按结果调整 SAN/HP。 */
async function keeperPublicRoll(
  keeper: Page,
  keeperFrames: Frames,
  spec: string,
): Promise<number> {
  await closeConsole(keeper);
  await keeper.getByTestId("btn-keeper-dice").click();
  const dialog = keeper.getByRole("dialog", {
    name: "主持普通骰",
    exact: true,
  });
  await dialog.getByRole("textbox", { name: "骰式" }).fill(spec);
  await dialog
    .getByRole("combobox", { name: "接收范围" })
    .selectOption("public");
  const before = framesOf(keeperFrames.received, "keeper_roll_resolved").length;
  await keeper.getByTestId("keeper-dice-submit").click();
  await expect
    .poll(
      () => framesOf(keeperFrames.received, "keeper_roll_resolved").length,
      {
        timeout: 20_000,
      },
    )
    .toBeGreaterThan(before);
  const payload = lastPayload(keeperFrames.received, "keeper_roll_resolved");
  if (await dialog.isVisible().catch(() => false)) {
    const closeButton = dialog.getByRole("button", {
      name: "关闭",
      exact: true,
    });
    if (await closeButton.count()) {
      await closeButton.click();
    } else {
      await keeper.keyboard.press("Escape");
    }
  }
  return Number(payload?.total ?? 0);
}

async function adjustStat(
  keeper: Page,
  keeperFrames: Frames,
  investigatorId: string,
  field: string,
  delta: string,
  reason: string,
): Promise<void> {
  await keeperCmd(keeper, keeperFrames, "adjust_stat", {
    investigator_id: investigatorId,
    field,
    delta,
    reason,
  });
}

// ---------------------------------------------------------------- 主测试

test("猩红文档全真主线：人类主持+两玩家零模型跑完 truth_and_seal", async ({
  browser,
}) => {
  test.setTimeout(900_000);
  const keeperContext: BrowserContext = await browser.newContext();
  const playerAContext: BrowserContext = await browser.newContext();
  const playerBContext: BrowserContext = await browser.newContext();
  const keeper = await keeperContext.newPage();
  const playerA = await playerAContext.newPage();
  const playerB = await playerBContext.newPage();
  for (const page of [keeper, playerA, playerB]) page.setDefaultTimeout(30_000);
  pages.keeper = keeper;
  pages.playerA = playerA;
  pages.playerB = playerB;
  const keeperFrames = collectFrames(keeper);
  const playerAFrames = collectFrames(playerA);
  const playerBFrames = collectFrames(playerB);
  frameDump.keeper = keeperFrames;
  frameDump.playerA = playerAFrames;
  frameDump.playerB = playerBFrames;
  const keeperName = `keeper${runId}`;
  const playerAName = `alice${runId}`;
  const playerBName = `bob${runId}`;
  const roomName = `猩红全真${runId}`;

  try {
    await bootServer();

    // ==================== P0 建房 → 授主持 → 选角 → 开局 ====================
    await stage("P0", "建房/邀请/选角/开局", async () => {
      await register(keeper, keeperName);
      await keeper.getByLabel("房间名称").fill(roomName);
      const moduleSelect = keeper.getByLabel("选择模组");
      await expect(
        moduleSelect.locator('option[value="猩红文档"]'),
        "模组候选里没有猩红文档",
      ).toHaveCount(1);
      await moduleSelect.selectOption(MODULE);
      await keeper.getByRole("radio", { name: "人类主持" }).click();
      await keeper.getByRole("button", { name: "创建房间" }).click();
      await expect(
        keeper.getByRole("heading", { name: roomName }),
      ).toBeVisible();

      await keeper.getByLabel("有效期（小时）").fill("72");
      await keeper.getByLabel("使用次数").fill("5");
      await keeper.getByRole("button", { name: "生成邀请码" }).click();
      const invite = (
        await keeper.locator(".invite-token").textContent()
      )?.trim();
      expect(invite).toBeTruthy();

      for (const [page, name] of [
        [playerA, playerAName],
        [playerB, playerBName],
      ] as const) {
        await register(page, name);
        await page
          .getByRole("textbox", { name: "邀请码", exact: true })
          .fill(invite!);
        await page.getByRole("button", { name: "加入房间" }).click();
        await expect(
          page.getByRole("heading", { name: roomName }),
        ).toBeVisible();
        await page.getByRole("button", { name: "选择" }).first().click();
        await expect(page.getByRole("button", { name: "释放" })).toBeVisible();
      }

      // 调查员标识从服务端 REST 投影取（不猜候选顺序）。
      const claimOf = async (page: Page) =>
        page.evaluate(async () => {
          const wid = localStorage.getItem("trpg-online-world-id") ?? "";
          const me = await (
            await fetch("/api/auth/me", { credentials: "include" })
          ).json();
          const info = await (
            await fetch(`/api/worlds/${encodeURIComponent(wid)}/members`, {
              credentials: "include",
            })
          ).json();
          const rows = (info.members ?? []) as {
            user_id: string;
            investigator?: { character_key?: string } | null;
          }[];
          return {
            wid,
            key:
              rows.find((row) => row.user_id === me.id)?.investigator
                ?.character_key ?? "",
          };
        });
      const a = await claimOf(playerA);
      const b = await claimOf(playerB);
      worldId = a.wid;
      invA = a.key;
      invB = b.key;
      expect(invA, "甲没有认领到调查员").not.toBe("");
      expect(invB, "乙没有认领到调查员").not.toBe("");
      expect(invA).not.toBe(invB);

      for (const page of [keeper, playerA, playerB]) {
        await expect(page.locator(".member-row").first()).toBeVisible();
        await page.getByRole("button", { name: "准备" }).click();
      }
      await expect
        .poll(
          () => {
            const states = framesOf(keeperFrames.received, "room_state");
            const latest = JSON.parse(states[states.length - 1] ?? "{}") as {
              ready_user_ids?: string[];
            };
            return latest.ready_user_ids?.length ?? 0;
          },
          { timeout: 30_000, message: "三人准备状态没有生效" },
        )
        .toBe(3);

      const start = keeper.getByRole("button", { name: "开始游戏" });
      await expect(start, "全员已准备后开局按钮仍不可用").toBeEnabled();
      await start.click();
      for (const page of [playerA, playerB]) {
        await expect(page.locator("#user-input")).toBeEnabled({
          timeout: 90_000,
        });
        await expect(page.getByTestId("structured-tool-row")).toBeVisible();
      }
      // 开局状态：初始场景 + 两把黄铜钥匙 + 三条开局线索共享可见。
      for (const [page, frames] of [
        [playerA, playerAFrames],
        [playerB, playerBFrames],
      ] as const) {
        await expect(page.locator(".header-scene-name")).toHaveText(
          "密斯卡托尼克大学",
        );
        const snapshot = JSON.parse(
          framesOf(frames.received, "session_snapshot").at(-1)!,
        ).payload;
        const labels = (snapshot.items ?? []).map(
          (item: { label: string }) => item.label,
        );
        expect(labels).toContain("莱特办公室的黄铜钥匙");
        expect(labels).toContain("莱特小屋的黄铜钥匙");
        const clueTexts = JSON.stringify(snapshot.clues ?? []);
        expect(clueTexts).toContain("心力衰竭");
        expect(clueTexts).toContain("下落不明");
      }
    });

    // ==================== P1 主持读取模组资料 ====================
    await stage(
      "P1",
      "主持读取模组手册（含 NPC 秘密），玩家不可见",
      async () => {
        await ensureConsole(keeper);
        const library = keeper.getByTestId("keeper-library");
        const toggle = library.getByRole("button", {
          name: /打开资料库|收起资料库/,
        });
        if ((await toggle.textContent())?.includes("打开"))
          await toggle.click();
        await library
          .getByRole("button", { name: "模组手册", exact: true })
          .click();
        await expect(library.locator(".keeper-library-text")).toContainText(
          "# NPC",
        );
        await expect(library.locator(".keeper-library-text")).toContainText(
          "费德曼",
        );
        // 主持据此选定真凶叙事（安东尼·弗林德斯），记为仅主持可见的事实。
        await keeperCmd(keeper, keeperFrames, "record_fact", {
          audience_kind: "keeper",
          text: "主持选定：本案真凶为安东尼·弗林德斯（模组允许的可选真凶），后续按其秘密叙事。",
        });
        // 玩家偷读主持手册（REST 探针）：必须 404。
        const status = await playerB.evaluate(
          async (wid) =>
            (
              await fetch(
                `/api/worlds/${encodeURIComponent(wid)}/keeper-guide`,
                {
                  credentials: "include",
                },
              )
            ).status,
          worldId,
        );
        expect(status, "玩家读到了主持手册").toBe(404);
        await closeConsole(keeper);
      },
    );

    // ==================== B0 法伦办公室 ====================
    await stage(
      "B0",
      "开局：法伦办公室简报 + 联系人线索 + 法伦照片",
      async () => {
        await publishNpc(
          keeper,
          keeperFrames,
          "bryce_fallon",
          "莱特教授死在办公室里，校方希望低调处理。这两把钥匙交给你们——办公室和小屋的。参与文档评估的人，我可以列给你们。",
        );
        for (const page of [playerA, playerB]) {
          await expect(page.locator("body")).toContainText(
            "莱特教授死在办公室里",
          );
        }
        const granted = await tryGrantClue(
          keeper,
          keeperFrames,
          "fallon_document_contacts",
          [invA, invB],
          "玩家追问法伦：还有谁参与文档评估。",
        );
        expect(granted).toBe(true);
        await checkKeeperCatalogueLayout(keeper);
        for (const frames of [playerAFrames, playerBFrames]) {
          await expect
            .poll(() => framesOf(frames.received, "clue_granted").length)
            .toBeGreaterThan(0);
        }
        await presentPhoto(keeper, "布莱斯·法伦", [invA, invB]);
        await ackHandout(playerA);
        await ackHandout(playerB);
      },
    );

    // ==================== B1 停尸房 ====================
    await stage(
      "B1",
      "停尸房验尸：场景切换/尸体照片/验尸线索/定向分发隔离",
      async () => {
        await moveParty(
          keeper,
          keeperFrames,
          playerA,
          playerB,
          "miskatonic_medical",
          "密斯卡托尼克大学医学院",
        );
        await publishNpc(
          keeper,
          keeperFrames,
          "john_whitcroft",
          "法伦打过电话了。跟我来吧——遗体在下面。死因栏我写的是心力衰竭，但有些事情我对不上。",
        );
        await presentPhoto(keeper, "约翰·惠特克罗夫特", [invA, invB]);
        await ackHandout(playerA);
        await ackHandout(playerB);
        // 尸体照片先只发给甲：验证按对象分发（乙连帧都不该收到）。
        await presentPhoto(keeper, "莱特教授尸体", [invA]);
        await ackHandout(playerA);
        await playerA.waitForTimeout(1500);
        expect(
          framesOf(playerBFrames.received, "handout_presented").filter((raw) =>
            raw.includes("wright_body"),
          ),
          "尸体照片未经定向就发到了乙",
        ).toHaveLength(0);
        // 验尸：甲 freeform → 主持补发照片给乙 + 发放线索 + 裁定 + SAN。
        const reqId = await freeform(
          playerA,
          playerAFrames,
          "我戴上手套，仔细检查莱特教授的遗体：皮肤、指甲、脖颈与任何非自然痕迹。",
        );
        await presentPhoto(keeper, "莱特教授尸体", [invB]);
        await ackHandout(playerB);
        await tryGrantClue(
          keeper,
          keeperFrames,
          "wright_body_evidence",
          [invA, invB],
          "甲验尸成功：发现真实死因征象。",
          { rule: 0, actor: invA },
        );
        // 目睹遗体：模组 san_triggers 0/1D4，主持公开骰后按结果扣 SAN。
        const loss = await keeperPublicRoll(keeper, keeperFrames, "1d4");
        // 0/1D4：模组描述为成功 0 / 失败 1D4；这里简化由主持骰 1d4 并按 0 起解释。
        if (loss > 0) {
          await adjustStat(
            keeper,
            keeperFrames,
            invA,
            "san",
            `-${loss}`,
            `目睹莱特遗体（停尸房），公开骰 1D4=${loss}。`,
          );
        }
        await resolvePending(
          keeper,
          keeperFrames,
          reqId,
          "验尸完成：发现并非单纯心力衰竭。",
        );
        await shot(playerA, "B1-playerA-san");
      },
    );

    // ==================== B2 两个研究生 ====================
    await stage(
      "B2",
      "研究生线：考特（心理学检定）+ 洛奇 + 弗林德斯",
      async () => {
        await moveParty(
          keeper,
          keeperFrames,
          playerA,
          playerB,
          "miskatonic_history",
          "密斯卡托尼克大学历史系研究生自习室",
        );
        await publishNpc(
          keeper,
          keeperFrames,
          "emilia_court",
          "莱特教授？他最近总往校外跑……我只负责整理卡片目录，评估的事你去问洛奇教授。",
        );
        await presentPhoto(keeper, "艾米莉亚·考特", [invA, invB]);
        await ackHandout(playerA);
        await ackHandout(playerB);
        const outcome = await requestCheckAndRoll(
          keeper,
          keeperFrames,
          playerB,
          playerBFrames,
          invB,
          "psychology",
          "观察考特谈及莱特时的神态",
        );
        expect(["success", "failure"]).toContain(outcome);
        // 心理学结果只影响叙事信息，不设模组 flag。
        await moveParty(
          keeper,
          keeperFrames,
          playerA,
          playerB,
          "miskatonic_lodge_office",
          "哈兰德·洛奇的历史系办公室",
        );
        await publishNpc(
          keeper,
          keeperFrames,
          "harland_lodge",
          "评估是我、考特和弗林德斯协助的。文档清单？都在莱特那里……他死在办公室，你可别乱翻别人的东西。",
        );
        await presentPhoto(keeper, "哈兰德·洛奇", [invA, invB]);
        await ackHandout(playerA);
        await ackHandout(playerB);
        await moveParty(
          keeper,
          keeperFrames,
          playerA,
          playerB,
          "miskatonic_student_commons",
          "密斯卡托尼克大学学生公共休息区",
        );
        await publishNpc(
          keeper,
          keeperFrames,
          "anthony_flinders",
          "我？我只是帮忙搬过箱子。莱特教授死的那晚……我在宿舍。谁都别想证明别的。",
        );
        await presentPhoto(keeper, "安东尼·弗林德斯", [invA, invB]);
        await ackHandout(playerA);
        await ackHandout(playerB);
      },
    );

    // ==================== B3a 莱特办公室 ====================
    await stage(
      "B3a",
      "莱特办公室：暗格日记（真实检定，失败换人/耗时重试）",
      async () => {
        await moveParty(
          keeper,
          keeperFrames,
          playerA,
          playerB,
          "wright_office",
          "莱特的办公室",
        );
        const reqId = await freeform(
          playerA,
          playerAFrames,
          "我用黄铜钥匙开门，逐一检查书桌的抽屉与夹层，找暗格。",
        );
        // 检定：spot_hidden（模组 requires_success）。失败则换人/耗时重试，
        // 最多 6 次真实掷骰（全灭概率 ~0.6%；真全灭则如实记 ⑤ 骰运）。
        let found = false;
        let foundActor = "";
        let foundCheck = "";
        const attempts: string[] = [];
        for (let round = 0; round < 6 && !found; round += 1) {
          const page = round % 2 === 0 ? playerA : playerB;
          const frames = round % 2 === 0 ? playerAFrames : playerBFrames;
          const inv = round % 2 === 0 ? invA : invB;
          const outcome = await requestCheckAndRoll(
            keeper,
            keeperFrames,
            page,
            frames,
            inv,
            "spot_hidden",
            round === 0
              ? "搜查书桌暗格"
              : `继续彻底搜查书桌（第 ${round + 1} 次）`,
            "wright_private_diary",
          );
          attempts.push(`${inv}:${outcome}`);
          found = outcome === "success";
          if (found) {
            foundActor = inv;
            foundCheck = String(
              lastPayload(frames.received, "check_resolved")
                ?.check_request_id || "",
            );
          }
        }
        if (!found) {
          recordGap(
            "⑤",
            "暗格检定连续失败",
            `spot_hidden 六轮结果 ${attempts.join(", ")}；按任务约定不补状态，主线日记线索本轮缺席。`,
          );
        }
        expect(found, `暗格检定六轮全失败（${attempts.join(", ")}）`).toBe(
          true,
        );
        await tryGrantClue(
          keeper,
          keeperFrames,
          "wright_private_diary",
          [invA, invB],
          `检定成功（${attempts.join(" → ")}）：在暗格找到莱特的私人日记。`,
          { rule: 0, actor: foundActor, check: foundCheck, acquire: true },
        );
        await ruleFlag(
          keeper,
          keeperFrames,
          "office_searched",
          "调查员搜完莱特办公室，找到暗格日记。",
        );
        // Physical diary is acquired exactly once and visible in the real keeper UI.
        await ensureConsole(keeper);
        await keeper.getByTestId("keeper-cmd-transfer_item").click();
        await expect(
          keeper
            .locator('[data-field="item_id"] select option')
            .filter({ hasText: "莱特的私人日记" }),
        ).toHaveCount(1);
        await resolvePending(
          keeper,
          keeperFrames,
          reqId,
          "暗格日记到手：莱特死前在追查复制件与亨特。",
        );
      },
    );

    // ==================== B3b 莱特小屋 ====================
    await stage(
      "B3b",
      "莱特小屋：搜索 + cottage_searched（模组此处无强制检定）",
      async () => {
        await moveParty(
          keeper,
          keeperFrames,
          playerA,
          playerB,
          "wright_cottage",
          "莱特的小屋",
        );
        const reqId = await freeform(
          playerB,
          playerBFrames,
          "我搜查小屋：壁炉、书架、信件堆，找任何与疗养院或复制件有关的痕迹。",
        );
        // 模组对小屋没有任何 requires_success 的检定门槛（discovery_rules 无此
        // 场景条目），主持按模组直接落账，不人为加骰。
        await ruleFlag(
          keeper,
          keeperFrames,
          "cottage_searched",
          "小屋搜查完成：发现疗养院来信，指向亨特。",
        );
        await publishKeeper(
          keeper,
          keeperFrames,
          "信件堆里有一封阿卡姆疗养院的回函：院方确认收治过一名叫塞西尔·亨特的前学生，他反复提到『莱特教授的文档』。",
        );
        await resolvePending(
          keeper,
          keeperFrames,
          reqId,
          "小屋搜完：疗养院来信指向亨特。",
        );
      },
    );

    // ==================== B4 精神病院 ====================
    await stage("B4", "阿卡姆疗养院：亨特 + 复制件线索", async () => {
      await moveParty(
        keeper,
        keeperFrames,
        playerA,
        playerB,
        "arkham_sanatorium",
        "阿卡姆疗养院",
      );
      await publishNpc(
        keeper,
        keeperFrames,
        "cecil_hunter",
        "（束缚衣里的年轻人抬起头）莱特死了？那东西……那份文档里的东西会出来。我抄过几页，都在枕头底下，拿去，别再来找我。",
      );
      await presentPhoto(keeper, "塞西尔·亨特", [invA, invB]);
      await ackHandout(playerA);
      await ackHandout(playerB);
      const reqId = await freeform(
        playerA,
        playerAFrames,
        "我接过亨特的复制件，当场逐页查看并收好。",
      );
      await tryGrantClue(
        keeper,
        keeperFrames,
        "hunter_copy",
        [invA, invB],
        "亨特当面交出审判文档复制件（examine/take 均无需检定）。",
      );
      await ruleFlag(
        keeper,
        keeperFrames,
        "sanatorium_visited",
        "调查员在疗养院见到亨特并取得复制件。",
      );
      const loss = await keeperPublicRoll(keeper, keeperFrames, "1d3");
      if (loss > 0) {
        for (const inv of [invA, invB]) {
          await adjustStat(
            keeper,
            keeperFrames,
            inv,
            "san",
            `-${loss}`,
            `阅读蠕动的墨迹复制件，公开骰 1D3=${loss}。`,
          );
        }
      }
      await resolvePending(
        keeper,
        keeperFrames,
        reqId,
        "复制件到手：墨迹似乎在缓缓蠕动。",
      );
    });

    // ==================== X1 越权探针（协议级，单独标注） ====================
    await stage(
      "X1",
      "越权探针：玩家代发主持命令/代掷他人检定均被拒",
      async () => {
        // 起一条真实的待掷检定（甲的），供乙代掷探针使用。
        await keeperCmd(keeper, keeperFrames, "request_check", {
          investigator_id: invA,
          skill: "library_use",
          difficulty: "regular",
          attempt: "向院方登记查阅许可",
          visibility: "public",
        });
        await closeConsole(keeper);
        const requested = lastPayload(keeperFrames.received, "check_requested");
        const checkId = String(requested?.check_request_id ?? "");
        expect(checkId).not.toBe("");

        // 乙的原始 WS（同一账号真实凭证；仅用于越权探针）。
        const cookies = await playerBContext.cookies();
        const cookieHeader = cookies
          .map((cookie) => `${cookie.name}=${cookie.value}`)
          .join("; ");
        const probeFrames: string[] = [];
        const ws = new WsClient(
          `${baseUrl.replace("http", "ws")}/ws/room?world_id=${worldId}`,
          {
            headers: { Origin: baseUrl, Cookie: cookieHeader },
          },
        );
        ws.on("message", (data: unknown) => probeFrames.push(String(data)));
        await new Promise<void>((resolveOpen, rejectOpen) => {
          ws.once("open", () => resolveOpen());
          ws.once("error", rejectOpen);
        });
        // 等连接后的首个快照，拿当前 revision。
        await expect
          .poll(() => framesOf(probeFrames, "session_snapshot").length, {
            timeout: 15_000,
          })
          .toBeGreaterThan(0);
        const revision = JSON.parse(
          framesOf(probeFrames, "session_snapshot").at(-1)!,
        ).revision as number;

        const sendProbe = async (frame: Record<string, unknown>) => {
          const before = framesOf(probeFrames, "request_error").length;
          ws.send(JSON.stringify(frame));
          await expect
            .poll(() => framesOf(probeFrames, "request_error").length, {
              timeout: 15_000,
            })
            .toBeGreaterThan(before);
          return JSON.parse(framesOf(probeFrames, "request_error").at(-1)!)
            .payload;
        };

        // 探针 1：乙代发主持命令 move_party。
        const error1 = await sendProbe({
          type: "command_request",
          protocol_version: 1,
          command_id: `probe-${runId}-1`,
          world_id: worldId,
          expected_revision: revision,
          kind: "move_party",
          payload: { destination_scene_id: "sheb_tavern" },
        });
        expect(
          ["keeper_required", "not_authorized"],
          `玩家代发主持命令未被拒：${JSON.stringify(error1)}`,
        ).toContain(error1?.code);

        // 探针 2：乙代掷甲的检定。
        const error2 = await sendProbe({
          type: "check_response",
          protocol_version: 1,
          request_id: `probe-${runId}-2`,
          world_id: worldId,
          check_request_id: checkId,
          decision: "roll",
        });
        expect(
          [
            "keeper_required",
            "not_authorized",
            "invalid_action",
            "not_investigator_controller",
          ],
          `玩家代掷他人检定未被拒：${JSON.stringify(error2)}`,
        ).toContain(error2?.code);
        ws.close();

        // 世界未被探针改变：场景仍是疗养院，检定仍待甲响应。
        await expect(playerA.locator(".header-scene-name")).toHaveText(
          "阿卡姆疗养院",
        );
        const card = playerA
          .locator('.check-request-card[data-status="pending"]')
          .last();
        await expect(card).toBeVisible();
        await card.getByRole("button", { name: "掷骰", exact: true }).click();
        await expect
          .poll(
            () =>
              framesOf(playerAFrames.received, "check_resolved").filter((raw) =>
                raw.includes(checkId),
              ).length,
            { timeout: 30_000 },
          )
          .toBe(1);
      },
    );

    // ==================== B5 买家分别调查 ====================
    await stage(
      "B5",
      "买家线：希布酒馆（露西+黑帮）与霍布豪斯宅邸",
      async () => {
        await moveParty(
          keeper,
          keeperFrames,
          playerA,
          playerB,
          "sheb_tavern",
          "希布酒馆",
        );
        await publishNpc(
          keeper,
          keeperFrames,
          "lucy_stone",
          "查尔斯？他常来……最后一次留了张名片——『轻率琐事』古董店的维克先生。别提是我说的，角落里那两个人一直在盯着我。",
        );
        await presentPhoto(keeper, "露西·斯通", [invA, invB]);
        await ackHandout(playerA);
        await ackHandout(playerB);
        await presentPhoto(keeper, "奥克斯与肖纳西", [invA, invB]);
        await ackHandout(playerA);
        await ackHandout(playerB);
        const outcome = await requestCheckAndRoll(
          keeper,
          keeperFrames,
          playerB,
          playerBFrames,
          invB,
          "fast_talk",
          "稳住黑帮打手，避免当场冲突",
        );
        expect(["success", "failure"]).toContain(outcome);
        await ruleFlag(
          keeper,
          keeperFrames,
          "double_life_exposed",
          "露西证实莱特与古董店维克来往密切（莱特的双重生活曝光）。",
        );
        await ruleFlag(
          keeper,
          keeperFrames,
          "gangsters_dealt_with",
          `黑帮打手被周旋离开（fast_talk=${outcome}），未发生暴力冲突。`,
        );
        await moveParty(
          keeper,
          keeperFrames,
          playerA,
          playerB,
          "hobhouse_mansion",
          "霍布豪斯宅邸",
        );
        await publishKeeper(
          keeper,
          keeperFrames,
          "柯布家族的代表证实了捐赠与评估的来龙去脉，并委婉地催促：家族名誉不容有失，请尽快给个交代。",
        );
        await ruleFlag(
          keeper,
          keeperFrames,
          "hobhouse_explored",
          "调查员拜访霍布豪斯宅邸，核实了捐赠背景。",
        );
      },
    );

    // ==================== X2 玩家主动 UI 操作：出示/使用/移动请求 ====================
    await stage(
      "X2",
      "玩家主动操作：出示线索/使用物品/申请移动（均经主持裁决）",
      async () => {
        // —— 甲出示开局线索（口头描述给宅邸代表）——
        if (
          (
            await playerA.locator("#char-panel").getAttribute("class")
          )?.includes("collapsed")
        ) {
          await playerA.locator("#btn-panel").click();
        }
        const clueToggle = playerA.locator("#inv-card-toggle-clues");
        if ((await clueToggle.getAttribute("aria-expanded")) !== "true") {
          await clueToggle.click();
        }
        const clueCard = playerA
          .locator(".inv-clue-row")
          .filter({ hasText: "心力衰竭" })
          .first();
        await expect(clueCard).toBeVisible();
        await clueCard
          .getByRole("button", { name: "出示", exact: true })
          .click();
        const presentDialog = playerA.locator("#structured-action-dialog");
        await expect(presentDialog).toBeVisible();
        // 目标：宅邸没有在场 NPC 候选，走「描述其他对象」。
        const targetSelect = presentDialog.locator("select").last();
        await targetSelect.selectOption("__unresolved__");
        await presentDialog
          .getByPlaceholder(/交给守秘人澄清/)
          .fill("霍布豪斯家族的代表");
        await presentDialog
          .getByPlaceholder(/你认得这份证明吗/)
          .fill("你们对莱特的死因了解多少？");
        const presentBefore = framesOf(
          playerAFrames.sent,
          "action_request",
        ).length;
        await presentDialog
          .getByRole("button", { name: "提交请求", exact: true })
          .click();
        await expect
          .poll(() => framesOf(playerAFrames.sent, "action_request").length)
          .toBeGreaterThan(presentBefore);
        const presentRaw = JSON.parse(
          framesOf(playerAFrames.sent, "action_request").at(-1)!,
        ).request_id as string;
        const presentId = await settlePlayerRequest(
          playerA,
          playerAFrames,
          presentRaw,
        );
        // 主持待办里应出现「申请出示」，裁定完成。
        await ensureConsole(keeper);
        const presentCard = keeper
          .getByTestId("keeper-pending-request")
          .filter({ hasText: presentId });
        await expect(presentCard).toBeVisible();
        await expect(
          presentCard.locator(".keeper-pending-label"),
        ).toContainText("申请出示");
        await resolvePending(
          keeper,
          keeperFrames,
          presentId,
          "代表听过死因疑点，答应回去核实家族记录。",
        );
        await publishKeeper(
          keeper,
          keeperFrames,
          "（电话那头）家族代表核实后回复：死亡证明确系惠特克罗夫特签署，措辞被校方压过。",
        );

        // —— 乙使用真实持有物品（不消耗）——
        const bItems = JSON.parse(
          framesOf(playerBFrames.received, "session_snapshot").at(-1)!,
        ).payload.items as { id: string; label: string; quantity: number }[];
        const useTarget =
          bItems.find(
            (item) => item.quantity >= 1 && !item.label.startsWith(".38口径"),
          ) ?? bItems[0];
        if (
          (
            await playerB.locator("#char-panel").getAttribute("class")
          )?.includes("collapsed")
        ) {
          await playerB.locator("#btn-panel").click();
        }
        const itemRow = playerB.locator(
          `.inv-item-row[data-item-id="${useTarget.id}"]`,
        );
        await expect(itemRow).toBeVisible();
        await itemRow
          .getByRole("button", { name: "使用", exact: true })
          .click();
        const useDialog = playerB.getByRole("dialog", { name: "使用道具" });
        await useDialog
          .getByLabel("补充做法（即兴用法）")
          .fill("用怀表核对监视轮班时间。");
        await useDialog
          .getByRole("button", { name: "提交请求", exact: true })
          .click();
        const useRaw = JSON.parse(
          framesOf(playerBFrames.sent, "action_request").at(-1)!,
        ).request_id as string;
        const useReq = await settlePlayerRequest(
          playerB,
          playerBFrames,
          useRaw,
        );
        await ensureConsole(keeper);
        const useCard = keeper
          .getByTestId("keeper-pending-request")
          .filter({ hasText: useReq });
        await expect(useCard).toBeVisible();
        await useCard
          .getByRole("button", { name: "准备使用", exact: true })
          .click();
        const used = await submitKeeperCommand(keeper, keeperFrames);
        expect(used.accepted, used.lastError).toBe(true);
        await resolvePending(
          keeper,
          keeperFrames,
          useReq,
          "对时完成，未消耗。",
        );
        // 不消耗：物品仍在乙背包。
        const bItemsAfter = JSON.parse(
          framesOf(playerBFrames.received, "inventory_changed").at(-1)!,
        ).payload.items as { id: string; quantity: number }[];
        expect(
          bItemsAfter.find((item) => item.id === useTarget.id)?.quantity,
        ).toBe(useTarget.quantity);

        // —— 乙通过「前往…」申请回大学（发起请求；实际移动由主持批准执行）——
        await playerB.getByTestId("btn-move").click();
        const moveDialog = playerB.locator("#move-panel");
        const destButton = moveDialog.locator(
          '[data-scene-id="miskatonic_university"]',
        );
        await expect(destButton).toBeVisible();
        const moveBefore = framesOf(
          playerBFrames.sent,
          "action_request",
        ).length;
        await destButton.click();
        await expect
          .poll(() => framesOf(playerBFrames.sent, "action_request").length)
          .toBeGreaterThan(moveBefore);
        const moveRaw = JSON.parse(
          framesOf(playerBFrames.sent, "action_request").at(-1)!,
        ).request_id as string;
        const moveReq = await settlePlayerRequest(
          playerB,
          playerBFrames,
          moveRaw,
        );
        // 玩家申请不会自行改场景：在主持批准前页首场景不变。
        await expect(playerB.locator(".header-scene-name")).toHaveText(
          "霍布豪斯宅邸",
        );
        await moveParty(
          keeper,
          keeperFrames,
          playerA,
          playerB,
          "miskatonic_university",
          "密斯卡托尼克大学",
        );
        await resolvePending(
          keeper,
          keeperFrames,
          moveReq,
          "主持批准，整队返回大学休整。",
        );
      },
    );

    // ==================== B5b 多日监视古董店 ====================
    await stage(
      "B5b",
      "多日监视古董店：advance_time 结算世界时钟与案件时钟",
      async () => {
        await moveParty(
          keeper,
          keeperFrames,
          playerA,
          playerB,
          "trivial_pursuits",
          "轻率琐事古董店",
        );
        await publishKeeper(
          keeper,
          keeperFrames,
          "你们在店对面租下阁楼窗口，开始不分昼夜的监视。维克深居简出，费德曼兄妹偶尔抬着沉重的木箱进出后门。",
        );
        const latestClock = () =>
          framesOf(keeperFrames.received, "state_changed")
            .map((raw) => JSON.parse(raw).payload.clock?.elapsed_minutes)
            .filter((value) => typeof value === "number")
            .at(-1) as number | undefined;
        const clockBefore =
          latestClock() ??
          (JSON.parse(
            framesOf(keeperFrames.received, "session_snapshot").at(-1)!,
          ).payload.clock.elapsed_minutes as number);
        await keeperCmd(keeper, keeperFrames, "advance_time", {
          minutes: "2880",
          activity: "wait",
          reason:
            "两日轮班监视古董店（模组 monster_manifestation 时钟 time_advance=every 2880min）。",
        });
        await closeConsole(keeper);
        const latest = latestClock();
        expect(latest! - clockBefore).toBe(2880);
        for (const page of [keeper, playerA, playerB]) {
          await expect(page.getByTestId("header-game-clock")).toContainText(
            "天",
          );
        }
        await ensureConsole(keeper);
        await expect(
          keeper.getByTestId("keeper-clock-monster_manifestation"),
        ).toContainText("1 / 6");
        await expect(
          keeper.getByTestId("keeper-clock-clue_clarity"),
        ).toContainText("4 / 5"); // exactly four distinct author clues so far
        for (const player of [playerA, playerB])
          await expect(
            player.getByTestId("keeper-progress-catalogue"),
          ).toHaveCount(0);
        for (const frames of [playerAFrames, playerBFrames])
          expect(
            framesOf(frames.received, "keeper_progress_updated"),
          ).toHaveLength(0);
        await closeConsole(keeper);
      },
    );

    // ==================== B6a 搜古董店找地下室 ====================
    await stage(
      "B6a",
      "搜店：店面后方 → 活板门（发现链两级裁定）",
      async () => {
        // 队伍已在 trivial_pursuits（B5b）。
        const reqSearch = await freeform(
          playerA,
          playerAFrames,
          "我绕过货架搜查店面后方：隔断、储藏室与装卸区。",
        );
        await tryGrantClue(
          keeper,
          keeperFrames,
          "wick_shop_secrets",
          [invA, invB],
          "搜查店面后方发现通往更深处的暗门（模组 search 规则无需检定）。",
          { rule: 0, actor: invA },
        );
        await resolvePending(
          keeper,
          keeperFrames,
          reqSearch,
          "店面后方搜完：发现向下的暗门。",
        );
        const reqTrapdoor = await freeform(
          playerB,
          playerBFrames,
          "我掀开那扇半掩的活板门，沿阶梯下到深层地下室查看。",
        );
        await tryGrantClue(
          keeper,
          keeperFrames,
          "wick_trapdoor",
          [invA, invB],
          "活板门下的深层地下室（requires_flags=wicks_shop_searched 已满足）。",
          { rule: 0, actor: invB },
        );
        // 地下室尸体收藏：major SAN（1/1D6+1，公开骰）。
        const loss = (await keeperPublicRoll(keeper, keeperFrames, "1d6")) + 1;
        for (const inv of [invA, invB]) {
          await adjustStat(
            keeper,
            keeperFrames,
            inv,
            "san",
            `-${loss}`,
            `目睹深层地下室的尸体收藏，公开骰 1D6+1=${loss}。`,
          );
        }
        await resolvePending(
          keeper,
          keeperFrames,
          reqTrapdoor,
          "深层地下室：成堆的木箱与不该存在的东西。",
        );
      },
    );

    // ==================== B6b 古董店伏击战 ====================
    await stage("B6b", "费德曼兄妹伏击战（真实掷骰，含弹药消耗）", async () => {
      const latestCombat = () => {
        const received = keeperFrames.received;
        const snapshotIdx = received.findLastIndex((frame) =>
          frame.includes('"type":"session_snapshot"'),
        );
        const combatIdx = received.findLastIndex((frame) =>
          frame.includes('"type":"combat_updated"'),
        );
        if (snapshotIdx > combatIdx) {
          return (JSON.parse(received[snapshotIdx]) as { payload?: any })
            .payload?.combat;
        }
        if (combatIdx >= 0) {
          return (JSON.parse(received[combatIdx]) as { payload?: unknown })
            .payload;
        }
        return undefined;
      };
      // 在场目标：费德曼兄妹必须真实在场（不靠注入）。
      const targets = framesOf(keeperFrames.received, "state_changed")
        .map((raw) => JSON.parse(raw).payload.targets)
        .filter(Array.isArray)
        .at(-1) as { kind: string; id: string }[] | undefined;
      expect(
        targets?.some((target) => target.id === "hector_kara_feldman"),
        "费德曼兄妹不在古董店在场目标里",
      ).toBe(true);

      await publishKeeper(
        keeper,
        keeperFrames,
        "楼上传来维克冰冷的声音：『处理了。』——费德曼兄妹从货架阴影里扑出，半胶质的皮肤在灯下泛着死光。",
      );
      await ensureConsole(keeper);
      await keeper.getByTestId("keeper-cmd-combat_start").click();
      await setKeeperField(keeper, "participants", "hector_kara_feldman");
      const started = await submitKeeperCommand(keeper, keeperFrames);
      expect(started.accepted, started.lastError).toBe(true);
      await closeConsole(keeper);

      // 枪械持有者（.38 左轮是真实角色物品）：第一枪验证弹药消耗。
      const holderOf = async (frames: Frames) =>
        JSON.parse(framesOf(frames.received, "session_snapshot").at(-1)!)
          .payload;
      const snapA = await holderOf(playerAFrames);
      const gunOwnerA = (snapA.items ?? []).some((item: { label: string }) =>
        item.label.startsWith(".38口径左轮手枪"),
      );
      const gunInv = gunOwnerA ? invA : invB;
      const gunPage = gunOwnerA ? playerA : playerB;
      const gunFrames = gunOwnerA ? playerAFrames : playerBFrames;
      let gunFired = false;

      let rounds = 0;
      let feldmanDown = false;
      let npcCountered = false;
      while (rounds < 10) {
        const combat = latestCombat();
        expect(combat, "战斗状态丢失").toBeTruthy();
        const feldman = combat.participants.find(
          (p: { id: string }) => p.id === "hector_kara_feldman",
        );
        if (!feldman || feldman.hp <= 0) {
          feldmanDown = true;
          break;
        }
        const actor = combat.current_actor;
        if (!actor) break;
        const actorKind = combat.participants.find(
          (p: { id: string }) => p.id === actor,
        )?.kind;
        await ensureConsole(keeper);
        await keeper.getByTestId("keeper-cmd-combat_action").click();
        await setKeeperField(keeper, "actor_id", actor);
        const isGunTurn = actorKind === "pc" && actor === gunInv && !gunFired;
        if (actorKind === "pc") {
          await setKeeperField(
            keeper,
            "action_type",
            isGunTurn ? "firearm" : "melee",
          );
          await setKeeperField(keeper, "target_id", "hector_kara_feldman");
          if (isGunTurn) {
            const weaponSelect = keeper.locator(
              '[data-field="weapon_item_id"] select',
            );
            const options = await weaponSelect
              .locator("option")
              .evaluateAll((nodes) =>
                nodes.map((node) => ({
                  id: (node as HTMLOptionElement).value,
                  label: node.textContent || "",
                })),
              );
            const gun = options.find((option) =>
              option.label.startsWith(".38口径左轮手枪"),
            );
            expect(gun, "持枪者行动时没有真实左轮可选").toBeTruthy();
            await weaponSelect.selectOption(gun!.id);
          }
          await setKeeperField(
            keeper,
            "damage_spec",
            isGunTurn ? "1d10" : "1d3",
          );
          await setKeeperField(
            keeper,
            "description",
            isGunTurn ? "开枪射击扑来的兄妹" : "近身搏斗",
          );
        } else {
          // NPC 行动：非骰移动/压制，伤害由主持公开骰 + adjust_stat 裁定。
          await setKeeperField(keeper, "action_type", "other");
          await setKeeperField(
            keeper,
            "description",
            "费德曼兄妹扑击缠斗（伤害由主持公开骰裁定）。",
          );
        }
        // 行动者页与基线计数必须在提交准备之前确定（回执帧可能立刻到达）。
        const actorPage =
          actor === invA ? playerA : actor === invB ? playerB : keeper;
        const actorFrames =
          actor === invA
            ? playerAFrames
            : actor === invB
              ? playerBFrames
              : keeperFrames;
        const decBefore = framesOf(
          actorFrames.received,
          "combat_decision_required",
        ).length;
        const rollReqBefore = framesOf(
          actorFrames.received,
          "combat_roll_required",
        ).length;
        const prepared = await submitKeeperCommand(keeper, keeperFrames);
        expect(prepared.accepted, prepared.lastError).toBe(true);
        await closeConsole(keeper);

        // 准备后等待卡片进入可操作态：先决策门（确认暴力），再掷骰；
        // 无需掷骰的动作（move/other）会直接推进行动顺位。
        const needsInput = await expect
          .poll(
            () =>
              framesOf(actorFrames.received, "combat_decision_required")
                .length -
              decBefore +
              (framesOf(actorFrames.received, "combat_roll_required").length -
                rollReqBefore),
            { timeout: 8_000 },
          )
          .toBeGreaterThan(0)
          .then(() => true)
          .catch(() => false);
        if (!needsInput) {
          // 无骰动作：确认顺位已推进，进入下一轮。
          const advanced = latestCombat()?.current_actor;
          expect(
            advanced,
            `NPC 无骰动作后顺位未推进（仍在 ${actor}）`,
          ).not.toBe(actor);
        } else {
          const card = actorPage.getByTestId("combat-field-record");
          await expect(card).toBeVisible({ timeout: 30_000 });
          // 只响应本轮新到的决策门（历史决策帧会留在记录里，不能按最新值瞎点）。
          const hasNewDecision =
            framesOf(actorFrames.received, "combat_decision_required").length >
            decBefore;
          if (hasNewDecision) {
            const decision = lastPayload(
              actorFrames.received,
              "combat_decision_required",
            );
            const proceed = (decision?.options ?? []).find(
              (option: { id: string }) => option.id === "confirm_violence",
            );
            if (proceed) {
              await card
                .getByRole("button", { name: proceed.label, exact: true })
                .click();
            }
          }
          const rollButton = card.getByRole("button", {
            name: "掷骰",
            exact: true,
          });
          await expect(rollButton).toBeEnabled({ timeout: 30_000 });
          const rollsBefore = framesOf(
            actorFrames.received,
            "combat_roll_resolved",
          ).length;
          await rollButton.click();
          await expect
            .poll(
              () =>
                framesOf(actorFrames.received, "combat_roll_resolved").length,
              { timeout: 30_000 },
            )
            .toBeGreaterThan(rollsBefore);
          if (isGunTurn) {
            gunFired = true;
            // 弹药真实消耗：持有者收到 inventory_changed，左轮 label 由
            // （6发）降为（5发）（弹药编码在标签里，quantity 恒为 1 把枪）。
            await expect
              .poll(
                () => {
                  const latest = framesOf(
                    gunFrames.received,
                    "inventory_changed",
                  ).at(-1);
                  if (!latest) return "";
                  const items = JSON.parse(latest).payload.items as {
                    label: string;
                  }[];
                  return (
                    items.find((item) =>
                      item.label.startsWith(".38口径左轮手枪"),
                    )?.label ?? ""
                  );
                },
                { timeout: 30_000 },
              )
              .toBe(".38口径左轮手枪（5发）");
          }
        }
        // NPC 反击伤害（每场一次）：主持公开骰 1D4 → 扣当前行动顺位首个 PC。
        if (actorKind !== "pc" && !npcCountered) {
          npcCountered = true;
          const wound = await keeperPublicRoll(keeper, keeperFrames, "1d4");
          const targetPc = combat.participants.find(
            (p: { kind: string }) => p.kind === "pc",
          )?.id;
          if (wound > 0 && targetPc) {
            await adjustStat(
              keeper,
              keeperFrames,
              targetPc,
              "hp",
              `-${wound}`,
              `费德曼兄妹反击命中，公开骰 1D4=${wound}。`,
            );
          }
        }
        rounds += 1;
      }
      expect(rounds, "伏击战一轮都没打").toBeGreaterThan(0);

      // 兄妹被打倒时战斗可能已由服务端自动结算结束；只在仍 active 时收尾。
      if (latestCombat()?.active !== false) {
        await ensureConsole(keeper);
        await keeper.getByTestId("keeper-cmd-combat_end").click();
        await setKeeperField(
          keeper,
          "reason",
          feldmanDown
            ? "费德曼兄妹被当场击毙。"
            : `费德曼兄妹受创后逃入暗道（${rounds} 轮真实交锋后主持收尾）。`,
        );
        const ended = await submitKeeperCommand(keeper, keeperFrames);
        expect(ended.accepted, ended.lastError).toBe(true);
        await closeConsole(keeper);
      }
      await ruleFlag(
        keeper,
        keeperFrames,
        "feldman_ambushed",
        `伏击战发生并结束：${rounds} 轮真实掷骰，${gunFired ? "含一次真实开枪（弹药 6→5），" : ""}${npcCountered ? "PC 受反击伤一次，" : ""}兄妹${feldmanDown ? "被击毙" : "被击退"}。`,
      );
    });

    // ==================== B6c 找回文档 + 徽章 ====================
    await stage("B6c", "找回文档与银徽章；文档显形事件", async () => {
      await tryGrantClue(
        keeper,
        keeperFrames,
        "witch_trial_documents_read",
        [invA],
        "只当面阅读，不带走原件。",
        { rule: 0, actor: invA },
      );
      const readingProgress = lastPayload(
        keeperFrames.received,
        "keeper_progress_updated",
      );
      const original = readingProgress.clues.find(
        (c: { id: string }) => c.id === "witch_trial_documents",
      );
      expect(original.item_id, "阅读不得自动取得原件").toBe("");
      const sealRule = readingProgress.clues.find(
        (c: { id: string }) => c.id === "monster_sealed",
      ).rules[0];
      expect(
        sealRule.conditions.find(
          (c: { flag_id: string }) => c.flag_id === "documents_recovered",
        ).satisfied,
      ).toBe(false);
      expect(
        framesOf(playerBFrames.received, "clue_granted").map(
          (f) => JSON.parse(f).payload.clue_id,
        ),
      ).not.toContain("witch_trial_documents_read");
      const reqDocs = await freeform(
        playerA,
        playerAFrames,
        "我在深层地下室的箱底找出那叠失踪的阿卡姆女巫审判文档，收进随身的防水袋。",
      );
      await tryGrantClue(
        keeper,
        keeperFrames,
        "witch_trial_documents",
        [invA, invB],
        "深层地下室取回文档原件（requires_flags=deep_basement_found 已满足）。",
        { rule: 0, actor: invA, acquire: true },
      );
      await resolvePending(
        keeper,
        keeperFrames,
        reqDocs,
        "文档到手：泛黄的纸页上墨迹微微蠕动。",
      );
      // 模组内建节奏：取回文档触发墨迹显形（ink_manifestation）。
      await publishKeeper(
        keeper,
        keeperFrames,
        "文档在你们怀里发烫。墨迹从纸页上剥落，在地下室中央聚成一团人形暗影——一只幽绿的独眼睁开。",
      );
      await presentPhoto(keeper, "从文字中显形", [invA, invB]);
      await ackHandout(playerA);
      await ackHandout(playerB);
      await ruleFlag(
        keeper,
        keeperFrames,
        "monster_manifested",
        "取回文档后墨迹显形（模组 crisis_triggers.ink_manifestation 的叙事落实）。",
      );
      const manifestLoss = await keeperPublicRoll(keeper, keeperFrames, "1d4");
      if (manifestLoss > 0) {
        for (const inv of [invA, invB]) {
          await adjustStat(
            keeper,
            keeperFrames,
            inv,
            "san",
            `-${manifestLoss}`,
            `目睹墨中怪物部分显形，公开骰 1D4=${manifestLoss}。`,
          );
        }
      }
      const reqSeal = await freeform(
        playerB,
        playerBFrames,
        "我冲上楼上办公室，撬开那只上锁的锡盒，找出里面的银质徽章。",
      );
      await tryGrantClue(
        keeper,
        keeperFrames,
        "abner_seal",
        [invA, invB],
        "伏击战后搜锁箱找到银质徽章（requires_flags=feldman_ambushed 已满足）。",
        { rule: 0, actor: invB, acquire: true },
      );
      // 模组 granted_item（文档/徽章）在结构化物品层的可达性复核。
      await ensureConsole(keeper);
      await keeper.getByTestId("keeper-cmd-transfer_item").click();
      const itemOptions = await keeper
        .locator(
          '[data-field="item_id"] select option, [data-field="item_id"] datalist option',
        )
        .evaluateAll((nodes) => nodes.map((node) => node.textContent ?? ""));
      for (const label of ["阿卡姆女巫审判文档", "阿伯那·维克的银质徽章"]) {
        expect(
          itemOptions.some((text) => text.includes(label)),
          `实物 ${label} 必须实际落入注册表`,
        ).toBe(true);
      }
      await closeConsole(keeper);
      await resolvePending(
        keeper,
        keeperFrames,
        reqSeal,
        "锡盒撬开：五角星蛇形纹的银徽章躺在里面。",
      );
    });

    // Player original-presentation uses the actual acquired object, not any
    // similarly named backpack entry; the act does not transfer/consume it.
    if (
      (await playerA.locator("#char-panel").getAttribute("class"))?.includes(
        "collapsed",
      )
    )
      await playerA.locator("#btn-panel").click();
    const originalClueToggle = playerA.locator("#inv-card-toggle-clues");
    if ((await originalClueToggle.getAttribute("aria-expanded")) !== "true")
      await originalClueToggle.click();
    // This actual author card is a task clue, not an investigation clue.
    await playerA.getByRole("tab", { name: "任务", exact: true }).click();
    await playerA
      .locator('.inv-clue-row[data-clue$=":witch_trial_documents"]')
      .getByRole("button", { name: "出示", exact: true })
      .click();
    const originalDialog = playerA.getByRole("dialog", { name: "出示线索" });
    await originalDialog
      .getByRole("radio", { name: "展示原件", exact: true })
      .click();
    const originals = originalDialog.getByLabel("出示哪件原件");
    const originalOption = originals
      .locator("option")
      .filter({ hasText: "阿卡姆女巫审判文档" });
    await expect(originalOption).toHaveCount(1);
    const originalId = await originalOption.getAttribute("value");
    await originals.selectOption(originalId!);
    await originalDialog
      .getByLabel("向谁出示 / 说明")
      .selectOption(`investigator:${invB}`);
    await originalDialog
      .getByRole("button", { name: "提交请求", exact: true })
      .click();
    const originalRequest = JSON.parse(
      framesOf(playerAFrames.sent, "action_request").at(-1)!,
    );
    expect(originalRequest.action.physical_item_id).toBe(originalId);
    await resolvePending(
      keeper,
      keeperFrames,
      originalRequest.request_id,
      "当面展示文档原件，不转交、不消耗。",
    );
    await closeConsole(keeper);
    await expect(
      playerA.locator(`.inv-item-row[data-item-id="${originalId}"]`),
    ).toHaveCount(1);
    await expect(
      playerB.locator(`.inv-item-row[data-item-id="${originalId}"]`),
    ).toHaveCount(0);

    // ==================== B6d 徽章封印 ====================
    await stage(
      "B6d",
      "徽章封印怪物（monster_sealed → monster_defeated）",
      async () => {
        // Use the genuinely acquired badge through the player's actual use button.
        await closeConsole(keeper);
        if (
          !(await playerB
            .locator(".inv-item-row")
            .filter({ hasText: "银质徽章" })
            .isVisible())
        )
          await playerB.locator("#btn-panel").click();
        const badgeRow = playerB
          .locator(".inv-item-row")
          .filter({ hasText: "银质徽章" });
        await expect(badgeRow).toHaveCount(1);
        const badgeId = await badgeRow.getAttribute("data-item-id");
        expect(badgeId).toBeTruthy();
        const reqBefore = framesOf(playerBFrames.sent, "action_request").length;
        await badgeRow
          .getByRole("button", { name: "使用", exact: true })
          .click();
        const useDialog = playerB.getByRole("dialog", { name: "使用道具" });
        await useDialog
          .getByLabel("补充做法（即兴用法）")
          .fill("把银徽章覆在文档图示上，完成封印。");
        await useDialog.getByRole("button", { name: "提交请求" }).click();
        await expect
          .poll(() => framesOf(playerBFrames.sent, "action_request").length)
          .toBeGreaterThan(reqBefore);
        const rawRequest = JSON.parse(
          framesOf(playerBFrames.sent, "action_request").at(-1)!,
        );
        const reqSealUse = String(rawRequest.request_id);
        expect(rawRequest.action.item_id).toBe(badgeId);
        await playerB.keyboard.press("Escape");
        await keeperCmd(keeper, keeperFrames, "use_item", {
          investigator_id: invB,
          item_id: badgeId!,
          quantity: "1",
          operation: "以徽章封印文档",
          effect_clue_id: "monster_sealed",
          effect_rule_index: "0",
          basis: "人类主持核对银徽章覆于实际文档原件，前置条件满足。",
        });
        expect(
          framesOf(keeperFrames.sent, "command_request")
            .map((r) => JSON.parse(r))
            .filter(
              (f) =>
                f.kind === "record_ruling" &&
                f.payload.flag_id === "monster_defeated",
            ),
        ).toHaveLength(0);
        await publishKeeper(
          keeper,
          keeperFrames,
          "银纹亮起，暗影发出纸张烧焦般的嘶鸣，被一寸寸拖回文档的图示里。独眼闭合，墨迹归于死寂。",
        );
        await resolvePending(
          keeper,
          keeperFrames,
          reqSealUse,
          "封印完成：怪物被逐回文档。",
        );
      },
    );

    // ==================== B8 结案 truth_and_seal ====================
    await stage(
      "B8",
      "结案：truth_and_seal 条件齐备 → end_game → 逐人奖励",
      async () => {
        await ensureConsole(keeper);
        const audit = keeper.getByTestId("keeper-ruling-audit");
        await audit.locator(":scope > summary").click();
        const target = audit.locator('[data-ending-id="truth_and_seal"]');
        await expect(target).toContainText("条件已齐");
        await target.getByRole("button", { name: "准备结算" }).click();
        await expect(keeper.getByLabel("模组结局 ID")).toHaveValue(
          "truth_and_seal",
        );
        const ended = await submitKeeperCommand(keeper, keeperFrames);
        expect(ended.accepted, ended.lastError).toBe(true);
        // game_ended 公共可见；case_settled 逐人（本人 + 主持各一份）。
        for (const frames of [keeperFrames, playerAFrames, playerBFrames]) {
          await expect
            .poll(() => framesOf(frames.received, "game_ended").length, {
              timeout: 30_000,
            })
            .toBeGreaterThan(0);
        }
        const settledA = framesOf(playerAFrames.received, "case_settled").map(
          (raw) => JSON.parse(raw).payload,
        );
        const settledB = framesOf(playerBFrames.received, "case_settled").map(
          (raw) => JSON.parse(raw).payload,
        );
        expect(
          settledA.some(
            (payload: { investigator_id?: string }) =>
              payload.investigator_id === invA,
          ),
          "甲没有收到自己的结案结算",
        ).toBe(true);
        expect(
          settledB.some(
            (payload: { investigator_id?: string }) =>
              payload.investigator_id === invB,
          ),
          "乙没有收到自己的结案结算",
        ).toBe(true);
        expect(
          settledA.some(
            (payload: { investigator_id?: string }) =>
              payload.investigator_id === invB,
          ),
          "甲收到了乙的结案结算（串线）",
        ).toBe(false);
        await closeConsole(keeper);
        for (const page of [keeper, playerA, playerB]) {
          await expect(page.getByTestId("combat-ending-record")).toContainText(
            "真相大白",
          );
        }
      },
    );

    // ==================== B9 各自保存结案角色 ====================
    await stage(
      "B9",
      "两名玩家各自保存结案角色（互不覆盖、互不可见）",
      async () => {
        await expect(keeper.getByTestId("case-character-actions")).toHaveCount(
          0,
        );
        for (const [page, frames, name] of [
          [playerA, playerAFrames, `结案-甲-${runId}`],
          [playerB, playerBFrames, `结案-乙-${runId}`],
        ] as const) {
          const card = page.getByTestId("case-character-actions");
          await expect(card.getByLabel("新角色名")).toBeEnabled({
            timeout: 30_000,
          });
          await card.getByLabel("新角色名").fill(name);
          await card.getByRole("button", { name: "保存为新角色" }).click();
          await expect(
            card.getByRole("button", { name: "已保存", exact: true }),
          ).toBeDisabled({ timeout: 30_000 });
          const entries = await page.evaluate(async () => {
            const response = await fetch("/api/character-library", {
              credentials: "include",
            });
            return (await response.json()).entries as { name: string }[];
          });
          expect(
            entries.filter((entry) => entry.name === name),
            `${name} 没有写入本人角色库`,
          ).toHaveLength(1);
          const other = name.includes("甲")
            ? `结案-乙-${runId}`
            : `结案-甲-${runId}`;
          expect(
            entries.some((entry) => entry.name === other),
            `${name} 看到了对方的结案角色（串线）`,
          ).toBe(false);
          // 结案角色的 HP/SAN 应是战斗后的真实值，不是满血新卡。
          const settled = framesOf(frames.received, "case_settled").map(
            (raw) => JSON.parse(raw).payload,
          );
          expect(settled.length).toBeGreaterThan(0);
        }
      },
    );

    for (const frames of [playerAFrames, playerBFrames])
      expect(framesOf(frames.received, "keeper_progress_updated")).toHaveLength(
        0,
      );
    expect(
      gaps.filter((g) => g.category !== "⑤"),
      "不允许将工具缺口用叙事/裁定替代后宣布通过",
    ).toEqual([]);
    // ---- 全程无模型错误提示 ----
    for (const page of [keeper, playerA, playerB]) {
      await expect(
        page.getByText(/无法连接配置的模型|模型.*不可用|connect.*fail/i),
      ).toHaveCount(0);
    }
  } finally {
    await keeperContext.close();
    await playerAContext.close();
    await playerBContext.close();
  }
});
