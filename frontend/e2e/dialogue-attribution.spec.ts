/** Real local backend + scripted provider: separate departure, arrival and avatars. */
import { spawn, spawnSync, type ChildProcess } from "node:child_process";
import { existsSync, mkdtempSync, rmSync } from "node:fs";
import { createServer, type Server } from "node:http";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { expect, request, test } from "@playwright/test";
import { modelDialogueContent } from "./helpers/model-dialogue";

const root = resolve(import.meta.dirname, "../..");
const baseUrl = "http://127.0.0.1:8878";
const python =
  process.env.TRPG_E2E_PYTHON ||
  (existsSync(join(root, "venv/bin/python"))
    ? join(root, "venv/bin/python")
    : "python");
let runtimeRoot = "";
let backend: ChildProcess | null = null;
let provider: Server | null = null;
let serverLog = "";

test.beforeAll(async () => {
  runtimeRoot = mkdtempSync(join(tmpdir(), "trpg-dialogue-e2e-"));
  provider = createServer((incoming, response) => {
    let body = "";
    incoming.on("data", (chunk) => {
      body += String(chunk);
    });
    incoming.on("end", () => {
      const wire = JSON.parse(body);
      const latest =
        wire.messages?.findLast(
          (item: { role: string }) => item.role === "user",
        )?.content || "";
      if (
        wire.tools?.some(
          (item: { function: { name: string } }) =>
            item.function.name === "adjudicate_action",
        )
      ) {
        const input = JSON.parse(latest).player_input;
        const ready = input.includes("独自出发");
        const plan = {
          intent: ready ? "move" : "prepare_move",
          input_quote: input,
          approach: ready ? "独自前往藏宝楼" : "先确定伊芙琳是否同行",
          destination_scene_id: "shop",
          destination_reason: "玩家选择藏宝楼",
          departure_question: ready ? "" : "伊芙琳同行，还是留在报馆？",
          time_minutes: ready ? 20 : 0,
        };
        response.writeHead(200, { "Content-Type": "application/json" });
        response.end(
          JSON.stringify({
            id: "adjudication",
            choices: [
              {
                finish_reason: "tool_calls",
                message: {
                  role: "assistant",
                  content: null,
                  tool_calls: [
                    {
                      id: "call-plan",
                      type: "function",
                      function: {
                        name: "adjudicate_action",
                        arguments: JSON.stringify(plan),
                      },
                    },
                  ],
                },
              },
            ],
          }),
        );
        return;
      }
      let content = modelDialogueContent(
        body,
        "雨幕笼罩着阿卡姆。你在办公室里准备开始调查。",
      );
      if (latest.includes("[出发前过渡回合")) {
        content = JSON.stringify({
          segments: [
            { kind: "narration", text: "你仍在报馆，伊芙琳等待你安排。" },
            {
              kind: "speech",
              speaker_id: "su",
              text: "金三爷做生意三十年，问他时要留心。",
            },
            {
              kind: "speech",
              speaker_id: "evelyn",
              text: "我可以跟您一起去，也可以留在这里。",
            },
          ],
          introduced_npc_ids: [],
          choices: [],
        });
      } else if (
        latest.includes("[模型裁决的移动已结算") ||
        (latest.startsWith("[引擎控制指令") &&
          body.includes("[模型裁决的移动已结算"))
      ) {
        content = JSON.stringify({
          segments: [
            {
              kind: "narration",
              text: "你独自乘车抵达藏宝楼，推开古玩店的门。",
            },
            {
              kind: "speech",
              speaker_id: "jin",
              text: "哟，稀客。看看可以，问价得先让我看看您的诚意。",
            },
          ],
          introduced_npc_ids: [],
          choices: [],
        });
      }
      response.writeHead(200, { "Content-Type": "text/event-stream" });
      response.write(
        `data: ${JSON.stringify({ id: "story", choices: [{ index: 0, delta: { role: "assistant", content }, finish_reason: null }] })}\n\n`,
      );
      response.write(
        `data: ${JSON.stringify({ id: "story", choices: [{ index: 0, delta: {}, finish_reason: "stop" }] })}\n\n`,
      );
      response.end("data: [DONE]\n\n");
    });
  });
  await new Promise<void>((done) => provider!.listen(0, "127.0.0.1", done));
  const address = provider.address();
  if (!address || typeof address === "string")
    throw new Error("provider address missing");
  backend = spawn(
    python,
    ["-m", "uvicorn", "server:app", "--host", "127.0.0.1", "--port", "8878"],
    {
      cwd: root,
      env: {
        ...process.env,
        TRPG_RUNTIME_ROOT: runtimeRoot,
        TRPG_DATABASE_URL: `sqlite:///${join(runtimeRoot, "test.db")}`,
        OPENAI_API_KEY: "scripted-e2e-key",
        OPENAI_BASE_URL: `http://127.0.0.1:${address.port}/v1`,
        TRPG_WRITE_COMPAT_EXPORTS: "0",
        TRPG_ENABLE_LOREBOOK: "0",
        TRPG_ENABLE_TURN_AUDIT: "0",
        TRPG_STREAM_USAGE: "0",
        TRPG_ACTION_ADJUDICATION: "1",
      },
      stdio: ["ignore", "pipe", "pipe"],
    },
  );
  backend.stdout!.on("data", (chunk) => {
    serverLog += String(chunk);
  });
  backend.stderr!.on("data", (chunk) => {
    serverLog += String(chunk);
  });
  const client = await request.newContext();
  try {
    await expect
      .poll(
        async () => {
          try {
            return (await client.get(`${baseUrl}/api/health`)).ok();
          } catch {
            return false;
          }
        },
        { timeout: 30000 },
      )
      .toBe(true);
  } finally {
    await client.dispose();
  }
});

test.afterAll(async () => {
  backend?.kill("SIGTERM");
  if (backend && backend.exitCode === null)
    await new Promise<void>((done) => backend!.once("exit", () => done()));
  if (provider)
    await new Promise<void>((done) => provider!.close(() => done()));
  if (runtimeRoot) rmSync(runtimeRoot, { recursive: true, force: true });
});

function prepareDialogueWorld(worldId: string) {
  const script = [
    "import os, sys",
    "from pathlib import Path",
    "from src.app.runtime import RuntimeContext",
    "ctx=RuntimeContext.create(sys.argv[1], '猩红文档', runtime_root=Path(os.environ['TRPG_RUNTIME_ROOT']))",
    "with ctx.world_store.transaction() as state:",
    "    state['scene_catalog']['office']={'id':'office','name':'申报馆','npcs_present':['su','evelyn'],'exits':['shop']}",
    "    state['scene_catalog']['shop']={'id':'shop','name':'豫园藏宝楼','npcs_present':['jin'],'exits':['office']}",
    "    state['current_scene']=dict(state['scene_catalog']['office'])",
    "    state['npcs']=[{'id':i,'name':n,'current_location':s,'revealed':{'level':1,'entries':[]}} for i,n,s in [('su','苏晚晴','office'),('evelyn','伊芙琳·奥斯古德','office'),('jin','金三爷','shop')]]",
    "    state['asset_map']['npcs']={'su':{'file':'露西·斯通.png'},'evelyn':{'file':'哈兰德·洛奇.png'},'jin':{'file':'布莱斯·法伦.png'}}",
  ].join("\n");
  const result = spawnSync(python, ["-c", script, worldId], {
    cwd: root,
    encoding: "utf8",
    env: {
      ...process.env,
      TRPG_RUNTIME_ROOT: runtimeRoot,
      TRPG_DATABASE_URL: `sqlite:///${join(runtimeRoot, "test.db")}`,
      TRPG_WRITE_COMPAT_EXPORTS: "0",
    },
  });
  if (result.status !== 0) throw new Error(result.stderr);
}

test("真实后端：过渡与抵达分两回合，三位人物姓名/头像正确，读档重述保持归属", async ({
  page,
}, info) => {
  test.setTimeout(120000);
  await page.goto(`${baseUrl}/?mode=local`);
  await expect(page.locator(".boot-loader")).toBeHidden({ timeout: 30000 });
  await page.locator(".module-select-trigger").click();
  await page.getByRole("option", { name: /猩红文档/ }).click();
  await page.locator("#btn-start").click();
  await page.locator("#btn-character-confirm").click();
  await expect(page.getByText("雨幕笼罩着阿卡姆").first()).toBeVisible({
    timeout: 60000,
  });
  await expect(page.locator("#user-input")).toBeEnabled();
  const worldId = await page.evaluate(
    () => localStorage.getItem("trpg-active-world-id") || "",
  );
  prepareDialogueWorld(worldId);
  await page.locator("#user-input").fill("先去藏宝楼吧");
  await page.locator("#user-input").press("Enter");
  const evelyn = page
    .locator(".speech-unit")
    .filter({ hasText: "我可以跟您一起去" })
    .last();
  const su = page
    .locator(".speech-unit")
    .filter({ hasText: "问他时要留心" })
    .last();
  await expect(evelyn.locator(".speech-name")).toHaveText("伊芙琳·奥斯古德");
  await expect(su.locator(".speech-name")).toHaveText("苏晚晴");
  await expect(page.locator(".header-scene-name")).toHaveText("申报馆");
  await expect(evelyn.locator("img")).toBeVisible();
  const evelynImage = await evelyn.locator("img").getAttribute("src");
  const suImage = await su.locator("img").getAttribute("src");
  expect(evelynImage).toBeTruthy();
  expect(evelynImage).not.toBe(suImage);
  await page.screenshot({ path: info.outputPath("departure-speakers.png") });
  await expect(page.locator("#user-input")).toBeEnabled();
  await page
    .locator("#user-input")
    .fill("伊芙琳留在报馆，我独自出发去藏宝楼。");
  await page.locator("#user-input").press("Enter");
  const jin = page
    .locator(".speech-unit")
    .filter({ hasText: "问价得先让我看看您的诚意" })
    .last();
  await expect(jin.locator(".speech-name")).toHaveText("金三爷");
  await expect(page.locator(".header-scene-name")).toHaveText("豫园藏宝楼");
  await expect(jin.locator("img")).toBeVisible();
  expect(await jin.locator("img").getAttribute("src")).not.toBe(suImage);
  await page.screenshot({ path: info.outputPath("arrival-speaker.png") });
  await expect(page.locator("#user-input")).toBeEnabled();
  await page.reload();
  await expect(page.locator(".boot-loader")).toBeHidden({ timeout: 30000 });
  const continueButton = page.locator("#btn-continue");
  await expect(continueButton).toBeVisible({ timeout: 30000 });
  await continueButton.click();
  await page
    .getByRole("dialog", { name: "从存档开始" })
    .getByRole("button", { name: "读取", exact: true })
    .first()
    .click();
  await expect(jin.locator(".speech-name"), serverLog.slice(-2000)).toHaveText(
    "金三爷",
    { timeout: 30000 },
  );
  await expect(page.locator(".header-scene-name")).toHaveText("豫园藏宝楼");
});
