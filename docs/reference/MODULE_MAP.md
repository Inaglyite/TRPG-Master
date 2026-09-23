# 模块与源码索引

基线：`46d5760`，整理于 2026-09-16。用于定位代码，不表示每个模块都完成产品验收。当前边界见[架构](../ARCHITECTURE.md)与[状态](../STATUS.md)。源码路径均相对仓库根目录。

## 后端模块地图

下列为模块导航，未标为关键链路实读的部分属于“已定位”。`__init__.py` 等包声明不逐项重复。

### 3.1 应用装配、会话和房间

| 模块 | 责任与边界 |
|---|---|
| `server.py` | FastAPI 组合根、路由装配、本地 WS 会话；入口会实例化旧引擎适配对象，但结构化动作由独立 wire 接管 |
| `src/app/config.py`、`runtime.py` | 配置、运行目录、模组绑定、数据库上下文；静态模组更新与旧载荷升级钩子在 runtime |
| `src/app/game_application.py` | 旧玩法开局、继续、行动、改写、存档用例，不负责 HTTP 身份认证 |
| `src/app/engine.py`、`agent_graph.py`、`engine_primitives.py` | 旧回合编排、模型职责路由、回合上下文与 finalize；不是新 runner 的同义名 |
| `src/app/event_stream.py` | 旧路径有序事件投递；不能与 outbox 游标混成一个序号 |
| `src/app/settings_service.py` | 模型设置的应用层服务 |
| `src/app/game_loop.py`、`logger.py`；根目录 `game_loop.py/start.py/start_desktop.sh` | 终端、配置与桌面启动、日志入口 |
| `src/multiplayer/ws.py`、`ws_session.py`、`ws_router.py`、`messages.py` | 连接生命周期、协议路由、消息适配 |
| `src/multiplayer/room_runtime.py`、`room_events.py`、`recovery.py` | 按世界共享引擎与房间事件、重连与恢复；保留进程内所有权假设 |
| `src/multiplayer/service.py`、`guards.py`、`world_creation.py` | 世界、成员、调查员认领和开局控制 |
| `src/multiplayer/private_state.py` | 登录用户专属状态投影，不得进入公共广播缓存 |
| `src/multiplayer/http.py`、`archive_http.py`、`solo_timeline_http.py` | 房间、归档、单人时间线 HTTP 入口 |
| `src/multiplayer/solo_timeline_ws.py`、`world_timeline_ws.py` | 世界切换、分支等控制消息；新分支不能伪造 legacy turn_id |

### 3.2 结构化平台（本轮重点实读）

| 模块 | 责任与关键关系 |
|---|---|
| `bootstrap.py`、`engine_gate.py` | 模式元数据、keeper 授权、本地隐式操作者；human 新世界不要求模型客户端 |
| `validation.py`、`errors.py` | JSON Schema 帧/命令/事件校验与结构化错误；唯一协议正本在 `schemas/structured-play/v1/` |
| `principal.py` | 服务端会话/成员派生 Principal，调查员控制权、人类接管和 Agent run/epoch 栅栏 |
| `gateway.py` | 帧校验、连接世界绑定、principal 解析、服务调用、接收者过滤、调度；有按世界的进程内 asyncio 锁 |
| `server_integration.py`、`room_integration.py` | 本地与房间接线；新模式拒绝静默返回旧文字行动路径；恢复、快照与开局名册 |
| `service.py` | 玩家请求、命令提交、检定响应、快照、outbox 重放、记忆查询；不进入旧 turn_cache |
| `domains.py` | 发言、移动、时间、属性、NPC 在场、事实、线索、素材、物品、请求/草稿收尾、记忆等 handler |
| `checks.py` | 普通骰、持久检定、条件复核、孤注一掷、decline 与时间代价 |
| `ids.py`、`registries.py` | 摘要/稳定 ID、物品和线索注册表、旧标签数据兼容；不能拿名称当身份 |
| `agent.py` | JSON 决策循环、上下文、查询、命令校验与执行、等待、暂停、接管、预算、assisted 草稿 |
| `agent_runtime.py` | BYOK caller、模型响应诊断、触发调度；活动 task 表按进程保存，非分布式队列 |
| `agent_prompts.py` | 实际生效系统契约与简写命令目录；不是 legacy 的 skill pin 组装器 |
| `interactions.py` | 跨请求交互线程、待办归属、continue/replace/close、抵达联动与公开投影 |
| `memories.py` | 角色知识记录、提交后派生、幂等补建、带预算检索、更正链；不反写权威世界事实 |
| `branch.py` | 从当前已提交状态分支、结构化读档 reconcile；复制哪些账本、不复制哪些事件有独立契约 |

命令目录当前由 `domains.COMMAND_HANDLERS` 的 14 项加 `request_check/resolve_check` 构成 16 项。玩家行动请求目前是 `present_clue/use_item/move/freeform`。普通骰与检定回应不是自由文本快捷命令。

### 3.3 确定性玩法（`src/gameplay/`）

| 子域与文件 | 责任 |
|---|---|
| `action_adjudication.py`、`action_checks.py`、`action_resolution.py`、`destination_grounding.py` | 旧路径的裁决提案、校验、检定和移动规划；不能未经审查拿来接管新模式自由文本 |
| `action_preflight.py`、`escalation_preflight.py`、`transition_prelude.py` | 旧预演、升级威胁门、过渡/接待节拍与旧载荷迁移；与新正常叙事等待不同 |
| `discovery.py`、`encounters.py`、`consequences.py`、`crisis.py`、`endings.py` | 发现、遭遇、后果、危机、结局；新命令是否完整复用需逐项证明 |
| `combat.py`、`combat_agent.py`、`combat_authority.py` | 战斗状态机、提示职责与权限 |
| `percentile.py`、`resolution.py`、`check_context.py` | 百分骰/检定结果、重复尝试上下文 |
| `sanity_sources.py`、`personality.py` | SAN 来源及角色心理相关逻辑 |
| `case_clock_time.py`、`case_clocks.py`、`world_time.py` | 时间结算、案件时钟、时间表示；时间必须来自实际结算 |
| `inventory.py`、`handouts.py` | 物品与素材候选/展示授权、静态配置刷新 |
| `characters.py`、`investigators.py` | 角色档案、角色表、队伍状态与本地履历 |
| `npc_conversations.py`、`npc_speaker_aliases.py`、`speaker_parser.py` | 旧 NPC 对话记录、别名、叙事发言者推断；新消息已有显式 speaker，不应再猜气泡归属 |
| `choices.py`、`scene_projection.py` | 选项与玩家安全的场景显示投影 |
| `state_paths.py`、`turn_mutations.py`、`turn_reconciler.py` | 状态路径、回合变更与对账 |
| `narrative_consistency.py`、`turn_performance.py` | 可模式化的一致性检查与性能证据；不是通用叙事真实性证明 |

### 3.4 AI 模型、上下文、规则知识与旧工具

| 子域与文件 | 责任 |
|---|---|
| `src/ai/model/llm.py`、`model_request.py`、`model_session.py` | 旧模型请求和会话生命周期 |
| `model_streamer.py`、`model_stream_helpers.py`、`model_stream_capacity.py`、`model_stream_diagnostics.py` | 流式解析、容量处理、诊断 |
| `model_tool_catalog.py`、`provider_adapter.py`、`llm_concurrency.py` | 请求工具面、供应商适配与并发；新 caller 是否复用每一项不能默认成立 |
| `model_settings.py`、`route_service.py` | 服务配置、角色模型绑定、有效路由 |
| `crypto_box.py`、`egress_guard.py`、`pinned_transport.py` | API Key 加密、目标地址约束与固定出站连接 |
| `src/ai/context/context_capacity.py`、`context_overflow.py`、`history_compactor.py`、`context_summary.py` | 旧请求容量、溢出与摘要；预算说明必须标注运行模式 |
| `context_checkpoint.py`、`context_events.py`、`context_maintenance.py`、`context_shadow.py`、`narrative_history.py` | 上下文留档、维护、对照和可见历史 |
| `lorebook.py` | 模组知识条目的有界筛选、门槛与冷却；与角色经历检索不是一回事 |
| `structured_memory.py` | 旧 H3 shadow candidates/facts；不能与新 `src/structured/memories.py` 混称已接入长期记忆 |
| `src/ai/skills/skill_manifest.py`、`skill_pins.py`、`skill_resolver.py`、`skill_activation.py` | 规则知识目录、世界冻结版本、激活、注入；项目开发 SKILL 与游戏运行 skill 是两种概念 |
| `src/ai/tools/registry.py`、`tool_aux_handlers.py`、`tool_schema_defs.py` | 旧 handler 与工具 schema |
| `tool_runtime.py`、`tool_pipeline.py`、`tool_policy.py`、`tool_request_authority.py`、`tool_protocol.py` | 旧工具注册、请求级授权、审计与 DSML 隔离；不等于新命令 service |

### 3.5 数据、模组、HTTP 与身份

| 模块 | 责任 |
|---|---|
| `src/storage/database.py` | ORM、连接与会话；本地 SQLite、云端 PostgreSQL 支持 |
| `database_store.py`、`world_store.py`、`world_migrations.py` | 数据库世界状态、文件兼容实现、JSON 状态版本迁移 |
| `database_turn_journal.py`、`turn_journal.py` | legacy 回合日志、事件、完成/中断与恢复 |
| `persistence.py` | 数据库存档/快照、兼容导入导出，亦包含旧系统 prompt 装配；不能按过时文件夹注释理解其全部权威来源 |
| `world_branches.py`、`player_notes.py` | 旧回合时间线、分支元数据、用户笔记（不自动进模型） |
| `model_config_store.py` | 云端用户/世界 BYOK 配置与路由解析；密钥密文存储 |
| `src/modules/module_format.py`、`module_compiler.py`、`module_diagnostics.py`、`module_migrations.py` | 模组作者格式、编译、诊断、格式升级 |
| `module_registry.py`、`editor_projects.py` | 包预检、版本化安装、路径/解压安全、编辑工程 |
| `src/web/module_http.py`、`editor_api.py` | 模组与编辑器 HTTP 适配 |
| `asset_payload.py`、`frontend_payload.py`、`frontend_http.py` | 安全素材载荷、前端初始化投影和静态服务 |
| `src/auth/service.py`、`http.py` | Argon2 密码、Session、Origin/本地凭证、账号与世界权限 |

## 前端、桌面与内容目录

| 入口/区域 | 职责与应守边界 |
|---|---|
| `frontend/src/react-main.tsx`、`react/App.tsx`、`react/GameShell.tsx` | React 装配与布局；新业务 UI 保持声明式，不恢复 DOM 手工状态机 |
| `ws.ts`、`room-ws.ts` | 本地/房间传输、模式切换、历史与重连，结构化事件交给共用适配层 |
| `protocol/server-message.ts` | 外层事件白名单；新事件不能只更新内层 schema 而被这里丢弃 |
| `protocol/structured.ts`、`keeper-commands.ts`、`structured-fixtures.ts` | 新协议类型、解析、序列去重、命令表单与 fixtures；对照后端 JSON Schema |
| `structured-transport.ts`、`structured-effects.ts` | 统一发帧、重试同 ID、快照/事件投影；发送成功不等于执行成功，不把按钮变回句子 |
| `state/structured-store.ts`、`structured-editor-store.ts` | 请求、检定、线程、记忆查询、编辑器暂态 |
| `state/app-store.ts`、`message-store.ts`、`scene-store.ts` | UI 状态、消息播放和当前位置；服务端事实与客户端临时状态要分开 |
| `state/online-store.ts`、`start-store.ts`、`model-store.ts`、`investigator-panel-store.ts` | 联机身份、开局、模型配置、角色面板 |
| `react/components/structured/` | 主持控制台、过渡/检定卡、移动、assisted 草稿与工具条 |
| `react/components/investigator/`、`investigator-actions.ts` | 状态/线索/物品卡及操作；按运行模式分流，稳定 ID 不依赖名称 |
| `react/components/online/` | 登录、大厅、房间、云端单人、角色选择与时间线 |
| `start.ts`、`panels.ts`、`options.ts`、`settings.ts`、`utility.ts` | 开局、面板命令、自由输入、模型与诊断、笔记等适配 |
| `renderer.ts`、`text.ts`、`theme.ts`、`dice3d/`、`styles/` | 叙事渲染、文本安全、主题、骰子动画与布局；骰面只能消费服务端结果 |
| `frontend/electron/` | 受信云端 origin、窄 IPC、主进程启动/停止本地后端、凭证与路径边界 |
| `frontend/e2e/`、组件/协议/store 单测 | 从真实 WS 到 DOM 的联调证据，和脚本化模型测试分开 |
| `editor/dist/`、`tools/sync_editor_bundle.sh` | 仓库有编辑器发布产物；作者源码与同步来源需单独确认，不把压缩 bundle 当常规源码修改 |
| `mod/`、`characters/`、`skills/`、`rules/`、`schemas/trpgmod/` | 剧情模板与素材、角色模板、游戏知识、规则配置、作者契约；不是玩家运行数据库 |
| `tools/` | 规则 CLI、导入、维护、回合恢复、性能、主线与过渡验收；实际验收路径必须记录 |
