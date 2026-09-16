/**
 * 真实模型 + 真实界面：agent 主持的过渡回合（live 规格，默认跳过）。
 *
 * 需要真实模型额度与钥匙，因此默认不跑；显式打开才执行：
 *     TRPG_LIVE_MODEL=1 npx playwright test e2e/structured-transition-agent-live.spec.ts
 *
 * 它验证的是「界面这一层」接真实模型时的表现：
 * 玩家自由说话 → 守秘人（agent）叙事并等待 → 位置不因意愿而改变 →
 * 玩家决定后才由主持命令落账。模型判断本身有随机性，所以这里只把
 * 平台不变量做成断言（不移动、只由已提交事件改位置），模型表现记进报告。
 *
 * 世界只存在于临时 runtime root；BYOK 绑定写在临时库里，不碰任何真实存档与配置。
 */

import { spawn, spawnSync, type ChildProcess } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

import { expect, request, test, type Page } from "@playwright/test";

import { openLocalStartScreen } from "./readiness";

const port = 8773;
const baseUrl = `http://127.0.0.1:${port}`;
const repositoryRoot = resolve(import.meta.dirname, "../..");
const screenshotsDir = resolve(import.meta.dirname, "../../docs/screenshots");
const MODULE = "猩红文档";
const live = process.env.TRPG_LIVE_MODEL === "1";

let runtimeRoot = "";
let server: ChildProcess | null = null;
let serverOutput = "";

function pythonPath(): string {
  return (
    process.env.TRPG_E2E_PYTHON ??
    (existsSync(resolve(repositoryRoot, ".venv/bin/python"))
      ? resolve(repositoryRoot, ".venv/bin/python")
      : "python")
  );
}

function modelConfig(): { apiKey: string; baseUrl: string; model: string } {
  const cfg = JSON.parse(
    readFileSync(resolve(repositoryRoot, ".env.json"), "utf-8"),
  ) as Record<string, string>;
  return {
    apiKey: cfg.api_key,
    baseUrl: cfg.base_url,
    model: cfg.narrative_model || cfg.flash_model,
  };
}

/** 把隔离世界切到 structured_v1 + agent，并写入房主的 BYOK 绑定。 */
function configureAgentWorld(worldId: string): string {
  const cfg = modelConfig();
  const script = [
    "import os, sys",
    "from pathlib import Path",
    "from src.storage.database import World, session_scope, database_url",
    "from src.structured.bootstrap import apply_profile_metadata, ensure_local_operator",
    "from src.storage import model_config_store",
    "from src.ai.model.route_service import EffectiveSettings, RoleBinding, ServiceSpec",
    "root = Path(os.environ['TRPG_RUNTIME_ROOT'])",
    "world_id = sys.argv[1]",
    "url = database_url(root)",
    "with session_scope(url) as session:",
    "    world = session.get(World, world_id)",
    "    if world is None: raise SystemExit('world not found')",
    "    world.metadata_json = apply_profile_metadata(world.metadata_json, execution_profile='structured_v1', keeper_mode='agent')",
    "    ensure_local_operator(session, world_id)",
    "    session.add(world)",
    "owner = model_config_store.current_world_owner_id(url, world_id)",
    "service = ServiceSpec(label='live-check', provider_kind='deepseek',",
    "    base_url=os.environ['OPENAI_BASE_URL'], api_key=os.environ['OPENAI_API_KEY'],",
    "    model_id=os.environ['TRPG_NARRATIVE_MODEL'], window_tokens=None,",
    "    max_output_tokens=16000, capabilities=None, allow_private=True)",
    "binding = RoleBinding(mode='custom', service=service)",
    "model_config_store.save_scope(url, owner_user_id=owner, world_id=world_id,",
    "    settings_no_revision=EffectiveSettings(narrative=binding, judgement=binding, revision=0))",
    "print('agent world ready')",
  ].join("\n");
  const result = spawnSync(pythonPath(), ["-c", script, worldId], {
    cwd: repositoryRoot,
    env: {
      ...process.env,
      TRPG_RUNTIME_ROOT: runtimeRoot,
      TRPG_DATABASE_URL: `sqlite:///${join(runtimeRoot, "e2e.db")}`,
      TRPG_WRITE_COMPAT_EXPORTS: "0",
      OPENAI_API_KEY: cfg.apiKey,
      OPENAI_BASE_URL: cfg.baseUrl,
      TRPG_NARRATIVE_MODEL: cfg.model,
    },
    encoding: "utf-8",
  });
  if (result.status !== 0) {
    throw new Error(
      `configureAgentWorld failed:\n${result.stderr || result.stdout}`,
    );
  }
  return result.stdout.trim();
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
  throw new Error(`server did not start:\n${serverOutput.slice(-3000)}`);
}

test.describe("真实模型 live（需 TRPG_LIVE_MODEL=1）", () => {
  test.skip(!live, "需要真实模型额度：设置 TRPG_LIVE_MODEL=1 才运行");

  test.beforeAll(async () => {
    const cfg = modelConfig();
    runtimeRoot = mkdtempSync(join(tmpdir(), "trpg-transition-live-"));
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
          OPENAI_API_KEY: cfg.apiKey,
          OPENAI_BASE_URL: cfg.baseUrl,
          TRPG_NARRATIVE_MODEL: cfg.model,
          TRPG_JUDGEMENT_MODEL: cfg.model,
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
    if (runtimeRoot) rmSync(runtimeRoot, { recursive: true, force: true });
  });

  test("agent 主持的过渡回合：意愿不移动、等待可回应、决定后才执行", async ({
    page,
  }) => {
    test.setTimeout(600_000);
    page.setDefaultTimeout(60_000);
    const sent: string[] = [];
    const received: string[] = [];
    page.on("websocket", (socket) => {
      socket.on("framesent", (event) => sent.push(String(event.payload)));
      socket.on("framereceived", (event) =>
        received.push(String(event.payload)),
      );
    });
    const sceneChanged = () =>
      received.filter((frame) => frame.includes('"type":"scene_changed"'))
        .length;

    // 1) 开局（旧路径拿世界 ID）→ 切成 structured_v1 + agent + BYOK → 重连
    await openLocalStartScreen(page, `${baseUrl}/?mode=local`);
    await page.locator(".module-select-trigger").click();
    await page.getByRole("option", { name: new RegExp(MODULE) }).click();
    await page.locator("#btn-start").click();
    await page.locator("#btn-character-confirm").click();
    await expect(page.locator("#user-input")).toBeEnabled({ timeout: 90_000 });
    const worldId = await page.evaluate(
      () => localStorage.getItem("trpg-active-world-id") || "",
    );
    expect(configureAgentWorld(worldId)).toContain("agent world ready");
    await page.reload();
    await expect(page.locator(".boot-loader")).toBeHidden({ timeout: 30_000 });
    await expect(page.getByTestId("structured-tool-row")).toBeVisible({
      timeout: 60_000,
    });
    const startScene = await page.locator(".header-scene-name").innerText();

    const say = async (text: string, label: string) => {
      const before = sceneChanged();
      await page.locator("#user-input").fill(text);
      await page.locator("#btn-send").click();
      // 等这一轮真正落定：queued/processing 都还在跑，必须等到终态或等待态。
      const settled = [
        "awaiting_player",
        "completed",
        "declined",
        "cancelled",
        "paused",
        "failed",
      ];
      const deadline = Date.now() + 420_000;
      let settledStatus = "";
      while (Date.now() < deadline) {
        const card = page.locator(".action-status-card").last();
        if (await card.count()) {
          settledStatus = (await card.getAttribute("data-status")) ?? "";
          if (settled.includes(settledStatus)) break;
        }
        await page.waitForTimeout(1000);
      }
      expect(
        settled.includes(settledStatus),
        `等待本轮落定超时，最后状态=${settledStatus}`,
      ).toBe(true);
      await expect(page.locator("#user-input")).toBeEnabled({
        timeout: 30_000,
      });
      const status =
        (await page
          .locator(".action-status-card")
          .last()
          .getAttribute("data-status")) ?? "";
      const scene = await page.locator(".header-scene-name").innerText();
      console.log(
        `[live] ${label}｜状态=${status}｜场景=${scene}｜scene_changed(${before}→${sceneChanged()})`,
      );
      await page.screenshot({
        path: `${screenshotsDir}/structured-transition-live-${label}.png`,
      });
      return { status, scene };
    };

    // 2) 意愿：只应得到叙事与等待，不能移动
    const t1 = await say("说实话，我想先看看莱特教授的尸体。", "t1-intent");
    expect(t1.scene).toBe(startScene);
    expect(sceneChanged()).toBe(0);
    expect(["awaiting_player", "paused", "completed"]).toContain(t1.status);
    // 叙事内容从 WS 帧取（不依赖 DOM 选择器）：最近一条已完成的公共消息。
    const narrations = received
      .filter((frame) => frame.includes('"type":"message_completed"'))
      .map((frame) => {
        const payload = (JSON.parse(frame) as { payload?: { text?: string } })
          .payload;
        return payload?.text ?? "";
      })
      .filter(Boolean);
    console.log(`[live] t1 叙事条数=${narrations.length}`);
    if (narrations.length) {
      console.log(`[live] t1 末条叙事：${narrations.at(-1)!.slice(0, 160)}`);
    }

    // 3) 追问：仍然不应该移动（平台不变量）
    const t2 = await say("那位值班医生和我们熟吗？", "t2-follow-up");
    expect(t2.scene).toBe(startScene);
    expect(sceneChanged()).toBe(0);

    // 4) 决定：允许模型移动；若移动必须是已提交命令（scene_changed 计数递增）
    const t3 = await say("那麻烦你联系一下，我现在过去。", "t3-decide");
    const changed = sceneChanged();
    if (changed > 0) {
      console.log(`[live] t3 由已提交命令改变场景 → ${t3.scene}`);
      expect(t3.scene).not.toBe(startScene);
    } else {
      console.log(`[live] t3 未移动（模型选择继续等待/澄清）→ ${t3.scene}`);
      expect(t3.scene).toBe(startScene);
    }
  });
});
