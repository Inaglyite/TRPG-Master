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

## 6. 第二个真实缺陷：提交后遗留的关闭定时器会清掉刚重新打开的草稿

排查就绪判据时，在 CI 条件下（2 vCPU + xvfb）本地复现出另一条**产品级**竞态，
它和上面那条超时无关，但同样会让 CI 红：

```
structured-play.spec.ts:269 › 提交冲突：位置不变、可同 ID 重试、拒绝后草稿仍可编辑
  2 vCPU 下 --repeat-each=4 → 3/4 失败（普通机器上通常不出现）
  Error: expect(locator).toHaveValue(expected) failed
  Locator: dialog "出示线索" → label "想询问什么（可选）"
  Error: element(s) not found
```

定位手段与证据（临时插桩后从 Playwright trace 读回，插桩已撤销）：

```
[editor] openPresent        ← 出示编辑器打开
[dialog] submit clicked     ← 提交请求
[editor] close  (submit)    ← submitStructuredEditor → editor.close()（草稿置空）
[dialog] requestClose called ← 提交成功后 dialog 再排一个 150ms 的退出动画定时器
[editor] openPresent        ← 玩家点「重新编辑」，草稿恢复
[dialog] close timer fired  ← 那个遗留定时器到点
[editor] close              ← 把刚恢复的草稿清掉
```

机制：提交成功 → 立刻关编辑器 → 再排 150ms 退出定时器；玩家在这段窗口内重新打开
（「重新编辑」）时，遗留定时器仍会执行关闭，**把刚恢复的草稿连同玩家改到一半的内容一起清掉**。
窗口是 150ms 的固定值，但定时器在慢机器/受限 CPU 上会被推迟触发（CI 上就这样），
所以本地快机难复现、CI 必现。

修复（产品代码，UI 生命周期，不涉及协议/模型路径）：
`StructuredActionDialog` 在编辑器**重新打开**时撤销上一次的延迟关闭
（清掉定时器 + 复位 `closing`），与其它浮层的既有约定一致（「退出途中重新打开：取消退出」）。

回归测试（确定性、可双向验证）：
`src/react/components/investigator/StructuredActionDialog.test.tsx` →
「提交后立刻重新打开：遗留的关闭定时器不得清掉刚恢复的草稿」。
把修复停用后该用例**必红**，恢复后**必绿**（两个方向都跑过）。

E2E 侧验证：`structured-play.spec.ts:269` 在 2 vCPU 下 `--repeat-each=4` →
修复前 3/4 失败，**修复后 4/4 通过**。

## 7. 本地复跑结果（修复后）

- 前端单测：**767 passed**（新增 1 条竞态回归）。
- `tsc --noEmit`、`prettier --check src e2e`、`npm run build`：通过。
- E2E（CI 条件：2 vCPU + xvfb + `CI=1`，全量）：**37 收集 → 35 passed / 2 skipped / 0 failed**（9.6m）。
  其中 `structured-play.spec.ts:269`（第二条竞态的用例）在修复前 3/4 失败，修复后通过。
  2 条 skip 是环境/未授权：`staging-recovery`（需外部 staging）、`transition-agent-live`
  （需 `TRPG_LIVE_MODEL=1`）。

## 8. 需要 Kimi 知道的两点

1. **本轮改了产品行为一处**（`StructuredActionDialog` 的关闭定时器竞态修复）。
   它只影响对话框生命周期，不触碰协议、命令、模型调用路径，因此**不需要**真实模型补验；
   但按约定登记在此，供你在联合版本记录里核对。
2. CI 产物上传步骤是 workflow 级改动（`.github/workflows/quality.yml`），不影响产品。

## 9. 交给 Kimi 的推送与 CI 验证请求

我这侧**不推送**（集成与推送仍由你负责）。请把下面两个本地提交推上 `experiment/keeper-platform` 形成最终候选版本：

| 提交 | 内容 | 性质 |
|---|---|---|
| `24bbf69` | E2E 就绪判据改为语义就绪 + 失败取证；workflow 失败时上传 `frontend/test-results` | 验收设施 + CI |
| `0eac7b9` | `StructuredActionDialog` 遗留关闭定时器竞态修复 | **产品代码（UI 生命周期）** |

推送后请在 CI 上确认（我这边无法读取 CI 运行时的页面现场，只能等你推完读日志）：

1. `frontend` job 的 `xvfb-run --auto-servernum npm run test:e2e` 是否变绿；
2. 若仍红，**这次日志会直接给出原因**：新就绪等待会在超时时抛出
   `{ mounted, startScreen, inGame, triggerCount, overlayClass, readyState }`
   + `failedRequests` + `consoleErrors` + `pageErrors`；失败时也会上传
   `frontend-e2e-failure` 产物（trace/截图/error-context）。把那段贴回来即可定位。

预期：`mounted=true` 且 `startScreen=true`（旧判据留下的白屏窗口已被语义等待覆盖）。
若日志出现 `mounted=false`，那就是页面根本没挂载（资源/脚本未执行），属于新一类问题，
按上传的 trace 继续查——不要先用扩大超时或重试掩盖。

请在你的联合版本记录里写明：`quality` 的 E2E 结果对应的是 `24bbf69` + `0eac7b9`（或其后继），
以及本轮唯一的产品行为变化是 `0eac7b9` 的对话框生命周期修复（不影响真实模型路径）。
