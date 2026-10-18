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
`,
    ],
    { cwd: root, env, encoding: "utf8" },
  );
  if (seed.status !== 0) throw Error(seed.stderr);
  backend = spawn(
    python,
    ["-m", "uvicorn", "server:app", "--host", "127.0.0.1", "--port", "8823"],
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

test("本地游戏→从存档开始→读取→进入游戏；返回后换模组再读取，零模型调用", async ({
  page,
}) => {
  const frames: any[] = [];
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("websocket", (socket) =>
    socket.on("framereceived", (frame) => {
      try {
        frames.push(JSON.parse(String(frame.payload)));
      } catch {
        /* binary */
      }
    }),
  );
  await page.addInitScript(() => {
    localStorage.setItem("trpg-active-world-id", "save-first");
    localStorage.setItem("trpg-active-world-module", "mansion_of_madness");
  });
  await openLocalStartScreen(page, `${baseUrl}/?mode=local`);
  await page.locator("#btn-continue").click();
  await page
    .locator('[data-adventure="save-first"]')
    .getByRole("button", { name: "读取", exact: true })
    .click();
  await expect(page.locator("#user-input")).toBeEnabled({ timeout: 10_000 });
  await expect(page.locator("#start-overlay")).toBeHidden();
  await expect(page.locator("#messages")).toContainText(
    "读档验收：你停在保存时的大厅。",
  );
  await expect(page.locator("#messages")).not.toContainText(
    "PRIVATE_KEEPER_PROMPT",
  );
  await page.screenshot({ path: join(runtime, "restored.png") });
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
  await expect(page.locator("#user-input")).toBeEnabled();
  await expect(page.locator("#messages")).toContainText(
    "另一份存档：你站在大学办公室。",
  );
  await expect(page.locator("#messages")).not.toContainText("读档验收");
  expect(
    frames.filter((frame) => frame.type === "loaded" && frame.restore_only),
  ).toHaveLength(2);
  expect(frames.filter((frame) => frame.type === "gm_turn_start")).toHaveLength(
    0,
  );
  expect(modelRequests).toBe(0);
  expect(errors).toEqual([]);
});
