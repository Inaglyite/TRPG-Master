# 架构师接手台账与模块地图

> 历史归档（2026-09-16 整理）：正文保留当时的结论与适用版本，不作为当前完成状态或执行授权。现行入口见 [README](../../../../README.md)，未完成事项见 [STATUS](../../../STATUS.md)。正文中的仓库路径按仓库根目录理解，`/tmp` 产物不保证仍存在。

日期：2026-09-16。首次勘查代码基线：`46d5760`，实验分支 `experiment/keeper-platform`。

## 1. 职责、范围和证据等级

产品方向：面向人类主持和 Agent 主持的跑团平台，带 Agent harness；不是让关键词解析器代替主持决定行动。按钮提交结构化意图，自由文本用于正常扮演与交流，确定性命令负责授权、结算和持久化。

分工约定：

- 用户：产品方向、取舍、额度与对外发布授权。
- Codex：架构负责人；维护模块与数据边界、协议评审、跨模块方案、风险台账、任务拆分和验收结论。默认不抢实现；收到明确实现任务或补位要求后再编码。
- Kimi：当前后端主要实现者与集成人；并非永久排他权限，后续可由用户调整。
- zcode：当前前端与 E2E 主要实现者；与 Kimi 按冻结协议协作。
- 正式部署、真实 API 消费、生产数据操作仍分别需要相应授权，架构师角色不扩大权限。

本台账是**源码入口与关键链路的第一轮静态勘查**，不是“所有文件逐行审计完成”。证据区分：

1. **已静态核对**：本轮实际阅读关键函数或调用入口；说明代码意图与连接关系，不证明运行无缺陷。
2. **已定位**：找到模块、符号与相关测试，尚未深入每个分支。
3. **交付报告证据**：来自双方报告或仓库验收记录，本轮没有重新执行。
4. **待验证**：需定向复现、真实模型、CI 或部署环境核查才能下结论。

本轮未读取密钥、玩家数据库或真实存档，未运行模型、全量测试、远程 CI 查询或部署；仅新增本文件。原有截图工作区改动保留。

## 2. 总体结构：两条运行路径并存

```text
Electron / Browser
  React + Zustand：按钮、自由输入、叙事、检定、主持控制台
            │ HTTP / WS（本地 /ws；房间 /ws/room）
     FastAPI + Session / 世界权限 / 房间控制面
            │ 按世界 execution_profile 分流
            ├─ legacy
            │   GameApplication → GameEngine → LangGraph
            │   裁决/确定性后备 → 旧工具管线 → turn_cache
            │   DatabaseTurnJournal：整回合原子提交与恢复
            │
            └─ structured_v1
                StructuredGateway → StructuredPlayService
                   ↑ 人类主持 / assisted / KeeperAgentRunner
                领域命令：每命令短事务
                WorldState + GameCommand + EventOutbox
                提交后投递 → 前端去重、投影与恢复

共用基础：数据库、模组资源、账号、BYOK 配置、部分领域规则
不能自动视作共用：完整提示词、工具目录、记忆、回合日志、恢复语义
```

关键代码：`server.py`，`src/app/{engine,agent_graph,game_application}.py`，`src/structured/{server_integration,room_integration,gateway,service,agent}.py`。

世界元数据里的 `execution_profile` 与 `keeper_mode` 是两根不同轴。`legacy/structured_v1` 决定执行路径，`human/assisted/agent` 决定新平台的主持方式；本地/云端、单人/多人又是另外的维度。不能用“云端”等同于“新模式”，也不能用“新模式”宣称“完整游戏主线已支持”。

## 3. 后端模块地图

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

## 4. 前端、桌面与内容目录

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

## 5. 权威数据与三种恢复

### 5.1 数据所有权

| 数据 | 所有者/存储 | 不应被误认为 |
|---|---|---|
| 世界模式、成员、角色认领 | `worlds/world_members/world_investigators` | 客户端自报 principal |
| 当前世界事实 | `world_states`；legacy store 或 structured service 按模式写入 | 聊天文字、摘要或人物记忆 |
| legacy 完成回合 | `turns/turn_events/snapshots/save_slots` | structured 命令游标 |
| 玩家请求/检定 | `player_requests/check_requests` | 已经成功的行动 |
| 命令提交账本 | `game_commands` | 模型的建议或 provider tool-call ID |
| 新事件与接收范围 | `event_outbox` | 全员可见的公共日志 |
| 主持控制权 | `keeper_control` | 房主身份天然拥有全部秘密 |
| 当前交互 | `interaction_threads` | 出发授权或长期经历 |
| 新角色记忆 | `character_memories` | 绝对事实或 legacy H3 fact |
| 旧 shadow 记忆 | `memory_fact_candidates/memory_facts` | 已接入新 runner 的召回库 |
| 游戏规则 pin | `world_skill_pins/world_skill_pin_manifests` | 读取当前磁盘文件就等于旧世界使用的规则 |
| 上下文追踪 | `context_sessions/model_context_events` | 所有模式已经共用的诊断通道 |
| BYOK 凭据 | `model_service_configs` 密文；主密钥另存 | 可写进存档、prompt、日志的普通设置 |
| 玩家笔记与跨案履历 | `player_notes`；本地 profile | NPC 记忆或自动授权的检索内容 |

`revision`、`sequence`、`event_id`、`request_id`、`command_id`、`thread_id`、`check_request_id` 各有用途。尤其同 revision 可以有多条消息，不能按 revision 一刀去重。没有世界可落库的连接级错误会使用合成信封，前端不能因 `event_id=0` 而全部丢掉。

### 5.2 三种操作不能共用错误语义

- **刷新/断线重连**：恢复当前已提交世界、待办和合法事件，不是读档，不取消未完成决定。
- **主动读档**：结构化恢复显式 reconcile 请求、检定、线程、记忆与未来事件；当前实现会令未决请求 failed、pending 检定作废、开放线程取消。这是产品差异，不得宣传为完整回到当时所有交互的精确快照。
- **创建分支**：legacy 从有效完成 turn 分叉；structured 从当前 committed revision 分叉。新世界独立世界 ID，控制权与 outbox 不直接继承，其他复制内容遵守协议。

这些边界已阅读 `branch.py` 与协议；并发读档、迟到 Agent、账本重放等交叉行为仍需要专门审计，不能因源码注释说“天然安全”就直接认定安全。

## 6. Agent 上下文的实际装配

### 6.1 旧引擎

`persistence.load_system_prompt` → 世界 skill pins / 模组内容 → 权威状态 → Lorebook/按需 skill → ModelSession 历史与容量管理。旧 `src/ai/context/structured_memory.py` 明确是 shadow-only，尚不能称为玩家记忆功能。

### 6.2 新 runner

已实读 `KeeperAgentRunner._build_context`、`_run_queries`、`run` 与 `build_byok_caller`：

- system：`agent_prompts.build_system_prompt()` 的固定主持契约和命令简表。
- user：JSON 对象，含 `snapshot/open_threads/trigger_context/pending_requests/recent_public_messages/character_memories/run_log`。
- 近期消息：最多 8 条 `message_completed` 候选，每条正文截到 400 字符，按允许 audience 选择；不是完整旧 ModelSession。
- 记忆：角色数据库最近候选做本地过滤与评分；自动注入默认最多 8 条/800 字符，主动查询每运行最多 3 次，单次结果有预算。不是向量数据库，也不是无限全历史检索。
- 请求：当前 caller 用 JSON object response；模型产出命令列表，runner 校验再执行。不是直接复用旧 ToolRuntime 的 provider 原生工具循环。
- 每运行默认最多 6 次模型调用、12 条提交命令；输出预算读取 BYOK 绑定并有 32768 上限。估算 token 预算与真实账单不能等同。
- 提交命令后派生记忆是独立事务，可失败补建；显式 `record_memory` 则走正常命令事务。
- 新查询字段与结果会回到下一步上下文；这证明通道存在，不能代替“正确检索且正确使用”的真实模型评测。

**架构核查重点**：当前新 runner 使用的 `session_snapshot` 是受角色过滤的投影，列出场景、目的地、目标、物品、线索、请求等；不能把它叫作完整原始世界状态。当前 `_build_context/build_system_prompt` 未见旧模组 prompt/skill pin/Lorebook 全套装配。必须针对场景描述、NPC 动机、发现依据、规则、危机与结局做“新主持实际能读到什么”的覆盖矩阵，不能因为这些数据在世界 JSON 中存在就认为 Agent 已知。

## 7. 已确认的架构原则

1. 按钮发结构化意图，自由语言由主持理解；不把新平台重新接回关键词自动移动。
2. 过渡是正常叙事与自由回应；引擎可靠记录未执行事项，不要求固定台词，不用确认弹窗替代主持。
3. 主持理解合理性，工具复核权限、对象、状态和确定性规则；模型不直接改数据库。
4. 分开“传输送达”“命令提交”“游戏成败”“整条请求完成”。
5. 开放线程不是授权，追问完成不是原任务完成，抵达不是调查完成。
6. 叙事的权威结果必须有已提交依据；提交时序正确也不保证模型不会写错，后者单独验收。
7. 人类与 Agent 使用同一领域服务；人类无 Key 能玩，但不意味着可越权读取秘密。
8. 玩家、房主、keeper、Agent 的权限分开。显示上隐藏不等于服务端保密；keeper 可知也不等于 NPC 知情。
9. 新旧模式并存期间显式声明覆盖范围，不用一套测试替两套路径背书。
10. 不因模块化需求立即引入 K8s/微服务。现有 room、active task、锁存在进程内所有权，横向多副本需要先设计世界归属、调度和跨进程事件。

## 8. 当前风险与后续核查顺序

以下不是已证实漏洞清单，不在本轮自动实施。

| 项目 | 当前证据与结论 | 下一步/责任建议 |
|---|---|---|
| CI 开局超时 | 仓库最终报告写红并归因冷启动；本轮未读远端 trace，根因仍待验证 | zcode 按实际日志定位；不只提高 timeout/retry |
| 真实模型 A–F | 最后交付明确未执行 | 按用户单独额度授权执行；同版本完整链路与对偶，不挑成功样本 |
| 新主持的知识面 | `_build_context`、固定 prompt 与旧装配确实不同 | Codex 建能力矩阵；Kimi 按实际缺口实现受控读取；先查数据可达性再评模型强弱 |
| 新旧规则覆盖 | 新目录是 16 命令；不能据旧战斗/SAN/结局模块存在就推定全部新玩法闭环成立 | 逐项核对领域效果所有权、条件门与结局通路；必要时界定首发范围 |
| 查询后来源保留 | 查询 API 返回 source；runner 的查询结果转成较短文本，需核对溯源与更正所需信息是否仍充分 | 定向模型上下文检查，不盲目扩大全量注入 |
| 恢复与运行栅栏 | 回滚 revision、保留账本、活动运行等有不同生命周期 | 定向核查迟到命令/重放/读档后记忆补建；不只看正常重连 |
| 并发与扩容 | gateway 锁、活动 Agent task、房间实例有进程内归属；PostgreSQL/SQLite 锁行为也不同 | 先核对部署进程数与多连接/多世界对偶，再谈多副本 |
| 架构门禁覆盖 | `check_architecture.py` 已有旧大文件行数和 gameplay 依赖门禁；未见 structured 子域同等细分约束 | 把新边界评审纳入架构台账，后续经授权补门禁，不以“架构绿”代表全面审计 |
| 文档漂移 | 总架构文档主体仍是 legacy；对新记忆、新事务描述不完整 | 本文件先补入口；后续统一更新总文档，保留旧模式说明 |
| 部署约定与实现漂移 | AGENTS 仍描述临时脚本 sudo 限制；当前 workflow 已调用固定 `trpg-activate-release` | 不自行改保护约定；需授权维护者确认主机是否安装安全入口及权限，再更新事实说明 |
| rate_limited | 交付报告登记结构化限制未实现；不等于整个系统完全无任何限流（auth 另有 limiter） | 核实究竟缺哪一层、影响什么首发场景，禁止笼统判断安全或不安全 |

补充：`memories.retrieve` 的场景/主题/文本主要影响相关度，无命中可退回近期条目。查询面板“过滤条件”与模型“查询结果”的用词不应暗示所有条件均为严格过滤；深度审查时确认是否需要显示匹配来源/回退标志。

## 9. 测试与发布证据

代码门禁：`tools/check_architecture.py`、ruff、pytest；前端 Vitest、类型/格式、Vite build、Playwright；协议 fixtures 对照、迁移/打包安全测试。

维护导航：

- 世界/持久化：`test_database_persistence.py`、`test_world_store.py`、`test_turn_journal.py`、`test_postgresql_integration.py`。
- 新平台：`test_structured_commands.py`、`test_structured_play_protocol.py`、`test_structured_ws.py`、`test_structured_room.py`、`test_structured_agent.py`。
- 过渡/记忆/恢复：`test_structured_transition_turn.py`、`test_structured_interaction_context.py`、`test_structured_character_memory.py`、`test_structured_pause_and_thread_lifecycle.py`、`test_structured_branch.py`。
- `test_structured_memory.py` 属于旧 shadow 记忆；与新 character memory 测试不要混数。
- 前端核心对偶：`structured-pending-sync`、`structured-branch-online`、`structured-load-branch`、`structured-interaction-duals` 等 E2E。
- 真实模型：`tools/transition_real_model_check.py`、live E2E、`tools/playthrough.py`；先核查脚本实际 profile 和授权。
- 安全/交付：auth、local trust、egress、crypto、log redaction、release archive、restore drill、packaged upgrade 测试。

最新联合报告声称后端 1385 passed/7 skipped、前端 766、E2E 34 passed/3 skipped；本轮未复跑，这些数字作为历史证据引用而非本轮认证。已静态核对 quality 是 push/PR 触发，生产 deploy 是手动触发、master 限定，且部署 workflow 自身也重新运行检查。

发布顺序不变：明确版本 → 本地/CI 验收 → 独立真实模型与预发布证据 → 用户确认发布 → master quality → 手动正式发布。Pi 为默认 staging；不可用时须另定明确获准的外网预发布路径，不在正式环境玩测试世界。

部署代码位于 `deploy/`：Nginx、systemd、备份、监控、恢复演练、固定提权激活入口；Windows 为 PyInstaller runtime hook + Electron NSIS/portable。数据库升级要连同 BYOK 主密钥备份与恢复策略检查，不能把“切回旧代码”当作任意迁移都可回滚。

## 10. 架构协作流程与完成标准

### 10.1 新需求先回答八个问题

1. 玩家实际要完成什么，何时必须停下来交还决定权？
2. 覆盖哪些 profile/keeper_mode/客户端/本地云端组合？
3. 权威状态、历史证据和展示状态分别由谁拥有？
4. 入口/协议/权限/领域命令/存储/投影各改哪里？
5. 失败、重试、取消、并发、重连、读档、分支分别怎么办？
6. NPC、主持、各玩家能知道什么，发送模型的资料边界是什么？
7. 旧世界/旧模组/旧打包库是否兼容，如何恢复？
8. 验收在哪个版本、哪条路径执行，哪些结论依赖真实模型？

### 10.2 给实现者的最小任务包

每项任务明确：唯一负责人、允许修改文件、依赖提交、协议正反例、非目标、对偶验收、数据/额度/发布授权、交接对象和完成条件。共享文件只由一人负责提交。schema、提示词目录、后端 handler 与前端 builder 必须同口径。

后端交付具体 SHA 与 fixture 后，前端应能直接开始验收，不再等用户口头转发“可以继续”。前端回执到达后集成人核对最终树并推送；若认证过程中再改代码，必须声明新认证目标，而不是双方各自报一个“最终版本”。

### 10.3 Codex 介入编码的条件

用户明确指派框架/公共边界实现，或将具体阻塞交给 Codex。开始前核对工作区归属、影响范围和回滚方案；不因做评审而顺手修改两边在途代码。

架构结论必须标明“已读源码 / 已复现 / 交付报告 / 待验证”。维护模块索引和决策记录，不能仅依赖对话记忆，更不能声称跨会话永远记得全部代码。

## 11. 当前行动队列

1. zcode：CI 根因取证与修复（若已获用户指令），交具体提交与 CI 运行结果。
2. Kimi：按另行确认的模型授权执行真实验收；先证明新主持拥有完成任务所需知识和工具，再定位剩余模型行为。
3. Codex：维护本文与新旧能力矩阵；收到结果后核对实际链路、覆盖范围和架构风险。首要深挖点为新主持知识面、规则闭环与恢复栅栏。
4. 集成人：将认证结果绑定最终联合树，不重新打开已关闭的两项待办/云端分支缺陷，除非出现新复现。
5. 用户：在 CI、玩法与预发布证据清楚后决定首发范围和正式发布版本。当前“接手架构师”不等于授权发布。

相关规格：`STRUCTURED_PLAY_PLATFORM_PLAN_20260913.md`、`STRUCTURED_PLAY_PROTOCOL_V1.md`、`STRUCTURED_PLAY_BACKEND_ACCEPTANCE_20260914.md`、`STRUCTURED_CONTEXT_MEMORY_HANDOFF_20260914.md`、`STRUCTURED_JOINT_ACCEPTANCE_FINAL_20260915.md`。本文以实读源码纠正范围认知，但不擅自替代既有权限约定或扩展发布授权。
