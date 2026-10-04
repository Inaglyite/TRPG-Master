/** Two independent temporary TLS backends; never connects to production. */
import { spawn, spawnSync, type ChildProcess } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { expect, request, test, type Page } from "@playwright/test";

const repositoryRoot = resolve(import.meta.dirname, "../..");
const origins = ["https://127.0.0.1:8788", "https://127.0.0.1:8789"];
const instances: { root: string; child: ChildProcess; output: string }[] = [];
const runId = Date.now();

test.beforeAll(async () => {
  const health = await request.newContext({ ignoreHTTPSErrors: true });
  try {
    for (const [index, origin] of origins.entries()) {
      const root = mkdtempSync(join(tmpdir(), "trpg-server-boundary-"));
      const certificate = join(root, "certificate.pem");
      const privateKey = join(root, "private-key.pem");
      const tls = spawnSync(
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
      if (tls.status !== 0)
        throw new Error("Cannot create isolated TLS fixture");
      const child = spawn(
        process.env.TRPG_E2E_PYTHON ??
          resolve(repositoryRoot, ".venv/bin/python3"),
        [
          "-m",
          "uvicorn",
          "server:app",
          "--host",
          "127.0.0.1",
          "--port",
          String(8788 + index),
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
            TRPG_RUNTIME_ROOT: root,
            TRPG_DATABASE_URL: `sqlite:///${join(root, "e2e.db")}`,
            TRPG_REQUIRE_AUTH: "1",
            TRPG_ALLOW_REGISTRATION: "1",
            TRPG_ALLOWED_ORIGINS: origins.join(","),
            TRPG_WRITE_COMPAT_EXPORTS: "0",
            OPENAI_API_KEY: "isolated-unusable-fixture-key",
            OPENAI_BASE_URL: "http://127.0.0.1:9/v1",
            TRPG_STREAM_USAGE: "0",
          },
          stdio: ["ignore", "pipe", "pipe"],
        },
      );
      const instance = { root, child, output: "" };
      instances.push(instance);
      child.stdout?.on("data", (chunk) => {
        instance.output += String(chunk);
      });
      child.stderr?.on("data", (chunk) => {
        instance.output += String(chunk);
      });
      let ready = false;
      for (let attempt = 0; attempt < 100; attempt += 1) {
        try {
          if ((await health.get(`${origin}/api/health`)).ok()) {
            ready = true;
            break;
          }
        } catch {
          /* starting */
        }
        await new Promise((resolveWait) => setTimeout(resolveWait, 125));
      }
      if (!ready)
        throw new Error(
          `Isolated backend failed: ${instance.output.slice(-3000)}`,
        );
    }
  } finally {
    await health.dispose();
  }
});

test.afterAll(async () => {
  for (const { root, child } of instances) {
    if (child.exitCode === null) {
      child.kill("SIGTERM");
      await new Promise<void>((resolveWait) => {
        const timer = setTimeout(resolveWait, 3000);
        child.once("exit", () => {
          clearTimeout(timer);
          resolveWait();
        });
      });
      if (child.exitCode === null) child.kill("SIGKILL");
    }
    rmSync(root, { recursive: true, force: true });
  }
});

async function registerInUi(page: Page, name: string) {
  await page.getByRole("tab", { name: "注册" }).click();
  await page.getByLabel("用户名").fill(name);
  await page.getByLabel("密码", { exact: true }).fill("isolated test password");
  await page.getByLabel("确认密码").fill("isolated test password");
  await page.getByRole("button", { name: "注册并登录" }).click();
  await expect(page.getByRole("heading", { name: "我的冒险" })).toBeVisible();
  await expect(
    page.getByRole("button", { name: "刷新", exact: true }),
  ).toBeEnabled();
}

async function inspectConnectionButtons(page: Page, stem: string) {
  for (const width of [1280, 939, 640, 390]) {
    await page.setViewportSize({ width, height: width === 390 ? 640 : 900 });
    const buttons = page.locator(
      ".online-server button, .online-auth-recovery button, .online-auth-heading button",
    );
    for (const button of await buttons.all()) {
      await button.scrollIntoViewIfNeeded();
      const metrics = await button.evaluate((node) => {
        const box = node.getBoundingClientRect();
        const style = getComputedStyle(node);
        const hit = document.elementFromPoint(
          box.x + box.width / 2,
          box.y + box.height / 2,
        );
        return {
          height: box.height,
          padding: parseFloat(style.paddingLeft),
          fits: node.scrollWidth <= node.clientWidth + 1,
          nowrap: style.whiteSpace,
          hit: !!hit && (hit === node || node.contains(hit)),
        };
      });
      expect(metrics.height).toBeGreaterThanOrEqual(44);
      expect(metrics.padding).toBeGreaterThanOrEqual(10);
      expect(metrics.fits).toBe(true);
      expect(metrics.nowrap).toBe("nowrap");
      expect(metrics.hit).toBe(true);
    }
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth + 1,
      ),
    ).toBe(true);
    const folder = page.locator(".archive-folder-panel").first();
    await folder.scrollIntoViewIfNeeded();
    const skin = await folder.evaluate((node) => {
      const style = getComputedStyle(node, "::before");
      return {
        source: style.borderImageSource,
        slice: style.borderImageSlice,
        edges: [
          style.borderTopWidth,
          style.borderRightWidth,
          style.borderBottomWidth,
          style.borderLeftWidth,
        ],
      };
    });
    expect(skin.source).toContain("folder-panel-");
    expect(skin.slice).toContain("fill");
    expect(skin.edges).toEqual(
      stem === "editor"
        ? ["60px", "24px", "24px", "177px"]
        : ["54px", "21px", "24px", "126px"],
    );
    await page.screenshot({
      path: `/tmp/trpg-server-${stem}-${width}.png`,
      fullPage: true,
    });
  }
}

test("switching actual servers discards credentials and ignores a delayed real old-server 401", async ({
  page,
}) => {
  await page.goto(origins[0]);
  await page.getByRole("button", { name: /云端单人/ }).click();
  await registerInUi(page, `server_a_${runId}`);
  const modules = await (
    await page.request.get(`${origins[0]}/api/modules`)
  ).json();
  const created = await page.request.post(`${origins[0]}/api/worlds`, {
    headers: { Origin: origins[0] },
    data: {
      module: modules.modules[0].id,
      name: "仅在服务器A的调查",
      play_mode: "solo",
      execution_profile: "structured_v1",
      keeper_mode: "human",
    },
  });
  expect(created.ok()).toBe(true);
  await page.getByRole("button", { name: "刷新", exact: true }).click();
  await expect(
    page.getByText("仅在服务器A的调查", { exact: true }),
  ).toBeVisible();

  // Revoke A's cookie at the real server. Its next real /worlds 401 is held
  // in flight, rather than replacing it with a fabricated success/error.
  expect(
    (
      await page.request.post(`${origins[0]}/api/auth/logout`, {
        headers: { Origin: origins[0] },
      })
    ).ok(),
  ).toBe(true);
  let received!: () => void;
  const oldResponseReady = new Promise<void>((resolveReady) => {
    received = resolveReady;
  });
  let release!: () => void;
  const gate = new Promise<void>((resolveGate) => {
    release = resolveGate;
  });
  await page.route(`${origins[0]}/api/worlds`, async (route) => {
    const response = await route.fetch();
    expect(response.status()).toBe(401);
    received();
    await gate;
    await route.fulfill({ response });
  });
  try {
    await page.getByRole("button", { name: "刷新", exact: true }).click();
    await oldResponseReady;
    await page.getByRole("button", { name: "退出登录", exact: true }).click();
    await expect(
      page.getByRole("button", { name: "登录", exact: true }),
    ).toBeEnabled();
    await page.getByLabel("用户名").fill("do-not-forward-to-b");
    await page.getByLabel("密码", { exact: true }).fill("old typed credential");
    await page.getByRole("button", { name: "修改服务器" }).click();
    await expect(page.getByLabel("服务器地址")).toBeFocused();
    await page.getByLabel("服务器地址").fill(origins[1]);
    await inspectConnectionButtons(page, "editor");
    await page.getByRole("button", { name: "保存并重新检查" }).click();
    await expect(
      page.getByRole("button", { name: "登录", exact: true }),
    ).toBeEnabled();
    await expect(page.getByLabel("用户名")).toHaveValue("");
    await expect(page.getByLabel("密码", { exact: true })).toHaveValue("");
    await expect(page.locator(".online-server-origin")).toHaveText(origins[1]);
    await registerInUi(page, `server_b_${runId}`);
    const done = page.waitForResponse(`${origins[0]}/api/worlds`);
    release();
    expect((await done).status()).toBe(401);
    await expect(page.getByRole("heading", { name: "我的冒险" })).toBeVisible();
    await expect(page.locator(".online-user")).toHaveText(`server_b_${runId}`);
    await expect(
      page.getByText("仅在服务器A的调查", { exact: true }),
    ).toHaveCount(0);
    // A current request to B still succeeds after the delayed old 401.
    await page.getByRole("button", { name: "刷新", exact: true }).click();
    await expect(
      page.getByRole("button", { name: "刷新", exact: true }),
    ).toBeEnabled();
    expect((await page.request.get(`${origins[1]}/api/auth/me`)).ok()).toBe(
      true,
    );
  } finally {
    release();
    await page.unroute(`${origins[0]}/api/worlds`);
  }
});

test("retina UI uses a 2x paper illustration and keyboard-operable folder actions", async ({
  browser,
}) => {
  const context = await browser.newContext({
    ignoreHTTPSErrors: true,
    deviceScaleFactor: 2,
    viewport: { width: 1280, height: 900 },
  });
  const page = await context.newPage();
  try {
    // Simulate a transport failure, not a model or an invented auth payload.
    await page.route(`${origins[0]}/api/auth/me`, (route) =>
      route.abort("failed"),
    );
    await page.goto(origins[0]);
    await page.getByRole("button", { name: /云端单人/ }).click();
    await expect(
      page.getByRole("heading", { name: "无法连接服务器" }),
    ).toBeVisible();
    const art = page.locator(".online-connection-art img");
    await expect
      .poll(() => art.evaluate((node) => (node as HTMLImageElement).complete))
      .toBe(true);
    expect(
      await art.evaluate((node) => (node as HTMLImageElement).currentSrc),
    ).toContain("@2x");
    expect(await page.evaluate(() => devicePixelRatio)).toBe(2);
    await page.screenshot({
      path: "/tmp/trpg-server-recovery-retina.png",
      fullPage: true,
    });
    await page.getByRole("button", { name: "修改服务器" }).focus();
    await page.keyboard.press("Enter");
    await expect(page.getByLabel("服务器地址")).toBeFocused();
    await expect(
      page.getByRole("button", { name: "保存并重新检查" }),
    ).toBeVisible();
    await page.getByRole("button", { name: "取消", exact: true }).focus();
    await page.keyboard.press("Enter");
    await expect(
      page.getByRole("heading", { name: "无法连接服务器" }),
    ).toBeVisible();
    await page.getByRole("button", { name: "返回模式选择" }).focus();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("button", { name: /云端单人/ })).toBeVisible();
    await expect(page.locator(".online-auth-screen")).toHaveCount(0);
  } finally {
    await context.close();
  }
});

test("checking can be cancelled and a stalled session check times out with usable recovery", async ({
  page,
}) => {
  let release!: () => void;
  let gate = new Promise<void>((resolveGate) => {
    release = resolveGate;
  });
  await page.route(`${origins[0]}/api/auth/me`, async (route) => {
    const response = await route.fetch();
    await gate;
    try {
      await route.fulfill({ response });
    } catch {
      /* deliberately cancelled fetch */
    }
  });
  try {
    await page.goto(origins[0]);
    await page.getByRole("button", { name: /云端单人/ }).click();
    await expect(
      page.locator(".online-auth-screen").getByRole("status"),
    ).toContainText("正在检查登录状态");
    await page.getByRole("button", { name: /返回模式选择/ }).click();
    release();
    await expect(page.getByRole("button", { name: /云端单人/ })).toBeVisible();
    await expect(page.locator(".online-auth-screen")).toHaveCount(0);

    gate = new Promise<void>((resolveGate) => {
      release = resolveGate;
    });
    await page.getByRole("button", { name: /云端单人/ }).click();
    await expect(
      page.locator(".online-auth-screen").getByRole("status"),
    ).toContainText("正在检查登录状态");
    await expect(page.getByRole("alert")).toContainText("服务器未及时响应", {
      timeout: 20_000,
    });
    await expect(page.getByRole("button", { name: "重新检查" })).toBeEnabled();
    await inspectConnectionButtons(page, "recovery");
    release();
    await page.unroute(`${origins[0]}/api/auth/me`);
    await page.getByRole("button", { name: "重新检查" }).click();
    await expect(
      page.getByRole("button", { name: "登录", exact: true }),
    ).toBeEnabled();
    await expect(
      page.getByText("服务器未及时响应", { exact: false }),
    ).toHaveCount(0);
    await expect(
      page.getByRole("heading", { name: "云端单人", exact: true }),
    ).toBeVisible();
  } finally {
    release();
    await page.unroute(`${origins[0]}/api/auth/me`);
  }
});

test("角色库云端账号隔离：真实会话过期立即隐藏旧档案，下一账号无旧卡", async ({
  page,
}) => {
  test.setTimeout(120_000);
  await page.goto(origins[0]);
  await page.getByRole("button", { name: /云端单人/ }).click();
  await registerInUi(page, `library_a_${runId}`);
  const card = {
    name: "甲账号的私密调查员",
    occupation: "记者",
    attributes: {
      STR: 50,
      DEX: 50,
      CON: 50,
      INT: 60,
      POW: 60,
      SIZ: 50,
      APP: 50,
      EDU: 60,
    },
    skills: { library_use: 60 },
    backstory: { description: "甲的未公开经历" },
  };
  const created = await page.request.post(
    `${origins[0]}/api/character-library`,
    { headers: { Origin: origins[0] }, data: card },
  );
  expect(created.ok()).toBe(true);
  await page.getByRole("button", { name: "角色库", exact: true }).click();
  const panel = page.getByRole("dialog", { name: "角色库" });
  await panel.locator(".library-row-main", { hasText: card.name }).click();
  await expect(panel.getByText("甲的未公开经历")).toBeVisible();
  await page.setViewportSize({ width: 939, height: 900 });
  await page.waitForTimeout(1000);
  await page.screenshot({ path: "/tmp/trpg-library-cloud-939.png" });
  expect(
    (
      await page.request.post(`${origins[0]}/api/auth/logout`, {
        headers: { Origin: origins[0] },
      })
    ).ok(),
  ).toBe(true);
  // An actual rejected mutation invokes the shared session-expiry path. No
  // fabricated auth event or direct store manipulation is used in this test.
  const rejected = page.waitForResponse(
    (response) =>
      response.url().includes("/api/character-library/") &&
      response.url().endsWith("/duplicate"),
  );
  await panel.getByRole("button", { name: "复制", exact: true }).click();
  expect((await rejected).status()).toBe(401);
  await expect(panel).not.toBeAttached();
  await expect(page.getByText("甲的未公开经历")).toHaveCount(0);
  await expect(page.getByText("登录已过期，请重新登录")).toBeVisible();
  await registerInUi(page, `library_b_${runId}`);
  await page.getByRole("button", { name: "角色库", exact: true }).click();
  await expect(panel.getByText("角色库还是空的")).toBeVisible();
  await expect(panel.getByText(card.name)).toHaveCount(0);
  const ownList = await (
    await page.request.get(`${origins[0]}/api/character-library`)
  ).json();
  expect(ownList.entries).toHaveLength(0);
  const oldId = (await created.json()).entry.id;
  expect(
    (
      await page.request.get(`${origins[0]}/api/character-library/${oldId}`)
    ).status(),
  ).toBe(404);
});
