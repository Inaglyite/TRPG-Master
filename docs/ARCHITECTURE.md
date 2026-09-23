# 架构：跑团平台与 Agent harness

现行入口，整理于 2026-09-16；汇总基线 `46d5760`。本文件描述边界，不作为在途改动的验收证明。[状态与限制](STATUS.md)单独维护。

## 1. 产品与设计原则

平台同时服务人类守秘人和 Agent 守秘人。玩家通过按钮提交明确对象的操作意图，也可以自由说话；主持判断情境、请求检定、调用命令并叙事。服务端负责权限、确定性计算和持久化，前端只展示公开投影。

- 结构化按钮不能先拼成句子，再交关键词解析器决定效果。
- 意愿不等于执行，出示不等于赠送，抵达不等于调查或获得线索。
- 过渡是正常叙事与自由回应：需要玩家决定时真正停下，而不是播放固定提醒后继续替玩家行动。
- 人类和 Agent 复用领域服务；模型输出不是权威事实。
- 同一效果只有一个执行所有者，不让新旧引擎同时结算。

## 2. 两条执行路径

```text
Browser / Electron → React + Zustand → HTTP / WebSocket
                         ↓
          FastAPI + 身份/世界/房间权限
                         ↓ execution_profile
          ┌──────────────┴────────────────┐
       legacy                       structured_v1
 GameApplication                    StructuredGateway
 GameEngine / LangGraph              StructuredPlayService
 裁决 + 旧工具管线                      ↑ 人类 / assisted / Agent runner
 turn_cache                         逐命令短事务
 TurnJournal 整回合提交               状态 + 命令账本 + outbox 同事务
          └──────────────┬────────────────┘
                    SQLite / PostgreSQL
                         ↓
                  有权限的事件与快照 → UI
```

`execution_profile=legacy|structured_v1` 决定执行路径；新模式的 `keeper_mode=human|assisted|agent` 决定主持方式。本地/云端、单人/多人是另两个维度，不等于运行模式。

legacy 保留原有文本裁决与确定性后备。structured 不因协议或模型失败静默返回旧路径。旧模式通过的战斗、结局或主线测试，不自动证明新模式覆盖同样玩法。

## 3. 模块责任

| 区域 | 所有权 | 主要入口 |
|---|---|---|
| HTTP/WS 装配 | 请求入口与生命周期，不承载玩法规则 | `server.py`、`src/web/` |
| 旧应用编排 | 整回合、模型会话、开局、继续、存档 | `src/app/` |
| 新平台 | 意图、命令、主持控制、暂停、交互、角色记忆 | `src/structured/` |
| 确定性玩法 | 检定、发现、战斗、SAN、时间、结局 | `src/gameplay/` |
| AI 基础设施 | 供应商、路由、容量、旧工具/上下文/skill | `src/ai/` |
| 持久化 | 世界、快照、回合、笔记、BYOK 配置 | `src/storage/` |
| 房间控制面 | 成员、认领、共享引擎、恢复和时间线 | `src/multiplayer/` |
| 身份 | 密码、Session、来源检查、世界权限 | `src/auth/` |
| 作者工具链 | 格式、编译、包检查、版本化安装 | `src/modules/` |
| 客户端 | React 展示、请求、事件校验与恢复 | `frontend/src/` |
| 桌面/运维 | 进程、IPC、打包、部署与恢复 | `frontend/electron/`、`packaging/`、`deploy/` |

逐文件导航见[模块地图](reference/MODULE_MAP.md)。旧工作流细节见[legacy 实现参考](reference/LEGACY_ARCHITECTURE.md)，不要把其中“单引擎”描述套到新平台上。

## 4. 权威状态与事务

| 数据 | 存储/所有者 | 非权威替代物 |
|---|---|---|
| 当前世界事实 | `world_states`；按模式由 store 或命令服务写入 | 叙事、摘要、人物记忆 |
| 世界模式/成员/控制关系 | `worlds/world_members/world_investigators` | 前端自报身份 |
| 旧回合与恢复 | `turns/turn_events/snapshots/save_slots` | 新命令游标 |
| 新请求/检定 | `player_requests/check_requests` | 行动已经成功 |
| 新命令/事件 | `game_commands/event_outbox` | 模型建议或发送成功 |
| 主持控制权 | `keeper_control` | 房主自动拥有主持权 |
| 当前交互/新记忆 | `interaction_threads/character_memories` | 执行授权/世界真相 |

legacy 的 mutation 可先进入 `turn_cache`，最终由日志服务与状态一起提交；流式展示不等于持久提交。structured 每条命令短事务写状态、命令结果与 outbox，提交后投递；模型中途失败不回滚已提交命令。记忆派生在提交后独立执行，失败可补建，不反写事实。

请求、命令、检定、交互线程使用不同 ID。重复同一操作复用同 ID/载荷；同 ID 不同载荷拒绝。`revision`、世界事件 `sequence`、`event_id` 不可互换；聊天事件可能不推进 revision。

## 5. 暂停、重连、读档、分支

- 普通重连：恢复当前已提交状态和待办，不取消玩家尚未回答的决定。
- 正常叙事等待：保存尚未执行行动、已告知事项与交互目标，runner 结束，玩家自由回应。追问完成不等于原行动完成。
- 主动读档：structured 使用专用 reconcile，未决请求置失败、pending 检定作废、开放线程取消，并清理/修正未来记忆与事件；不是完整恢复当时交互快照。
- 分支：legacy 从完成回合分叉；structured 从当前已提交状态分叉，可用 `expected_revision` 钉住分叉点，不伪造 turn ID。新世界不继承活动控制权和原 outbox。
- 模型不可用/预算耗尽：明确暂停及原因，保留已提交结果，允许合法接管；禁止永久 loading 或自动改用平台 Key。

细节与信封见[协议](PROTOCOL.md)；相关数据清理只由对应生命周期服务执行。

## 6. 模型与主持模式

human 不依赖 Key；assisted 产出主持私有草稿，批准不等于自动执行建议命令；agent 使用受限命令循环，按预算与控制权停止。

新 runner 当前通过 BYOK caller 请求 JSON 决策，再校验、执行命令，不直接复用旧 ToolRuntime。触发主要来自玩家行动与检定回应；普通骰和主持命令不自动推进剧情。并发到达请求、开场以及恢复后的调度覆盖应以测试证明，不能仅根据提示词推断。

BYOK 按本地或账号/世界作用域解析；云端由相应世界所有者的有效绑定供本场使用，不允许缺配置时回退共享平台额度。Key 不进入角色记忆、prompt、前端回显或日志。

## 7. 上下文、规则知识与记忆

### 7.1 游戏 Skill 与世界 pin

旧引擎的规则知识由 catalog、世界冻结的 skill pin/manifest 和模组正文装配。已 pin 的旧世界不能随磁盘更新静默换规则。项目开发者使用的 `.agents/skills` 与游戏中的 `skills/` 不同。

### 7.2 四层上下文

| 层 | 内容 | 注入原则 |
|---|---|---|
| 权威状态 | 当前地点、合法对象、已提交结果 | 当前任务所需部分可靠提供 |
| 当前交互 | 已讨论目标、未执行行动、已提醒条件 | 跨请求保留，不靠长期检索碰运气 |
| 近期对话 | 玩家与主持刚刚的交流 | 连贯、有界，保留指代 |
| 角色记忆 | 亲历、被告知、传闻、推测 | 按需查询，附类型与来源 |

新路径的 `_build_context` 组装快照、交互、未决请求、近期消息、角色记忆和运行结果；system 来自 `agent_prompts.py`。这不是旧 ModelSession 的完整历史，也不能假定自动继承旧模组/skill/Lorebook 注入。

### 7.3 按需知识与查询

旧 Lorebook 和 skill activation 是有门槛、有预算的知识选择。新角色记忆来自 `src/structured/memories.py`，支持提交事件派生、显式记录、替代更正与预算检索。当前检索有近期候选上限和相关度回退，不应宣传为精确语义搜索。

Agent 要知道上下文是局部注入：未提供不等于未发生，缺依据可查询，不能虚构；但工具是否能提供任务所需模组知识仍须查实际请求。主持知道秘密不代表其扮演的 NPC 知情。

### 7.4 摘要与来源

旧摘要是非权威连续性缓存；新记忆也不拥有世界事实。检索结果、规则 pin、当前交互与世界状态必须保持来源区别。提示词约束不能证明叙事绝无越界，真实模型表现单独验收。

### 7.5 旧 shadow memory 与新角色记忆

`src/ai/context/structured_memory.py` 和 `memory_fact_candidates/memory_facts` 是旧 H3 shadow 机制；`src/structured/memories.py` 和 `character_memories` 是新运行路径的角色记忆。二者不是同一接口，测试也分别为 `test_structured_memory.py` 与 `test_structured_character_memory.py`。

## 8. 前端、权限与进程边界

React → typed transport → HTTP/WS；Zod/协议校验 → Zustand → 组件。`structured-transport.ts` 统一两类 WS 的发帧、重试和事件处理，`structured-effects.ts` 负责显示投影。新消息使用显式 speaker，骰子动画不决定骰值，场景栏不从叙事猜位置。

角色权限由服务端解析。owner、player、viewer 与 can_keeper/调查员控制权分开；私人内容在实时帧、快照、重放和模型上下文中分别检查。兼任 keeper 的人本来能看主持资料，不能把其连接当普通玩家隔离验收。

房间实例、Agent 活动任务和部分锁是进程内状态。当前单 worker 假设下的测试不能证明多进程/多副本安全；扩容前先设计世界归属和事件调度，不直接增加 worker 或套 K8s。

## 9. 扩展方式

先定义用户行为、运行模式、效果所有者、权限、事务及恢复，再改 schema/handler/提示词目录/前端 builder，并加正常与拒绝对偶。新增迁移同时核对打包接管表集；新增事件同时核对外层白名单、实时投影与快照恢复。

跨模块任务须给出唯一负责人、文件边界、冻结 SHA、fixtures、验收与交接对象；流程见[开发与协作](DEVELOPMENT.md)。运维与发布授权见[运维](OPERATIONS.md)。架构检查通过仅证明已有静态门禁通过，不代替完整审计。
