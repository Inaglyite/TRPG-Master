# CI 交接：boot-loader 拦截开局点击（给 zcode，2026-09-16）

对象：`quality` workflow → `frontend` job（CI 运行 35058831650 / 35059395902，
分支 experiment/keeper-platform @ 6ac6d6e）。 backend job 已绿；以下只在
前端/E2E 设施域。

## 1. 现状

你的就绪判据修复（24bbf69）**生效了**：失败签名从「找不到按钮」变成了
「按钮在但点不动」。两个运行的失败聚类一致（21 处同型）：

```
- locator resolved to <button id="module-select" class="module-select-trigger" …>
- attempting click action
- element is visible, enabled and stable
- <div role="status" aria-live="polite" class="boot-loader">…</div> intercepts pointer events
- retrying …（直至 30s 超时）
```

证据：CI 产物 `frontend-e2e-failure`（你加的上传步骤已生效）→
`structured-interaction-duals-刷新不丢公开待办…/error-context.md`。

## 2. 根因

`BootLoader`（frontend/src/react/components/BootLoader.tsx）的退场条件：
boot-manifest 全部 UI 图片预载完成 +（local 模式）WS 首连与模组背景图；
`preloadImages` 的总预载兜底超时是 **180s**（boot/preload.ts
`DEFAULT_TIMEOUT_MS`）。CI 共享 runner 冷启动 + 受限 CPU 上，首次预载超过
Playwright 点击的 30s 行动性超时——应用已挂载、开局页已在 DOM（你的语义
就绪判据通过），但 loader 覆盖层还在拦截点击。

也就是说：语义就绪判据回答的是「页面在不在」，而点击需要的是「覆盖层走没走」。
两者都要。

## 3. 建议修复（E2E 侧）

在 `readiness.ts` 的就绪条件里加第二条：**`.boot-loader` 从 DOM 消失**。
loader 在完成时会真正卸载（`phase === "gone" → return null`），所以
`waitFor({ state: "detached" })` / `toHaveCount(0)` 是真实状态转移，
不是旧 `toBeHidden` 的虚判据。组合即：

1. `#app` 已挂载 + 开局页存在（你已实现）；
2. `.boot-loader` detached（新增）。

不放宽断言、不加 retry、不放大产品超时。

## 4. 产品层需要你一并判断（不动就是你的回答）

180s 的硬阻断上限对真实慢网用户意味着什么，是产品问题不是测试问题：
进度条期间用户不能与开局页交互。如果「应用已就绪但图片没载完」时应当
放行交互（图片继续后台加载），那是 BootLoader 的行为变更，由你决定；
E2E 侧的第 2 条就绪条件两种行为下都成立。

另登记一个本地观察项：结构化面板道具卡「使用」按钮在 Playwright 行动性
检查下持续报 outside of the viewport（截图中按钮可见可点；疑面板重渲染
致驱动层失稳），建议按 ui-button-check 在小视口复核一次。

## 5. 验收锚点

修复后推实验分支，CI 的 frontend job 应变绿；若仍红，error-context 与
trace 已在产物里。真实模型路径本轮与 CI 修复无交集（0eac7b9 只触对话框
生命周期），无需额外真实模型补验。
