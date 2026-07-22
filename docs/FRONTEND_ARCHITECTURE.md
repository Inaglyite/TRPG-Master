# React 前端架构

前端栈为 Electron + Vite + React + TypeScript。FastAPI 世界状态仍是规则权威，
React 只管理客户端展示与交互；未来地图通过版本化协议接入 React Three Fiber，不允许
AI 直接生成可执行前端代码。

## 阅读前提

本文引用的路径相对 `frontend/` 目录，显式注明为后端的除外（如 `src/speaker_parser.py`
是仓库根 `src/` 下的后端文件）。两个从后端借来的术语：

- `keeper_npc.skill`：`skills/keeper/` 下的守秘人 NPC 约束文件，规定模型以
  `【npc:<id>】…【/npc】` 包裹 NPC 直接引语，服务端据此做发言归因。
- 模组 spine：模组内首行声明 `<!-- trpg-master:prompt-role=spine -->` 的 skill 文件，
  即该模组的剧情脊柱 prompt（见 `docs/ARCHITECTURE.md`），承载模组级叙事契约。

## 依赖方向与渲染契约

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

React 覆盖范围按原则约定：玩家可见的全部界面区域——开始菜单与模组选择、角色/线索/
笔记面板、聊天历史与流式叙事、行动输入与检定确认、覆盖层（handout、存档、诊断等）、
顶栏与连接状态——必须由 React 组件渲染，实现集中于 `src/react/`（`App.tsx` 入口、
`GameShell.tsx` 组合、`components/` 按区域拆分），具体清单以该目录为准。新地图、面板
或业务交互必须直接实现为 React 组件和类型化状态，不得引入命令式 DOM 渲染 adapter。

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

## 协议入口约定

### 断线重连

`ws.ts` 持有唯一连接。连接关闭时置连接状态为 `disconnected` 并按有界指数退避重连：
退避序列为 1/2/5/10/30 秒（`RECONNECT_DELAYS`），30 秒封顶、不限次数重试；用户主动
断开（`disconnectCleanly`）不触发重连。重连成功后重置退避计数、排空离线发送队列并
重新请求角色状态；若断线发生在活动回合中，前端停止 loading、关闭失效决定，重连后
以原 `turn_id` 发送 `turn_recovery_get` 核对回合提交状态。整个断线过程只更新 Zustand
中的一条连接状态提示。

### 入口消息校验

所有服务端消息必须经 `protocol/server-message.ts` 的 `parseServerMessage` 入口校验
后才进入分发：

- `type` 必须落在 `serverMessageTypes` 白名单枚举内；未知消息类型、非法 JSON、
  schema 校验失败一律安全拒绝——界面显示“无法识别的协议消息，已安全忽略”，
  消息不进入任何业务处理器。
- 通用信封使用宽松对象：白名单内消息的未知字段原样透传，载荷校验由各域处理器负责。
- `chat_events` 使用严格 schema，仅保留公开展示字段，额外字段被剥离。
- 最后防线：任何包含 DSML 工具协议文本的消息直接丢弃，后端回归也不得把工具调用
  文本渲染进 Electron 界面。

## 样式与主题契约

样式全部位于 `src/styles/`（全局作用域，无 CSS Modules）：`tokens.css` 保存设计令牌
与 `--ui-*` 贴图变量，`base.css` 为 reset/滚动条/选区/焦点，`buttons.css` 为按钮体系，
`effects.css` 为氛围层（胶片噪点、暗角、烛光呼吸），`layout.css` 为页面骨架，
`components/` 按界面区域拆分，`index.css` 按级联顺序 `@import` 汇总，由
`react-main.tsx` 引入。

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
确定，前端不得从正文推断或接受模型自报身份。

### 协议层

- `narrative_chunk` 增量可带 `npc_id`：属于某个 NPC 发言段时携带，旁白增量不携带；
  没有 `npc_id` 的增量一律视为守秘人叙述。
- `narrative_segment` 在某个 NPC 的发言段开始时下发（旁白段为默认，不单独发送），
  `segment.speaker` 含 `type/id/name/avatar`。
- `chat_events` 在 `done` 前下发一次，给出经过严格 schema 过滤的权威聊天事件；
  客户端必须以它覆盖流式期间的临时布局。
- 回合记录持久化兼容字段 `narrative_segments`，分支/恢复/回放同时携带同内容的
  `chat_events`；无段结构的旧消息按单段守秘人叙述渲染。

服务端归因规则（后端 `src/speaker_parser.py`）：模型按叙述契约以 `【npc:<id>】…【/npc】`
包裹直接引语（契约来自 `keeper_npc.skill`、模组 spine、开场/改写契约与回合级用户消息
提示），服务端增量解析并剥离标签；NPC 仅被提及、在场或关联线索时不产生发言段，
未闭合/未知 id 一律按旁白处理。定稿阶段另有白名单恢复：仅当一行以当前世界的公开
NPC 姓名和冒号开头，或中文小说式引号前后明确出现该 NPC 姓名时，补为发言。因此漏标签
不会丢头像；无归属引文、任意标题或陌生姓名不能伪造身份。

### 状态层

`state/message-store.ts` 的 `ChatMessage.segments` 保存段结构（`NarrativeSegment[]`，
`ChatEvent` 为其兼容别名），段携带 `kind/npcId/speaker`。`renderer.ts` 流式期间按
`npc_id` 实时构建段并写入 store，权威 `chat_events` 到达时整体覆盖临时段。头像数据
一律来自服务端 `speaker.avatar`（`asset_url` 或 `asset_data_uri`）：NPC 头像出自模组
`asset_map.npcs[id]`，调查员头像出自角色卡 `portrait` 字段（后端 `src/asset_payload.py`
相对模组 assets 解析）。

### 渲染层

`MessageList` 把段渲染为群聊事件流。守秘人与 NPC 位于左侧，调查员行动位于右侧；
守秘人采用较宽的案卷式叙事气泡，NPC 使用人物气泡，避免所有内容退化成同一种即时通信
样式。无头像素材时 `AvatarDisc` 显示姓名首字徽章。系统、骰点、线索和错误消息继续使用
居中的专用事件卡，不参与发言者体系。

### 流式播放节奏

模型网络流与可见播放相互解耦：`renderer.ts` 全速接收增量并写入内存队列，逐字播放。
服务端 `done` 只代表网络完成，输入框和行动选项必须等待播放队列排空；点击当前聊天流
或“显示全文”可立即排空。检测到“你可以/您可以”或结构化 `choices` 后，剩余行动菜单
切到快速收尾节奏，避免选项已定却仍按正文速度等待。`prefers-reduced-motion` 下不播放
逐字动画。权威 `chat_events` 在队列排空时覆盖临时段；素材至少等第一段文字真实可见后
展示，避免事件越过叙事。

以下播放节奏为当前调参值（易变实现细节，以 `src/renderer.ts` 顶部常量为准，
调整它们不属于契约变更）：

| 参数 | 当前值 | 出处（`src/renderer.ts`） |
| --- | --- | --- |
| 基础节拍 | 每 34ms 展示 1 字 | `BASE_TICK_MS` |
| 逗号/顿号/分号/冒号后停顿 | 72ms | `COMMA_PAUSE_MS` |
| 句末（。！？!?）停顿 | 185ms | `SENTENCE_PAUSE_MS` |
| 换行停顿 | 110ms | `playbackDelay` |
| 发言者切换停顿 | 240ms | `SPEAKER_PAUSE_MS` |
| 积压超过 160 字 | 每拍 2 字 | `charactersPerTick` |
| 积压超过 500 字 | 每拍 3 字 | `charactersPerTick` |
| 选项收尾（fast） | 每拍 6 字、18ms（句末 46ms） | `charactersPerTick` / `playbackDelay` |
