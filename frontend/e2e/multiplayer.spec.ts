import { spawn, spawnSync, type ChildProcess } from "node:child_process";
import { existsSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

import {
  _electron as electron,
  expect,
  request,
  test,
  type Page,
} from "@playwright/test";

const port = 8767;
const externalBaseUrl = process.env.TRPG_E2E_EXTERNAL_BASE_URL?.replace(
  /\/+$/,
  "",
);
const baseUrl = externalBaseUrl ?? `https://127.0.0.1:${port}`;
const repositoryRoot = resolve(import.meta.dirname, "../..");
let runtimeRoot = "";
let server: ChildProcess | null = null;
let serverOutput = "";

async function waitForServer(): Promise<void> {
  const client = await request.newContext({ ignoreHTTPSErrors: true });
  try {
    for (let attempt = 0; attempt < 80; attempt += 1) {
      try {
        const response = await client.get(`${baseUrl}/api/health`);
        if (response.ok()) return;
      } catch {
        // Uvicorn 或 TLS 仍在启动。
      }
      await new Promise((resolveWait) => setTimeout(resolveWait, 125));
    }
  } finally {
    await client.dispose();
  }
  throw new Error(`E2E server did not start:\n${serverOutput.slice(-4000)}`);
}

async function waitForUrl(
  url: string,
  ignoreHTTPSErrors = false,
): Promise<void> {
  const client = await request.newContext({ ignoreHTTPSErrors });
  try {
    for (let attempt = 0; attempt < 80; attempt += 1) {
      try {
        const response = await client.get(url);
        if (response.ok()) return;
      } catch {
        // 子进程仍在启动。
      }
      await new Promise((resolveWait) => setTimeout(resolveWait, 125));
    }
  } finally {
    await client.dispose();
  }
  throw new Error(`Timed out waiting for ${url}`);
}

async function stopProcess(process: ChildProcess | null): Promise<void> {
  if (!process || process.exitCode !== null) return;
  process.kill("SIGTERM");
  await new Promise<void>((resolveWait) => {
    const timeout = setTimeout(resolveWait, 3000);
    process.once("exit", () => {
      clearTimeout(timeout);
      resolveWait();
    });
  });
  if (process.exitCode === null) process.kill("SIGKILL");
}

async function register(page: Page, username: string): Promise<void> {
  await page.goto(`${baseUrl}/?mode=online`);
  await page.getByRole("tab", { name: "注册" }).click();
  await page.getByLabel("用户名").fill(username);
  await page
    .getByLabel("密码", { exact: true })
    .fill("multiplayer test password");
  await page.getByLabel("确认密码").fill("multiplayer test password");
  await page.getByRole("button", { name: "注册并登录" }).click();
  await expect(page.getByRole("heading", { name: "联机大厅" })).toBeVisible();
}

function collectWebSocketFrames(page: Page): string[] {
  const frames: string[] = [];
  page.on("websocket", (socket) => {
    socket.on("framereceived", ({ payload }) => {
      frames.push(String(payload));
    });
  });
  return frames;
}

test.beforeAll(async () => {
  if (externalBaseUrl) {
    await waitForServer();
    return;
  }
  runtimeRoot = mkdtempSync(join(tmpdir(), "trpg-multiplayer-e2e-"));
  const certificate = join(runtimeRoot, "certificate.pem");
  const privateKey = join(runtimeRoot, "private-key.pem");
  const generated = spawnSync(
    "openssl",
    [
      "req",
      "-x509",
      "-newkey",
      "rsa:2048",
      "-nodes",
      "-days",
      "1",
      "-subj",
      "/CN=127.0.0.1",
      "-keyout",
      privateKey,
      "-out",
      certificate,
    ],
    { stdio: "ignore" },
  );
  if (generated.status !== 0) {
    throw new Error("Failed to generate the temporary E2E TLS certificate");
  }
  const repositoryPython = resolve(repositoryRoot, "venv/bin/python");
  server = spawn(
    process.env.TRPG_E2E_PYTHON ??
      (existsSync(repositoryPython) ? repositoryPython : "python"),
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
      "--ssl-certfile",
      certificate,
      "--ssl-keyfile",
      privateKey,
    ],
    {
      cwd: repositoryRoot,
      env: {
        ...process.env,
        TRPG_RUNTIME_ROOT: runtimeRoot,
        TRPG_DATABASE_URL: `sqlite:///${join(runtimeRoot, "e2e.db")}`,
        TRPG_REQUIRE_AUTH: "1",
        TRPG_ALLOW_REGISTRATION: "1",
        TRPG_ALLOWED_ORIGINS: baseUrl,
        TRPG_WRITE_COMPAT_EXPORTS: "0",
        TRPG_ROOM_IDLE_SECONDS: "0",
        OPENAI_API_KEY: "e2e-placeholder",
        OPENAI_BASE_URL: "https://127.0.0.1:9/v1",
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
  await waitForServer();
});

test.afterAll(async () => {
  if (externalBaseUrl) return;
  if (server && server.exitCode === null) {
    server.kill("SIGTERM");
    await new Promise<void>((resolveWait) => {
      const timeout = setTimeout(resolveWait, 3000);
      server?.once("exit", () => {
        clearTimeout(timeout);
        resolveWait();
      });
    });
    if (server.exitCode === null) server.kill("SIGKILL");
  }
  if (runtimeRoot) rmSync(runtimeRoot, { recursive: true, force: true });
});

test("两个真实浏览器完成建房、邀请、选角、恢复、隐私与开局", async ({
  browser,
}) => {
  const runId = externalBaseUrl ? `${Date.now()}` : "";
  const ownerUsername = `e2e_owner${runId}`;
  const playerUsername = `e2e_player${runId}`;
  const ownerContext = await browser.newContext({ ignoreHTTPSErrors: true });
  const playerContext = await browser.newContext({ ignoreHTTPSErrors: true });
  const owner = await ownerContext.newPage();
  const player = await playerContext.newPage();
  const ownerFrames = collectWebSocketFrames(owner);
  const playerFrames = collectWebSocketFrames(player);

  await register(owner, ownerUsername);
  await owner.getByLabel("房间名称").fill("双客户端验收房");
  await owner.getByRole("button", { name: "创建房间" }).click();
  await expect(
    owner.getByRole("heading", { name: "双客户端验收房" }),
  ).toBeVisible();
  await expect(owner.getByText("已连接", { exact: true })).toBeVisible();

  await owner.getByRole("button", { name: "选择" }).first().click();
  await expect(owner.getByRole("button", { name: "释放" })).toBeVisible();
  await owner.getByRole("button", { name: "生成邀请码" }).click();
  const inviteToken = (
    await owner.locator(".invite-token").textContent()
  )?.trim();
  expect(inviteToken).toBeTruthy();

  await register(player, playerUsername);
  await player
    .getByRole("textbox", { name: "邀请码", exact: true })
    .fill(inviteToken!);
  await player.getByRole("button", { name: "加入房间" }).click();
  await expect(
    player.getByRole("heading", { name: "双客户端验收房" }),
  ).toBeVisible();
  await expect(owner.getByText(playerUsername, { exact: true })).toBeVisible();

  await player.getByRole("button", { name: "选择" }).first().click();
  await expect(player.getByRole("button", { name: "释放" })).toBeVisible();
  await expect(
    owner.locator(".member-row", { hasText: playerUsername }),
  ).toContainText(/在线/);

  const worldIdText = await owner
    .locator(".online-subtitle")
    .first()
    .textContent();
  const worldId = worldIdText?.match(/world-[a-f0-9]+/)?.[0];
  expect(worldId).toBeTruthy();
  const privateNote = "E2E-PRIVATE-NOTE-7b369d";
  await player.evaluate(
    async ({ id, note }) => {
      const protocol = location.protocol === "https:" ? "wss:" : "ws:";
      const socket = new WebSocket(
        `${protocol}//${location.host}/ws/room?world_id=${encodeURIComponent(id)}`,
      );
      await new Promise<void>((resolveSocket, rejectSocket) => {
        const timer = window.setTimeout(
          () => rejectSocket(new Error("private note websocket timeout")),
          8000,
        );
        socket.onopen = () => {
          socket.send(
            JSON.stringify({
              type: "player_notes_update",
              text: note,
              revision: 0,
            }),
          );
        };
        socket.onmessage = (event) => {
          if (String(event.data).includes(note)) {
            window.clearTimeout(timer);
            socket.close();
            resolveSocket();
          }
        };
        socket.onerror = () => {
          window.clearTimeout(timer);
          rejectSocket(new Error("private note websocket failed"));
        };
      });
    },
    { id: worldId!, note: privateNote },
  );
  await expect
    .poll(() => playerFrames.some((frame) => frame.includes(privateNote)))
    .toBe(true);
  await new Promise((resolveWait) => setTimeout(resolveWait, 300));
  expect(ownerFrames.some((frame) => frame.includes(privateNote))).toBe(false);

  // 浏览器刷新后使用 Session + LAST_ROOM_KEY 自动回房，并恢复自己的私密状态。
  await player.reload();
  await expect(
    player.getByRole("heading", { name: "双客户端验收房" }),
  ).toBeVisible();
  await expect
    .poll(() => playerFrames.some((frame) => frame.includes(privateNote)))
    .toBe(true);
  expect(ownerFrames.some((frame) => frame.includes(privateNote))).toBe(false);

  await player.getByRole("button", { name: "准备" }).click();
  await owner.getByRole("button", { name: "准备" }).click();
  const start = owner.getByRole("button", { name: "开始游戏" });
  await expect(start).toBeEnabled();
  await start.click();

  await expect(owner.getByTestId("game-room-bar")).toBeVisible();
  await expect(player.getByTestId("game-room-bar")).toBeVisible();
  await expect(
    player.getByText(new RegExp(`等待 ${ownerUsername} 行动`)),
  ).toBeVisible();

  await ownerContext.close();
  await playerContext.close();
});

test("Electron 真实进程可在内置启动器与 HTTPS 多人页之间安全往返", async () => {
  test.skip(Boolean(externalBaseUrl), "外部 staging 验收只运行浏览器联机场景");
  const electronEnvironment = { ...process.env };
  delete electronEnvironment.ELECTRON_RUN_AS_NODE;
  delete electronEnvironment.NODE_ENV;
  const electronApp = await electron.launch({
    executablePath: resolve(
      repositoryRoot,
      "frontend/node_modules/electron/dist/electron",
    ),
    args: ["--ignore-certificate-errors", resolve(repositoryRoot, "frontend")],
    env: electronEnvironment,
  });
  try {
    const page = await electronApp.firstWindow();
    await expect(page.getByTestId("mode-select")).toBeVisible();
    await expect(page.getByText("单机游戏", { exact: true })).toBeVisible();
    await expect(page.getByText("多人游戏", { exact: true })).toBeVisible();
    await page.getByLabel("云端服务器地址").fill(baseUrl);
    await page.getByText("多人游戏", { exact: true }).click();

    await page.waitForURL(`${baseUrl}/?mode=online`);
    await expect(page.getByRole("heading", { name: "多人游戏" })).toBeVisible();
    await page.getByRole("button", { name: "← 返回模式选择" }).click();

    await page.waitForURL(/^file:.*\/dist\/index\.html$/);
    await expect(page.getByTestId("mode-select")).toBeVisible();
  } finally {
    await electronApp.evaluate(({ app }) => app.exit(0));
  }
});

test("Electron 开发进程选择单机后连接真实本地后端", async () => {
  test.skip(Boolean(externalBaseUrl), "外部 staging 验收只运行浏览器联机场景");
  const localRuntime = mkdtempSync(join(tmpdir(), "trpg-local-e2e-"));
  const repositoryPython = resolve(repositoryRoot, "venv/bin/python");
  const python =
    process.env.TRPG_E2E_PYTHON ??
    (existsSync(repositoryPython) ? repositoryPython : "python");
  const localServer = spawn(
    python,
    [
      "-m",
      "uvicorn",
      "server:app",
      "--host",
      "127.0.0.1",
      "--port",
      "8765",
      "--workers",
      "1",
    ],
    {
      cwd: repositoryRoot,
      env: {
        ...process.env,
        TRPG_RUNTIME_ROOT: localRuntime,
        TRPG_DATABASE_URL: `sqlite:///${join(localRuntime, "local.db")}`,
        TRPG_REQUIRE_AUTH: "0",
        TRPG_WRITE_COMPAT_EXPORTS: "0",
        TRPG_ROOM_IDLE_SECONDS: "0",
      },
      stdio: "ignore",
    },
  );
  const vite = spawn(
    resolve(repositoryRoot, "frontend/node_modules/.bin/vite"),
    ["--host", "127.0.0.1", "--port", "5173"],
    {
      cwd: resolve(repositoryRoot, "frontend"),
      env: process.env,
      stdio: "ignore",
    },
  );
  let electronApp: Awaited<ReturnType<typeof electron.launch>> | null = null;
  try {
    await Promise.all([
      waitForUrl("http://127.0.0.1:8765/api/health"),
      waitForUrl("http://127.0.0.1:5173/"),
    ]);
    const electronEnvironment = {
      ...process.env,
      NODE_ENV: "dev",
      VITE_DEV_SERVER_URL: "http://127.0.0.1:5173",
    };
    delete electronEnvironment.ELECTRON_RUN_AS_NODE;
    electronApp = await electron.launch({
      executablePath: resolve(
        repositoryRoot,
        "frontend/node_modules/electron/dist/electron",
      ),
      args: [resolve(repositoryRoot, "frontend")],
      env: electronEnvironment,
    });
    const page =
      electronApp
        .windows()
        .find((candidate) =>
          candidate.url().startsWith("http://127.0.0.1:5173"),
        ) ??
      (await electronApp.waitForEvent("window", {
        predicate: (candidate) =>
          candidate.url().startsWith("http://127.0.0.1:5173"),
      }));
    await expect(page.getByTestId("mode-select")).toBeVisible();
    await page.getByText("单机游戏", { exact: true }).click();
    await expect(page.getByTestId("mode-select")).toBeHidden();
    await expect(page.locator("#btn-start")).toBeVisible();
    await expect(page.getByText("已连接到守秘人……")).toBeVisible();
  } finally {
    if (electronApp) {
      await electronApp.evaluate(({ app }) => app.exit(0));
    }
    await Promise.all([stopProcess(vite), stopProcess(localServer)]);
    rmSync(localRuntime, { recursive: true, force: true });
  }
});
