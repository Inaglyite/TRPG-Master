import { spawn, spawnSync, type ChildProcess } from "node:child_process";
import { createHash, randomUUID, X509Certificate } from "node:crypto";
import {
  existsSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { createServer, type Server } from "node:http";
import { createConnection, isIP } from "node:net";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { connect as connectTls } from "node:tls";

import {
  _electron as electron,
  expect,
  request,
  test,
  type Locator,
  type Page,
} from "@playwright/test";

const port = 8767;
const externalBaseUrl = process.env.TRPG_E2E_EXTERNAL_BASE_URL?.replace(
  /\/+$/,
  "",
);
const baseUrl = externalBaseUrl ?? `https://127.0.0.1:${port}`;
// 每次运行都使用唯一后缀；本地后端的 SQLite 数据可能跨重跑保留，不能复用
// 固定的 e2e_owner/e2e_player 用户名。外部 staging 也使用同一后缀，避免并发运行互撞。
const runId = `${Date.now()}-${randomUUID().slice(0, 8)}`;
const repositoryRoot = resolve(import.meta.dirname, "../..");
let runtimeRoot = "";
let modelBaseUrl = "";
let tlsSpkiFingerprint = "";
let server: ChildProcess | null = null;
let serverOutput = "";
let modelServer: Server | null = null;
type ElectronApplication = Awaited<ReturnType<typeof electron.launch>>;

async function startModelStub(): Promise<string> {
  modelServer = createServer((incoming, response) => {
    if (incoming.method !== "POST" || incoming.url !== "/v1/chat/completions") {
      response.writeHead(404).end();
      return;
    }
    let body = "";
    incoming.on("data", (chunk) => {
      body += String(chunk);
    });
    incoming.once("end", () => {
      // 行动类请求延迟响应，制造确定的“回合进行中”窗口，供并发拒绝断言使用；
      // 其余请求（开场等）立即返回，避免拖慢整体用例。
      const delayMs = body.includes("我检查门锁和附近的脚印") ? 4000 : 0;
      setTimeout(() => {
        if (response.destroyed) return;
        const content =
          "雨幕笼罩着阿卡姆，你在约定的办公室里见到了等待已久的委托人。" +
          "他把一份尚未拆封的档案推到桌边，示意你先听完事情的来龙去脉。" +
          "\n\n**你可以——**\n1. 请他说明委托\n2. 观察办公室" +
          "\n3. 检查档案封面\n4. [自由行动] 你决定做什么？";
        const chunk = {
          id: "chatcmpl-e2e-opening",
          object: "chat.completion.chunk",
          created: 1,
          model: "e2e-model",
          choices: [
            {
              index: 0,
              delta: { role: "assistant", content },
              finish_reason: null,
            },
          ],
        };
        const terminal = {
          ...chunk,
          choices: [{ index: 0, delta: {}, finish_reason: "stop" }],
        };
        response.writeHead(200, {
          "Content-Type": "text/event-stream; charset=utf-8",
          "Cache-Control": "no-cache",
          Connection: "close",
        });
        response.write(`data: ${JSON.stringify(chunk)}\n\n`);
        response.write(`data: ${JSON.stringify(terminal)}\n\n`);
        response.end("data: [DONE]\n\n");
      }, delayMs);
    });
  });
  await new Promise<void>((resolveListen, rejectListen) => {
    modelServer!.once("error", rejectListen);
    modelServer!.listen(0, "127.0.0.1", () => resolveListen());
  });
  const address = modelServer.address();
  if (!address || typeof address === "string") {
    throw new Error("E2E model stub did not expose a TCP address");
  }
  return `http://127.0.0.1:${address.port}/v1`;
}

async function stopModelStub(): Promise<void> {
  if (!modelServer) return;
  await new Promise<void>((resolveClose) =>
    modelServer!.close(() => resolveClose()),
  );
  modelServer = null;
}

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

function certificateSpkiFingerprint(certificate: Buffer | string): string {
  const x509 = new X509Certificate(certificate);
  const spki = x509.publicKey.export({ type: "spki", format: "der" });
  return createHash("sha256").update(spki).digest("base64");
}

/** 从线上服务器证书计算 SPKI 指纹（供 Electron 按证书钉住，而非全局放行）。 */
async function fetchRemoteSpkiFingerprint(url: string): Promise<string | null> {
  const { hostname, port: rawPort } = new URL(url);
  const remotePort = Number(rawPort || "443");
  return await new Promise((resolveFingerprint) => {
    let settled = false;
    const finish = (value: string | null) => {
      if (settled) return;
      settled = true;
      resolveFingerprint(value);
    };
    const socket = connectTls({
      host: hostname,
      port: remotePort,
      ...(isIP(hostname) ? {} : { servername: hostname }),
      rejectUnauthorized: false,
    });
    socket.setTimeout(10_000);
    socket.once("secureConnect", () => {
      try {
        const certificate = socket.getPeerCertificate(true);
        finish(
          certificate.raw ? certificateSpkiFingerprint(certificate.raw) : null,
        );
      } catch {
        finish(null);
      } finally {
        socket.end();
      }
    });
    socket.once("timeout", () => {
      socket.destroy();
      finish(null);
    });
    socket.once("error", () => finish(null));
  });
}

async function tcpPortIsOpen(
  host: string,
  targetPort: number,
  timeoutMs = 400,
): Promise<boolean> {
  return await new Promise<boolean>((resolveOpen) => {
    let settled = false;
    const socket = createConnection({ host, port: targetPort });
    const finish = (open: boolean) => {
      if (settled) return;
      settled = true;
      socket.destroy();
      resolveOpen(open);
    };
    socket.setTimeout(timeoutMs);
    socket.once("connect", () => finish(true));
    socket.once("timeout", () => finish(false));
    socket.once("error", () => finish(false));
  });
}

async function waitForTcpPortClosed(
  host: string,
  targetPort: number,
  timeoutMs = 10_000,
): Promise<boolean> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (!(await tcpPortIsOpen(host, targetPort))) return true;
    await new Promise((resolveWait) => setTimeout(resolveWait, 100));
  }
  return !(await tcpPortIsOpen(host, targetPort));
}

function electronApplicationIsRunning(
  application: ElectronApplication,
): boolean {
  try {
    return application.process().exitCode === null;
  } catch {
    return false;
  }
}

async function exitElectronImmediately(
  application: ElectronApplication,
): Promise<void> {
  if (!electronApplicationIsRunning(application)) return;
  const closed = application
    .waitForEvent("close", { timeout: 10_000 })
    .catch(() => undefined);
  await application.evaluate(({ app }) => app.exit(0)).catch(() => undefined);
  await closed;
}

async function quitElectronNormally(
  application: ElectronApplication,
): Promise<void> {
  if (!electronApplicationIsRunning(application)) return;
  const closed = application
    .waitForEvent("close", { timeout: 10_000 })
    .catch(() => undefined);
  await application
    .evaluate(({ app, dialog }) => {
      // 测试里自动确认退出；其余退出流程仍走 main.cjs 的真实 close /
      // window-all-closed / before-quit 处理器。
      dialog.showMessageBoxSync = () => 1;
      app.quit();
    })
    .catch(() => undefined);
  await closed;
}

function shellSingleQuote(value: string): string {
  return `'${value.replaceAll("'", "'\"'\"'")}'`;
}

function markedBackendProcessGroup(
  pidFile: string,
  marker: string,
): number | null {
  if (process.platform !== "linux") return null;
  try {
    const pid = Number(readFileSync(pidFile, "utf8").trim());
    if (!Number.isSafeInteger(pid) || pid <= 1) return null;
    const environment = readFileSync(`/proc/${pid}/environ`, "utf8").split(
      "\0",
    );
    if (!environment.includes(`TRPG_E2E_BACKEND_MARKER=${marker}`)) return null;
    const stat = readFileSync(`/proc/${pid}/stat`, "utf8");
    const fields = stat
      .slice(stat.lastIndexOf(") ") + 2)
      .trim()
      .split(/\s+/);
    const processGroupId = Number(fields[2]);
    return processGroupId === pid ? pid : null;
  } catch {
    return null;
  }
}

async function terminateMarkedBackendProcessGroup(
  pidFile: string,
  marker: string,
): Promise<void> {
  let pid = markedBackendProcessGroup(pidFile, marker);
  if (pid === null) return;
  try {
    process.kill(-pid, "SIGTERM");
  } catch {
    return;
  }
  for (let attempt = 0; attempt < 30; attempt += 1) {
    await new Promise((resolveWait) => setTimeout(resolveWait, 100));
    if (markedBackendProcessGroup(pidFile, marker) === null) return;
  }
  pid = markedBackendProcessGroup(pidFile, marker);
  if (pid !== null) {
    try {
      process.kill(-pid, "SIGKILL");
    } catch {
      // 进程恰好已退出。
    }
  }
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

function websocketFramesContainType(
  frames: string[],
  startIndex: number,
  expectedType: string,
): boolean {
  return frames.slice(startIndex).some((frame) => {
    try {
      return (JSON.parse(frame) as { type?: unknown }).type === expectedType;
    } catch {
      return false;
    }
  });
}

async function resolveOptionalCheckAndWaitForInput(
  page: Page,
  input: Locator,
  timeout: number,
): Promise<void> {
  const suggestConfirm = page.getByRole("button", { name: /确定尝试/ });
  await expect
    .poll(
      async () => {
        if (await suggestConfirm.isVisible()) {
          await suggestConfirm.click().catch(() => undefined);
        }
        return await input.isEnabled();
      },
      { timeout },
    )
    .toBe(true);
}

test.beforeAll(async () => {
  if (externalBaseUrl) {
    await waitForServer();
    return;
  }
  runtimeRoot = mkdtempSync(join(tmpdir(), "trpg-multiplayer-e2e-"));
  modelBaseUrl = await startModelStub();
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
  // 计算临时证书 SPKI 指纹，供 Electron 按证书钉住（而非全局忽略证书错误）。
  tlsSpkiFingerprint = certificateSpkiFingerprint(readFileSync(certificate));
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
        OPENAI_BASE_URL: modelBaseUrl,
        TRPG_STREAM_USAGE: "0",
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
  await stopModelStub();
  if (runtimeRoot) rmSync(runtimeRoot, { recursive: true, force: true });
});

test("两个真实浏览器完成建房、邀请、选角、恢复、隐私与开局", async ({
  browser,
}) => {
  test.setTimeout(externalBaseUrl ? 240_000 : 90_000);
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

  // 房间号在界面上截断显示，完整 world id 在 title 属性中。
  const worldIdTitle = await owner
    .locator(".online-subtitle span[title]")
    .first()
    .getAttribute("title");
  const worldId = worldIdTitle?.match(/world-[a-f0-9]+/)?.[0];
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
  // 记录刷新前已收到的帧数，只断言刷新后新收到的帧——否则刷新前的推送会让
  // “恢复体包含私有数据”的断言假绿。
  const framesBeforeReload = playerFrames.length;
  await player.reload();
  await expect(
    player.getByRole("heading", { name: "双客户端验收房" }),
  ).toBeVisible();
  await expect
    .poll(() =>
      playerFrames
        .slice(framesBeforeReload)
        .some((frame) => frame.includes(privateNote)),
    )
    .toBe(true);
  // 另一账号的恢复体（room_full_state）真实存在，且绝不包含秘密。
  const ownerFullStates = ownerFrames.filter((frame) => {
    try {
      return (
        (JSON.parse(frame) as { type?: string }).type === "room_full_state"
      );
    } catch {
      return false;
    }
  });
  expect(ownerFullStates.length).toBeGreaterThan(0);
  expect(ownerFullStates.some((frame) => frame.includes(privateNote))).toBe(
    false,
  );
  expect(ownerFrames.some((frame) => frame.includes(privateNote))).toBe(false);

  // 外部 staging 的真实模型 opening 可达 20 秒以上（树莓派实测约 21 秒），
  // 超过 Playwright 默认 12 秒 expect 超时；turnTimeout 必须在点击“开始游戏”
  // 前定义，并显式覆盖开局阶段的 dock、行动提示与输入框断言。
  const turnTimeout = externalBaseUrl ? 120_000 : 30_000;
  await player.getByRole("button", { name: "准备" }).click();
  await owner.getByRole("button", { name: "准备" }).click();
  const start = owner.getByRole("button", { name: "开始游戏" });
  await expect(start).toBeEnabled();
  await start.click();

  await expect(owner.getByTestId("online-room-dock")).toBeVisible({
    timeout: turnTimeout,
  });
  await expect(player.getByTestId("online-room-dock")).toBeVisible({
    timeout: turnTimeout,
  });
  await expect(
    player.getByText(new RegExp(`等待 ${ownerUsername} 行动`)),
  ).toBeVisible({ timeout: turnTimeout });

  // 本地 stub 用确定的 4s 窗口验证并发门禁；外部 staging 不假设模型响应
  // 时长，只验证双方都收到同一行动及权威 done 终态。
  const input = owner.locator("#user-input");
  await expect(input).toBeEnabled({ timeout: turnTimeout });
  const actionText = `验收行动-${runId}：我检查门锁和附近的脚印。`;
  const ownerFramesBeforeAction = ownerFrames.length;
  const playerFramesBeforeAction = playerFrames.length;
  await input.fill(actionText);
  await owner.locator("#btn-send").click();
  await expect(owner.getByText(actionText, { exact: true })).toBeVisible();
  await expect(player.getByText(actionText, { exact: true })).toBeVisible();
  await expect(input).toBeDisabled();
  if (!externalBaseUrl) {
    const concurrentCode = await owner.evaluate(async (id) => {
      const protocol = location.protocol === "https:" ? "wss:" : "ws:";
      const socket = new WebSocket(
        `${protocol}//${location.host}/ws/room?world_id=${encodeURIComponent(id)}`,
      );
      return await new Promise<string>((resolveSocket, rejectSocket) => {
        const timer = window.setTimeout(() => {
          socket.close();
          rejectSocket(new Error("concurrent action rejection timeout"));
        }, 15_000);
        socket.onopen = () => {
          socket.send(
            JSON.stringify({
              type: "action",
              action_id: `overlap-${crypto.randomUUID()}`,
              content: "并发提交不应启动第二个模型回合",
            }),
          );
        };
        socket.onmessage = (event) => {
          const payload = JSON.parse(String(event.data)) as {
            type?: string;
            code?: string;
          };
          if (payload.type !== "room_action_rejected") return;
          window.clearTimeout(timer);
          socket.close();
          resolveSocket(payload.code ?? "");
        };
        socket.onerror = () => {
          window.clearTimeout(timer);
          rejectSocket(new Error("concurrent action websocket failed"));
        };
      });
    }, worldId!);
    expect(concurrentCode).toBe("room_turn_in_progress");
  }
  // 行动可能较晚才触发检定建议；轮询期间出现就确认，避免依赖固定时间窗。
  await resolveOptionalCheckAndWaitForInput(owner, input, turnTimeout);
  await expect
    .poll(
      () =>
        websocketFramesContainType(
          ownerFrames,
          ownerFramesBeforeAction,
          "done",
        ) &&
        websocketFramesContainType(
          playerFrames,
          playerFramesBeforeAction,
          "done",
        ),
      { timeout: turnTimeout },
    )
    .toBe(true);

  await owner.getByRole("button", { name: "快速存档" }).click();
  await expect(owner.getByRole("button", { name: "快速存档" })).toHaveAttribute(
    "title",
    "已保存",
    { timeout: 30_000 },
  );
  await owner.getByRole("button", { name: "打开存档管理" }).click();
  await expect(owner.getByRole("heading", { name: "存档管理" })).toBeVisible();
  await expect(
    owner.getByRole("button", { name: "读取" }).first(),
  ).toBeVisible();
  await owner.getByRole("button", { name: "读取" }).first().click();
  await expect(input).toBeEnabled({ timeout: 60_000 });

  await ownerContext.close();
  await playerContext.close();
});

test("Electron 与浏览器真实双客户端完成联机回合并安全返回启动器", async ({
  browser,
}) => {
  test.setTimeout(externalBaseUrl ? 240_000 : 120_000);
  // 仅钉住当前服务器证书的 SPKI 指纹；不使用全局 ignore-certificate-errors。
  const spki = externalBaseUrl
    ? await fetchRemoteSpkiFingerprint(baseUrl)
    : tlsSpkiFingerprint;
  test.skip(!spki, "无法取得服务器证书指纹，跳过 Electron HTTPS 场景");
  const electronEnvironment = { ...process.env };
  delete electronEnvironment.ELECTRON_RUN_AS_NODE;
  delete electronEnvironment.NODE_ENV;
  const electronUserData = mkdtempSync(
    join(tmpdir(), "trpg-electron-online-e2e-"),
  );
  let electronApp: ElectronApplication | null = null;
  let peerContext: Awaited<ReturnType<typeof browser.newContext>> | null = null;
  try {
    electronApp = await electron.launch({
      executablePath: resolve(
        repositoryRoot,
        "frontend/node_modules/electron/dist/electron",
      ),
      args: [
        `--user-data-dir=${electronUserData}`,
        `--ignore-certificate-errors-spki-list=${spki}`,
        resolve(repositoryRoot, "frontend"),
      ],
      env: electronEnvironment,
    });
    peerContext = await browser.newContext({ ignoreHTTPSErrors: true });
    const page = await electronApp.firstWindow();
    const electronFrames = collectWebSocketFrames(page);
    const peer = await peerContext.newPage();
    const peerFrames = collectWebSocketFrames(peer);
    await expect(page.getByTestId("mode-select")).toBeVisible();
    await expect(page.getByText("本地单人", { exact: true })).toBeVisible();
    await expect(page.getByText("多人游戏", { exact: true })).toBeVisible();
    await page.getByText("自定义服务器", { exact: false }).click();
    await page.getByLabel("云端服务器地址").fill(baseUrl);
    await page.getByText("多人游戏", { exact: true }).click();

    await page.waitForURL(`${baseUrl}/?mode=online`);
    await expect(page.getByRole("heading", { name: "多人游戏" })).toBeVisible();

    // 联机页面真实注册、创建并进入房间（不只是验证页面加载）。
    await page.getByRole("tab", { name: "注册" }).click();
    await page.getByLabel("用户名").fill(`e2e_electron${runId}`);
    await page
      .getByLabel("密码", { exact: true })
      .fill("electron e2e password");
    await page.getByLabel("确认密码").fill("electron e2e password");
    await page.getByRole("button", { name: "注册并登录" }).click();
    await expect(page.getByRole("heading", { name: "联机大厅" })).toBeVisible();
    const roomName = `Electron 双端验收房${runId}`;
    await page.getByLabel("房间名称").fill(roomName);
    await page.getByRole("button", { name: "创建房间" }).click();
    await expect(page.getByRole("heading", { name: roomName })).toBeVisible();
    await expect(page.getByText("已连接", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "选择" }).first().click();
    await page.getByRole("button", { name: "生成邀请码" }).click();
    const inviteToken = (
      await page.locator(".invite-token").textContent()
    )?.trim();
    expect(inviteToken).toBeTruthy();

    await register(peer, `e2e_electron_peer${runId}`);
    await peer
      .getByRole("textbox", { name: "邀请码", exact: true })
      .fill(inviteToken!);
    await peer.getByRole("button", { name: "加入房间" }).click();
    await expect(peer.getByRole("heading", { name: roomName })).toBeVisible();
    await peer.getByRole("button", { name: "选择" }).first().click();
    await expect(
      page.locator(".member-row", { hasText: `e2e_electron_peer${runId}` }),
    ).toContainText("在线");

    // 同上：外部真实模型 opening 可超过默认 12 秒 expect 超时，turnTimeout
    // 在点击“开始游戏”前定义，覆盖双方 dock 与紧邻的输入框状态断言。
    const turnTimeout = externalBaseUrl ? 120_000 : 60_000;
    await peer.getByRole("button", { name: "准备" }).click();
    await page.getByRole("button", { name: "准备" }).click();
    const start = page.getByRole("button", { name: "开始游戏" });
    await expect(start).toBeEnabled();
    await start.click();

    await expect(page.getByTestId("online-room-dock")).toBeVisible({
      timeout: turnTimeout,
    });
    await expect(peer.getByTestId("online-room-dock")).toBeVisible({
      timeout: turnTimeout,
    });
    const electronInput = page.locator("#user-input");
    const peerInput = peer.locator("#user-input");
    await expect(electronInput).toBeEnabled({ timeout: turnTimeout });
    await expect(peerInput).toBeDisabled({ timeout: turnTimeout });

    // 两种客户端必须收到同一条公共开场，而不是各自启动独立引擎。
    const electronOpening = page
      .locator(".msg.gm:not(.streaming-cursor)")
      .last();
    const peerOpening = peer.locator(".msg.gm:not(.streaming-cursor)").last();
    await expect(electronOpening).toBeVisible({ timeout: turnTimeout });
    await expect(peerOpening).toBeVisible({ timeout: turnTimeout });
    expect(await peerOpening.locator(".chat-event-list").innerText()).toBe(
      await electronOpening.locator(".chat-event-list").innerText(),
    );

    const actionText = `Electron 双端行动-${runId}：我检查门锁和附近的脚印。`;
    await electronInput.fill(actionText);
    await page.locator("#btn-send").click();
    await expect(page.getByText(actionText, { exact: true })).toBeVisible();
    await expect(peer.getByText(actionText, { exact: true })).toBeVisible();
    await resolveOptionalCheckAndWaitForInput(page, electronInput, turnTimeout);

    // 刷新浏览器端必须恢复到同一房间、同一公开历史；双方 WS 都真实收过叙述。
    await peer.reload();
    await expect(peer.getByTestId("online-room-dock")).toBeVisible({
      timeout: turnTimeout,
    });
    await expect(peer.getByText(actionText, { exact: true })).toBeVisible();
    expect(electronFrames.some((frame) => frame.includes("narrative_"))).toBe(
      true,
    );
    expect(peerFrames.some((frame) => frame.includes("narrative_"))).toBe(true);

    // 经大厅返回内置启动器（return-launcher IPC）。
    await page.locator(".online-room-dock-toggle").click();
    await page.getByRole("button", { name: "房间管理" }).click();
    await page.getByRole("button", { name: "← 大厅" }).click();
    await expect(page.getByRole("heading", { name: "联机大厅" })).toBeVisible();
    await page.getByRole("button", { name: "← 返回模式选择" }).click();

    await page.waitForURL(/^file:.*\/dist\/index\.html$/);
    await expect(page.getByTestId("mode-select")).toBeVisible();
  } finally {
    await peerContext?.close();
    if (electronApp) await exitElectronImmediately(electronApp);
    rmSync(electronUserData, { recursive: true, force: true });
  }
});

test("Electron 源码进程自主启动并回收真实本地后端", async () => {
  test.skip(Boolean(externalBaseUrl), "外部 staging 验收只运行浏览器联机场景");
  test.skip(
    process.platform !== "linux",
    "源码进程组生命周期验收在 Linux CI 运行",
  );
  test.skip(
    await tcpPortIsOpen("127.0.0.1", 8765),
    "本机 8765 已被占用；为避免触碰用户服务，跳过本用例",
  );
  test.setTimeout(150_000);

  const localRuntime = mkdtempSync(join(tmpdir(), "trpg-local-e2e-"));
  const electronUserData = join(localRuntime, "electron-user-data");
  const backendPidFile = join(localRuntime, "backend.pid");
  const backendMarker = `trpg-e2e-${randomUUID()}`;
  const sourceLauncher = resolve(repositoryRoot, "start_desktop.sh");
  const testLauncher = join(localRuntime, "launch-backend.sh");
  writeFileSync(
    testLauncher,
    [
      "#!/usr/bin/env bash",
      "set -eu",
      `printf '%s\\n' "$$" > ${shellSingleQuote(backendPidFile)}`,
      `exec ${shellSingleQuote(sourceLauncher)} "$@"`,
      "",
    ].join("\n"),
    { encoding: "utf8", mode: 0o700 },
  );

  let electronApp: ElectronApplication | null = null;
  let backendStopped = false;
  try {
    const electronEnvironment = {
      ...process.env,
      TRPG_SOURCE_BACKEND_LAUNCHER: testLauncher,
      TRPG_RUNTIME_ROOT: localRuntime,
      TRPG_DATABASE_URL: `sqlite:///${join(localRuntime, "local.db")}`,
      TRPG_REQUIRE_AUTH: "0",
      TRPG_WRITE_COMPAT_EXPORTS: "0",
      TRPG_ROOM_IDLE_SECONDS: "0",
      TRPG_E2E_BACKEND_MARKER: backendMarker,
      TRPG_E2E_BACKEND_PID_FILE: backendPidFile,
      OPENAI_API_KEY: "e2e-placeholder",
      OPENAI_BASE_URL: modelBaseUrl,
      TRPG_STREAM_USAGE: "0",
    };
    delete electronEnvironment.ELECTRON_RUN_AS_NODE;
    delete electronEnvironment.NODE_ENV;
    electronApp = await electron.launch({
      executablePath: resolve(
        repositoryRoot,
        "frontend/node_modules/electron/dist/electron",
      ),
      args: [
        `--user-data-dir=${electronUserData}`,
        resolve(repositoryRoot, "frontend"),
      ],
      env: electronEnvironment,
    });
    const page = await electronApp.firstWindow();
    await expect(page.getByTestId("mode-select")).toBeVisible();
    await page.getByText("本地单人", { exact: true }).click();
    await expect(page.getByTestId("mode-select")).toBeHidden();
    await waitForUrl("http://127.0.0.1:8765/api/health");
    await expect
      .poll(() => markedBackendProcessGroup(backendPidFile, backendMarker))
      .not.toBeNull();
    await expect(page.locator("#btn-start")).toBeVisible();
    await expect(page.getByText("已连接到守秘人……")).toBeVisible();

    // 实际开局：选择默认调查员，等待 stub 模型的开场叙述。
    await page.locator("#btn-start").click();
    const confirm = page.locator("#btn-character-confirm");
    await expect(confirm).toBeVisible();
    await confirm.click();
    await expect(page.getByText("雨幕笼罩着阿卡姆")).toBeVisible({
      timeout: 60_000,
    });

    // 完成一次无需检定的普通行动；检定/并发路径已由上面的多人用例覆盖。
    const input = page.locator("#user-input");
    await expect(input).toBeEnabled({ timeout: 60_000 });
    const localAction = "我向委托人点头，请他继续说明案件。";
    await input.fill(localAction);
    await page.locator("#btn-send").click();
    await expect(page.getByText(localAction, { exact: true })).toBeVisible();
    await resolveOptionalCheckAndWaitForInput(page, input, 60_000);

    // 保存并读取：快速存档写入自动槽后，经存档管理读回。
    await page.getByRole("button", { name: "快速存档" }).click();
    await expect(
      page.getByRole("button", { name: "快速存档" }),
    ).toHaveAttribute("title", "已保存", { timeout: 30_000 });
    await page.getByRole("button", { name: "打开存档管理" }).click();
    await expect(page.getByRole("heading", { name: "存档管理" })).toBeVisible();
    // 存档位视图的主按钮是“继续游戏”（恢复当前时间线最近状态），读取单槽
    // 在时间线子视图内；这里验证存档后的恢复回路即可。
    await page.getByRole("button", { name: "继续游戏" }).first().click();
    await expect(input).toBeEnabled({ timeout: 60_000 });

    // 走真实 app.quit → before-quit/window-all-closed，验证 main.cjs 回收它
    // 自己拉起的整个源码后端进程组。
    await quitElectronNormally(electronApp);
    backendStopped = await waitForTcpPortClosed("127.0.0.1", 8765);
    expect(backendStopped).toBe(true);
    expect(markedBackendProcessGroup(backendPidFile, backendMarker)).toBeNull();
  } finally {
    if (electronApp && electronApplicationIsRunning(electronApp)) {
      await quitElectronNormally(electronApp);
    }
    if (electronApp && electronApplicationIsRunning(electronApp)) {
      await exitElectronImmediately(electronApp);
    }
    if (!backendStopped && (await tcpPortIsOpen("127.0.0.1", 8765))) {
      // 只允许终止带本测试随机 marker、且 PID 等于 PGID 的进程组。
      await terminateMarkedBackendProcessGroup(backendPidFile, backendMarker);
      backendStopped = await waitForTcpPortClosed("127.0.0.1", 8765);
    }
    rmSync(localRuntime, { recursive: true, force: true });
    expect(backendStopped || !(await tcpPortIsOpen("127.0.0.1", 8765))).toBe(
      true,
    );
  }
});
