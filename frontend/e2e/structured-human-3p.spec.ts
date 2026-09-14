/**
 * 三客户端人类主持闭环（真实后端，一个 uvicorn 进程，无模型 Key）。
 *
 * 一位人类主持 + 两位玩家，在 structured_v1 + human 主持房间里：
 * 建房（勾选结构化模式）→ 邀请 → 选角（主持不占角色）→ 准备 → 开局 →
 * ① 主持定向私发线索给甲（乙不可见）
 * ② 主持请求检定 → 甲在持久检定卡上掷骰 → 服务端结算
 * ③ 主持调整 SAN → 甲的数值面板按事件更新
 * ④ 整队移动 → 两端场景一致
 * ⑤ 存档入口可用 + 甲刷新重连后位置与结构化入口恢复
 *
 * 全程不调用模型：模型 base URL 指向关闭端口，任何误调用都会以错误暴露。
 */

import { spawn, type ChildProcess } from "node:child_process";
import { existsSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

import {
  expect,
  request,
  test,
  type BrowserContext,
  type Page,
} from "@playwright/test";

const port = 8765;
const baseUrl = `http://127.0.0.1:${port}`;
const repositoryRoot = resolve(import.meta.dirname, "../..");
const runId = Math.random().toString(36).slice(2, 8);
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

async function waitForServer(): Promise<void> {
  const client = await request.newContext();
  try {
    for (let attempt = 0; attempt < 80; attempt += 1) {
      try {
        const response = await client.get(`${baseUrl}/api/health`);
        if (response.ok()) return;
      } catch {
        // 启动中
      }
      await new Promise((resolveWait) => setTimeout(resolveWait, 125));
    }
  } finally {
    await client.dispose();
  }
  throw new Error(`E2E server did not start:\n${serverOutput.slice(-4000)}`);
}

test.beforeAll(async () => {
  runtimeRoot = mkdtempSync(join(tmpdir(), "trpg-structured-3p-"));
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
        OPENAI_API_KEY: "e2e-placeholder",
        OPENAI_BASE_URL: "http://127.0.0.1:9/v1",
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
  if (runtimeRoot) rmSync(runtimeRoot, { recursive: true, force: true });
});

function collectFrames(page: Page): { sent: string[]; received: string[] } {
  const sent: string[] = [];
  const received: string[] = [];
  page.on("websocket", (socket) => {
    socket.on("framesent", (event) => sent.push(String(event.payload)));
    socket.on("framereceived", (event) => received.push(String(event.payload)));
  });
  return { sent, received };
}

async function register(page: Page, username: string): Promise<void> {
  await page.goto(`${baseUrl}/?mode=online`);
  await page.getByRole("tab", { name: "注册" }).click();
  await page.getByLabel("用户名").fill(username);
  await page
    .getByLabel("密码", { exact: true })
    .fill("structured e2e password");
  await page.getByLabel("确认密码").fill("structured e2e password");
  await page.getByRole("button", { name: "注册并登录" }).click();
  await expect(page.getByRole("heading", { name: "联机大厅" })).toBeVisible();
}

function framesOf(received: string[], type: string): string[] {
  return received.filter((frame) => frame.includes(`"type":"${type}"`));
}

/** 用当前选择器（input 或 select）给主持表单字段赋值。 */
async function setKeeperField(page: Page, field: string, value: string) {
  const control = page.locator(
    `[data-field="${field}"] select, [data-field="${field}"] input`,
  );
  const tag = await control.evaluate((node) => node.tagName);
  if (tag === "SELECT") {
    await control.selectOption(value);
  } else {
    await control.fill(value);
  }
}

/**
 * 提交主持命令并等一个确定结局（受理或拒绝）。
 *
 * 世界 revision 会因别人的命令前进，主持端可能带着上一版提交 → 服务端按冻结
 * 错误码回 `revision_conflict`。这里走的是**界面自己的恢复动作**
 * （“用最新版本重新提交”），既让用例稳定，也顺带验收这条恢复路径。
 */
async function submitKeeperCommand(
  page: Page,
  frames: { sent: string[]; received: string[] },
  options: { expectAck?: boolean; attempts?: number } = {},
): Promise<{ accepted: boolean; lastError: string }> {
  const attempts = options.attempts ?? 3;
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    // 命令的结局帧是 request_error 或 action_status（受理确认）；
    // `action_ack` 不保证出现，不能只等它。
    const settled = () =>
      framesOf(frames.received, "request_error").length +
      framesOf(frames.received, "action_status").length +
      framesOf(frames.received, "action_ack").length;
    const before = settled();
    const errorsBefore = framesOf(frames.received, "request_error").length;
    await page.getByTestId("keeper-submit").click();
    await expect.poll(settled, { timeout: 20_000 }).toBeGreaterThan(before);
    const errors = framesOf(frames.received, "request_error");
    if (errors.length === errorsBefore)
      return { accepted: true, lastError: "" };
    const last = JSON.parse(errors[errors.length - 1]) as {
      payload?: { code?: string };
    };
    const code = last.payload?.code ?? "";
    if (code !== "revision_conflict") {
      return { accepted: false, lastError: errors[errors.length - 1] };
    }
    // 世界已前进：冲突事件的信封里带着新的 revision，store 已据此更新；
    // 再点一次提交就是用最新版本 + 新 command_id 重发同一意图（主持台的
    // 提交按钮每次都生成新的 command_id）。状态卡上的“用最新版本重新提交”
    // 是给玩家的同一恢复动作，但它位于聊天区、会被主持台浮层遮住，
    // 因此这里在控制台内完成重发。
    await page.waitForTimeout(500);
  }
  return { accepted: false, lastError: "revision_conflict 多次未能提交" };
}

test("三客户端：人类主持 + 两位玩家，无模型完成私发/检定/SAN/移动/存档重连", async ({
  browser,
}) => {
  test.setTimeout(420_000);
  const keeperContext: BrowserContext = await browser.newContext();
  const playerAContext: BrowserContext = await browser.newContext();
  const playerBContext: BrowserContext = await browser.newContext();
  const keeper = await keeperContext.newPage();
  const playerA = await playerAContext.newPage();
  const playerB = await playerBContext.newPage();
  // 本项目 Playwright 配置没有 actionTimeout：不显式给超时，不可交互的定位
  // 会一直等到用例超时，把真正的失败原因藏起来。
  for (const page of [keeper, playerA, playerB]) page.setDefaultTimeout(20_000);
  const keeperFrames = collectFrames(keeper);
  const playerAFrames = collectFrames(playerA);
  const playerBFrames = collectFrames(playerB);
  const keeperName = `keeper${runId}`;
  const playerAName = `alice${runId}`;
  const playerBName = `bob${runId}`;
  const roomName = `结构化验收房${runId}`;

  try {
    // ---- 建房：勾选“结构化操作模式（人类主持）” ----
    await register(keeper, keeperName);
    await keeper.getByLabel("房间名称").fill(roomName);
    await keeper.getByLabel(/结构化操作模式/).check();
    await keeper.getByRole("button", { name: "创建房间" }).click();
    await expect(keeper.getByRole("heading", { name: roomName })).toBeVisible();
    // 主持不认领调查员（规格 §7）：模组只有两个可选角色，主持占一个会让
    // 第二位玩家无角可选。
    await keeper.getByRole("button", { name: "生成邀请码" }).click();
    const invite = (
      await keeper.locator(".invite-token").textContent()
    )?.trim();
    expect(invite).toBeTruthy();

    // ---- 两位玩家加入并各认领一名调查员 ----
    for (const [page, name] of [
      [playerA, playerAName],
      [playerB, playerBName],
    ] as const) {
      await register(page, name);
      await page
        .getByRole("textbox", { name: "邀请码", exact: true })
        .fill(invite!);
      await page.getByRole("button", { name: "加入房间" }).click();
      await expect(page.getByRole("heading", { name: roomName })).toBeVisible();
      await page.getByRole("button", { name: "选择" }).first().click();
      await expect(page.getByRole("button", { name: "释放" })).toBeVisible();
    }

    // 甲的调查员标识（结构化层的 character_key）：从服务端 REST 投影里取，
    // 而不是猜候选顺序 —— 后面所有定向操作都必须打在甲身上。
    const playerAInvestigator = await playerA.evaluate(async () => {
      const worldId = localStorage.getItem("trpg-online-world-id") ?? "";
      const me = await (
        await fetch("/api/auth/me", { credentials: "include" })
      ).json();
      const info = await (
        await fetch(`/api/worlds/${encodeURIComponent(worldId)}/members`, {
          credentials: "include",
        })
      ).json();
      const rows = (info.members ?? []) as {
        user_id: string;
        investigator?: { character_key?: string } | null;
      }[];
      return (
        rows.find((row) => row.user_id === me.id)?.investigator
          ?.character_key ?? ""
      );
    });
    expect(playerAInvestigator, "甲没有认领到调查员").not.toBe("");

    // ---- 准备（以服务端广播的 room_state.ready_user_ids 为准） ----
    for (const page of [keeper, playerA, playerB]) {
      await expect(page.locator(".member-row").first()).toBeVisible();
      await page.getByRole("button", { name: "准备" }).click();
    }
    const readyCount = () => {
      const states = framesOf(keeperFrames.received, "room_state");
      const latest = JSON.parse(states[states.length - 1] ?? "{}") as {
        ready_user_ids?: string[];
      };
      return latest.ready_user_ids?.length ?? 0;
    };
    await expect
      .poll(readyCount, { timeout: 30_000, message: "三人准备状态没有生效" })
      .toBe(3);

    // ---- 开局（human：不建模型会话、不要求 BYOK） ----
    const start = keeper.getByRole("button", { name: "开始游戏" });
    await expect(start, "全员已准备后开局按钮仍不可用").toBeEnabled();
    await start.click();
    for (const page of [keeper, playerA, playerB]) {
      await expect(page.locator("#user-input")).toBeEnabled({
        timeout: 90_000,
      });
      await expect(page.getByTestId("structured-tool-row")).toBeVisible({
        timeout: 60_000,
      });
      await expect(page.getByTestId("btn-move")).toBeVisible();
    }
    expect(framesOf(keeperFrames.sent, "start")).toHaveLength(1);
    expect(
      playerAFrames.received.filter((frame) =>
        frame.includes("session_snapshot"),
      ),
    ).not.toHaveLength(0);

    // ---- ① 主持定向私发线索给甲（乙不可见） ----
    await keeper.getByTestId("btn-keeper-console").click();
    await expect(keeper.getByRole("dialog", { name: "主持台" })).toBeVisible();
    await keeper.getByTestId("keeper-cmd-grant_clue").click();
    const clueOptions = await keeper
      .locator('[data-field="clue_id"] select option')
      .evaluateAll((nodes) =>
        nodes
          .map((node) => (node as HTMLOptionElement).value)
          .filter((value) => value.length > 0),
      );
    expect(clueOptions.length, "主持台没有可发放的线索候选").toBeGreaterThan(0);
    await keeper
      .locator('[data-field="clue_id"] select')
      .selectOption(clueOptions[0]);

    // 接收者候选是结构化层的调查员标识（character_key）。
    const recipients = (
      (await keeper
        .locator('[data-field="recipient_investigator_ids"] input')
        .getAttribute("placeholder")) ?? ""
    )
      .split(",")
      .map((value) => value.trim())
      .filter(Boolean);
    expect(recipients.length, "主持台没有调查员候选").toBeGreaterThan(0);
    // 定向发放必须打在甲身上（候选里的标识应与 REST 投影一致）。
    expect(
      recipients,
      `主持台候选中没有甲的调查员 ${playerAInvestigator}`,
    ).toContain(playerAInvestigator);
    const targetInvestigator = playerAInvestigator;
    await keeper
      .locator('[data-field="recipient_investigator_ids"] input')
      .fill(targetInvestigator);
    await keeper.locator('[data-field="basis"] input').fill("医生当面说明");
    const grantSubmit = await submitKeeperCommand(keeper, keeperFrames);
    expect(grantSubmit.accepted, `私发线索被拒：${grantSubmit.lastError}`).toBe(
      true,
    );

    await expect
      .poll(() => framesOf(keeperFrames.sent, "command_request").length, {
        timeout: 20_000,
      })
      .toBe(1);
    const grantFrame = JSON.parse(
      framesOf(keeperFrames.sent, "command_request")[0],
    ) as { kind: string; payload: Record<string, unknown> };
    expect(grantFrame.kind).toBe("grant_clue");
    expect(grantFrame.payload).toMatchObject({
      basis: "医生当面说明",
      recipient_investigator_ids: [targetInvestigator],
    });

    await expect
      .poll(() => framesOf(playerAFrames.received, "clue_granted").length, {
        timeout: 30_000,
      })
      .toBeGreaterThan(0);
    expect(framesOf(playerBFrames.received, "clue_granted")).toHaveLength(0);
    // M5：记忆是主持侧资料 —— 玩家连接（帧级）不应收到记忆记录或查询结果。
    for (const player of [playerAFrames, playerBFrames]) {
      expect(framesOf(player.received, "memory_recorded")).toHaveLength(0);
      expect(framesOf(player.received, "memory_query_result")).toHaveLength(0);
    }
    // 玩家页面上也不应出现主持只读的记忆查询入口。
    expect(
      await playerB.locator('[data-testid="keeper-memory-query"]').count(),
    ).toBe(0);
    await expect(keeper.locator(".action-status-card").last()).toContainText(
      "已处理完成",
    );

    // ---- ② 主持请求检定 → 甲在持久检定卡上掷骰 ----
    // 技能必须用角色卡上真实存在的技能键（服务端按技能键校验）。
    await keeper.getByTestId("keeper-cmd-request_check").click();
    const skillOptions = await keeper
      .locator("#keeper-skill-options option")
      .evaluateAll((nodes) =>
        nodes.map((node) => (node as HTMLOptionElement).value),
      );
    expect(skillOptions.length, "主持台没有技能候选").toBeGreaterThan(0);

    let checkAccepted = false;
    let lastCheckError = "";
    for (const skill of skillOptions.slice(0, 4)) {
      await keeper.getByTestId("keeper-cmd-request_check").click();
      await setKeeperField(keeper, "investigator_id", targetInvestigator);
      await setKeeperField(keeper, "skill", skill);
      await keeper
        .locator('[data-field="difficulty"] select')
        .selectOption("regular");
      await keeper
        .locator('[data-field="attempt"] input')
        .fill("向医生说明来意");
      await keeper
        .locator('[data-field="visibility"] select')
        .selectOption("public");
      const before = framesOf(keeperFrames.received, "check_requested").length;
      const submitted = await submitKeeperCommand(keeper, keeperFrames);
      if (
        submitted.accepted &&
        framesOf(keeperFrames.received, "check_requested").length > before
      ) {
        checkAccepted = true;
        break;
      }
      lastCheckError = submitted.lastError;
    }
    expect(checkAccepted, `检定被拒：${lastCheckError}`).toBe(true);

    const checkCard = playerA.locator(".check-request-card");
    await expect(checkCard).toBeVisible({ timeout: 30_000 });
    // 检定参数来自服务端请求：卡片里没有可改参数的控件。
    expect(await checkCard.locator("input, select").count()).toBe(0);
    await checkCard.getByRole("button", { name: "掷骰", exact: true }).click();
    await expect
      .poll(() => framesOf(playerAFrames.sent, "check_response").length, {
        timeout: 30_000,
      })
      .toBe(1);
    const checkResponse = JSON.parse(
      framesOf(playerAFrames.sent, "check_response")[0],
    ) as Record<string, unknown>;
    expect(checkResponse).toMatchObject({ decision: "roll" });
    // 按钮不携带任何能影响结果的参数。
    expect(checkResponse).not.toHaveProperty("skill");
    expect(checkResponse).not.toHaveProperty("target_value");
    await expect(playerA.locator(".check-request-card")).toContainText("vs", {
      timeout: 30_000,
    });
    // 乙不是被指定的调查员：他没有可掷骰的卡。
    await expect(
      playerB
        .locator(".check-request-card")
        .getByRole("button", { name: "掷骰" }),
    ).toHaveCount(0);

    // ---- ③ 主持调整 SAN ----
    const statusText = () =>
      playerA.evaluate(
        () => document.querySelector(".inv-card-status")?.textContent ?? "",
      );
    const sanBefore = await statusText();
    await keeper.getByTestId("keeper-cmd-adjust_stat").click();
    await setKeeperField(keeper, "investigator_id", targetInvestigator);
    await keeper.locator('[data-field="field"] select').selectOption("san");
    await keeper.locator('[data-field="delta"] input').fill("-3");
    await keeper.locator('[data-field="reason"] input').fill("目击遗体");
    const sanSubmit = await submitKeeperCommand(keeper, keeperFrames);
    expect(sanSubmit.accepted, `调整 SAN 被拒：${sanSubmit.lastError}`).toBe(
      true,
    );
    await expect
      .poll(() => framesOf(playerAFrames.received, "state_changed").length, {
        timeout: 30_000,
      })
      .toBeGreaterThan(0);
    await expect.poll(statusText, { timeout: 30_000 }).not.toBe(sanBefore);

    // ---- ④ 整队移动：主持用 move_party（玩家式 action_request{move} 需要
    // 认领调查员；主持不占角色，所以权威路径是主持命令），两端场景一致。 ----
    const sceneBefore = await playerA.locator(".header-scene-name").innerText();
    await keeper.getByTestId("keeper-cmd-move_party").click();
    const sceneOptions = await keeper
      .locator(
        '[data-field="destination_scene_id"] select option, [data-field="destination_scene_id"] datalist option',
      )
      .evaluateAll((nodes) =>
        nodes
          .map((node) => (node as HTMLOptionElement).value)
          .filter((value) => value.length > 0),
      );
    const destination = sceneOptions.find((value) => value !== "") ?? "";
    expect(destination, "主持台没有可选目的地").not.toBe("");
    await setKeeperField(keeper, "destination_scene_id", destination);
    const moveSubmit = await submitKeeperCommand(keeper, keeperFrames);
    expect(moveSubmit.accepted, `整队移动被拒：${moveSubmit.lastError}`).toBe(
      true,
    );
    await keeper.getByRole("button", { name: "关闭主持台" }).click();
    await expect
      .poll(async () => playerA.locator(".header-scene-name").innerText(), {
        timeout: 30_000,
      })
      .not.toBe(sceneBefore);
    expect(await playerB.locator(".header-scene-name").innerText()).toBe(
      await playerA.locator(".header-scene-name").innerText(),
    );

    // ---- ⑤ 存档入口 + 甲刷新重连 ----
    await keeper.getByTestId("btn-keeper-console").click();
    await expect(keeper.getByTestId("keeper-save-panel")).toBeVisible();
    await keeper.getByRole("button", { name: "关闭主持台" }).click();

    const sceneNow = await keeper.locator(".header-scene-name").innerText();
    await playerA.reload();
    await expect(playerA.getByTestId("structured-tool-row")).toBeVisible({
      timeout: 90_000,
    });
    await expect(playerA.locator(".header-scene-name")).toHaveText(sceneNow, {
      timeout: 30_000,
    });

    // 全程没有模型调用（base url 指向关闭端口）。
    for (const page of [keeper, playerA, playerB]) {
      await expect(
        page.getByText(/无法连接配置的模型|模型.*不可用|connect.*fail/i),
      ).toHaveCount(0);
    }
  } finally {
    await keeperContext.close();
    await playerAContext.close();
    await playerBContext.close();
  }
});
