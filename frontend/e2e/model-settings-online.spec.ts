/**
 * 模型设置 e2e（云端：账号鉴权 + TLS + BYOK-only）。
 *
 * 闭环 A（云端单人）：注册 → 开世界 → 选角页模型设置入口 → 未配置开局被拒
 * （model_not_configured + 引导按钮）→ 私网地址被 SSRF 策略拒绝（可读原因）→
 * 配置到 E2E 桩（运营白名单 + 连接钉扎）→ 测试连接真实探针 → 开局成功（平台
 * 兜底地址指向死端口，被调用即失败）→ 刷新后回填且 Key 不回显 → 清除配置回到
 * 未配置。
 * 闭环 B（多人房间）：未配置开局被拒 → 房主在房间页配置 → 成员只读脱敏且知晓
 * 费用归属 → 开局成功 → 成员收到目的地告知。
 */
import { spawn, spawnSync, type ChildProcess } from "node:child_process";
import { randomUUID } from "node:crypto";
import { createServer, type Server } from "node:http";
import { mkdtempSync, rmSync, existsSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

import { expect, request, test, type Page } from "@playwright/test";

const port = 8779;
const baseUrl = `https://127.0.0.1:${port}`;
const repositoryRoot = resolve(import.meta.dirname, "../..");
const runId = `${Date.now()}-${randomUUID().slice(0, 8)}`;
let runtimeRoot = "";
let server: ChildProcess | null = null;
let serverOutput = "";
let modelServer: Server | null = null;
let modelBaseUrl = "";

async function startModelStub(): Promise<string> {
  modelServer = createServer((incoming, response) => {
    // 连接测试探针：models.list
    if (incoming.method === "GET" && incoming.url === "/v1/models") {
      response.writeHead(200, { "Content-Type": "application/json" });
      response.end(JSON.stringify({ object: "list", data: [] }));
      return;
    }
    if (incoming.method !== "POST" || incoming.url !== "/v1/chat/completions") {
      response.writeHead(404).end();
      return;
    }
    let body = "";
    incoming.on("data", (chunk) => {
      body += String(chunk);
    });
    incoming.once("end", () => {
      let parsed: { stream?: boolean; tools?: unknown[] } = {};
      try {
        parsed = JSON.parse(body || "{}");
      } catch {
        /* 非 JSON 按流式处理 */
      }
      // 连接测试探针：tool_calling（非流式 + tools → 必须回 tool_calls）
      if (!parsed.stream && Array.isArray(parsed.tools)) {
        response.writeHead(200, { "Content-Type": "application/json" });
        response.end(
          JSON.stringify({
            id: "chatcmpl-e2e-tools",
            object: "chat.completion",
            created: 1,
            model: "e2e-model",
            choices: [
              {
                index: 0,
                message: {
                  role: "assistant",
                  content: null,
                  tool_calls: [
                    {
                      id: "call_probe",
                      type: "function",
                      function: {
                        name: "probe_noop",
                        arguments: JSON.stringify({ echo: "ok" }),
                      },
                    },
                  ],
                },
                finish_reason: "tool_calls",
              },
            ],
          }),
        );
        return;
      }
      // 连接测试探针：generation（非流式）
      if (!parsed.stream) {
        response.writeHead(200, { "Content-Type": "application/json" });
        response.end(
          JSON.stringify({
            id: "chatcmpl-e2e-chat",
            object: "chat.completion",
            created: 1,
            model: "e2e-model",
            choices: [
              {
                index: 0,
                message: { role: "assistant", content: "好" },
                finish_reason: "stop",
              },
            ],
          }),
        );
        return;
      }
      const content =
        "雨幕笼罩着阿卡姆，你在约定的办公室里见到了等待已久的委托人。" +
        "\n\n**你可以——**\n1. 请他说明委托\n2. 观察办公室" +
        "\n3. 检查档案封面\n4. [自由行动] 你决定做什么？";
      const chunk = {
        id: "chatcmpl-e2e-online",
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
    });
  });
  await new Promise<void>((resolveListen, rejectListen) => {
    modelServer!.once("error", rejectListen);
    modelServer!.listen(0, "127.0.0.1", () => resolveListen());
  });
  const address = modelServer.address();
  if (!address || typeof address === "string") {
    throw new Error("model stub did not expose a TCP address");
  }
  return `http://127.0.0.1:${address.port}/v1`;
}

async function waitForServer(): Promise<void> {
  const client = await request.newContext({ ignoreHTTPSErrors: true });
  try {
    for (let attempt = 0; attempt < 80; attempt += 1) {
      try {
        const response = await client.get(`${baseUrl}/api/health`);
        if (response.ok()) return;
      } catch {
        // 启动中
      }
      await new Promise((resolveWait) => setTimeout(resolveWait, 150));
    }
  } finally {
    await client.dispose();
  }
  throw new Error(`E2E server did not start:\n${serverOutput.slice(-4000)}`);
}

test.beforeAll(async () => {
  runtimeRoot = mkdtempSync(join(tmpdir(), "trpg-settings-online-e2e-"));
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
  const repositoryPython = resolve(repositoryRoot, ".venv/bin/python");
  const python =
    process.env.TRPG_E2E_PYTHON ??
    (existsSync(repositoryPython) ? repositoryPython : "python");
  server = spawn(
    python,
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
        ...Object.fromEntries(
          Object.entries(process.env).filter(
            ([key]) => !/^(http_proxy|https_proxy|all_proxy)$/i.test(key),
          ),
        ),
        NO_PROXY: "127.0.0.1,localhost",
        TRPG_RUNTIME_ROOT: runtimeRoot,
        TRPG_DATABASE_URL: `sqlite:///${join(runtimeRoot, "e2e.db")}`,
        TRPG_REQUIRE_AUTH: "1",
        TRPG_ALLOW_REGISTRATION: "1",
        TRPG_ALLOWED_ORIGINS: baseUrl,
        TRPG_WRITE_COMPAT_EXPORTS: "0",
        // BYOK-only 下平台兜底绝不应被触碰：指到必死的地址，被调用即测试失败。
        OPENAI_API_KEY: "e2e-placeholder",
        OPENAI_BASE_URL: "http://127.0.0.1:9/v1",
        // E2E 桩是私网地址：仅此环境经运营白名单放行（生产该变量为空）。
        TRPG_EGRESS_ALLOWED_PRIVATE_HOSTS: "127.0.0.1",
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
  if (modelServer) {
    await new Promise<void>((resolveClose) =>
      modelServer!.close(() => resolveClose()),
    );
  }
  if (runtimeRoot) rmSync(runtimeRoot, { recursive: true, force: true });
});

async function register(page: Page, username: string, solo: boolean) {
  // ?mode=online 直跳多人认证；云端单人意图要在模式选择页点“云端单人”
  await page.goto(solo ? `${baseUrl}/` : `${baseUrl}/?mode=online`);
  if (solo) {
    await page.getByRole("button", { name: /云端单人/ }).click();
  }
  await page.getByRole("tab", { name: "注册" }).click();
  await page.getByLabel("用户名").fill(username);
  await page.getByLabel("密码", { exact: true }).fill("settings e2e password");
  await page.getByLabel("确认密码").fill("settings e2e password");
  await page.getByRole("button", { name: "注册并登录" }).click();
  await expect(
    page.getByRole("heading", { name: solo ? "我的冒险" : "联机大厅" }),
  ).toBeVisible({ timeout: 30_000 });
}

/** 把面板里两个角色都配置到 E2E 桩并保存（BYOK 就绪）。 */
async function configureByokToStub(dialog: ReturnType<Page["getByRole"]>) {
  for (const role of ["narrative", "judgement"] as const) {
    const card = dialog.locator(`[data-role="${role}"]`);
    await card.getByRole("button", { name: "自定义服务" }).click();
    await card
      .getByPlaceholder("https://api.deepseek.com/v1")
      .fill(modelBaseUrl);
    await card.getByPlaceholder("sk-…").fill("sk-e2e-byok");
    await card.getByPlaceholder("deepseek-flash").fill("e2e-model");
  }
  await dialog.getByRole("checkbox").check();
  await dialog.getByRole("button", { name: /保存配置/ }).click();
  await expect(dialog.getByText(/下一回合生效/)).toBeVisible({
    timeout: 15_000,
  });
}

test("云端单人 BYOK 闭环：未配置被拒→引导配置→测试→开局→持久化→清除", async ({
  page,
}) => {
  test.setTimeout(300_000);
  await register(page, `e2e_solo_${runId}`, true);

  // 创建冒险到选角页
  await page.getByText("开始新冒险", { exact: true }).first().click();
  await page.locator(".module-select-trigger").click();
  await page.getByRole("option", { name: /猩红文档/ }).click();
  await page.getByRole("button", { name: /创建冒险/ }).click();
  const confirmCharacter = page.locator("#btn-character-confirm");
  await expect(confirmCharacter).toBeVisible({ timeout: 30_000 });
  // 云端单人需先点角色卡认领，确认按钮才可用
  await page.locator(".character-card").first().click();
  await expect(confirmCharacter).toBeEnabled({ timeout: 15_000 });

  // 选角页有模型设置入口；面板显示 BYOK 未配置警示
  await page
    .getByTestId("solo-character-select")
    .getByRole("button", { name: "模型设置" })
    .click();
  const dialog = page.getByRole("dialog", { name: "模型设置" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByText(/云端为自带 Key（BYOK）模式/)).toBeVisible();
  await dialog.getByRole("button", { name: "取消" }).click();

  // 未配置开局被拒：可读原因 + 引导按钮（平台兜底绝不被调用）
  await confirmCharacter.click();
  await expect(page.getByText(/尚未配置/)).toBeVisible({ timeout: 15_000 });
  await page
    .locator(".online-notice")
    .getByRole("button", { name: "打开模型设置" })
    .click();
  await expect(dialog).toBeVisible();

  // SSRF：非白名单私网地址被云端策略拒绝，原因可读
  const narrativeCard = dialog.locator('[data-role="narrative"]');
  await narrativeCard.getByRole("button", { name: "自定义服务" }).click();
  await narrativeCard
    .getByPlaceholder("https://api.deepseek.com/v1")
    .fill("https://10.9.9.9/v1");
  await narrativeCard.getByPlaceholder("sk-…").fill("sk-private");
  await narrativeCard.getByPlaceholder("deepseek-flash").fill("e2e-model");
  await dialog.getByRole("checkbox").check();
  await dialog.getByRole("button", { name: /保存配置/ }).click();
  await expect(dialog.locator("#model-settings-status")).toContainText(
    /非公网|拒绝/,
    { timeout: 15_000 },
  );

  // 配置到 E2E 桩（白名单私网地址，连接钉扎到校验 IP）并保存
  await configureByokToStub(dialog);
  // BYOK 警示条在配置齐全后消失
  await expect(dialog.getByText(/云端为自带 Key（BYOK）模式/)).toHaveCount(0);

  // 测试连接：真实探针打桩（connectivity + generation）。
  // 保存成功后确认勾选会被重置，探针发送前需重新勾选。
  await dialog.getByRole("checkbox").check();
  await narrativeCard.getByRole("button", { name: "测试连接" }).click();
  await expect(narrativeCard.getByText(/✓ 测试通过/)).toBeVisible({
    timeout: 30_000,
  });

  // 关闭面板后开局成功（开场叙事来自 BYOK 桩而非平台）
  await dialog.getByRole("button", { name: "取消" }).click();
  await confirmCharacter.click();
  await expect(page.getByText("雨幕笼罩着阿卡姆").first()).toBeVisible({
    timeout: 90_000,
  });

  // 持久化：同浏览器上下文新标签页继续冒险，配置回填且 Key 不回显
  const page2 = await page.context().newPage();
  await page2.goto(`${baseUrl}/`);
  await page2.getByRole("button", { name: /云端单人/ }).click();
  await expect(page2.getByRole("heading", { name: "我的冒险" })).toBeVisible({
    timeout: 30_000,
  });
  await page2.getByRole("button", { name: "继续冒险" }).first().click();
  const settingsEntry = page2.locator("#btn-model-settings");
  await settingsEntry.waitFor({ state: "visible", timeout: 60_000 });
  await settingsEntry.click();
  const dialog2 = page2.getByRole("dialog", { name: "模型设置" });
  await expect(dialog2).toBeVisible();
  const narrativeCard2 = dialog2.locator('[data-role="narrative"]');
  await expect(
    narrativeCard2.getByPlaceholder("https://api.deepseek.com/v1"),
  ).toHaveValue(modelBaseUrl);
  await expect(narrativeCard2.getByPlaceholder("已配置，不回显")).toHaveValue(
    "",
  );

  // 清除配置 = 回到未配置（云端没有平台默认可回退）
  await dialog2.getByRole("button", { name: "清除配置" }).click();
  await expect(dialog2.getByText(/已清除自定义配置/)).toBeVisible({
    timeout: 15_000,
  });
  await expect(dialog2.getByText(/云端为自带 Key（BYOK）模式/)).toBeVisible();
});

test("多人房间：BYOK 门禁、房主配置、成员只读脱敏与费用告知", async ({
  browser,
}) => {
  test.setTimeout(300_000);
  const ownerContext = await browser.newContext({ ignoreHTTPSErrors: true });
  const memberContext = await browser.newContext({ ignoreHTTPSErrors: true });
  const owner = await ownerContext.newPage();
  const member = await memberContext.newPage();
  try {
    await register(owner, `e2e_owner_${runId}`, false);
    await owner.getByLabel("房间名称").fill("模型设置验收房");
    await owner.getByRole("button", { name: "创建房间" }).click();
    await expect(
      owner.getByRole("heading", { name: "模型设置验收房" }),
    ).toBeVisible();
    await owner.getByRole("button", { name: "选择" }).first().click();
    await expect(owner.getByRole("button", { name: "释放" })).toBeVisible();
    await owner.getByRole("button", { name: "生成邀请码" }).click();
    const inviteToken = (
      await owner.locator(".invite-token").textContent()
    )?.trim();
    expect(inviteToken).toBeTruthy();

    await register(member, `e2e_member_${runId}`, false);
    await member
      .getByRole("textbox", { name: "邀请码", exact: true })
      .fill(inviteToken!);
    await member.getByRole("button", { name: "加入房间" }).click();
    await expect(
      member.getByRole("heading", { name: "模型设置验收房" }),
    ).toBeVisible();
    await member.getByRole("button", { name: "选择" }).first().click();
    await member.getByRole("button", { name: "准备" }).click();
    await owner.getByRole("button", { name: "准备" }).click();

    // 未配置时开局被拒：房主看到可读原因与 CTA
    await owner.getByRole("button", { name: "开始游戏" }).click();
    await expect(owner.getByText(/尚未配置/)).toBeVisible({ timeout: 15_000 });
    await owner
      .locator(".online-notice")
      .getByRole("button", { name: "打开模型设置" })
      .click();
    const ownerDialog = owner.getByRole("dialog", { name: "模型设置" });
    await expect(ownerDialog).toBeVisible();

    // 房主在房间页直接配置到 E2E 桩
    await configureByokToStub(ownerDialog);
    await ownerDialog.getByRole("button", { name: "取消" }).click();

    // 成员面板：只读 + 费用归属说明 + 无保存按钮
    await member
      .locator(".online-room-screen")
      .getByRole("button", { name: "模型设置" })
      .click();
    const memberDialog = member.getByRole("dialog", { name: "模型设置" });
    await expect(memberDialog).toBeVisible();
    await expect(memberDialog.getByText(/仅房主可修改/)).toBeVisible();
    await expect(memberDialog.getByText(/额度由房主的 Key 承担/)).toBeVisible();
    await expect(
      memberDialog.getByRole("button", { name: /保存配置/ }),
    ).toHaveCount(0);
    await member.keyboard.press("Escape");

    // 配置后开局成功（双方都收到开场）
    await owner.getByRole("button", { name: "开始游戏" }).click();
    await expect(owner.getByText("雨幕笼罩着阿卡姆").first()).toBeVisible({
      timeout: 90_000,
    });
    await expect(member.getByText("雨幕笼罩着阿卡姆").first()).toBeVisible({
      timeout: 90_000,
    });

    // 成员收到目的地告知（系统消息含主机名，不含 Key）
    await expect(
      member.getByText(/房主已更新房间模型配置/).first(),
    ).toBeVisible({ timeout: 15_000 });
    await expect(member.getByText(/127\.0\.0\.1/).first()).toBeVisible();

    // 成员再开面板：看到服务商与目的地主机名，看不到 Key 与完整 URL
    await member.locator("#btn-model-settings").click();
    const memberDialog2 = member.getByRole("dialog", { name: "模型设置" });
    await expect(memberDialog2).toBeVisible();
    await expect(memberDialog2.getByText(/127\.0\.0\.1/).first()).toBeVisible();
    const memberText = await memberDialog2.textContent();
    expect(memberText).not.toContain("sk-e2e-byok");
    expect(memberText).not.toContain(modelBaseUrl);
  } finally {
    await ownerContext.close();
    await memberContext.close();
  }
});
