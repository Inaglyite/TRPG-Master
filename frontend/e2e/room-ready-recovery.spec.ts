/** Real isolated backend; deliberately delay only the room WS authority snapshot. */
import { spawn, type ChildProcess } from "node:child_process";
import { existsSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { expect, request, test } from "@playwright/test";

const root = resolve(import.meta.dirname, "../..");
const base = "http://127.0.0.1:8874";
let runtime = "";
let processHandle: ChildProcess | null = null;
let output = "";

test.beforeAll(async () => {
  runtime = mkdtempSync(join(tmpdir(), "trpg-room-ready-"));
  const python =
    process.env.TRPG_E2E_PYTHON ??
    (existsSync(resolve(root, ".venv/bin/python"))
      ? resolve(root, ".venv/bin/python")
      : "python");
  processHandle = spawn(
    python,
    [
      "-m",
      "uvicorn",
      "server:app",
      "--host",
      "127.0.0.1",
      "--port",
      "8874",
      "--workers",
      "1",
    ],
    {
      cwd: root,
      env: {
        ...Object.fromEntries(
          Object.entries(process.env).filter(
            ([key]) => !/^(http_proxy|https_proxy|all_proxy)$/i.test(key),
          ),
        ),
        NO_PROXY: "127.0.0.1,localhost",
        TRPG_RUNTIME_ROOT: runtime,
        TRPG_DATABASE_URL: `sqlite:///${join(runtime, "e2e.db")}`,
        TRPG_ALLOWED_ORIGINS: base,
        TRPG_REQUIRE_AUTH: "1",
        TRPG_WRITE_COMPAT_EXPORTS: "0",
        OPENAI_API_KEY: "e2e-placeholder",
        OPENAI_BASE_URL: "http://127.0.0.1:9/v1",
      },
      stdio: ["ignore", "pipe", "pipe"],
    },
  );
  processHandle.stdout?.on("data", (chunk) => {
    output += String(chunk);
  });
  processHandle.stderr?.on("data", (chunk) => {
    output += String(chunk);
  });
  const client = await request.newContext();
  try {
    for (let attempt = 0; attempt < 80; attempt++) {
      try {
        if ((await client.get(`${base}/api/health`)).ok()) return;
      } catch {
        /* Starting. */
      }
      await new Promise((done) => setTimeout(done, 125));
    }
    throw new Error(
      `Local backend did not become ready: ${output.slice(-4000)}`,
    );
  } finally {
    await client.dispose();
  }
});

test.afterAll(async () => {
  if (processHandle && processHandle.exitCode === null) {
    processHandle.kill("SIGTERM");
    await new Promise<void>((done) => {
      const timer = setTimeout(done, 3000);
      processHandle?.once("exit", () => {
        clearTimeout(timer);
        done();
      });
    });
    if (processHandle.exitCode === null) processHandle.kill("SIGKILL");
  }
  if (runtime) rmSync(runtime, { recursive: true, force: true });
});

test("房间镜像未同步时不能准备，同步后仅主动点击才落账", async ({ page }) => {
  test.setTimeout(120000);
  let allowSnapshot = false;
  const releaseSnapshots: (() => void)[] = [];
  const sent: Record<string, unknown>[] = [];
  let deletes = 0;
  page.on("request", (request) => {
    if (request.method() === "DELETE" && /\/api\/worlds\//.test(request.url()))
      deletes++;
  });
  await page.addInitScript((origin) => {
    localStorage.setItem("trpg-cloud-origin", origin);
  }, base);
  await page.routeWebSocket(/\/ws\/room\?/, (socket) => {
    const upstream = socket.connectToServer();
    socket.onMessage((message) => {
      sent.push(JSON.parse(String(message)));
      upstream.send(message);
    });
    upstream.onMessage((message) => {
      const frame = JSON.parse(String(message));
      if (!allowSnapshot && frame.type === "room_full_state") {
        releaseSnapshots.push(() => socket.send(message));
      } else socket.send(message);
    });
  });
  await page.goto(`${base}/?mode=online`);
  await expect(page.locator(".boot-loader")).toHaveCount(0);
  await page.getByRole("tab", { name: "注册", exact: true }).click();
  await page.getByLabel("用户名", { exact: true }).fill(`ready${Date.now()}`);
  await page.getByLabel("密码", { exact: true }).fill("e2e-test-password-only");
  await page
    .getByLabel("确认密码", { exact: true })
    .fill("e2e-test-password-only");
  await page.getByRole("button", { name: "注册并登录", exact: true }).click();
  await page.getByLabel("房间名称").fill("等待房间同步验收");
  await page.getByRole("radio", { name: "人类主持", exact: true }).click();
  await page.getByRole("button", { name: "创建房间", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "等待房间同步验收" }),
  ).toBeVisible();
  await expect.poll(() => releaseSnapshots.length).toBeGreaterThan(0);
  const ready = page.getByRole("button", { name: "准备", exact: true });
  const deleteEntry = page.getByRole("button", {
    name: "删除房间",
    exact: true,
  });
  await expect(ready).toBeDisabled();
  for (const width of [1280, 939, 640, 390]) {
    await page.setViewportSize({ width, height: 480 });
    for (const button of [ready, deleteEntry]) {
      await button.scrollIntoViewIfNeeded();
      await expect(button).toBeInViewport();
      const shape = await button.evaluate((button) => {
        const rect = button.getBoundingClientRect();
        const style = getComputedStyle(button);
        return {
          height: rect.height,
          inside:
            rect.left >= 0 &&
            rect.right <= innerWidth &&
            rect.top >= 0 &&
            rect.bottom <= innerHeight,
          hit: button.contains(
            document.elementFromPoint(
              rect.x + rect.width / 2,
              rect.y + rect.height / 2,
            ),
          ),
          padding: parseFloat(style.paddingLeft),
          nowrap: style.whiteSpace,
        };
      });
      expect(shape.height).toBeGreaterThanOrEqual(44);
      expect(shape.inside).toBe(true);
      expect(shape.hit).toBe(true);
      expect(shape.padding).toBeGreaterThanOrEqual(10);
      expect(shape.nowrap).toBe("nowrap");
    }
    await ready.scrollIntoViewIfNeeded();
    await page.screenshot({
      path: `test-results/room-ready-wait-${width}.png`,
    });
  }
  await deleteEntry.click();
  await expect(
    page.getByRole("button", { name: "确认删除房间", exact: true }),
  ).toBeVisible();
  expect(deletes).toBe(0);
  await page.getByRole("button", { name: "取消", exact: true }).click();
  expect(deletes).toBe(0);
  expect(sent.filter((frame) => frame.type === "room_ready")).toHaveLength(0);
  allowSnapshot = true;
  releaseSnapshots.forEach((release) => release());
  await expect(ready).toBeEnabled();
  expect(sent.filter((frame) => frame.type === "room_ready")).toHaveLength(0);
  // Check the actual room, not just the disabled loading placeholder. A short
  // viewport must still allow character, invitation and keeper operations.
  for (const { width, height } of [
    { width: 1280, height: 720 },
    { width: 939, height: 640 },
    { width: 640, height: 480 },
    { width: 390, height: 360 },
  ]) {
    await page.setViewportSize({ width, height });
    const controls = page.locator(".online-room-screen").getByRole("button");
    for (const button of await controls.all()) {
      if (!(await button.isVisible())) continue;
      await button.scrollIntoViewIfNeeded();
      const shape = await button.evaluate((element) => {
        const rect = element.getBoundingClientRect();
        const style = getComputedStyle(element);
        return {
          label: element.textContent,
          height: rect.height,
          inside:
            rect.left >= 0 &&
            rect.right <= innerWidth &&
            rect.top >= 0 &&
            rect.bottom <= innerHeight,
          hit: element.contains(
            document.elementFromPoint(
              rect.x + rect.width / 2,
              rect.y + rect.height / 2,
            ),
          ),
          nowrap: style.whiteSpace,
          padding: parseFloat(style.paddingLeft),
        };
      });
      expect(shape.height, shape.label ?? "button").toBeGreaterThanOrEqual(44);
      expect(shape.inside, shape.label ?? "button").toBe(true);
      expect(shape.hit, shape.label ?? "button").toBe(true);
      expect(shape.nowrap, shape.label ?? "button").toBe("nowrap");
      expect(shape.padding, shape.label ?? "button").toBeGreaterThanOrEqual(10);
    }
    await page
      .locator(".online-room-screen")
      .getByRole("heading", { name: "调查员", exact: true })
      .scrollIntoViewIfNeeded();
    await page.screenshot({ path: `/tmp/trpg-room-controls-${width}.png` });
    for (const name of ["邀请角色", "有效期（小时）", "使用次数"]) {
      const control = page.getByLabel(name, { exact: true });
      const label = control.locator("..");
      await label.scrollIntoViewIfNeeded();
      await expect(label.locator("span")).toHaveText(name);
      const shape = await label.evaluate((element) => {
        const rect = element.getBoundingClientRect();
        const input = element.querySelector("input,select")!;
        const field = input.getBoundingClientRect();
        return {
          inside:
            rect.left >= 0 &&
            rect.right <= innerWidth &&
            rect.top >= 0 &&
            rect.bottom <= innerHeight,
          height: field.height,
          hit: input.contains(
            document.elementFromPoint(
              field.x + field.width / 2,
              field.y + field.height / 2,
            ),
          ),
        };
      });
      expect(shape.inside, name).toBe(true);
      expect(shape.height, name).toBeGreaterThanOrEqual(44);
      expect(shape.hit, name).toBe(true);
    }
    await page.locator(".invite-create").scrollIntoViewIfNeeded();
    await page.screenshot({
      path: `/tmp/trpg-room-invite-labels-${width}.png`,
    });
  }
  await page.getByRole("button", { name: "撤销主持", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "确认撤销主持", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "取消", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "撤销主持", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "选择", exact: true }).first().click();
  await expect(
    page.getByRole("button", { name: "释放", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "释放", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "释放", exact: true }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "生成邀请码", exact: true }).click();
  await expect(page.locator(".invite-token")).toBeVisible();
  await expect(page.locator(".invite-token")).not.toHaveText("");
  await page
    .locator(".invite-box")
    .getByRole("button", { name: "撤销", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "生成邀请码", exact: true }),
  ).toBeVisible();
  await ready.click();
  await expect(
    page.getByRole("button", { name: "取消准备", exact: true }),
  ).toBeVisible();
  expect(sent.filter((frame) => frame.type === "room_ready")).toHaveLength(1);
});
