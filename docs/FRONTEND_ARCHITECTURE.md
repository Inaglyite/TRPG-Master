# React 前端架构

前端栈为 Electron + Vite + React + TypeScript。FastAPI 世界状态仍是规则权威，
React 只管理客户端展示与交互；未来地图通过版本化协议接入 React Three Fiber，不允许
AI 直接生成可执行前端代码。

## 依赖方向

```text
React components
      ↓
Zustand client state ← validated WebSocket messages (Zod)
      ↓
typed transport / HTTP services
      ↓
FastAPI authoritative world state
```

`src/react/GameShell.tsx` 负责组合 React 页面与覆盖层。WebSocket 服务会在 React 首次
提交 DOM 后由 `App` 启动。业务状态写入 Zustand，视图只由 React 组件渲染；主题服务仅在
平台边界写入 CSS 变量和窗口标题。禁止新增直接 `document.getElementById` 渲染逻辑。

React 已覆盖：

- React 应用入口和完整 JSX 元素树；
- 顶栏、主题标题和连接状态；
- 角色属性、HP/SAN、物品与分类线索册；
- 玩家输入、结构化行动选项、检定确认、多选决定与结局确认；
- 聊天历史、流式叙事、等待/判定状态、骰子动画和回合改写/分支操作；
- 调查笔记、revision 冲突状态、自动保存和快捷行动；
- Handout、线索提示、角色面板、快速存档、存档重命名与时间线切换；
- 模型路由预设、受控模型 ID 输入和完整回合/Lorebook 诊断；
- 开始菜单、模组选择、调查员档案、新游戏重试、读档入口与 `.trpgmod` 导入；
- WebSocket 消息类型白名单和畸形消息安全拒绝。

## 状态与服务边界

- `state/app-store.ts`：连接、角色、线索、存档、handout、笔记和通用覆盖层状态。
- `state/message-store.ts`：聊天消息、流式叙事、滚动请求和回合操作状态。
- `state/start-store.ts`：开始菜单、模组、调查员和开局状态。
- `state/model-store.ts`：模型路由草稿与回合诊断。
- `ws.ts`：协议连接、重连、事件归属和服务端消息分发，不直接渲染 DOM。
- `renderer.ts`：把回合事件转换为消息状态，不持有业务世界状态。

时间线按钮显示在结果消息上，但分支源由公开历史中的 `parent_turn_id` 决定，因此语义是
“回到本次行动之前尝试另一选择”，而不是“复制当前结果之后的状态”。历史回放、实时回合、
断线恢复和重新叙述必须使用同一个锚点规则。

## 样式与主题契约

样式全部位于 `src/styles/`（全局作用域，无 CSS Modules）：`tokens.css` 保存设计令牌
与 `--ui-*` 贴图变量，`base.css` 为 reset/滚动条/选区/焦点，`effects.css` 为氛围层
（胶片噪点、暗角、烛光呼吸），`layout.css` 为页面骨架，`components/` 按界面区域
拆分，`index.css` 按级联顺序 `@import` 汇总，由 `react-main.tsx` 引入。

模组 `theme.json` 经 WS `theme` 消息原样透传，由 `src/theme.ts` 安全校验后注入：

- `colors`：`bg/bg2/bg3/bg4/text/textDim/textFaint/gold/goldDim/goldBright/red/
  redDim/redBright/crimson/crimsonBright/green/blue/blueBright/purple/ink/border/
  borderBright` 映射为对应 CSS 变量（如 `bg→--bg`、`textDim→--text-dim`）；
  未知键与非法颜色值被忽略。
- `fonts`：`body→--font`、`mono→--font-mono`、`heading→--font-display`
  （拒绝含 `;{}<>` 或 `url(` 的值）。
- `title/subtitle/description/startButtonText`：写入 app-store，由开始页渲染；
  缺省时回退内置默认文案。
- `backgroundImage`：模组 `assets/` 内的相对路径（禁 `..`、协议头、反斜杠，仅图片
  扩展名），拼为 `/api/assets/{module}/{path}` 注入 `--module-bg-image`；开始页
  背景以 `var(--module-bg-image, var(--ui-start-bg))` 消费，缺省自动回退打包贴图。
- `effects`：`{grain, flicker, vignette}` 任一为 `false` 时在 `<html>` 上设置
  `data-fx-*="off"` 关闭对应氛围特效；`prefers-reduced-motion` 同步禁用动画。

每次应用主题会先清除上一个模组注入的全部受管变量与特效属性，未提供的字段回退到
`tokens.css` 默认值，因此模组只需给出想覆盖的子集。组件颜色必须引用 `tokens.css`
中的变量（必要时用 `color-mix` 派生透明度），禁止硬编码色值。

## 3D 检定骰

检定骰默认以 3D 物理骰呈现（`@3d-dice/dice-box-threejs`，three + cannon-es）。
点数仍由后端权威：投掷记法带 `@` 预定结果（如 `2d10@3,2`），物理翻滚后强制停在
服务器给出的面值。`src/dice3d/controller.ts` 为单例懒加载封装（动态 import，独立
chunk 不进主包），覆盖层挂载于 `#chat-panel`，物理定格/超时（5s）/点击跳过都会
落定并显示结果文本。以下情况一律静默回退 CSS 八边形骰：`prefers-reduced-motion`、
无 WebGL（含 jsdom 测试环境）、初始化失败、非标准面数（d2/d4/d6/d8/d10/d12/d20
之外）、并发忙线。骰子颜色取自主题 CSS 变量（`--gold-bright`/`--bg3`/`--gold-dim`），
`applyTheme` 时重建。

## 扩展约束

新地图、面板或业务交互必须直接实现为 React 组件和类型化状态，不得引入命令式 DOM
渲染 adapter。
