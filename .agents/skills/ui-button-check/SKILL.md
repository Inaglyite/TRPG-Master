---
name: ui-button-check
description: 新增/修改前端按钮或含按钮的工具行时的验收流程，防止按钮被挤压、无内边距、文字换行等反复出现过的布局事故
type: prompt
whenToUse: 当在 frontend/ 新增按钮、修改按钮样式、或往已有工具行/操作区里加控件时；完成后验收也必须走本流程
---

# 按钮/控件验收（trpg-master 前端）

本项目按钮事故反复发生（solo CTA 失去居中、模组工坊按钮被挤成一条等），
根因都是同一个模式：**主题基类不含尺寸**。按下面流程做，不要跳步。

## 一、写之前必须知道的契约

- `frontend/src/styles/buttons.css` 的 `.btn-ghost` / `.btn-primary` **只提供主题**
  （边框/底色/字色/hover/disabled），**没有** padding、min-height、white-space、flex。
  尺寸一律由使用处所在组件的 CSS 文件补充（buttons.css 头部注释：
  「选择器名单制，组件零改动；尺寸/布局类覆盖留在组件文件」）。
- 新按钮优先直接用 `.btn-ghost` / `.btn-primary` class，**不要**把新 id 加进
  buttons.css 的选择器名单；尺寸覆盖写进对应组件 css（如 start-screen.css）。
- flex 行里按钮默认 `flex: 0 1 auto` —— **会被压缩**。行内兄弟控件通常都是
  `flex: 0 0 auto`，新按钮必须对齐兄弟的写法。
- 中文短标签不会断词，缺 `white-space: nowrap` 会竖排换行。
- 检查 `frontend/src/styles/components/responsive.css` 的媒体查询：窄屏下可能有
  既有约定（如 `#btn-import-module span { display: none }` 图标化）。新按钮要想好
  窄屏行为，要么复用约定，要么显式写自己的规则。

## 二、改完后的硬性验收

1. 组件测试：`cd frontend && npx vitest run <相关测试文件>` 必须通过。
2. 重新构建：`cd frontend && npm run build`（本地 8765 服务直接吃 dist，不用重启）。
3. **截图验收**（不能只靠肉眼看代码）：用 Playwright 对本地 127.0.0.1:8765 截图，
   至少 3 个宽度：1280 / 939（用户常用窗口）/ 640（窄屏）。
   - 本地单人开始页在纯浏览器里用 `http://127.0.0.1:8765/?mode=local` 进入
     （`?mode=local` 是 GameShell 的官方入口参数）。
   - 必须等 boot loader 退出再截：等 `.boot-loader` detach/hidden，
     再等目标控件 visible，然后再等 ~1s 稳态。
   - 本机 Playwright 与浏览器版本可能不匹配，launch 时显式给
     `executablePath` 指向 `~/.cache/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell`
     （先 `ls ~/.cache/ms-playwright` 确认实际版本目录）。
   - 脚本写在 frontend/ 目录内跑（否则解析不到 node_modules 里的 playwright），
     跑完即删，不要留在仓库里。
4. 逐张截图核对 checklist（见下）。任何一条不过就回去改，不要交付。

## 三、截图 checklist

- 按钮与同一行的兄弟控件**等高**（依赖 `align-items: stretch` 的行要确认没有被
  自己的 min-height/height 顶破或压扁）。
- 按钮宽度由内容决定、**没有被压缩**：文字完整显示，四周 padding 肉眼可见且与
  兄弟控件一致（本项目工具行惯例 `padding: 0 13px`、`font-size: 13px`）。
- 中文标签没有竖排换行；窄屏下行为符合 responsive.css 约定或新写的规则。
- 整行没有溢出容器、没有意外换行（除非设计就是 wrap）。
- hover/disabled 态继承正常（基类已提供；若 disabled 是业务态，补 `cursor` 等）。
- 暗色背景上对比度正常，没有「融进背景看不见」。

## 四、事故案例（别再犯）

- 2026-08「模组工坊」按钮：JSX 给了 `className="btn-ghost module-workshop-link"`，
  但 `module-workshop-link` 从未在 CSS 定义 → 按钮只剩基类主题，无 padding、
  flex 可缩，被同行 `flex:1` 的下拉挤成一条。修复：在 start-screen.css 把它并进
  `#btn-import-module` 的尺寸规则（`flex: 0 0 auto; padding: 0 13px; white-space: nowrap` 等）。
- 2026-08 云端大厅「开始新冒险」CTA：容器被改成 flex 后丢了 `align-self: center`，
  CTA 被拉伸/错位。教训：改容器 display 时检查子项的 align-self/justify-self 假设。
