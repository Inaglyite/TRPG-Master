/**
 * 开局前的就绪等待（替代「等 boot-loader 消失」）。
 *
 * 为什么不能用 `.boot-loader` 消失当就绪信号：Playwright 的 `toBeHidden()`
 * 对**元素不存在**同样成立（本仓库有微证明：about:blank 上断言 .boot-loader
 * toBeHidden 通过）。也就是说页面白屏、React 根本没挂载时，这个断言照样通过，
 * 紧接着 `.module-select-trigger` 点不到，只能干等 30s 超时——CI 上就是这样
 * 表现成「module-select-trigger 超时」的。
 *
 * 这里改成**语义就绪**：先等 React 真的挂载（#app 由 React 渲染），再等开局
 * 选择页可用；两种形态都接受：
 *   - 开局选择页直接出现 → 直接返回；
 *   - 世界已在游戏中（结构化世界会随服务端快照自动续上）→ 走产品自己的
 *     「返回开局选择」入口（#btn-new）回到开局页。
 *
 * 超时不是重试、也不是跳过：它把页面侧的证据（挂载状态、覆盖层、失败的请求、
 * 控制台错误）收集起来一起抛出，让失败本身带原因。
 */

import { expect, type Page } from "@playwright/test";

export type BootDiagnostics = {
  consoleErrors: string[];
  pageErrors: string[];
  failedRequests: string[];
};

const attached = new WeakSet<Page>();

/** 挂上页面侧取证（每个 page 只挂一次）。 */
export function attachBootDiagnostics(page: Page): BootDiagnostics {
  const sink: BootDiagnostics = {
    consoleErrors: [],
    pageErrors: [],
    failedRequests: [],
  };
  if (attached.has(page)) return sink;
  attached.add(page);
  page.on("console", (message) => {
    if (message.type() === "error") {
      sink.consoleErrors.push(message.text().slice(0, 500));
    }
  });
  page.on("pageerror", (error) => {
    sink.pageErrors.push(String(error.message).slice(0, 500));
  });
  page.on("requestfailed", (request) => {
    sink.failedRequests.push(
      `${request.method()} ${request.url()} — ${
        request.failure()?.errorText ?? "unknown"
      }`,
    );
  });
  return sink;
}

type StartScreenState = {
  mounted: boolean;
  startScreen: boolean;
  inGame: boolean;
  triggerCount: number;
  overlayClass: string;
  readyState: string;
  inputEnabled: boolean;
};

async function readStartScreenState(page: Page): Promise<StartScreenState> {
  return page.evaluate(() => {
    const overlay = document.querySelector("#start-overlay");
    return {
      // #app 由 React（GameShell）渲染：存在即证明 React 已挂载。
      mounted: Boolean(document.querySelector("#app")),
      startScreen: Boolean(document.querySelector(".module-select-trigger")),
      inGame: Boolean(document.querySelector("#btn-new")),
      triggerCount: document.querySelectorAll(".module-select-trigger").length,
      overlayClass: overlay?.className ?? "missing",
      readyState: document.readyState,
      inputEnabled: !(
        document.querySelector("#user-input") as HTMLTextAreaElement | null
      )?.disabled,
    };
  });
}

/**
 * 等「可以选模组开局」这一语义状态。
 *
 * 注意：这里**不接受**「找不到元素就放过」；超时会带上取证信息抛出。
 */
export async function waitForStartScreen(
  page: Page,
  options: { timeout?: number } = {},
): Promise<BootDiagnostics> {
  // 45s：就绪等待 + 之后点击的 30s 仍小于用例 90s 的总超时，
  // 不会把「明确的失败」拖成「用例超时」（不靠放大超时掩盖问题）。
  const timeout = options.timeout ?? 45_000;
  const sink = attachBootDiagnostics(page);
  const deadline = Date.now() + timeout;
  let last: StartScreenState | null = null;

  while (Date.now() < deadline) {
    last = await readStartScreenState(page);
    if (last.startScreen) return sink;
    if (last.inGame) {
      // 已在游戏中（结构化世界随快照自动续上）：用产品自带的入口回开局页，
      // 而不是和自动续上的时序抢跑。
      await page.locator("#btn-new").click();
      await expect(page.locator(".module-select-trigger")).toBeVisible({
        timeout: 15_000,
      });
      return sink;
    }
    await page.waitForTimeout(100);
  }

  throw new Error(
    [
      "等待开局选择页超时（不是重试、不是跳过，这里必须查清原因）：",
      JSON.stringify({ ...last, diagnostics: sink }, null, 2),
      `page.url=${page.url()}`,
    ].join("\n"),
  );
}

/**
 * 本地模式开局前的完整就绪：跳转 → 语义就绪（React 已挂载 + 开局页可用）。
 * 调用方随后照常选模组、点开始——断言一条都不放宽。
 */
export async function openLocalStartScreen(
  page: Page,
  url: string,
  options: { timeout?: number } = {},
): Promise<BootDiagnostics> {
  await page.goto(url);
  return waitForStartScreen(page, options);
}
