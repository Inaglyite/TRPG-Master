/** Actual local backend: clicking Read must work with the model service offline. */
import { spawn, spawnSync, type ChildProcess } from "node:child_process";
import { createServer, type Server } from "node:http";
import { existsSync, mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { expect, request, test } from "@playwright/test";
import { openLocalStartScreen } from "./readiness";

const root = resolve(import.meta.dirname, "../..");
const baseUrl = "http://127.0.0.1:8823";
const python =
  process.env.TRPG_E2E_PYTHON ||
  [".venv/bin/python", "venv/bin/python"]
    .map((name) => resolve(root, name))
    .find(existsSync) ||
  "python";
let backend: ChildProcess;
let trap: Server;
let modelRequests = 0;
let output = "";
let runtime = "";

test.beforeAll(async () => {
  const artifacts = process.env.TRPG_E2E_ARTIFACT_DIR || tmpdir();
  mkdirSync(artifacts, { recursive: true, mode: 0o700 });
  runtime = mkdtempSync(join(artifacts, "local-save-restore-"));
  trap = createServer((_req, res) => {
    modelRequests++;
    res.writeHead(503).end("model service offline");
  });
  await new Promise<void>((done) => trap.listen(0, "127.0.0.1", done));
  const address = trap.address();
  if (!address || typeof address === "string")
    throw Error("model trap missing");
  const env = {
    ...Object.fromEntries(
      Object.entries(process.env).filter(
        ([key]) =>
          !/^(http_proxy|https_proxy|all_proxy|TRPG_DATABASE_URL)$/i.test(key),
      ),
    ),
    NO_PROXY: "127.0.0.1,localhost",
    TRPG_RUNTIME_ROOT: runtime,
    TRPG_DATABASE_URL: `sqlite:///${join(runtime, "test.db")}`,
    TRPG_AUTH_REQUIRED: "0",
    TRPG_ALLOWED_ORIGINS: baseUrl,
    TRPG_WRITE_COMPAT_EXPORTS: "0",
    TRPG_LOG_DIR: join(runtime, "logs"),
    OPENAI_API_KEY: "offline-fixture",
    OPENAI_BASE_URL: `http://127.0.0.1:${address.port}/v1`,
    GLM_API_KEY: "",
  };
  const seed = spawnSync(
    python,
    [
      "-c",
      `
from src.app.runtime import RuntimeContext
from src.storage.database_turn_journal import DatabaseTurnJournal
from src.storage.persistence import save_game
for world, module, text in [
    ('save-first', 'mansion_of_madness', '读档验收：你停在保存时的大厅。'),
    ('save-second', '猩红文档', '另一份存档：你站在大学办公室。'),
]:
    context = RuntimeContext.create(world, module)
    save_game([
        {'role': 'system', 'content': 'PRIVATE_KEEPER_PROMPT'},
        {'role': 'assistant', 'content': text + '\\n\\n**你可以——**\\n1. 查看四周\\n2. [自由行动] 你决定做什么？'},
    ], 'slot_000', context=context)
    if world == 'save-first':
        journal = DatabaseTurnJournal(context.world_dir, world_id=world, module_name=module)
        messages = [{'role': 'system', 'content': 'PRIVATE_KEEPER_PROMPT'}, {'role': 'assistant', 'content': text}]
        first = journal.begin(kind='opening', player_input=None)
        journal.complete(first, messages=messages, world_state=context.world_store.load(), narrative=text, choices=[])
        second = journal.begin(kind='action', player_input='检查书桌')
        messages += [{'role': 'user', 'content': '[玩家行动] 检查书桌'}, {'role': 'assistant', 'content': '读档验收：你已检查书桌。'}]
        journal.complete(second, messages=messages, world_state=context.world_store.load(), narrative='读档验收：你已检查书桌。', choices=[])
`,
    ],
    { cwd: root, env, encoding: "utf8" },
  );
  if (seed.status !== 0) throw Error(seed.stderr);
  backend = spawn(
    python,
    [
      "-c",
      `
import time, uvicorn
import server
original_load = server.GameEngine.load
def slow_local_load(self, *args, **kwargs):
    # Only this isolated test server simulates a slow disk. No model call.
    time.sleep(2.6)
    return original_load(self, *args, **kwargs)
server.GameEngine.load = slow_local_load
uvicorn.run(server.app, host='127.0.0.1', port=8823)
`,
    ],
    { cwd: root, env, stdio: ["ignore", "pipe", "pipe"] },
  );
  backend.stdout?.on("data", (chunk) => {
    output += chunk;
  });
  backend.stderr?.on("data", (chunk) => {
    output += chunk;
  });
  const client = await request.newContext();
  try {
    for (let i = 0; i < 160; i++) {
      try {
        if ((await client.get(`${baseUrl}/api/health`)).ok()) return;
      } catch {
        /* starting */
      }
      await new Promise((done) => setTimeout(done, 100));
    }
  } finally {
    await client.dispose();
  }
  throw Error(output.slice(-3000));
});

test.afterAll(async () => {
  if (runtime) writeFileSync(join(runtime, "backend.log"), output);
  if (backend && backend.exitCode === null) {
    backend.kill("SIGTERM");
    await new Promise<void>((done) => {
      backend.once("exit", () => done());
      setTimeout(done, 3000);
    });
    if (backend.exitCode === null) backend.kill("SIGKILL");
  }
  if (trap) await new Promise<void>((done) => trap.close(() => done()));
});

for (const width of [1280, 939, 640]) {
  test(`本地慢读档 ${width}px：恢复分支按钮→创建并重读分支→换模组，零模型调用`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 900 });
    const frames: any[] = [];
    const sent: any[] = [];
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    page.on("websocket", (socket) => {
      socket.on("framesent", (frame) => {
        try {
          sent.push(JSON.parse(String(frame.payload)));
        } catch {
          /* binary */
        }
      });
      socket.on("framereceived", (frame) => {
        try {
          frames.push(JSON.parse(String(frame.payload)));
        } catch {
          /* binary */
        }
      });
    });
    await page.addInitScript(() => {
      localStorage.setItem("trpg-active-world-id", "save-first");
      localStorage.setItem("trpg-active-world-module", "mansion_of_madness");
      // Measure the user's DOM click, excluding Playwright's wait for the card
      // animation to stop before it can deliver that click.
      document.addEventListener(
        "click",
        (event) => {
          const button = (event.target as Element)?.closest("button");
          if (button?.textContent?.trim() === "读取") {
            performance.clearMarks("save-read-click");
            performance.clearMarks("save-read-visible");
            performance.mark("save-read-click");
          }
        },
        true,
      );
      new MutationObserver(() => {
        if (
          document.querySelector(".save-load-overlay") &&
          performance.getEntriesByName("save-read-click").length &&
          !performance.getEntriesByName("save-read-visible").length
        )
          performance.mark("save-read-visible");
      }).observe(document, { childList: true, subtree: true });
    });
    await openLocalStartScreen(page, `${baseUrl}/?mode=local`);
    await page.locator("#btn-continue").click();
    await page
      .locator('[data-adventure="save-first"]')
      .getByRole("button", { name: "读取", exact: true })
      .click();
    await expect(
      page.getByRole("dialog", { name: "正在读取存档" }),
    ).toBeVisible();
    const feedbackMs = await page.evaluate(
      () =>
        performance.getEntriesByName("save-read-visible")[0].startTime -
        performance.getEntriesByName("save-read-click")[0].startTime,
    );
    expect(feedbackMs).toBeLessThan(1000);
    await expect(page.locator("#app")).toHaveAttribute("inert", "");
    await page.waitForTimeout(1000);
    await page.screenshot({ path: join(runtime, `loading-${width}.png`) });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth > innerWidth,
      ),
    ).toBe(false);
    await expect(page.locator(".save-load-overlay")).toBeHidden();
    await expect(page.locator("#user-input")).toBeEnabled({ timeout: 10_000 });
    await expect(page.locator("#start-overlay")).toBeHidden();
    await expect(page.locator("#messages")).toContainText(
      "读档验收：你停在保存时的大厅。",
    );
    await expect(page.locator("#messages")).not.toContainText(
      "PRIVATE_KEEPER_PROMPT",
    );
    const branches = page.getByRole("button", {
      name: "回到本次行动前并创建独立时间线分支",
    });
    await expect(branches).toHaveCount(2);
    await page.locator("#messages .msg.gm").last().hover();
    await expect(
      page.locator("#messages .msg.gm").last().locator(".turn-message-actions"),
    ).toHaveCSS("opacity", "1");
    await page.waitForTimeout(1000);
    await page.screenshot({ path: join(runtime, `restored-${width}.png`) });
    await branches.last().click();
    await expect(page.locator("#messages")).not.toContainText("你已检查书桌");
    await expect(branches).toHaveCount(1);
    const initial = frames.find(
      (frame) => frame.type === "loaded" && frame.world_id === "save-first",
    );
    const branched = frames.find((frame) => frame.type === "turn_branched");
    expect(initial.history_source).toBe("turn_journal");
    expect(branched.source_turn_id).toBe(initial.history[0].turn_id);
    expect(branched.world_id).not.toBe("save-first");
    // Return through the real start page and read the branch's automatic save.
    // Its journal provenance and message-level buttons must survive again.
    await page.locator("#btn-new").click();
    await page.locator("#btn-continue").click();
    await page
      .locator('[data-adventure="save-first"]')
      .getByRole("button", { name: "读取", exact: true })
      .click();
    await expect(page.locator(".save-load-overlay")).toBeVisible();
    await expect(page.locator(".save-load-overlay")).toBeHidden();
    await expect(branches).toHaveCount(1);
    await expect(page.locator("#messages")).not.toContainText("你已检查书桌");
    await page.locator("#messages .msg.gm").last().hover();
    await expect(
      page.locator("#messages .msg.gm").last().locator(".turn-message-actions"),
    ).toHaveCSS("opacity", "1");
    await page.waitForTimeout(1000);
    await page.screenshot({
      path: join(runtime, `branch-restored-${width}.png`),
    });
    await page.locator("#btn-new").click();
    await page.locator(".module-select-trigger").click();
    await page.getByRole("option", { name: /猩红文档/ }).click();
    await expect(page.locator(".module-select-trigger")).toContainText(
      "猩红文档",
    );
    await page.locator("#btn-continue").click();
    await page
      .locator('[data-adventure="save-second"]')
      .getByRole("button", { name: "读取", exact: true })
      .click();
    await expect(page.locator(".save-load-overlay")).toBeVisible();
    await expect(page.locator(".save-load-overlay")).toBeHidden();
    await expect(page.locator("#user-input")).toBeEnabled();
    await expect(page.locator("#messages")).toContainText(
      "另一份存档：你站在大学办公室。",
    );
    await expect(page.locator("#messages")).not.toContainText("读档验收");
    await expect(branches).toHaveCount(0);
    expect(
      frames.filter((frame) => frame.type === "loaded" && frame.restore_only),
    ).toHaveLength(3);
    expect(
      frames.filter((frame) => frame.type === "gm_turn_start"),
    ).toHaveLength(0);
    expect(modelRequests).toBe(0);
    expect(errors).toEqual([]);
    const timings = sent.filter((frame) => frame.type === "save_load_timing");
    expect(timings).toHaveLength(3);
    expect(timings.every((frame) => frame.total_ms >= 2600)).toBe(true);
    writeFileSync(
      join(runtime, `timing-${width}.json`),
      JSON.stringify({ feedbackMs, timings, modelRequests, errors }, null, 2),
    );
  });
}
