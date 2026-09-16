# CI `module-select-trigger` 超时：根因、证据与修复（2026-09-15）

对象：`quality` workflow → `frontend` job → `xvfb-run --auto-servernum npm run test:e2e`
（`.github/workflows/quality.yml:65`）。本文只涉及 CI 与前端验收代码，不动后端。

## 1. 症状（三个失败运行完全一致）

失败签名固定：测试在 `bootWorld` 的

```ts
244 | await page.goto(`${baseUrl}/?mode=local`);
245 | await expect(page.locator(".boot-loader")).toBeHidden({ timeout: 30_000 });
246 | await page.locator(".module-select-trigger").click();   // Timeout 30000ms exceeded
```

稳定复现的四个（+ 轮换的第五、六个，随版本略有出入）：

| 运行 | 失败用例 |
|---|---|
| 34875867502 | `context-memory:375`、`duels:574`、`duels:682`、`duels:773`、`load-branch:561`、`pending-sync:444` |
| 34874213367 | 同上六条 |
| 34864077408 | `context-memory:375`、`duels:574`、`duels:682`、`duels:773`、`load-branch:561`、`transition-turn:271` |
| 34864035210 | `context-memory:375`、`duels:574`、`duels:682`、`duels:737`、`load-branch:561`、`transition-turn:271` |

规律：**每个受影响文件里的「第 2 次开局」必失败**（duels 里是第 2、4、6 次）。

## 2. 根因

**就绪判据是虚的。** `expect(locator(".boot-loader")).toBeHidden()` 对「元素不存在」同样成立——
Playwright 的语义是「不可见即通过」，元素压根没有也算通过。本仓库实测（临时微用例）：

```
SEMANTICS_toBeHidden_passes_when_absent=true      # about:blank 上断言 .boot-loader toBeHidden → 通过
```

于是白屏（React 尚未挂载 / 脚本没跑起来）时，第 245 行**照样通过**，第 246 行再去点一个
不可能存在的按钮，只能干等 30s 超时。也就是说：CI 报的是「找不到模组选择按钮」，
真正发生的是**这一页当时还没渲染出应用**，而我们的就绪等待看不见这一点。

`.module-select-trigger` 只存在于「开局选择页」，而该页只在
`useStartStore.gameStarted === false` 时渲染（`StartScreen.tsx:71` 的
`useDelayedClose(!state.gameStarted, 360)`；关闭时返回 `<div id="start-overlay" class="hidden">`）。
所以按钮缺失只有两种可能：**（甲）应用没挂载**、**（乙）会话被判定已在游戏中**。二者都
不该用「等 loader 消失」来判定。

## 3. 排除「只是机器慢」

按「不预设机器慢」的要求逐项排除（本机 16 核，按 CI 条件限到 2 核 + xvfb + `CI=1`）：

| 实验 | 结果 |
|---|---|
| 只跑受影响 4 个文件（2 vCPU + xvfb + CI=1） | **12/12 通过** |
| 全量 37 用例（2 vCPU + xvfb + CI=1） | **35 passed / 0 failed** |
| 全量 + 自带 chromium（CI 用的是它，本机默认 snap chromium） | 34 passed / **1 unrelated 抖动**（`structured-play:265` 草稿值断言，另记） |
| 2 vCPU + xvfb + **14 个 CPU 占满进程** 再跑受影响 4 个文件 | **12/12 通过** |

结论：单纯资源紧张复现不出来；能复现的只有「页面尚未就绪而就绪判据放行」这一类。

另外单独验证了「结构化世界自动续上会不会把开局页顶掉」：新 context（无 localStorage）
打开处于 structured_v1 的世界，采样 12 秒，`.module-select-trigger` **一直在**（`structuredRow=0`、
无 pageerror），即不是这条路径导致 CI 失败。

## 4. 修复

新增 `frontend/e2e/readiness.ts`，把「等 loader 消失」换成**语义就绪**：

1. 先等 React 真挂载（`#app` 由 `GameShell` 渲染，存在即已挂载）；
2. 再等「可以选模组开局」：开局选择页出现即返回；若世界已在游戏中（产品自身的
   `#btn-new`「返回开局选择」入口在），走产品路径回到开局页（不与自动续上抢时序）；
3. 超时**不是重试也不是跳过**：把页面侧证据（是否挂载、覆盖层 class、`readyState`、
   失败的请求、`console`/`pageerror`）一并抛出，失败自带原因。

```ts
// 11 个开局点：goto + loader 等待 + 点 trigger  →  openLocalStartScreen(page, url)
await openLocalStartScreen(page, `${baseUrl}/?mode=local`);
await page.locator(".module-select-trigger").click();
```

覆盖面：`context-memory`、`interaction-duals`、`load-branch`、`pending-sync`、
`model-settings`、`structured-play`、`real-backend`、`real-integration`、`screens`、
`transition-turn`、`transition-agent-live`（共 11 处）。其余点（`investigator-panel`、
`scene-indicator`、三个 online 流程）本来就等的是语义元素（`#btn-start`／点击后的页面），未改。

**没有做的事**（按要求）：没有放宽断言、没有加 skip、没有加 retry、没有把超时放大，
也没有用前端遮掩产品状态。断言与用例结构完全不变。

## 5. 还缺的一环：CI 侧取证

CI 目前**不保留任何失败产物**（`gh api .../artifacts` → `total_count: 0`），所以
「CI 上那一页到底是白屏还是已在游戏中」无法从现有日志判定——这正是我在本地无法
直接观测到 CI 那个子形态的原因。

为此在 workflow 里补一个**失败时上传 `frontend/test-results`**（trace / 截图 / error-context）的步骤：
以后再红，日志里就有页面证据，不用再靠推断。这是取证能力的补齐，不改变任何判定。

## 6. 本地复跑结果（修复后）

见本文件末尾的「修复后门禁」一节（全量 E2E + 单测 + 类型/格式/构建）。
