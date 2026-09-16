# boot-loader 拦截开局点击：CI 取证、根因与有界放行修复（2026-09-16）

对应交接：`docs/CI_BOOT_LOADER_HANDOFF_20260916.md`（Kimi）。本轮只做前端 CI 收口，
不动后端、不操作他人文档/截图。

## 1. 先纠正归因：不是「资源还在加载」

我按 3 个失败运行（`430b02a` / `6ac6d6e` / `18fecb3`，运行号 35058831650 / 35059395902 /
35060615190）下载产物 `frontend-e2e-failure` 后，从 trace 里读出该页的**全部网络活动**：

```
资源条数 26；开始 05:50:10.060 → 结束 05:50:10.544（< 0.5s）
20 张图片：status 全部 200，单张 4–29ms（最慢 29ms）
ws 101 已连接（90ms）；boot-manifest.json 200（118ms）；模组背景图 200（11ms）
pending / failed / aborted：0
```

而同一次点击**被 `.boot-loader` 拦满 30s**（error-context：`locator resolved to
<button class="module-select-trigger">` → `element is visible, enabled and stable` →
`<div class="boot-loader"> intercepts pointer events` → 重试至超时）。

也就是说：资源早已全部成功返回，加载屏却还在。这属于交接文档里的第 3(b) 类
（**应用已就绪但覆盖层不退出 → 产品缺陷**），不是「慢」也不是「还在加载」。

## 2. 根因（代码级）

`BootLoader` 的放行条件原先等整批图片预载完成：

```ts
await preloadImages(files, …);      // 内部 Promise.all(workers) 或 180s 兜底
manifestDoneRef.current = true;     // 之后才可能放行
…
if (bgUrl) await preloadImages([bgUrl]);   // local 侧同样等预载
```

而 `preloadOne` 里 `image.decode()` **没有超时**：

```ts
image.onload = () => {
  const decoding = image.decode?.();       // 渲染进程被压住时可能长时间不落定
  if (decoding) decoding.catch(() => {}).finally(resolve);
```

一张图片的 `onload` 已触发但 `decode()` 不落定（既不 onload 也不 onerror 的等价形态）
→ `Promise.all(workers)` 不结束 → 覆盖层一直挡着，最长到 `DEFAULT_TIMEOUT_MS = 180s`。
覆盖层只有在 `--leaving` 时才 `pointer-events: none`，所以 `loading` 期间开局页点不动。

## 3. 修复（有界放行 + 可见失败反馈）

**产品侧**（`src/react/components/BootLoader.tsx`、`src/boot/preload.ts`）：

- 放行不再等整批预载：只要①清单已拿到（fetch 有界）②local 侧首连/背景图就绪
  （各自本来就有 3s/5s 上限）即可放行；图片预载转为**后台**继续。
- 资源仍在后台时的处理：先给 `PRELOAD_GRACE_MS = 1.2s` 宽限（正常首载在此内结束，
  不打扰用户）；宽限后仍没完成就照常放行，并显示**不阻断**的说明
  「界面资源仍在后台加载，不影响开始时操作。」
- 兜底预算 `SPLASH_BUDGET_MS = 6s`：无论资源什么状态，覆盖层都不会一直挡交互。
- 失败反馈：预载出现失败项时显示「部分界面资源未能加载（N 项），不影响操作。」，
  不再静默。
- `preloadOne` 的 `decode()` 加 1.5s 上限（超了照常算成功）；`preloadImages`
  返回 `{ failed }` 供上面两条反馈使用。

**验收侧**（`e2e/readiness.ts`）：就绪判据在原有「React 已挂载 + 开局页可用」之外，
新增**覆盖层真实卸载**这一条——`waitFor` 到 `.boot-loader` 从 DOM 消失（`phase === "gone"`
才 `return null`，是真实状态转移，不是旧的 `toBeHidden` 虚判据）；并额外做命中测试
（`bootLoaderBlocksPointer`）确认它不再拦指针。超时抛出的取证里包含
`mounted / startScreen / inGame / bootLoaderPresent / bootLoaderBlocksPointer /
failedRequests / consoleErrors / pageErrors`。**不用 force click**，点击走真实行动性检查。

## 4. 两种情况的区分（按要求）

| 情况 | 判定 | 处理 |
|---|---|---|
| 资源仍正常加载（慢但会到） | 合法 | 就绪判据等真实就绪；覆盖层在有界预算内放行并说明 |
| 资源失败、或完成后 loader 不退出 | **产品缺陷** | 修终止/失败反馈（本文 §3），不是延长测试等待 |

## 5. 针对性验证（都不加 skip / retry / force click）

单测（`src/react/components/BootLoader.test.tsx`、`src/boot/preload.test.ts`）：

- 正常加载：预载完成即先 `leaving` 再卸载，无多余提示；
- 延迟加载（预载一直不结束）：宽限后照常退场 + 显示「仍在后台加载」说明；
- 资源失败（`failed=1`）：显示「部分界面资源未能加载（1 项）」+ 照常退场；
- 清单迟迟不返回：兜底预算到点仍退场（假定时器推进）；
- 单张图片 `decode()` 永不落定：`preloadImages` 仍在 1.5s 上限内返回（旧实现会一直等）。

E2E（新增 `e2e/boot-loader-readiness.spec.ts`，真实后端、人类主持、零模型）：

| 用例 | 结果 |
|---|---|
| 1. 正常加载：覆盖层真实卸载后开局页可点、开局完成 | 通过 |
| 2. 延迟/卡住：图片请求**一直 pending**，界面仍在有界时间内可交互（实测 ~6s，断言 < 20s） | 通过 |
| 3. 资源失败：图片请求被 abort，界面照常可交互、不出现永远挡着的加载屏 | 通过 |

**双向验证**（用例确实能抓到缺陷）：把修复临时停用（放行重新等整批预载、预算调到 1h）后，
第 2 条**必红**：`等待开局选择页超时`（47.4s），且取证显示 `failedRequests: []`
（请求是 pending 而非失败）——与 CI 现象同型；恢复修复后 3/3 通过。

## 6. 需要 Kimi 记录的产品行为变化（交接文档 §4 问我判断的那条）

加载屏**不再**「等所有 UI 图片预载完才放行交互」。理由：图片是装饰/预缓存，
而覆盖层会挡住整个开局页；一张既不 onload 也不 onerror 的图片就能把真实用户
锁在加载屏上（CI 上就是这么挡住点击的，最长 180s）。
现在的语义是：应用可交互（清单 + 首连/背景图，都有界）即放行，
图片继续在后台缓存，没拿到时给可见但不阻断的说明。

影响面：仅启动屏的放行时机与提示；不涉及协议、命令、模型调用路径，
**不需要**真实模型补验。若你不认这条行为变更，我可以改为「保留原阻塞语义，
只在 180s 上限附近给失败反馈」——但那等于让慢网用户继续被挡，我不建议。

## 7. 门禁与交接

- 前端单测：**772 passed / 70 files**（新增 5 条：4 条启动屏 + 1 条预载卡死）。
- `tsc --noEmit`、`prettier --check src e2e`、`npm run build`：通过。
- E2E（CI 条件：2 vCPU + xvfb + `CI=1`，全量）：**40 收集 → 38 passed / 2 skipped / 0 failed**（10.3m）。
  2 条 skip 是环境/未授权：`staging-recovery`（需外部 staging）、`transition-agent-live`
  （需 `TRPG_LIVE_MODEL=1`）。新增的 3 条启动屏用例都在其中并通过。
- 交接 SHA 与文件清单见 commit message；**分支是共享的 experiment/keeper-platform，
  不需要 cherry-pick**，我只提交不推送。

## 8. 剩余问题（未扩大范围）

1. 交接文档 §4 的观察项：结构化面板道具卡「使用」按钮在 Playwright 行动性检查下
   报 `outside of the viewport`。我这轮没有展开排查（它不属于本轮 CI 拦截链，
   且需要按 ui-button-check 在小视口单独复核）。已登记，建议下一轮用窄屏规格复核。
2. 真实模型 A–F 仍未执行（等单独授权），与本轮 CI 修复无交集。

## 9. 交接回执（给 Kimi）

| 项 | 值 |
|---|---|
| 我的提交 | `127fb36` 修复：启动覆盖层有界放行 |
| 基线 | `18fecb3`（集成分支 tip，我的提交直接接在它上面，**在共享分支上，不需要 cherry-pick**） |
| 推送 | 我未推送；由你集成推送后 CI 才会用上这条修复 |
| 改动文件 | `src/react/components/BootLoader.tsx`、`src/boot/preload.ts`、`src/styles/components/boot-loader.css`、`e2e/readiness.ts`、新增 `e2e/boot-loader-readiness.spec.ts`，以及对应两个单测文件与本文 |

CI 结果（你推 127fb36 之后）：

- `frontend` job 的 `xvfb-run --auto-servernum npm run test:e2e` 应为绿；
- 若仍红，先看 `frontend-e2e-failure` 产物里的 `error-context.md`：新就绪等待超时时会打印
  `bootLoaderPresent` / `bootLoaderBlocksPointer` / `mounted` / `startScreen` /
  `failedRequests` / `consoleErrors`，能直接区分「覆盖层没退」与「页面没挂载」，
  不要再靠扩大超时或重试。

请记录（联合版本台账）：

1. 本轮唯一产品行为变化：启动屏有界放行（§6）。不涉及真实模型路径，无需真实模型补验。
2. 剩余问题：道具卡「使用」按钮 `outside of the viewport`（仅登记，未排查）；真实模型 A–F 未执行。
