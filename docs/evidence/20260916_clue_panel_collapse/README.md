# 调查员侧栏被压塌成竖排：根因与修复（2026-09-16）

用户报的现象：人物线索卡里标题与正文**一个字一行**（竖排），整个侧栏内容挤成细条。

## 1. 现象与复现

本地真实后端复现（猩红文档开局 → 打开「角色/线索」），量到的几何：

| 视口 | 修复前 | 修复后 |
|---|---|---|
| 1280 | `#main` 网格 `1279px 1px`；`#char-panel` **1px**；线索行 0×867；正文 0×846 | 网格 `940px 340px`；面板 340；线索行 273×102；正文 159×81 |
| 1180（用户窗口） | 同上（面板 1px） | 网格 `880px 300px`；面板 300；线索行 241×122；正文 127×101 |
| 939（抽屉） | 面板 1px | 抽屉 380；正文 199×60 |
| 640（抽屉全宽） | 面板 1px | 抽屉 640；正文 451×40 |

`1px = width:0 + 1px 左边框`（`box-sizing: border-box` 下边框不可为负）——也就是面板宽度被算成 0。

## 2. 根因

`layout.css` 里 `#main` 是网格：

```css
#main { display: grid; grid-template-columns: minmax(0, 1fr) auto; }
```

侧栏 `#char-panel` 是那个 **`auto` 轨道**的网格项，并且只有 `width: 340px`、
没有 `min-width`。网格项默认 `min-width: auto`，其**自动最小尺寸 = 内容的 min-content**；
而面板里的文本（`investigator-panel.css` 的 `overflow-wrap: anywhere`，中文按字断行）
min-content 只有一两个字宽 → 轨道下限被压到 ~1px → 面板宽度跟着塌，
面板内所有可断行文本退化成「一个字一行」。

证据链（都在浏览器里量过，不是推测）：

1. 该元素确实命中 `#char-panel{width:340px}`（CSSOM `matches` 验证）；
2. 但 `#main` 计算出的轨道是 `1179px 1px`，`#char-panel` 计算宽度 1px；
3. 临时加 `min-width: 340px` → 面板 340、轨道 `940px 340px`（恢复）；
4. 临时把轨道写成显式 `340px` → 面板 ~334（恢复）；
5. 临时给内联 `width: 340px` → 只有 117.578px（说明不是声明缺失，而是**轨道在按内容尺寸**算）。

补充：`.collapsed` 类（收起）在网格里同样要 `min-width: 0`，否则收不起来。

## 3. 修复

`src/styles/components/char-panel.css`：基础面板加 `min-width: 340px`，
`.collapsed` 加 `min-width: 0`；
`src/styles/components/responsive.css`：≤1199 的 300px 档加 `min-width: 300px`；
≤999 抽屉改为 `position: fixed` 后**不再受网格轨道影响**，显式
`min-width: 0`（避免小屏被 340px 下限顶出视口）；≤640 全宽档同样 `min-width: 0`。
每处都写了注释说明原因，避免以后被当成冗余样式删掉。

## 4. 回归守卫（双向验证过）

`e2e/investigator-panel.spec.ts` 在打开侧栏后新增断言：

- `#char-panel` 宽度 > 280（面板未被压塌）；
- 第一条线索正文宽度 > 100，且高度 < 宽度 × 3（竖排时高度会是宽度的几十倍）。

把 `min-width` 去掉重建后该用例**必红**（错误信息「侧栏被压塌（网格 auto 轨道被 min-content 挤没）」），
恢复后**必绿**。

## 5. 截图（按 ui-button-check 流程，1280 / 1180 / 939 / 640）

`/tmp/clue-fixed-1280.png`、`-1180.png`、`-939.png`、`-640.png`（临时产物，不入库）。
逐张核对：线索行横向排布、按钮完整无压缩、正文多字换行、窄屏抽屉为触控尺寸（≥44px），
未发现溢出或意外换行。

顺带确认交接文档 §4 登记的观察项：640 宽下道具卡的「使用」按钮在本轮截图里
完整位于视口内（未被挤压/溢出），本轮未再复现该问题。

## 6. 门禁

- 前端单测：772 passed / 70 files；
- `tsc --noEmit`、`prettier --check src e2e`、`npm run build`：通过；
- E2E（CI 条件 2 vCPU + xvfb + `CI=1`，全量）：**40 收集 → 38 passed / 2 skipped / 0 failed**（10.5m）。
  2 条 skip 是环境/未授权：`staging-recovery`（需外部 staging）、`transition-agent-live`
  （需 `TRPG_LIVE_MODEL=1`）。
