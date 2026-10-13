/** Real local backend, human mode, no model calls; private Markdown note flow. */
import { spawn, type ChildProcess } from "node:child_process";
import { mkdirSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { expect, request, test } from "@playwright/test";
import { openLocalStartScreen } from "./readiness";

const repositoryRoot = resolve(import.meta.dirname, "../..");
const baseUrl = "http://127.0.0.1:8765";
const artifacts =
  process.env.TRPG_E2E_ARTIFACT_ROOT ?? join(tmpdir(), "trpg-keeper-notes");
let runtimeRoot = "";
let server: ChildProcess | null = null;
let output = "";

test.beforeAll(async () => {
  mkdirSync(artifacts, { recursive: true, mode: 0o700 });
  runtimeRoot = mkdtempSync(join(artifacts, "runtime-"));
  server = spawn(
    process.env.TRPG_E2E_PYTHON ?? "python",
    ["-m", "uvicorn", "server:app", "--host", "127.0.0.1", "--port", "8765"],
    {
      cwd: repositoryRoot,
      env: {
        ...process.env,
        NO_PROXY: "127.0.0.1,localhost",
        TRPG_RUNTIME_ROOT: runtimeRoot,
        TRPG_DATABASE_URL: `sqlite:///${join(runtimeRoot, "notes.db")}`,
        TRPG_AUTH_REQUIRED: "0",
        TRPG_ALLOWED_ORIGINS: baseUrl,
        TRPG_WRITE_COMPAT_EXPORTS: "0",
        OPENAI_API_KEY: "",
        GLM_API_KEY: "",
        OPENAI_BASE_URL: "http://127.0.0.1:9/v1",
      },
      stdio: ["ignore", "pipe", "pipe"],
    },
  );
  server.stdout?.on("data", (chunk) => {
    output += String(chunk);
  });
  server.stderr?.on("data", (chunk) => {
    output += String(chunk);
  });
  const client = await request.newContext();
  try {
    for (let i = 0; i < 100; i++) {
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
  throw new Error(`notes backend did not start: ${output.slice(-2500)}`);
});

test.afterAll(async () => {
  if (server && server.exitCode === null) {
    server.kill("SIGTERM");
    await new Promise<void>((done) => {
      const timer = setTimeout(done, 3000);
      server!.once("exit", () => {
        clearTimeout(timer);
        done();
      });
    });
    if (server.exitCode === null) server.kill("SIGKILL");
  }
  if (runtimeRoot) rmSync(runtimeRoot, { recursive: true, force: true });
});

test("主持笔记：新建→三宽度→私有资料读回→更正→刷新后恢复", async ({ page }) => {
  test.setTimeout(150_000);
  const sent: Record<string, any>[] = [];
  const received: Record<string, any>[] = [];
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
  await page.getByRole("button", { name: "游玩方式：经典 AI 叙事" }).click();
  const mode = page.getByRole("dialog", { name: "游玩方式" });
  await mode.getByRole("radio", { name: "人类主持" }).click();
  await mode.getByRole("button", { name: "关闭游玩方式" }).click();
  await page.locator("#btn-character-confirm").click();
  await expect(page.getByTestId("btn-keeper-console")).toBeVisible({
    timeout: 60000,
  });
  await page.getByTestId("btn-keeper-console").click();
  const console = page.getByRole("dialog", { name: "主持工作台" });
  await console
    .getByRole("button", { name: "保存主持笔记", exact: true })
    .click();
  const fields: Record<string, string> = {
    note_id: "fallon-promise",
    expected_note_revision: "0",
    title: "法伦的承诺",
    summary: "明日同行，尚未出发",
    content: "## 私有承诺\n法伦答应明日陪同，尚未执行。",
    category: "promise",
    status: "open",
  };
  for (const [name, value] of Object.entries(fields)) {
    const control = console.locator(
      `[data-field="${name}"] input, [data-field="${name}"] textarea, [data-field="${name}"] select`,
    );
    if ((await control.evaluate((node) => node.tagName)) === "SELECT")
      await control.selectOption(value);
    else await control.fill(value);
  }
  for (const width of [1280, 939, 640]) {
    await page.setViewportSize({ width, height: 900 });
    await expect(page.locator(".boot-loader")).toHaveCount(0);
    await page.waitForTimeout(1000);
    const submit = console.getByTestId("keeper-submit");
    await submit.scrollIntoViewIfNeeded();
    const shape = await submit.evaluate((node) => ({
      padding: parseFloat(getComputedStyle(node).paddingLeft),
      nowrap: getComputedStyle(node).whiteSpace,
      fits: node.scrollWidth <= node.clientWidth + 1,
      height: node.getBoundingClientRect().height,
    }));
    expect(shape.padding).toBeGreaterThan(0);
    expect(shape.nowrap).toBe("nowrap");
    expect(shape.fits).toBe(true);
    expect(shape.height).toBeGreaterThanOrEqual(28);
    await page.screenshot({
      path: join(artifacts, `keeper-notes-${width}.png`),
      fullPage: true,
    });
  }
  await console.getByTestId("keeper-submit").click();
  await expect
    .poll(() =>
      received.some(
        (f) =>
          f.type === "action_status" &&
          sent.some(
            (s) =>
              s.kind === "record_keeper_note" &&
              s.command_id === f.payload?.request_id &&
              f.payload?.status === "completed",
          ),
      ),
    )
    .toBe(true);
  const command = sent.find((f) => f.kind === "record_keeper_note")!;
  expect(command.payload.expected_note_revision).toBe(0);
  const world = command.world_id;
  const guide = await page.request.get(
    `${baseUrl}/api/worlds/${world}/keeper-guide`,
  );
  const document = (await guide.json()).documents.find(
    (d: any) => d.id === "keeper-note:fallon-promise",
  );
  expect(document.text).toContain("当前版本：1");
  expect(document.text).toContain(fields.content);
  await console.getByRole("button", { name: /打开资料库/ }).click();
  await console.getByRole("button", { name: "主持笔记", exact: true }).click();
  await console.getByRole("button", { name: /主持笔记 · 法伦的承诺/ }).click();
  await expect(
    console.getByRole("article", { name: "资料预览" }),
  ).toContainText("当前版本：1");
  await expect(
    console.getByRole("article", { name: "资料预览" }),
  ).toContainText("尚未执行");
  for (const width of [1280, 939, 640]) {
    await page.setViewportSize({ width, height: 900 });
    const refresh = console.getByRole("button", { name: "刷新笔记" });
    await refresh.scrollIntoViewIfNeeded();
    await page.waitForTimeout(1000);
    const shape = await refresh.evaluate((node) => ({
      padding: parseFloat(getComputedStyle(node).paddingLeft),
      nowrap: getComputedStyle(node).whiteSpace,
      fits: node.scrollWidth <= node.clientWidth + 1,
      height: node.getBoundingClientRect().height,
    }));
    expect(shape.padding).toBeGreaterThan(0);
    expect(shape.nowrap).toBe("nowrap");
    expect(shape.fits).toBe(true);
    expect(shape.height).toBeGreaterThanOrEqual(28);
    await page.screenshot({
      path: join(artifacts, `keeper-note-library-${width}.png`),
      fullPage: true,
    });
  }
  await console
    .locator('[data-field="expected_note_revision"] input')
    .fill("1");
  await console
    .locator('[data-field="content"] textarea')
    .fill("## 更正\n法伦改约后日同行，仍未执行。");
  await console.getByTestId("keeper-submit").click();
  await expect
    .poll(async () => {
      const response = await page.request.post(
        `${baseUrl}/api/worlds/${world}/keeper-knowledge`,
        { data: { kind: "notes", source_id: "fallon-promise" } },
      );
      return JSON.parse((await response.json()).content).note_revision;
    })
    .toBe(2);
  await page.reload();
  await expect(page.getByTestId("btn-keeper-console")).toBeVisible({
    timeout: 60000,
  });
  const response = await page.request.post(
    `${baseUrl}/api/worlds/${world}/keeper-knowledge`,
    { data: { kind: "notes", source_id: "fallon-promise" } },
  );
  expect(JSON.parse((await response.json()).content).content).toContain(
    "后日同行",
  );
  expect(output).not.toContain("agent 模型调用");
});
