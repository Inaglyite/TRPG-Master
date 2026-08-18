# DeepSeek Harness 借鉴与采纳决策

状态：H0 安全边界已实施；H1+ 仍为架构提案

评审日期：2026-08-18

上游基线：`deepseek-ai/deepseek-harness` `dsh-v0.1.0-rc.7`，提交
[`99f6f02fecdb7dff40c3fbc9470f5907c29f74ca`](https://github.com/deepseek-ai/deepseek-harness/tree/99f6f02fecdb7dff40c3fbc9470f5907c29f74ca)

本文是长期架构决策，不是“把另一个框架接进来”的实施清单。评审对象包括当前工作区中的
TRPG Game 代码、数据库回合与存档模型、上下文压缩、Lorebook、Skill、工具调用和 DeepSeek
适配边界。上游仍处于 Developer Preview，明确不承诺兼容性，因此所有结论都固定到上述提交。

## 1. 结论

DeepSeek Harness 值得参考，但**不应整体引入，也不应替换现有 Python、LangGraph、FastAPI、
PostgreSQL 和确定性规则层**。正确做法是借鉴它的运行时约束，在现有架构里逐步实现一组小而稳定的
Python 接口。

优先拿来四件事：

1. **模型可见内容可重建**：把模型实际看到的上下文变成可审计的事件与投影，而不是只有一份会被
   就地改写的 `messages` 列表。
2. **统一工具执行流水线**：结构化 Function Calling 和 DSML 都必须经过同一套请求级 allowlist、
   schema、权限、幂等、超时、可见性和审计检查。
3. **非破坏性上下文压缩**：保留原始事件，只替换模型可见投影；先裁剪过大的工具结果，压缩边界不
   能拆开 tool call/result，摘要失败也不能删除历史。
4. **Skill Catalog + 确定性能力路由**：安全关键规则由规则系统、场景状态和行动分类强制加载；模型
   只在非关键或歧义场景下通过受控工具读取完整 Skill。关键词只能是弱提示，不能再做唯一触发器。

第一轮不做长期记忆 MCP、多 Agent、Cordis 插件树或通用代码执行。它们不能解决当前最紧迫的工具
授权和上下文可追踪问题，反而会扩大攻击面和重构范围。

## 2. 上游到底提供了什么

DeepSeek Harness 是一个 TypeScript Agent Harness。其核心思想是“所有能力都是插件”：模型适配器、
会话日志、提示词、工具、Agent loop、持久化和客户端由 Cordis 插件树组装。这个思路适合通用编码
Agent，但不是 TRPG Game 必须采用的运行时。

本项目最相关的上游机制如下：

| 上游机制 | 实际含义 | 对本项目的价值 |
|---|---|---|
| Append-only Session log | `turn/step/user/assistant/tool` 等事件只追加，模型历史由日志投影 | 高 |
| Session surface | `user/message`、`assistant/message`、`tool/result` 组成模型可见表层，压缩用 replace 节点遮蔽旧范围 | 高 |
| Tool execution pipeline | pre-execute、权限/策略、execute、post-execute、结果冻结和持久化 | 很高 |
| Compaction + result pruning | 按容量触发，先裁剪工具大结果，再在安全边界做摘要替换 | 很高 |
| Skill registry | 先发布名称/描述目录，完整正文按需加载，支持调用策略和来源 | 高，但需改造成游戏能力路由 |
| Token meter | 同时统计 system、tools、history、provider usage 和 cache hit | 高 |
| Repeat-tool reminder | 对相同工具与参数的连续调用做有界提醒，不直接修改结果 | 中 |
| Capability seam | Service Definition / Provider / Consumer 解耦 | 中，借鉴接口即可 |
| DeepSeek adapter | SSE、工具增量、reasoning passback、usage、稳定错误码、超时与外层重试策略 | 中高 |
| Cordis / Everything is a plugin | 动态插件树、patch、卸载与热更新 | 低，不采用 |
| Subagent / shell / filesystem / MCP | 通用编码 Agent 能力 | 在线游戏主链不采用 |

上游最重要的不变量是“模型可见即已记录”：送进一次模型请求的内容应能从持久事件重建。详见
[架构](https://github.com/deepseek-ai/deepseek-harness/blob/99f6f02fecdb7dff40c3fbc9470f5907c29f74ca/docs/architecture.zh.md)、
[Session](https://github.com/deepseek-ai/deepseek-harness/blob/99f6f02fecdb7dff40c3fbc9470f5907c29f74ca/docs/subsystems/session.zh.md)
和[工具执行流水线](https://github.com/deepseek-ai/deepseek-harness/blob/99f6f02fecdb7dff40c3fbc9470f5907c29f74ca/docs/tool-execution-pipeline.zh.md)。

### 2.1 它没有一套可以直接复制的“DeepSeek 长期记忆”

Harness 自身的“记忆”主要是会话事件日志、派生投影和上下文压缩。仓库中的 Memorix、Engram 和
MCP Reference Memory 只是**默认关闭的第三方 MCP 配置示例**；Harness 不负责这些服务的数据库、
embedding、冲突消解或遗忘策略。参考
[第三方记忆 MCP 示例](https://github.com/deepseek-ai/deepseek-harness/blob/99f6f02fecdb7dff40c3fbc9470f5907c29f74ca/examples/mcp-memory/README.zh.md)。

因此，本项目不应因为上游出现了 memory 示例，就把在线世界接入一个通用记忆服务器。TRPG 的
“谁知道什么、在哪条时间线知道、是否能向某个玩家透露”比普通聊天记忆严格得多。

### 2.2 许可证与成熟度

上游使用 [MIT License](https://github.com/deepseek-ai/deepseek-harness/blob/99f6f02fecdb7dff40c3fbc9470f5907c29f74ca/LICENSE)，
可以学习、修改和复用代码；如果复制实质代码，必须保留其版权与许可声明。本提案优先在 Python 中
重实现小型模式，而不是复制 TypeScript/Cordis 实现。上游 README 明确标注 Developer Preview 并
警告会有破坏性变更，所以不能依赖其内部事件格式作为本项目的持久协议。仓库中的 vendored 与第三
方材料还受 [`THIRD_PARTY_NOTICES.md`](https://github.com/deepseek-ai/deepseek-harness/blob/99f6f02fecdb7dff40c3fbc9470f5907c29f74ca/THIRD_PARTY_NOTICES.md)
约束，不能因为根许可证是 MIT 就整目录复制。

## 3. 当前项目基线

当前代码没有引入 DeepSeek Harness；运行栈仍是 LangGraph + OpenAI-compatible SDK。工作区最近的
主要改动集中在云端单人时间线和 UI，并没有建立另一套 Harness 运行时。

### 3.1 已有且应保留的基础

- [`src/game_application.py`](../src/game_application.py) 已有 `GameEnginePort` 和应用用例边界，适合
  将来放入 Runner/Model adapter，而不是让新逻辑继续堆进 `GameEngine`。
- [`src/database_turn_journal.py`](../src/database_turn_journal.py) 已能把 `WorldState`、`Turn`、
  `Snapshot`、自动存档、回放事件和模型调用在同一数据库事务中提交。
- [`src/database_store.py`](../src/database_store.py) 已通过 `turn_cache` 暂存本回合世界变更，再交给
  TurnJournal 原子提交。这是现有数据库写工具应继续遵守的 Unit of Work；兼容文件导出、外部服务和
  绕过 `DatabaseWorldStore` 的副作用不自动获得这项原子性。
- [`src/turn_mutations.py`](../src/turn_mutations.py) 已区分权威世界变更，可作为工具审计的基础。
- [`src/room_runtime.py`](../src/room_runtime.py) 已提供世界级串行行动、重连和事件补发边界；这些不应
  被通用 Agent 插件系统取代。
- [`src/lorebook.py`](../src/lorebook.py) 已有场景/NPC/flag/线索门禁、冷却、优先级、互斥组、token
  预算和诊断，是比通用向量记忆更适合模组知识的基础。
- [`src/model_session.py`](../src/model_session.py) 已有消息与流取消生命周期；
  [`src/model_streamer.py`](../src/model_streamer.py) 已记录 system/history/tool-schema 的体积、usage 和
  首 token 等性能数据。
- 世界状态、角色资源、线索、SAN、战斗和结局继续由确定性领域代码与数据库决定，模型只提出工具
  调用和生成叙述。

### 3.2 现有缺口

1. `ModelSession.messages` 仍是一份可变列表。完成回合会保存快照，但没有一条跨回合、可投影的
   “模型实际可见事件流”；Skill、Lore、控制指令和摘要也缺少统一来源标识。
2. [`src/history_compactor.py`](../src/history_compactor.py) 按固定玩家回合数触发，并直接替换消息；两次
   摘要失败会丢弃最早历史。摘要没有 source turn，也没有验证权威事实或是否真正缩小请求。
3. 可选 Skill 仍保留关键词作为兼容提示，且版本、依赖图、来源追踪尚未成为 manifest；不过模型已不能
   通过通用文件工具自行加载规则，受信引擎只会读取固定资源 allowlist。
4. schema、幂等、超时、输出可见性和 durable 工具审计仍分散在不同模块；H0 已补上严格 JSON、请求级
   allowlist 与模型/内部 caller 边界，H1 再收口为完整 pipeline。
5. `npc_conversations` 保存的是模型曾说过的话，不等于经规则确认的事实；它目前又会被放进后续权威
   上下文。对话记录、事实记忆和叙事摘要需要分层。
6. Prompt section、权威上下文、Lore、Skill 和控制消息由多个模块直接拼接，缺少统一的 priority、
   token budget、audience、provenance 和 digest。

### 3.3 必须先修的 P0 工具授权问题

H0 实施前，“没有把工具 schema 发给模型”并不等于“模型无权执行该工具”：

- [`src/tools.py`](../src/tools.py) 用 `MODEL_TOOLS` 隐藏 `read_file` 等 engine-only 工具；
- [`src/tool_protocol.py`](../src/tool_protocol.py) 会把文本 DSML 中任意合法工具名转成普通 tool call；
- [`src/model_streamer.py`](../src/model_streamer.py) 把这些调用并入结构化调用结果；
- [`src/agent_graph.py`](../src/agent_graph.py) 最终按全局 registry 执行，没有重新检查该名称是否属于
  **本次请求实际下发的 allowlist**。

`read_file` 又允许读取 `project_root` 与 `runtime_root` 下的普通文件。这意味着恶意模组文本、Skill、
prompt injection 或异常模型输出可能绕过 schema 隐藏边界。引入任何新的 Skill、MCP 或插件机制前，
必须先增加 request-scoped `ToolExecutionPolicy`；DSML 只能是一种解析格式，不能成为第二条授权通道。

request allowlist 还不够：当前模型可见的通用 `state_get`/`state_set` 没有路径级语义授权，前者可读取
`npcs.*.secret`、`private_memory` 等私密字段，后者可绕过领域工具修改任意权威状态。H0 必须把它们从
模型目录移除，改成公开投影和 typed domain tools。若为兼容暂时保留，必须按 caller principal 与精确
root/path policy 拒绝私密、身份、存档和控制字段；“工具名允许”不能等同于“任意参数语义允许”。

`get_npc_secret` 也不能只做一次 LLM 摘要或把 TIER 当作自动切分器。现有模组的 `npc.secret` 是整块
作者文本，TIER 是玩家披露边界，不是天然的 Keeper 可见字段。H0 选择安全优先：先从模型 catalog
移除该工具，只注入现有 public/revealed/disposition 投影。若后续确需隐藏动机辅助叙事，模组格式必须
先增加作者定义的 `keeper_behavior`、`private_motivation`、`reveal_facets`，再由确定性代码按 NPC、
场景和目的选择最小的 model-private section；该 section 不进入普通 message history、公共事件、摘要
或遥测。现有 module spine/keeper notes 也可能包含秘密，需按同一 audience 规则盘点，不能宣称仅修
一个工具就消除了模型对秘密的接触。

H0 已删除 active core/keeper Skill 中要求模型调用 `read_file` 的旧指令，并由受信引擎按固定 resource
ID 注入内容。native 与 DSML 的模型调用都携带同一请求快照，执行点会拒绝 `read_file`、私密工具和所有
未下发能力；普通日志只记录元数据。后续 H1 仍要补齐 durable audit、幂等和 timeout。

## 4. 目标架构

```text
玩家行动 / 房间动作
        |
        v
TurnCoordinator（已有世界锁、回合租约、revision、幂等）
        |
        +--> ContextBuilder
        |      |- system / module spine
        |      |- authoritative WorldState projection
        |      |- Lorebook selection
        |      |- Skill catalog + deterministic activations
        |      `- recent conversation surface / checkpoint
        |
        v
ModelGateway（provider adapter、timeout、retry、usage、cache metrics）
        |
        +--> visible narrative stream
        |
        `--> ToolCall
               |
               v
          ToolPipeline
          parse -> request allowlist -> schema -> permission/visibility
                -> idempotency/cancel/timeout -> domain handler
                -> output validation/redaction -> durable result
               |
               v
          WorldState + TurnJournal 原子提交

私有 ConversationEventLog  --投影--> 当前模型 surface --压缩替换--> bounded surface
公共 TurnEvent/RoomEvent     --过滤--> 浏览器/Electron/不同玩家
```

模型上下文日志和玩家回放日志必须分开：前者可能包含守秘人私有状态，默认仅服务端可读；后者继续使用
现有 audience 过滤。任何私密上下文都不能因为“可回放”而出现在 HTTP/WS、普通日志或遥测中。

## 5. 借鉴方案

### 5.1 可重建的模型上下文事件与投影

先新增一个内部、版本化的 `ConversationEvent`/`ModelContextEvent` 契约，再决定是否独立建表。至少需要：

```text
world_id / root_world_id / turn_id / step / sequence
event_type
source_kind + source_id + source_version/content_digest
audience + sensitivity
payload 或受控引用
surface_op (append / replace)
source_sequences
created_at
```

首批事件只需覆盖：entered player action、context injection、request envelope、assistant message、tool call、
tool result 和 compaction checkpoint。原始 `TurnRecord`、`WorldState`、`Snapshot` 继续是现有权威数据；
不要再创建一套世界事实总账。

“可重建”不等于把所有秘密复制到普通日志。静态 core/module section 可记录不可变版本与 digest，并保留
对应发布制品；动态私密 section 必须保存为仅服务端可读的受控 payload，或引用一个带 revision、可长期
解析的权威快照。仅保存 digest 却无法取得原文，不算可重建；把原文写进公开 trace 也不合格。

落地方式采用双写与影子投影：

1. 仍用当前 `messages` 发请求，同时记录新事件；
2. 用纯函数从事件投影出候选 messages；
3. 在测试与诊断中比较规范化后的请求 digest；
4. 只有真实回合持续一致后，才让投影成为读取来源；
5. 旧存档按现有 `messages` 作为一次 seed 导入，不强行伪造历史来源。

在事件投影成为读取来源前，必须先冻结时间线与存档恢复语义：每个分支记录
`parent_world_id + source_turn_id + source_sequence`；当前分支的可见事件等于祖先截止 source sequence
的前缀，加上本分支后缀，绝不读取兄弟分支。普通继续游戏留在同一 lineage；从旧存档回滚后继续必须
建立新的 branch/session epoch，不能把新事件接回未来历史。checkpoint 也必须带 branch/epoch 与覆盖范围。
删除或归档分支时采用引用感知 GC：仍被后代引用的祖先事件不能删除。

不记录或不传播原始隐藏推理。若 DeepSeek thinking 模式在带工具调用的 assistant 消息上要求
`reasoning_content` passback，应由 provider adapter 在最小生命周期内保管，并作为高敏感、不可展示
内容处理；不得进入玩家回放、摘要、普通审计或遥测。

### 5.2 Tool Pipeline V2

把现有 schema 与 handler 合并成 `ToolDescriptor`，逐步包装当前工具，而不是重写骰子、SAN、战斗和
WorldStore：

```text
ToolDescriptor
- name / input_schema / output_schema
- allowed_roles / allowed_request_profiles
- mutability: read | write
- visibility: model-private | actor-private | room-public
- idempotency: call-id | semantic | none
- timeout_ms / concurrency: exclusive | snapshot-read
- handler / model_renderer / player_renderer
```

每次模型请求必须产生不可变的执行快照：`request_id`、`step`、`request_profile`、caller principal、
`allowed_tool_names` 与 `tool_catalog_digest`。`ModelStreamer` 将该快照与调用一同交给执行点；执行时不能
按当前 role 或全局 registry 重新计算权限。受信引擎内部调用也进入显式 principal 对应的策略，不能为
兼容旧 handler 留一条无审计旁路。结构化调用和 DSML 统一规范化为 `ToolCall`，之后经过同一管线：

1. 名称必须存在于本次请求冻结的 allowlist；
2. 严格解析 JSON，解析失败直接形成受控错误结果；
3. 按 input schema 校验：顶层必须是 object、拒绝 unknown/additional properties，不能用 `{}` 猜测执行；
4. 权限策略单调收紧：后续中间件只能继续允许或拒绝，不能重新放行已拒绝调用；
5. 绑定 world/turn/step/call_id、actor、revision 和 deadline；
6. 写工具保持世界级串行；只有冻结 revision 上的纯读取以后才可并行；
7. handler 输出先按 canonical output schema 验证，再分别渲染模型结果与玩家结果；
8. call/result 一一配对并写入 TurnJournal，失败、拒绝和超时也必须有不回显敏感参数的 result；DSML
   call_id 由 request/step/sequence 组成，不能复用局部的 `dsml_0`；
9. late callback 不得在取消或回合结束后写世界状态。

“包装现有 handler”只适用于所有写入都经过 `DatabaseWorldStore.turn_cache` 的工具。绕过该缓存的 DB
写入必须先改成 `ToolUnitOfWork`/`MutationPlan`，和 TurnJournal 在一个事务内提交；兼容文件、对象存储
或外部 API 等无法同事务的副作用只能在提交后通过 outbox 执行，或提供明确补偿。否则 pipeline 只能
统一授权和审计，不能承诺原子性。

上游的 repeat-tool reminder 可以小型重实现，但它只是提示。骰子、检定、发线索等不可重复操作仍应由
持久幂等键保证；不能依赖模型看到提醒后自觉停止。

### 5.3 非破坏性压缩与工具结果裁剪

参考上游
[Compaction](https://github.com/deepseek-ai/deepseek-harness/blob/99f6f02fecdb7dff40c3fbc9470f5907c29f74ca/docs/subsystems/compaction.zh.md)，
把当前压缩器改造成独立 `ContextCompactor`：

1. 计算下一次真实 request envelope：system、tools、history、Lore、Skill 和 provider usage/cache；
2. 只有 provider/model 容量已知且完整 envelope 能装入时，才以容量约 75%–80% 作为可配置触发线；
   容量未知时使用保守的部署配置与 provider overflow 信号，不能宣称固定比例；
3. 先确定性裁剪过大的旧工具结果，保留有界 head + marker + tail，原结果仍留在事件日志；
4. 选择最旧、完整、call/result 配对平衡的 surface 范围；
5. 摘要必须记录 source sequences/turn ids，并验证输出比被替换范围更小；
6. 以 replace checkpoint 遮蔽旧 surface，不删除原始事件；
7. 摘要失败、超时、取消或不收敛时保持原 surface，不再使用“丢弃最早消息”兜底；
8. 只有发生了持久、可验证的 surface 缩减后，context-overflow 请求才可重试。

若 system core、工具 schema、最新不可拆消息或其他不可裁剪 section 本身已经超过容量，返回明确的
`irreducible_context` 诊断（包含各 section token、最大项和配置来源），停止自动重试；不得循环摘要或
偷偷删除安全规则。压缩性能应使用录制请求和构造的极限 context fixture 测量，不把公网模型延迟算作
本地 pipeline p95。

摘要结构必须是 TRPG 专用的，至少包含：当前场景与世界时间、玩家已知事实、已发现线索、NPC 已公开
互动、未完成目标、角色资源变化、精确骰值/规则结果、待处理选择和来源 turn_id。精确骰值、资源和
规则结论不能信任摘要模型复述，应在摘要后从 `WorldState`/权威 TurnEvent 确定性拼回。摘要只帮助连续
叙事，不能修改 `WorldState`，也不能把私密事实提升为玩家已知事实。

### 5.4 Skill Catalog，不再赌关键词

参考上游
[Skill 子系统](https://github.com/deepseek-ai/deepseek-harness/blob/99f6f02fecdb7dff40c3fbc9470f5907c29f74ca/docs/subsystems/skills.zh.md)，
为每个 Skill 增加 manifest：

```text
id / version / content_digest
rulesets / module_capabilities / scene_capabilities
activation predicates
required_tools / allowed_tools / dependencies
model_invocable / user_invocable
trust: core | bundled-module | local-author
max_context_tokens / resource allowlist
```

加载分三类：

- **常驻核心**：信息边界、禁止代替玩家说话、权威状态优先、工具协议等安全规则始终存在。
- **确定性激活**：`ruleset`、`combat.active`、SAN 状态、`ActionResolution.intent`、场景 capability、
  模组声明和即将暴露的工具决定本回合必须加载哪些 Skill。这是安全关键路径。
- **模型按需**：模型只看到有界的 `id + description` catalog，可用 `load_skill(id)` 读取非关键或歧义
  Skill。加载器按 allowlist 取正文，不给模型绝对路径或通用 `read_file`。

关键词命中可以保留为召回信号和漏加载诊断，但不能成为唯一条件。Skill 内容按
`world + version + digest` 固定；正在进行的世界不因磁盘文件变化热更新。每次激活/加载都作为带来源的
模型上下文事件记录，压缩后若目录消失，应由 ContextBuilder 重新建立当前目录。

第三方 Skill 首版只允许声明式文本和受控资源引用，不允许运行脚本、安装依赖、调用 shell、任意网络
或任意文件系统。模组制作 Agent 的 Skill 与在线守秘人 Skill 也要分开信任域。

### 5.5 TRPG 记忆分层

| 层 | 当前或目标数据 | 权威性 | 规则 |
|---|---|---|---|
| 世界事实 | `WorldState`、角色、线索、物品、战斗、flags | 权威 | 只能由确定性领域事务修改 |
| 模组知识 | Lorebook、NPC 私密条目、场景素材 | 作者态权威 | 注入前必须过 scene/flag/clue/TIER 门禁 |
| 事件记忆 | Turn、tool call/result、公开/私密行动事件 | 权威历史 | 只追加，可按时间线祖先关系读取 |
| 对话记忆 | NPC 曾经说过什么、玩家互动 | 非事实记录 | 不可自动升级为线索或世界事实 |
| 叙事 checkpoint | 对旧 surface 的有来源摘要 | 派生缓存 | 可重建、可废弃、不可反写权威状态 |
| 临时上下文 | 本回合 Lore/Skill/提醒/检查结果 | 临时 | 带 audience、TTL、来源和预算 |

如果以后增加结构化玩家/NPC 长期记忆，每条至少带：

```text
world_id / root_world_id / source_turn_id
subject_id / fact_type / value
audience / owner_user_id / tier
revision / provenance / supersedes / created_at
```

时间线只能读取共同祖先与当前分支上的事实，不能读取兄弟分支的新事件。模型只能提出 memory candidate；
引擎验证来源、权限、冲突和幂等后才能提交。embedding 或全文检索只能召回候选，结果注入前仍要经过
world、branch、audience、TIER 和 clue gate，不能成为事实来源。

当前 `npc_conversations` 应逐步拆成“verbatim transcript”和“accepted structured fact”。模型说过一句话
不等于 NPC 确实知道或世界中确实发生过这件事。

### 5.6 私密上下文的保留、访问与删除

追加式不等于永久保存全部原文。首版采用以下生命周期：

- 不持久化隐藏推理、provider 原始 chunk 或无诊断价值的中间缓冲；
- 静态 core/module/Skill 优先保存发布版本与 digest，由不可变制品重建，避免每回合复制；
- 动态 model-private payload 随 world/branch 生命周期保存，并单独加密或至少使用数据库访问边界；
- `archive` 只是停止活跃使用，不代表删除。用户明确删除世界时，删除该世界独有的私密 payload；共同
  祖先仍被后代时间线引用时，等引用清零后再 GC；
- 数据库加密备份按部署保留策略到期清除（当前默认约 30 天），产品界面必须说明删除后的备份窗口；
- 普通用户 API、WebSocket、前端日志和导出不得读取 model-private payload；管理员紧急访问必须授权、
  记录审计事件并说明目的；
- 遥测只记录 event type、digest、token、大小、耗时和策略结论，不记录私密正文；
- 给每世界/分支设置事件与 payload 容量上限，checkpoint 生效后用引用感知 GC 回收不再可达的派生数据。

### 5.7 只引入小型 Python seam

不复制 Cordis。等对应功能开始实施时，优先定义少量 Python `Protocol`：

- `ModelProvider`：provider 请求、流、错误、usage、缓存与 thinking 规则；
- `ContextProvider` / `ContextProjector`：带 priority、budget、audience、provenance 的上下文段；
- `ToolRegistry` / `ToolMiddleware`：工具定义、请求级视图和执行管线；
- `SkillProvider` / `SkillResolver`：发现、固定版本和确定性激活；
- `MemoryProjector` / `MemoryRetriever`：从权威事件派生可见记忆；
- `RuleSystem`：未来 COC/D&D 规则能力；
- `TelemetrySink`：默认 metadata-only 的诊断输出。

这些 seam 应放在 `GameEnginePort` 后面逐步接入。不要在 `GameEngine`、`tools.py` 或 LangGraph state 里
继续添加任意 plugin hook，也不要为了“可插拔”允许生产环境动态安装代码。

### 5.8 DeepSeek Provider Adapter 的可借鉴项

上游 [DeepSeek adapter](https://github.com/deepseek-ai/deepseek-harness/blob/99f6f02fecdb7dff40c3fbc9470f5907c29f74ca/packages/llm/llm-deepseek/README.md)
对本项目有以下参考价值：

- 稳定区分 AUTH、QUOTA、RATE_LIMIT、CONTEXT_WINDOW、SERVER、TRANSPORT、TIMEOUT 和 ABORT；
- 一条稳定 cancel signal 覆盖连接、读取和调用生命周期；
- 正确合并增量 tool call，并在完成时统一交付 usage；
- 记录 prompt cache hit，而不把 cache 指标混入普通输入 token；
- provider/model/context window/max output/reasoning effort 形成一次请求的冻结 envelope；
- retry 在完整 step 边界发生，并记录原因、次数和退避，而不是在不明状态下重放世界写入；
- 只有 DeepSeek API 明确要求时，才在带工具调用的 assistant 历史里 pass back reasoning。

当前 OpenAI-compatible SDK 可以继续使用；不必为了这些行为改成上游的 raw `fetch` 实现。应在现有
`ModelStreamer` 外建立 provider adapter，并用录制的 SSE/SDK fixtures 验证兼容网关差异。

Provider 原始流还应先归一化为服务端内部的 `reasoning | public_text | tool_call | usage | finish`
事件，再映射成 `narrative_chunk`、NPC 发言、骰子、选项等公开房间事件。`reasoning`、tool 参数增量和
未知 chunk 永远不能作为“兜底文本”发给前端；未完成或被截断的工具调用应失败关闭。这个边界可以
直接降低历史上 DSML、内部推理和工具协议被显示成守秘人叙述的风险。

## 6. 明确不拿什么

- 不引入 Cordis、整个 `@deepseek-ai/dsh` 或 TypeScript Agent runtime；
- 不把实时守秘人拆成 NPC Agent、战斗 Agent、记忆 Agent 等多个并发 Agent；
- 不给在线模型开放 Bash、代码执行、通用 MCP、任意文件系统或动态插件安装；
- 不直接连接 Memorix、Engram 或通用 MCP Memory 到生产世界；
- 不让模型自动把自然语言写成长期事实，不让向量相似度覆盖世界状态；
- 不并行执行骰子、SAN、线索、物品、战斗和其他写工具；
- 不在活跃世界中热更新 Skill；
- 不把隐藏推理、NPC secret、私密工具参数或完整会话内容送入第三方遥测；
- 不采用上游尚不稳定的持久事件格式作为外部 API；
- 不用 Harness 重写已经成熟的账号、房间、回合租约、数据库事务、时间线和发布系统。

## 7. 分阶段路线

### H0：安全边界与基线测试（已实施，2026-08-18）

- 每次模型请求冻结 `request_id/step/profile/caller/allowed_tool_names/catalog_digest`，DSML 与 structured
  call 携带同一快照到执行点，禁止按全局 registry 重新推导权限；
- 给工具增加参数语义授权；将通用 `state_get/state_set` 移出模型 catalog，改为公开读取投影与 typed
  domain tools，内部兼容调用按 principal 和精确 path policy 隔离；
- 禁止模型通过通用 `read_file` 加载 Skill，同时清理 active core/keeper Skill 中要求模型调用它的旧
  指令；受信引擎只能按 manifest resource allowlist 注入固定资源；
- 将 `get_npc_secret` 先移出模型 catalog，禁止完整私密对象进入普通历史；另行设计作者定义、确定性
  选择且不进入公共历史的最小私密行为投影，不用 TIER 或另一个 LLM 猜测拆分；
- 参数 JSON 严格失败，补拒绝 additional properties 的 input schema 与 path-level policy 校验；
- 修正工具轮数边界并为每个 tool call 保证一个结果；
- 工具规划阶段不向玩家发送未提交 prose；只有确认无工具，或工具提交后的最终叙述才能进入公开流；
- 建立模型请求 golden fixture：冻结 system section、tool catalog、context section 和 messages digest；
- 为公开事件、日志和错误路径增加 secret-leak 测试。

验收：任何未下发工具都无法通过 native/DSML/重放调用；`state_get/state_set` 无法触达私密/控制路径；
模型 caller 不能调用 `read_file/get_npc_secret`，engine-internal 只能读取固定资源；参数错误不执行 handler；
现有需要规则 Skill 的玩法由确定性注入维持，不因移除文件工具而退化；公开流、玩家可见历史和普通
日志无 secret 泄漏。

实现落点：`src/tool_policy.py` 冻结并校验请求快照，`src/model_request.py` 生成可重放的 request envelope，
`src/agent_graph.py` 对 structured/DSML 走同一拒绝路径，`src/skill_resources.py` 只加载固定资源。相关
golden、拒绝、泄漏与轮次边界回归在 `tests/test_tool_policy.py`，完整 Python 测试基线为 590 passed、5 skipped。

### H1：Context Envelope + Tool Pipeline V2

- 定义 `ContextSection`、`RequestEnvelope`、`ToolDescriptor`、`ToolCall`、`ToolOutcome`；
- 先包装符合 `turn_cache` 约束的现有 handler，不重写领域逻辑；其余写工具先迁移到
  `ToolUnitOfWork`/`MutationPlan`，跨介质副作用使用 post-commit outbox 或补偿；
- 把权限、可见性、幂等、超时、取消、输出校验和 TurnJournal 审计统一到管线；
- 在 `ModelCall.details` 中记录 provider/model、section/tool digest、容量、usage 和 cache metadata；
- 新旧路径通过 feature flag 和 shadow comparison 共存。

验收：每次调用可关联 world/turn/step/call_id；重试、取消和重连不重复写世界；用录制和合成 fixture
测得管线 p95 自身开销低于 10ms，正常回合本地编排 p95 延迟增幅不超过 5%（不把公网模型抖动计入）。

### H2：追加式 Context Event + 非破坏性压缩

- 双写模型上下文事件；
- 实现纯投影器并比较现有 messages digest；
- 固化 fork/save/resume 的 lineage、session epoch、checkpoint 范围和引用感知 GC；
- 上线 tool-result pruning、平衡边界、summary replace 和 context-overflow 受控重试；
- 原始权威 Turn 按产品存档策略保留；私密 Context Event 遵循第 5.6 节的访问、删除和备份生命周期，
  旧存档仍可 seed；
- TRPG 专用摘要对 gold scenes 做事实与可见性验证。

验收：重放得到相同模型可见 surface；摘要失败/崩溃不丢历史；call/result 始终配对；压缩后请求稳定在
配置目标以内；容量已知且 envelope 可压缩时通常维持在 75%–80%，不可约上下文则返回诊断而非删规则；
摘要不能增加权威事实或跨 TIER 泄密，兄弟时间线不能读取彼此事件。

### H3：Skill Catalog + 结构化记忆

- 官方 Skill 先迁移 manifest/catalog；
- 关键规则由 action/ruleset/state/capability 确定性激活；
- 模型按需 loader 仅覆盖非关键 Skill；
- 固定 world 级 Skill version/hash；
- 将 NPC transcript 与 accepted fact 分开；
- branch-aware、audience-aware 的记忆召回先运行 shadow mode，只记录候选不注入模型。

验收：安全关键规则加载覆盖率 100%；每条注入都有来源和 audience；跨世界、跨用户、跨分支、跨 TIER
错误召回为零；gold scenarios 的候选 precision 至少 95%，且误召回永远不能改变权威状态。

### H4：Provider 与评测收口

- 把 DeepSeek 特有 stream/tool/reasoning/usage/error 行为封装进 adapter；
- 增加模型请求/结果离线 replay、固定模组 gold turns 和故障注入；
- 观测 Skill token、Lore 命中、摘要压缩比、cache hit、工具拒绝、重试、首 token 和完整回合耗时；
- 本地与 Pi staging 灰度，经过回放一致性和真实游戏验收后才进入生产发布流程。

## 8. 测试与不变量

至少固定以下不变量：

1. 同一事件日志和相同代码版本投影出相同模型消息与 tool catalog digest；
2. 模型可见的持久输入都有来源，临时敏感输入也有 request trace 和 audience；
3. 每个 tool call 恰有一个 result，未知/未授权/非法参数不会进入 handler；
4. 写工具在同一世界串行且幂等，取消后的 late result 不能提交；
5. 公共事件不含 private memory、NPC secret、他人私密行动和隐藏 reasoning；
6. 压缩边界不拆 call/result，失败不改变 surface，摘要不能写 WorldState；
7. Skill 安全规则不依赖模型自主加载，活跃世界的 Skill digest 不漂移；
8. 分支只能读取共同祖先和本分支记忆，兄弟时间线互不污染；
9. Lore、记忆和摘要冲突时，WorldState 与确定性规则永远优先；
10. provider 重试只重做尚未产生权威副作用的模型 step，不重放已提交工具写入。

测试分层：纯投影和 schema 单测、工具管线故障注入、SQLite/PostgreSQL 并发集成、录制 provider stream
fixture、离线 turn replay、浏览器/Electron E2E、Pi staging 真实纵切。正式环境不用于这些实验。

## 9. 预期收益与成本

| 目标 | 预期收益 | 主要成本/风险 |
|---|---|---|
| 工具管线 | 关闭绕权、重复执行和不可诊断失败 | 需要逐个描述现有工具 schema/visibility |
| 可重建上下文 | 能解释每回合模型为何看到这些信息 | 私密日志的存储与访问控制必须严格 |
| 非破坏性压缩 | 长局稳定，不因摘要失败丢历史 | 多一次摘要调用和投影复杂度 |
| Skill Catalog | 降低常驻 prompt，解决关键词漏触发 | 需要给规则/场景建立结构化 capability |
| 结构化记忆 | 支持多人、时间线、地图和 D&D 的长期连续性 | branch/audience/TIER 设计错误会泄密 |
| Provider adapter | 错误、重试、cache 和 thinking 行为可控 | 需维护不同兼容网关 fixtures |

最重要的取舍是：**借鉴 Harness 是为了减少隐式行为和不可解释状态，而不是为了让架构看起来更像
Agent 框架。** 若某项“插件化”不能改善权限、重放、上下文预算、规则扩展或故障恢复，就不进入实现。

## 10. 建议的下一步

H0 已作为独立安全改动完成。下一轮为 H1 写最小接口和一条只读工具纵切；不要同时启动长期记忆、Skill
重做和 Agent loop 替换。

若需要直接复用上游的某段 MIT 代码，应先记录具体文件、固定提交、修改范围和许可证归属；否则默认
只复用设计思想并在 Python 中独立实现。

## 参考资料

- [DeepSeek Harness README](https://github.com/deepseek-ai/deepseek-harness/blob/99f6f02fecdb7dff40c3fbc9470f5907c29f74ca/README.md)
- [架构](https://github.com/deepseek-ai/deepseek-harness/blob/99f6f02fecdb7dff40c3fbc9470f5907c29f74ca/docs/architecture.zh.md)
- [Agent 生命周期](https://github.com/deepseek-ai/deepseek-harness/blob/99f6f02fecdb7dff40c3fbc9470f5907c29f74ca/docs/agent-lifecycle.zh.md)
- [Session 事件日志](https://github.com/deepseek-ai/deepseek-harness/blob/99f6f02fecdb7dff40c3fbc9470f5907c29f74ca/docs/subsystems/session.zh.md)
- [工具执行流水线](https://github.com/deepseek-ai/deepseek-harness/blob/99f6f02fecdb7dff40c3fbc9470f5907c29f74ca/docs/tool-execution-pipeline.zh.md)
- [上下文压缩](https://github.com/deepseek-ai/deepseek-harness/blob/99f6f02fecdb7dff40c3fbc9470f5907c29f74ca/docs/subsystems/compaction.zh.md)
- [Skill 子系统](https://github.com/deepseek-ai/deepseek-harness/blob/99f6f02fecdb7dff40c3fbc9470f5907c29f74ca/docs/subsystems/skills.zh.md)
- [Token Meter](https://github.com/deepseek-ai/deepseek-harness/blob/99f6f02fecdb7dff40c3fbc9470f5907c29f74ca/docs/subsystems/token-meter.zh.md)
- [Repeat Tool Reminder](https://github.com/deepseek-ai/deepseek-harness/blob/99f6f02fecdb7dff40c3fbc9470f5907c29f74ca/packages/guard/repeat-tool-reminder/README.zh.md)
- [DeepSeek Adapter](https://github.com/deepseek-ai/deepseek-harness/blob/99f6f02fecdb7dff40c3fbc9470f5907c29f74ca/packages/llm/llm-deepseek/README.md)
- [第三方记忆 MCP 示例](https://github.com/deepseek-ai/deepseek-harness/blob/99f6f02fecdb7dff40c3fbc9470f5907c29f74ca/examples/mcp-memory/README.zh.md)
- [MIT License](https://github.com/deepseek-ai/deepseek-harness/blob/99f6f02fecdb7dff40c3fbc9470f5907c29f74ca/LICENSE)
