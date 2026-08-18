# TRPG Game 产品经理代码地图（PM Code Map）

> 本文写给**看不懂代码的产品经理**：目标是"说一个功能，就知道对应哪些文件；
> 看得懂后端 API 怎么写的、前端怎么调用的"。所有路径、接口、消息均按当前仓库代码核实。
> 读者不需要会写代码，只要会"对着路径找文件、对着表格查名字"。

## 0. 这个项目是什么

一个 **AI 守秘人 TRPG 跑团应用**：AI 模型负责讲故事、理解玩家行动；**骰子、检定、
战斗、伤害、SAN、世界状态、存档全部由确定性 Python 代码结算**，模型不能编造规则。

两种形态：

| 形态 | 入口 | 说明 |
|---|---|---|
| 本地单机 | Electron 桌面壳 / `localhost:8765` | 一人一机，本地 SQLite 数据库，玩家自己填模型 API Key |
| 云端多人 | `https://trpggame.xyz`（staging: 8443） | 2–4 人房间，账号 + PostgreSQL，模型由服务器维护者配置 |

## 1. 技术栈速览

| 层 | 技术 | 位置 |
|---|---|---|
| 桌面壳 | Electron | `frontend/electron/main.cjs` |
| 前端 | React 19 + TypeScript + Vite + Zustand（状态管理） | `frontend/src/` |
| 后端 | Python FastAPI（HTTP + WebSocket）+ LangGraph（回合工作流） | `server.py` + `src/` |
| 规则实现 | 确定性 Python 工具（骰子/战斗/SAN/角色） | `tools/*.py` |
| 数据库 | SQLAlchemy + Alembic；本地 SQLite / 云端 PostgreSQL | `src/database.py`、`migrations/` |
| AI 模型 | OpenAI 兼容接口（默认 DeepSeek），GLM 可选做摘要 | `src/llm.py`、`src/model_streamer.py` |
| 模组 | `.trpgmod` 包（JSON + Markdown + 素材） | `mod/`、`schemas/trpgmod/`、`src/module_*.py` |

## 2. 顶层目录一句话

```
server.py        # 后端总入口：装配所有路由 + WebSocket 消息循环
src/             # 引擎、回合工作流、战斗、持久化、模组工具链（核心都在这）
tools/           # 确定性规则 CLI 工具（骰子、检定、伤害、SAN、模组打包）
skills/          # 守秘人约束（提示词 Skill）
rules/           # 结构化规则数据
mod/             # 内置模组（疯狂宅邸、猩红文档）
schemas/trpgmod/ # 模组格式 JSON Schema
examples/        # 模组工程模板
frontend/        # React UI + Electron 壳
docs/            # 架构、接口、部署、模组格式文档（本文件也在这）
migrations/      # 数据库结构版本迁移（Alembic）
```

## 3. 核心心智模型（最重要的一张图）

```
前端只会"说"，后端负责"算"，数据库负责"记"。

React 组件（界面按钮）
   ↓ 调用
Zustand store（界面状态）+ api/*.ts 或 ws.ts（通信层）
   ↓ 两种通道
① HTTP 请求（一次性操作：登录、建房、模组导入）
② WebSocket 消息（实时回合：开局、行动、骰子、存档）
   ↓
FastAPI 路由 / WsMessageRouter 分发
   ↓
src/ 领域模块（engine 引擎、combat 战斗、characters 角色……）
   ↓
tools/ 确定性规则 + 数据库事务（世界状态、回合日志、存档）
```

**铁律：前端永远不能直接改游戏状态，只能发请求；模型只能"提建议"（调工具），
规则由代码说了算。**

## 4. 功能 → 文件映射表（查代码第一入口）

> 用法：找你要了解的功能，先看"前端界面文件"，再顺着"前端发出的消息/接口"
> 去"后端处理文件"。

| 功能 | 前端界面文件 | 前端发什么 | 后端处理文件 |
|---|---|---|---|
| 启动/模式选择（单机/多人） | `frontend/electron/main.cjs`、`react/components/ModeSelectScreen.tsx` | — | `start_desktop.sh`、`server.py` |
| 登录/注册/登出 | `react/components/online/AuthScreen.tsx` | POST `/api/auth/login` 等 | `src/auth_http.py`、`src/auth.py`（Argon2 加密、Session Cookie） |
| 联机大厅、建房、邀请码 | `online/LobbyScreen.tsx`、`online.ts` | POST `/api/worlds`、`/api/worlds/{id}/invites` | `src/multiplayer_http.py`、`src/multiplayer.py` |
| 云端单人（我的冒险） | `online/SoloLobbyScreen.tsx` | POST `/api/worlds` | `src/multiplayer_http.py` |
| 单机开局（选模组→选调查员→开始） | `StartScreen.tsx`、`start.ts`、`state/start-store.ts` | WS `start` | `server.py:1275` → `src/game_application.py` → `src/engine.py:695 reset()` |
| 游戏主界面/聊天区 | `GameShell.tsx`、`MessageList.tsx`、`renderer.ts` | —（收事件渲染） | `src/event_stream.py`（把后端事件推给前端） |
| 玩家行动（每回合核心） | `GameControls.tsx`、`options.ts` | WS `action` | `server.py` → `src/game_application.py` → `src/engine.py:2078 handle_action()` → `src/agent_graph.py`（LangGraph 回合） |
| 检定确认弹窗 | `GameControls.tsx`（DecisionModal） | WS `suggest_reply` / `decision_reply` | `src/action_checks.py`、`src/combat.py`（决策等待） |
| 战斗 | 3D 骰子 `dice3d/`、`options.ts` | WS `action` / `decision_reply` | `src/combat.py`（权威状态机）、`src/combat_agent.py` |
| 存档/读档/重命名/删除 | `AppHeader.tsx`、`PanelLayers.tsx`（SavePanel）、`panels.ts` | WS `save`/`save_create`/`save_load`/`save_delete`/`save_rename` | `src/persistence.py`、`src/database_turn_journal.py` |
| 时间线分支/回合改写 | `MessageList.tsx` | WS `turn_branch_create` / `turn_rewrite` | `src/world_branches.py`、`src/turn_reconciler.py` |
| 角色面板/线索 | `CharacterPanelContent.tsx`、`panels.ts` | WS `state` | `src/characters.py`、`src/investigators.py` |
| 模型设置（叙述/判定模型） | `ModelSettingsPanel.tsx`、`settings.ts` | WS `model_settings_get/update` | `src/model_settings.py`、`src/config.py` |
| 模组导入（.trpgmod） | `ModuleImporter.tsx` | POST `/api/modules/inspect` → `/api/modules/import` | `src/module_http.py` → `src/module_registry.py`（安全检查/安装）→ `src/module_compiler.py`（编译） |
| 玩家笔记 | `UtilityPanel.tsx`、`utility.ts` | WS `player_notes_get/update` | `src/player_notes.py` |
| 断线恢复 | `ws.ts`、`room-ws.ts` | WS `turn_recovery_get` / 房间 `room_sync` | `src/database_turn_journal.py`、`src/multiplayer_recovery.py` |
| 结局结算 | `AppHeader.tsx` | WS `settle_case` | `src/endings.py`、`src/characters.py`（写长期履历） |
| 素材图片发放 | `HandoutLayer`（物证卡片） | —（收 `handout` 事件） | `src/handouts.py`、`src/asset_payload.py` |

## 5. HTTP API 全清单（后端怎么对外提供一次性能力）

路由分散在 `server.py` 与 `src/*_http.py`，全部如下（方法 + 路径 + 用途 + 代码位置）：

| 方法 | 路径 | 用途 | 代码 |
|---|---|---|---|
| GET | `/api/health` | 进程存活检查（不碰数据库） | `server.py:1587` |
| GET | `/api/ready` | 部署就绪（含数据库往返） | `server.py:1597` |
| GET | `/api/theme` | 当前模组主题 | `server.py:1581` |
| GET | `/api/characters` | 可选调查员列表 | `server.py:1615` |
| POST | `/api/auth/register` | 注册并建立登录 Session | `src/auth_http.py:38` |
| POST | `/api/auth/login` | 登录 | `src/auth_http.py:69` |
| POST | `/api/auth/logout` | 撤销 Session | `src/auth_http.py:98` |
| GET | `/api/auth/me` | 当前登录账号 | `src/auth_http.py:109` |
| GET/POST | `/api/worlds` | 列出/创建世界（房间） | `src/multiplayer_http.py:64/96` |
| DELETE | `/api/worlds/{id}` | 房主归档删除房间 | `src/multiplayer_archive_http.py:30` |
| GET/POST | `/api/worlds/{id}/invites` | 房主列出/创建邀请码 | `src/multiplayer_http.py:146/122` |
| DELETE | `/api/worlds/{id}/invites/{invite_id}` | 撤销邀请 | `src/multiplayer_http.py:156` |
| POST | `/api/invites/accept` | 用邀请码加入房间 | `src/multiplayer_http.py:167` |
| GET | `/api/worlds/{id}/members` | 成员与调查员占用 | `src/multiplayer_http.py:184` |
| PATCH/DELETE | `/api/worlds/{id}/members/{user_id}` | 改角色/移除成员 | `src/multiplayer_http.py:220/299` |
| POST | `/api/worlds/{id}/owner` | 移交房主 | `src/multiplayer_http.py:264` |
| GET | `/api/worlds/{id}/investigators/options` | 房间可选调查员 | `src/multiplayer_http.py:194` |
| POST/DELETE | `/api/worlds/{id}/investigators/claim` | 认领/释放调查员 | `src/multiplayer_http.py:326/388` |
| GET | `/api/modules` | 模组列表与活动模组 | `src/module_http.py:124` |
| GET | `/api/modules/schema/*` | 模组 JSON Schema（v1/v2/lorebook） | `src/module_http.py:132-148` |
| POST | `/api/modules/compile` | 无副作用编译预览 | `src/module_http.py:152` |
| POST | `/api/modules/inspect` | `.trpgmod` 预检（不安装） | `src/module_http.py:163` |
| POST | `/api/modules/import` | 校验并安装模组 | `src/module_http.py:175` |
| POST | `/api/modules/switch` | 切换默认模组 | `src/module_http.py:197` |
| GET | `/api/assets/{module}/{file}` | 读取模组素材 | `src/module_http.py:218` |
| GET/POST | `/api/editor/projects` | 模组编辑器工程会话 | `src/editor_api.py:37/41` |
| GET/PATCH/DELETE | `/api/editor/projects/{session_id}` | 编辑器会话读写 | `src/editor_api.py:49/56/69` |
| GET | `/` | 前端页面（构建产物） | `server.py:1685` |
| WS | `/ws` | 单机游戏实时通道 | `server.py:1625` |
| WS | `/ws/room?world_id=` | 多人房间实时通道 | `src/multiplayer_ws.py` |

## 6. WebSocket 协议速览（游戏回合的"对话语言"）

### 6.1 客户端可以发什么（前端 → 后端）

| 消息 type | 一句话用途 |
|---|---|
| `start` | 新游戏，带上所选调查员 |
| `action` | 玩家行动（每回合的核心消息） |
| `suggest_reply` | 回答检定确认（是/否） |
| `decision_reply` | 回答战斗/暴力确认弹窗（选项 ID） |
| `state` | 请求角色与线索状态 |
| `switch_module` | 切换模组 |
| `save` / `save_create` / `save_list` / `save_load` / `save_delete` / `save_rename` | 存档全系列 |
| `turn_rewrite` | 改写最后一轮叙事（无副作用） |
| `turn_branch_create` | 在决策点创建时间线分支 |
| `turn_recovery_get` | 断线后用 turn_id 请求恢复 |
| `turn_diagnostics_get` | 上一回合诊断 |
| `model_settings_get/update` | 模型设置读写 |
| `player_notes_get/update` | 玩家笔记读写 |
| `settle_case` | 确认结局、结算案件 |
| `quit` | 保存并退出 |
| `ping` | 心跳 |

多人房间额外有：`room_ready`（准备）、`actor_assign`（房主指定行动者）、`room_ack`/`room_sync`（断线增量恢复）。

### 6.2 服务端会发什么（后端 → 前端）

| 事件 type | 一句话用途 |
|---|---|
| `module_list` / `character_list` / `theme` / `model_settings` / `save_list` | 连接初始化时按固定顺序下发 |
| `gm_turn_start` | 一个守秘人回合开始（带 turn_id） |
| `turn_phase` | 回合阶段提示 |
| `narrative_chunk` | 流式叙事文字增量（打字机效果） |
| `chat_events` | 回合定稿后的权威聊天记录 |
| `tension` | 气氛提示 |
| `suggest_check` | 请求检定确认 |
| `decision_request` | 请求多选决定（战斗确认等） |
| `dice_result` | 骰子结果（前端做动画） |
| `handout` | 线索图片/素材发放 |
| `glm_summary` | 复杂工具结果的一句话摘要 |
| `choices` | 行动选项菜单（"你可以——"） |
| `done` | 回合完成（网络完成，展示完成后才解锁输入） |
| `character_state` / `state_data` | 权威角色面板 / 角色+线索 |
| `saved` / `loaded` / `save_deleted` / `save_renamed` | 存档回执 |
| `game_over` / `case_settled` / `quit_ok` | 结局与退出 |
| `error` / `protocol_error` / `turn_rejected` | 错误与回合冲突 |
| `turn_recovery` / `turn_rewritten` / `turn_branched` / `world_switched` | 恢复/改写/分支/时间线回执 |

### 6.3 一个回合的标准生命周期（最常看的图）

```
玩家发 action
  → 服务端发 gm_turn_start(turn_id, seq=1)   ← 回合开始
  → turn_phase（等待阶段）
  → 可选：narrative_chunk 前置叙事 / tension / suggest_check→suggest_reply
           / decision_request→decision_reply / dice_result / handout / glm_summary
  → narrative_chunk × N（流式叙事正文）
  → chat_events（定稿，覆盖流式临时布局）
  → choices（选项菜单）
  → done（回合结束，前端解锁输入）
```

## 7. 前后端调用模式（看懂"谁调谁"）

### 7.1 前端调 HTTP：统一走 `apiFetch`

所有 HTTP 请求必须经过 `frontend/src/api/client.ts` 的 `apiFetch()`——它统一带登录
Cookie、统一错误处理、用 zod 校验返回格式。业务文件只声明"调哪个接口"：

```ts
// frontend/src/api/worlds.ts —— 前端"创建房间"
export function createWorld(...) {
  return apiFetch("/api/worlds", schema, { method: "POST", body: {...} });
}
```

### 7.2 前端发 WebSocket：统一走 `safeSend` / `roomSend`

`frontend/src/ws.ts`（单机）与 `room-ws.ts`（多人）负责连接、心跳、断线重连；
业务代码只发 JSON 消息：

```ts
// frontend/src/start.ts —— 前端"开始新游戏"
safeSend(JSON.stringify({ type: "start", character_ref: selectedCharacterRef }));
```

### 7.3 后端收 HTTP：FastAPI 路由装饰器

每个功能模块一个 `create_xxx_router()` 工厂，路由用装饰器声明，函数里做业务、返回 JSON：

```python
# src/auth_http.py —— 后端"登录"
@router.post("/api/auth/login")
def login(data: dict, request: Request, response: Response):
    user = authenticate(db_url(), data.get("username"), data.get("password"))
    ...
    return {"id": user.id, "username": user.username}
```

### 7.4 后端收 WebSocket：注册表分发

不是装饰器，而是"消息注册表"（`src/ws_router.py`）：每种消息 type 注册一个处理函数，
前端发什么 type 就自动路由到哪个函数：

```python
# server.py —— 后端"处理 start 消息"
@router.handler("start")
async def handle_start(data: dict) -> None:
    intent = game_app.start_game.execute(data.get("character_ref"))
    ...
```

> 想找某个 WS 消息的后端处理：在 `src/` 和 `server.py` 里搜 `handler("<type>")`。

## 8. 深度专题：单机开局全流程（从按钮点到数据库）

以"点『以此调查员开始』进入游戏"为例，逐步拆解：

| 步骤 | 发生什么 | 代码位置 |
|---|---|---|
| 1 | 玩家在开屏选好模组、选中调查员卡，点"以此调查员开始" | `frontend/src/react/components/StartScreen.tsx`（按钮） |
| 2 | `startGame()` 置"开局中"状态，调用 `sendStartRequest()` | `frontend/src/start.ts:61` |
| 3 | 前端发 WS 消息 `{"type":"start","character_ref":...}` | `frontend/src/start.ts:25` → `ws.ts safeSend` |
| 4 | 后端消息循环按 type 路由到 `handle_start` | `server.py:1529`（dispatch）→ `server.py:1275` |
| 5 | `StartGame.execute()` → `engine.reset(character_ref)` | `src/game_application.py:52` → `src/engine.py:695` |
| 6 | `reset()` 里 `context.reset_world()`：用模组的 `world_state_initial.json` 模板重建世界状态，写入数据库 `world_states` | `src/engine.py:698` |
| 7 | `_apply_starting_character()`：把选中的调查员复制进 `world_state.pc`，并加上模组起始物品 | `src/engine.py:731`、`src/characters.py` |
| 8 | `prepare_session()`：重建 system prompt（模组 module.md + skills + lorebook），清空消息历史 | `src/engine.py:709`、`src/persistence.py` |
| 9 | 给模型写开局指令：要求 6–8 段开场演出 + 结尾"**你可以——**"3 个选项 + 自由行动 | `src/engine.py:710` |
| 10 | 启动"开场回合"（开局本身就是一次守秘人回合）：`launch_reserved_turn(engine.handle_action, ...)` | `server.py:1329` |
| 11 | 后端沿 WS 推回：`gm_turn_start` → 流式开场叙事 `narrative_chunk` → `chat_events` → `choices` → `done` | `src/event_stream.py` |
| 12 | 前端收到 `gm_turn_start`：`onGmTurnStart()` 置 `gameStarted=true`，关闭开屏进入游戏 | `frontend/src/start.ts:41` |
| 13 | 叙事流由 `renderer.ts` 变成聊天区打字机文字；选项由 `options.ts` 渲染成按钮 | `frontend/src/renderer.ts`、`options.ts` |

> 连接建立时（开局前）后端还会先下发 `module_list`、`character_list`、`theme`、
> `model_settings`、`save_list` 五个初始化事件，让开屏菜单有内容可显示
> （见 `server.py:1256-1271`、`docs/API.md` §5.1）。

## 9. 自己查代码三步法（产品经理版）

1. **找到界面上那个按钮/区域** → 在 `frontend/src/react/components/` 找对应组件
   （组件名基本就是功能名：`StartScreen`、`SavePanel`、`ModelSettingsPanel`……）。
2. **看它调用了什么** → 组件里调用 `frontend/src/` 根目录的 `*.ts`（`start.ts`、
   `options.ts`、`panels.ts`、`settings.ts`、`utility.ts`），看它们 `safeSend` 了什么
   type、或调了 `api/` 的哪个 HTTP 函数。
3. **去后端找处理** → HTTP 路径在 `src/*_http.py` 里搜（如 `/api/worlds` 在
   `multiplayer_http.py`）；WS type 在 `src/` 和 `server.py` 里搜 `handler("<type>")`；
   处理函数里调用的 `src/` 模块就是真正的业务实现。

## 10. 常用开发命令

```bash
# 后端单跑（开发）: http://127.0.0.1:8765
venv/bin/python server.py

# 前端开发服务器: http://127.0.0.1:5173
cd frontend && npm run dev

# 测试
venv/bin/python -m pytest -q          # 后端
cd frontend && npm test               # 前端单元
cd frontend && npm run test:e2e       # E2E（Playwright）

# 提交前静态检查
venv/bin/python -m ruff check src server.py tools tests
```

## 11. 更多文档

- `docs/ARCHITECTURE.md` —— 面向开发者的完整架构（进程、模块、数据所有权）
- `docs/API.md` —— 所有 HTTP/WebSocket 消息的字段级规范
- `docs/MODULE_FORMAT.md` —— 模组格式与安全边界
- `docs/DEPLOYMENT.md` —— 生产部署（PostgreSQL、TLS、备份）
- `docs/MULTIPLAYER_USER_GUIDE.md` —— 多人游戏使用说明
