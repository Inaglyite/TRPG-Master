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
之外）、并发忙线。回退路径短暂显示投掷状态后直接呈现后端权威文字结果，不再模拟第二套骰面。
骰子颜色取自主题 CSS 变量（`--gold-bright`/`--bg3`/`--gold-dim`），`applyTheme` 时取消旧投掷、
释放 WebGL renderer/context 并按新主题懒重建。初始化、投掷、跳过、超时和卸载共享同一代次令牌，
过期异步任务不得恢复覆盖层或占用新实例。

后端 HTTP/WS 地址统一由 `src/backend-url.ts` 生成；本地默认使用当前 hostname 的 8765 端口，
HTTPS 页面自动切换到 HTTPS/WSS，反向代理或非默认部署通过 `VITE_TRPG_BACKEND_ORIGIN` 覆盖。

## 发言者身份与头像

玩家可见叙述以「段」为结构：旁白段归属守秘人，发言段归属具体 NPC。归因完全由服务端
确定——模型按叙述契约以 `【npc:<id>】…【/npc】` 包裹直接引语（`keeper_npc.skill`、
模组 spine 技能、开场/改写契约、回合级用户消息提示），`src/speaker_parser.py` 增量
解析并剥离标签；NPC 仅被提及、在场或关联线索时不产生发言段，未闭合/未知 id 一律
按旁白处理。定稿阶段另有白名单恢复：仅当一行以当前世界的公开 NPC 姓名和冒号开头
时，或中文小说式引号前后明确出现该 NPC 姓名时，补为发言。因此漏标签不会丢头像；
无归属引文、任意标题或陌生姓名不能伪造身份。

协议：`narrative_chunk` 增量可带 `npc_id`；`narrative_segment` 在发言段开始时下
发（含名称与头像）；`chat_events` 在 `done` 前下发经过 schema 白名单过滤的权威事件。
回合记录持久化兼容字段 `narrative_segments`，分支/恢复/回放同时携带 `chat_events`；
无段结构的旧消息按单段守秘人叙述渲染。头像来自模组 `asset_map.npcs[id]`（NPC）或角色 JSON 的 `portrait` 字段（调查
员，相对模组 assets 解析），无素材时前端显示姓名首字徽章。

前端：`renderer.ts` 流式期间实时构建段并在定稿时以权威段覆盖；`MessageList` 把段
渲染为群聊事件流。守秘人与 NPC 位于左侧，调查员行动位于右侧；守秘人采用较宽的
案卷式叙事气泡，NPC 使用人物气泡，避免所有内容退化成同一种即时通信样式。系统、
骰点、线索和错误消息继续使用居中的专用事件卡，不参与发言者体系。

## 扩展约束

新地图、面板或业务交互必须直接实现为 React 组件和类型化状态，不得引入命令式 DOM
渲染 adapter。
