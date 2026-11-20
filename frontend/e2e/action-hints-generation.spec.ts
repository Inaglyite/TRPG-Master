/** Real local backend, loopback-only provider stub, no paid model calls. */
import { spawn, type ChildProcess } from "node:child_process";
import { createServer, type Server } from "node:http";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { expect, request, test } from "@playwright/test";
import { e2ePython } from "./python-runtime";
import { openLocalStartScreen } from "./readiness";

const repositoryRoot = resolve(import.meta.dirname, "../..");
const baseUrl = "http://127.0.0.1:8878";
let backend: ChildProcess | null = null;
let provider: Server | null = null;
let runtimeRoot = "";
let output = "";
const requests: any[] = [];

test.beforeAll(async () => {
  runtimeRoot = mkdtempSync(join(tmpdir(), "trpg-hints-generation-"));
  provider = createServer((incoming, response) => {
    if (incoming.method !== "POST") {
      response.writeHead(404).end();
      return;
    }
    let body = "";
    incoming.on("data", (chunk) => (body += String(chunk)));
    incoming.on("end", () => {
      const request = JSON.parse(body);
      requests.push(request);
      // Deliberately ignore the instruction to prove server-side suppression too.
      const content = JSON.stringify({
        segments: [
          { kind: "narration", text: "屋内只剩雨声。你合上手边的空白笔记。" },
        ],
        choices: Array.from(
          { length: 8 },
          (_, index) => `${index}号建议：${"观察窗沿".repeat(30)}`,
        ),
      });
      if (request.stream === false) {
        response.writeHead(200, { "Content-Type": "application/json" });
        response.end(
          JSON.stringify({
            choices: [{ finish_reason: "stop", message: { content } }],
          }),
        );
        return;
      }
      response.writeHead(200, { "Content-Type": "text/event-stream" });
      for (const choice of [
        {
          index: 0,
          delta: { role: "assistant", content },
          finish_reason: null,
        },
        { index: 0, delta: {}, finish_reason: "stop" },
      ])
        response.write(
          `data: ${JSON.stringify({ id: "hints-stub", object: "chat.completion.chunk", model: "e2e-hints", choices: [choice] })}\n\n`,
        );
      response.end("data: [DONE]\n\n");
    });
  });
  await new Promise<void>((done) => provider!.listen(0, "127.0.0.1", done));
  const address = provider.address();
  if (!address || typeof address === "string")
    throw new Error("provider not started");
  const modelUrl = `http://127.0.0.1:${address.port}/v1`;
  backend = spawn(
    e2ePython(repositoryRoot),
    [
      "-m",
      "uvicorn",
      "server:app",
      "--host",
      "127.0.0.1",
      "--port",
      "8878",
      "--workers",
      "1",
    ],
    {
      cwd: repositoryRoot,
      env: {
        ...Object.fromEntries(
          Object.entries(process.env).filter(
            ([key]) =>
              !/^(TRPG_|OPENAI_|GLM_|http_proxy$|https_proxy$|all_proxy$)/i.test(
                key,
              ),
          ),
        ),
        NO_PROXY: "127.0.0.1,localhost",
        TRPG_RUNTIME_ROOT: runtimeRoot,
        TRPG_DATABASE_URL: `sqlite:///${join(runtimeRoot, "hints.db")}`,
        TRPG_LOG_DIR: join(runtimeRoot, "logs"),
        TRPG_REQUIRE_AUTH: "0",
        TRPG_ALLOWED_ORIGINS: baseUrl,
        TRPG_WRITE_COMPAT_EXPORTS: "0",
        OPENAI_API_KEY: "e2e-not-a-real-key",
        OPENAI_BASE_URL: modelUrl,
        GLM_API_KEY: "",
        GLM_BASE_URL: modelUrl,
        TRPG_NARRATIVE_MODEL: "e2e-hints",
        TRPG_JUDGEMENT_MODEL: "e2e-hints",
        TRPG_STREAM_USAGE: "0",
      },
      stdio: ["ignore", "pipe", "pipe"],
    },
  );
  backend.stdout?.on("data", (chunk) => (output += String(chunk)));
  backend.stderr?.on("data", (chunk) => (output += String(chunk)));
  const client = await request.newContext();
  try {
    await expect
      .poll(
        async () => {
          if (backend?.exitCode !== null) throw new Error(output.slice(-2500));
          try {
            return (await client.get(`${baseUrl}/api/ready`)).ok();
          } catch {
            return false;
          }
        },
        { timeout: 30_000 },
      )
      .toBe(true);
  } finally {
    await client.dispose();
  }
});

test.afterAll(async () => {
  if (backend && backend.exitCode === null) {
    backend.kill("SIGTERM");
    await new Promise<void>((done) => {
      const timer = setTimeout(done, 3000);
      backend!.once("exit", () => {
        clearTimeout(timer);
        done();
      });
    });
    if (backend.exitCode === null) backend.kill("SIGKILL");
  }
  if (provider)
    await new Promise<void>((done) => provider!.close(() => done()));
  if (runtimeRoot) rmSync(runtimeRoot, { recursive: true, force: true });
});

test("关闭提示：真实开场请求禁用建议，违规选项不下发且输入及时恢复", async ({
  page,
}, testInfo) => {
  const received: any[] = [];
  const sent: any[] = [];
  page.on("websocket", (socket) => {
    socket.on("framesent", ({ payload }) => {
      try {
        sent.push(JSON.parse(String(payload)));
      } catch {
        /* binary */
      }
    });
    socket.on("framereceived", ({ payload }) => {
      try {
        received.push(JSON.parse(String(payload)));
      } catch {
        /* binary */
      }
    });
  });
  await page.addInitScript(() =>
    localStorage.setItem("trpg-show-action-hints", "false"),
  );
  await openLocalStartScreen(page, `${baseUrl}/?mode=local`);
  await page.locator("#btn-start").click();
  await page.locator("#btn-character-confirm").click();
  await expect
    .poll(() => received.some((frame) => frame.type === "done"), {
      timeout: 30_000,
    })
    .toBe(true);
  await expect(page.locator("#user-input")).toBeEnabled({ timeout: 5000 });
  expect(sent.find((frame) => frame.type === "start")).toMatchObject({
    generate_action_hints: false,
  });
  expect(
    requests.some((r) =>
      r.messages.some((m: any) =>
        String(m.content).includes("本轮玩家已关闭行动提示"),
      ),
    ),
  ).toBe(true);
  expect(
    received.filter((frame) => frame.type === "choices").at(-1)?.choices,
  ).toEqual([]);
  await expect(page.locator("#messages")).not.toContainText("你可以——");
  await expect(page.locator("#options-bar .opt-btn")).toHaveCount(0);
  await page.locator("#user-input").fill("我想自行调查窗外的情况");
  await page.waitForTimeout(500);
  await expect(page.locator("#user-input")).toHaveValue(
    "我想自行调查窗外的情况",
  );
  await expect(page.locator("#user-input")).toBeFocused();
  await page.screenshot({
    path: testInfo.outputPath("action-hints-disabled.png"),
  });
  await testInfo.attach("local-backend.log", {
    body: output,
    contentType: "text/plain",
  });
});
