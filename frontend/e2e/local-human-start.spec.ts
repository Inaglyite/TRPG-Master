/** True user creation: no post-start metadata editing, no configured key/model. */
import { execFileSync, spawn, type ChildProcess } from "node:child_process";
import { createServer, type Server } from "node:http";
import { existsSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { expect, request, test } from "@playwright/test";
import { openLocalStartScreen } from "./readiness";
import { e2ePython } from "./python-runtime";

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
        TRPG_REQUIRE_AUTH: "0",
        TRPG_ALLOWED_ORIGINS: baseUrl,
        TRPG_WRITE_COMPAT_EXPORTS: "0",
        OPENAI_API_KEY: "",
        GLM_API_KEY: "",
        TRPG_ORDINARY_ROLLS_PER_MINUTE: "1",
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
  // Real local operator, no key: host dice use a typed command, not player free-roll.
  await page.getByTestId("btn-keeper-dice").click();
  await expect(page.getByRole("dialog", { name: "主持普通骰" })).toBeVisible();
  await page.getByTestId("keeper-dice-submit").click();
  await expect
    .poll(
      () =>
        received.filter((frame) => frame.type === "keeper_roll_resolved")
          .length,
    )
    .toBe(1);
  const localDice = received.find(
    (frame) => frame.type === "keeper_roll_resolved",
  ).payload;
  await expect(
    page.locator(
      `[data-testid="keeper-dice-receipt"][data-command-id="${localDice.command_id}"]`,
    ),
  ).toContainText("仅主持可见");
  expect(sent.find((frame) => frame.kind === "keeper_roll").payload).toEqual({
    spec: "1d100",
    visibility: "keeper",
  });
  // A new ordinary roll is rate-limited, not silently converted to player
  // dice or a fabricated result. Manual retries keep exactly the same ID.
  await page.getByTestId("btn-keeper-dice").click();
  await page.getByTestId("keeper-dice-submit").click();
  const rejectedDice = sent
    .filter((frame) => frame.kind === "keeper_roll")
    .at(-1);
  expect(rejectedDice.command_id).not.toBe(localDice.command_id);
  await expect
    .poll(() =>
      received.some(
        (frame) =>
          frame.type === "request_error" &&
          frame.payload?.command_id === rejectedDice.command_id &&
          frame.payload?.code === "rate_limited",
      ),
    )
    .toBe(true);
  const rateCard = page.locator(
    `[data-request-id="${rejectedDice.command_id}"]`,
  );
  await expect(rateCard).toContainText("稍后重试");
  await expect(rateCard).toContainText("不会自动重掷");
  await expect(rateCard).not.toContainText("需修正后重试");
  for (const viewport of [
    { width: 1280, height: 720 },
    { width: 939, height: 640 },
    { width: 640, height: 480 },
    { width: 390, height: 360 },
  ]) {
    await page.setViewportSize(viewport);
    const retry = rateCard.getByRole("button", { name: "重试（同一请求 ID）" });
    await retry.scrollIntoViewIfNeeded();
    const geometry = await retry.evaluate((node) => {
      const rect = node.getBoundingClientRect();
      const style = getComputedStyle(node);
      const dock = node.closest(".structured-dock")!.getBoundingClientRect();
      return {
        left: rect.left,
        right: rect.right,
        top: rect.top,
        bottom: rect.bottom,
        height: rect.height,
        padding: parseFloat(style.paddingLeft),
        nowrap: style.whiteSpace,
        dockTop: dock.top,
        dockBottom: dock.bottom,
        appTop: document.querySelector("#app")!.getBoundingClientRect().top,
        bodyScroll: document.body.scrollTop,
        bodyHeight: document.body.scrollHeight,
        rootScroll: document.documentElement.scrollTop,
        rootHeight: document.scrollingElement!.scrollHeight,
        hit: node.contains(
          document.elementFromPoint(
            rect.x + rect.width / 2,
            rect.y + rect.height / 2,
          ),
        ),
      };
    });
    expect(geometry.height).toBeGreaterThanOrEqual(44);
    expect(geometry.padding).toBeGreaterThanOrEqual(10);
    expect(geometry.nowrap).toBe("nowrap");
    expect(geometry.left).toBeGreaterThanOrEqual(0);
    expect(geometry.top).toBeGreaterThanOrEqual(0);
    expect(geometry.right).toBeLessThanOrEqual(viewport.width);
    expect(geometry.bottom).toBeLessThanOrEqual(viewport.height);
    expect(geometry.rootHeight).toBeLessThanOrEqual(viewport.height);
    expect(geometry.bodyScroll).toBe(0);
    expect(geometry.bodyHeight).toBeLessThanOrEqual(viewport.height);
    expect(geometry.rootScroll).toBe(0);
    expect(geometry.appTop).toBe(0);
    expect(geometry.top).toBeGreaterThanOrEqual(geometry.dockTop);
    expect(geometry.bottom).toBeLessThanOrEqual(geometry.dockBottom);
    expect(geometry.hit).toBe(true);
    await page.screenshot({
      path: `../docs/design/platform-ui/keeper-dice-rate-${viewport.width}.png`,
    });
  }
  const diceSentBeforeRetry = sent.filter(
    (frame) => frame.kind === "keeper_roll",
  ).length;
  await rateCard.getByRole("button", { name: "重试（同一请求 ID）" }).click();
  await expect
    .poll(() => sent.filter((frame) => frame.kind === "keeper_roll").length)
    .toBe(diceSentBeforeRetry + 1);
  expect(sent.filter((frame) => frame.kind === "keeper_roll").at(-1)).toEqual(
    rejectedDice,
  );
  await expect
    .poll(
      () =>
        received.filter(
          (frame) =>
            frame.type === "request_error" &&
            frame.payload?.command_id === rejectedDice.command_id,
        ).length,
    )
    .toBe(2);
  expect(
    received.filter((frame) => frame.type === "keeper_roll_resolved"),
  ).toHaveLength(1);
  expect(
    sent.filter((frame) => frame.type === "free_roll_request"),
  ).toHaveLength(0);
  await page.setViewportSize({ width: 1280, height: 900 });
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
  await expect(
    page.locator(
      `[data-testid="keeper-dice-receipt"][data-command-id="${localDice.command_id}"]`,
    ),
  ).toBeVisible();
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

test("本地模组卡片支持拖动、键盘动效和切换后安全删除", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  const pack = join(runtimeRoot, "local-module-order.trpgmod");
  execFileSync(
    e2ePython(repositoryRoot),
    ["tools/module_packager.py", "pack", "examples/module-template", pack],
    { cwd: repositoryRoot },
  );
  const installed = await page.request.post(baseUrl + "/api/modules/import", {
    data: readFileSync(pack),
    headers: { "X-Module-Filename": "local-module-order.trpgmod" },
  });
  expect(installed.status()).toBe(201);
  const title = "低语档案馆";
  const url = baseUrl + "/?mode=local";
  await openLocalStartScreen(page, url);
  await page.locator(".module-select-trigger").click();
  await page.getByRole("option", { name: title }).click();
  await expect(page.locator("#start-title")).toHaveText(title);
  const other = await page.context().newPage();
  await openLocalStartScreen(other, url);
  await expect(other.locator("#start-title")).toHaveText(title);
  await page.bringToFront();

  const openManager = () =>
    page.getByRole("button", { name: "模组管理", exact: true }).click();
  await openManager();
  const manager = page.getByRole("dialog", { name: "模组管理" });
  const cards = manager
    .getByRole("list", { name: "模组排序" })
    .getByRole("listitem");
  const grip = manager.getByRole("button", { name: /调整低语档案馆顺序/ });
  const save = manager.getByRole("button", { name: "保存排序" });
  await expect(grip).toBeEnabled();
  const from = (await grip.boundingBox())!;
  const last = (await cards.last().boundingBox())!;
  await page.mouse.move(from.x + 20, from.y + from.height / 2);
  await page.mouse.down();
  await page.mouse.move(last.x + 20, last.y + last.height - 10, { steps: 12 });
  await expect(page.locator(".cloud-module-lift")).toContainText(title);
  await expect(cards.last()).toHaveAttribute("data-card-shift", "up");
  await page.mouse.up();
  await expect(cards.last()).toContainText(title);

  // Record actual Web Animations calls so slow runners need not catch a 150ms frame.
  await page.evaluate(() => {
    document.body.dataset.cardAnimations = "0";
    const original = Element.prototype.animate;
    Element.prototype.animate = function (keyframes, options) {
      if (this.classList.contains("module-library-card")) {
        document.body.dataset.cardAnimations = String(
          Number(document.body.dataset.cardAnimations) + 1,
        );
      }
      return original.call(this, keyframes, options);
    };
  });
  await grip.press("ArrowDown");
  await expect(page.locator("body")).toHaveAttribute(
    "data-card-animations",
    "0",
  );
  await grip.press("ArrowUp");
  await expect(cards.nth(1)).toContainText(title);
  await expect(grip).toBeFocused();
  await expect(page.locator("body")).toHaveAttribute(
    "data-card-animations",
    "2",
  );

  await page.emulateMedia({ reducedMotion: "reduce" });
  await grip.press("ArrowDown");
  await expect(cards.last()).toContainText(title);
  await expect(grip).toBeFocused();
  await expect(page.locator("body")).toHaveAttribute(
    "data-card-animations",
    "2",
  );
  await save.click();
  await expect(manager.getByRole("status")).toHaveText("排序已保存");
  await page.reload();
  await openManager();
  await expect(cards.last()).toContainText(title);
  await expect(save).toBeDisabled();
  for (const width of [1280, 939, 640, 390]) {
    await page.setViewportSize({ width, height: width === 390 ? 360 : 800 });
    await expect(save).toBeInViewport();
    await expect(
      manager.getByRole("button", { name: "关闭模组管理" }),
    ).toBeInViewport();
    await page.screenshot({
      path: test.info().outputPath(`local-module-cards-${width}.png`),
    });
  }
  await page.setViewportSize({ width: 1280, height: 800 });

  // The first delete switches this window. The other window still keeps the package in use.
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await manager.getByRole("button", { name: "删除" + title }).click();
  await manager.getByRole("button", { name: "切换并删除模组及存档" }).click();
  await expect(manager.getByRole("alert")).toContainText("此模组正在使用");
  await expect(cards.last()).toContainText(title);
  await expect(page.locator("#start-title")).not.toHaveText(title);
  await other.close();

  await manager.getByRole("button", { name: "确认删除模组及存档" }).click();
  await expect(manager.getByRole("status")).toContainText(
    "已删除「" + title + "」",
  );
  await expect(manager.getByRole("heading", { name: title })).toHaveCount(0);
});
