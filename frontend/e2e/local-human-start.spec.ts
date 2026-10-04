/** True user creation: no post-start metadata editing, no configured key/model. */
import { spawn, type ChildProcess } from "node:child_process";
import { createServer, type Server } from "node:http";
import { existsSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { expect, request, test } from "@playwright/test";
import { openLocalStartScreen } from "./readiness";

const repositoryRoot = resolve(import.meta.dirname, "../..");
const baseUrl = "http://127.0.0.1:8817";
let runtimeRoot = "";
let server: ChildProcess | null = null;
let modelServer: Server | null = null;
let modelRequests = 0;
let serverOutput = "";

test.beforeAll(async () => {
  runtimeRoot = mkdtempSync(join(tmpdir(), "trpg-local-human-create-"));
  modelServer = createServer((_incoming, response) => {
    modelRequests++;
    response.writeHead(500).end("human creation must not call a model");
  });
  await new Promise<void>((done) => modelServer!.listen(0, "127.0.0.1", done));
  const address = modelServer.address();
  if (!address || typeof address === "string")
    throw new Error("model trap unavailable");
  const python =
    process.env.TRPG_E2E_PYTHON ??
    (existsSync(resolve(repositoryRoot, ".venv/bin/python"))
      ? resolve(repositoryRoot, ".venv/bin/python")
      : "python");
  server = spawn(
    python,
    [
      "-m",
      "uvicorn",
      "server:app",
      "--host",
      "127.0.0.1",
      "--port",
      "8817",
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
        TRPG_AUTH_REQUIRED: "0",
        TRPG_ALLOWED_ORIGINS: baseUrl,
        TRPG_WRITE_COMPAT_EXPORTS: "0",
        OPENAI_API_KEY: "",
        GLM_API_KEY: "",
        OPENAI_BASE_URL: `http://127.0.0.1:${address.port}/v1`,
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
  const client = await request.newContext();
  try {
    const deadline = Date.now() + 25_000;
    while (Date.now() < deadline) {
      if (server.exitCode !== null) break;
      try {
        if ((await client.get(`${baseUrl}/api/health`)).ok()) return;
      } catch {
        /* starting */
      }
      await new Promise((done) => setTimeout(done, 125));
    }
  } finally {
    await client.dispose();
  }
  throw new Error(`local server startup failed: ${serverOutput.slice(-2500)}`);
});

test.afterAll(async () => {
  if (server && server.exitCode === null) {
    server.kill("SIGTERM");
    await new Promise<void>((done) => {
      const deadline = setTimeout(done, 3000);
      server!.once("exit", () => {
        clearTimeout(deadline);
        done();
      });
    });
    if (server.exitCode === null) server.kill("SIGKILL");
  }
  if (modelServer)
    await new Promise<void>((done) => modelServer!.close(() => done()));
  if (runtimeRoot) rmSync(runtimeRoot, { recursive: true, force: true });
});

test("本地人类主持：四窗口选择→无Key真实新建→发言→刷新→另建，零模型", async ({
  page,
}) => {
  test.setTimeout(150_000);
  const sent: any[] = [];
  const received: any[] = [];
  page.on("websocket", (socket) => {
    socket.on("framesent", (event) => {
      try {
        sent.push(JSON.parse(String(event.payload)));
      } catch {
        /* binary */
      }
    });
    socket.on("framereceived", (event) => {
      try {
        received.push(JSON.parse(String(event.payload)));
      } catch {
        /* binary */
      }
    });
  });
  await openLocalStartScreen(page, `${baseUrl}/?mode=local`);
  await page.locator(".module-select-trigger").click();
  await page.getByRole("option", { name: /猩红文档/ }).click();
  await page.locator("#btn-start").click();
  let selectedName = await page
    .locator(".character-card.selected .character-card-name")
    .innerText();
  await page.getByRole("button", { name: "游玩方式：经典 AI 叙事" }).click();
  const dialog = page.getByRole("dialog", { name: "游玩方式" });
  await dialog.getByRole("radio", { name: "人类主持" }).click();
  await expect(dialog).toContainText("不调用模型");
  await expect(dialog).toContainText("单人人类主持由你兼任");
  for (const viewport of [
    { width: 1280, height: 720 },
    { width: 939, height: 640 },
    { width: 640, height: 480 },
    { width: 390, height: 360 },
  ]) {
    await page.setViewportSize(viewport);
    await page.waitForTimeout(500);
    const reading = dialog.locator(".panel-action-body");
    expect(
      await reading.evaluate((node) => node.clientHeight),
    ).toBeGreaterThanOrEqual(100);
    await dialog
      .getByRole("radio", { name: "人类主持" })
      .scrollIntoViewIfNeeded();
    const close = dialog.getByRole("button", { name: "关闭游玩方式" });
    const geometry = await close.evaluate((node) => {
      const box = node.getBoundingClientRect();
      const hit = document.elementFromPoint(
        box.x + box.width / 2,
        box.y + box.height / 2,
      );
      return {
        height: box.height,
        width: box.width,
        hit: node.contains(hit),
        bottom: box.bottom,
      };
    });
    expect(geometry.height).toBeGreaterThanOrEqual(44);
    expect(geometry.width).toBeGreaterThanOrEqual(44);
    expect(geometry.hit).toBe(true);
    expect(geometry.bottom).toBeLessThanOrEqual(viewport.height);
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth + 1,
      ),
    ).toBe(true);
    await page.screenshot({
      path: `/tmp/trpg-local-start-mode-${viewport.width}.png`,
    });
  }
  await dialog.getByRole("button", { name: "关闭游玩方式" }).click();
  await expect(
    page.getByRole("button", { name: "游玩方式：人类主持" }),
  ).toBeFocused();
  for (const viewport of [
    { width: 1280, height: 720 },
    { width: 939, height: 640 },
    { width: 640, height: 480 },
    { width: 390, height: 360 },
  ]) {
    await page.setViewportSize(viewport);
    await page.waitForTimeout(500);
    for (const selector of [
      ".local-play-style-trigger",
      "#btn-character-confirm",
    ]) {
      const box = await page.locator(selector).evaluate((node) => {
        const rect = node.getBoundingClientRect();
        return {
          height: rect.height,
          right: rect.right,
          left: rect.left,
          bottom: rect.bottom,
          hit: node.contains(
            document.elementFromPoint(
              rect.x + rect.width / 2,
              rect.y + rect.height / 2,
            ),
          ),
          padding: parseFloat(getComputedStyle(node).paddingLeft),
          nowrap: getComputedStyle(node).whiteSpace,
        };
      });
      expect(box.height).toBeGreaterThanOrEqual(44);
      expect(box.padding).toBeGreaterThanOrEqual(10);
      expect(box.nowrap).toBe("nowrap");
      expect(box.left).toBeGreaterThanOrEqual(0);
      expect(box.right).toBeLessThanOrEqual(viewport.width);
      expect(box.bottom).toBeLessThanOrEqual(viewport.height);
      expect(box.hit).toBe(true);
    }
    await page.screenshot({
      path: `/tmp/trpg-local-start-footer-${viewport.width}.png`,
    });
  }
  const roster = page.locator("#character-choice-list");
  await roster.evaluate((node) => {
    node.scrollTop = 0;
  });
  const firstCard = page.locator(".character-card").first();
  const firstVisible = await firstCard.evaluate((node) => {
    const rect = node.getBoundingClientRect();
    const list = node
      .closest("#character-choice-list")!
      .getBoundingClientRect();
    return {
      top: rect.top,
      bottom: rect.bottom,
      listTop: list.top,
      listBottom: list.bottom,
    };
  });
  expect(firstVisible.top).toBeGreaterThanOrEqual(firstVisible.listTop);
  expect(firstVisible.bottom).toBeLessThanOrEqual(firstVisible.listBottom);
  const alternative = page.locator(".character-card").nth(1);
  await alternative.scrollIntoViewIfNeeded();
  await alternative.click();
  await expect(alternative).toHaveAttribute("aria-pressed", "true");
  selectedName = await alternative.locator(".character-card-name").innerText();
  await expect(page.locator(".character-detail-identity h3")).toHaveText(
    selectedName,
  );
  await page.screenshot({ path: "/tmp/trpg-local-character-choice-390.png" });
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.locator("#btn-character-confirm").click();
  await expect(page.getByTestId("structured-tool-row")).toBeVisible({
    timeout: 30_000,
  });
  await expect(page.locator("#char-name")).toHaveText(selectedName);
  const creation = sent.find((frame) => frame.type === "local_start");
  expect(creation).toMatchObject({
    execution_profile: "structured_v1",
    keeper_mode: "human",
  });
  const result = received.find(
    (frame) => frame.type === "local_start_result" && frame.ok,
  );
  expect(result.request_id).toBe(creation.request_id);
  expect(result.world_id).not.toBe(creation.source_world_id);
  expect(
    received.filter((frame) => frame.type === "gm_turn_start"),
  ).toHaveLength(0);
  expect(modelRequests).toBe(0);
  await page.getByTestId("btn-keeper-console").click();
  await page
    .locator('[data-field="text"] textarea')
    .fill("本地无模型开局验收：法伦把档案推过桌面。");
  await page.getByTestId("keeper-submit").click();
  await expect(page.locator(".keeper-feedback")).toContainText(
    "服务端已确认提交",
  );
  await page.getByRole("button", { name: "关闭主持台" }).click();
  await expect(page.locator("#messages")).toContainText("本地无模型开局验收");
  await page.reload();
  await expect(page.getByTestId("structured-tool-row")).toBeVisible({
    timeout: 30_000,
  });
  await expect(page.locator("#messages")).toContainText("本地无模型开局验收");
  await page.locator("#btn-new").click();
  await page.locator("#btn-start").click();
  const chooser = page.getByRole("button", { name: /^游玩方式：/ });
  await chooser.click();
  await page.getByRole("radio", { name: "人类主持" }).click();
  await page.getByRole("button", { name: "关闭游玩方式" }).click();
  await page.locator("#btn-character-confirm").click();
  await expect
    .poll(
      () =>
        received.filter(
          (frame) => frame.type === "local_start_result" && frame.ok,
        ).length,
    )
    .toBe(2);
  const second = received
    .filter((frame) => frame.type === "local_start_result" && frame.ok)
    .at(-1);
  expect(second.world_id).not.toBe(result.world_id);
  await expect(page.locator("#messages")).not.toContainText(
    "本地无模型开局验收",
  );
  expect(modelRequests).toBe(0);
  expect(sent.filter((frame) => frame.type === "start")).toHaveLength(0);
});
