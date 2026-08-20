# DeepSeek Harness 借鉴与采纳决策

状态：H0/H1/H2 已实施并受本地回归保护；H3 的 Skill Catalog、世界级内容/manifest pin 与 schema
integrity guard 已实施。结构化记忆仅为未接入模型、游戏回合、公网 API 或玩家界面的 shadow foundation。

评审日期：2026-08-21

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

当前代码没有引入 DeepSeek Harness；运行栈仍是 LangGraph + OpenAI-compatible SDK。项目只在
现有 Python 边界内实现了可审计的 request envelope、工具执行策略、上下文事件投影、容量预检与
Skill Catalog/pin，不存在另一套 Harness runtime，也不依赖其内部持久协议。

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

1. `messages` 仍是当前的运行 surface，但 H2 已追加记录其 seed、控制注入、assistant/tool 消息、请求
   envelope 与 checkpoint，并在 shadow 模式比较投影 digest；它尚未成为唯一读取来源。
2. 历史压缩现在只使用可验证的 replace/rebase checkpoint，失败不会删历史；摘要是**非权威连续性缓存**，
   不能证明模型没有杜撰公开事实，所以权威场景、线索、骰值和资源每回合仍从 `WorldState`/Turn 重投影。
3. Skill 已有 manifest、世界级 content/manifest pin、确定性 resolver、来源事件与 fail-closed schema
   guard；关键词只保留为漏加载诊断。结构化记忆表仍是 shadow foundation，尚未暴露给模型工具、HTTP、
   WebSocket、prompt 或正常游戏回合。
4. H1 已将模型调用收束进 issued request snapshot + V2 pipeline；受信引擎内部兼容调用和跨介质副作用仍
   需要继续按 principal/outbox 边界扩展，不能把新 handler 直接塞进全局 registry。
5. `npc_conversations` 仍是叙事 transcript，不是 accepted fact；任何未来记忆接入必须保持 transcript、
   candidate 与确定性接受的事实三层分离。
6. Context section 已有 digest、来源、audience、估算 token 和 capacity evidence；更细粒度的 provider
   adapter、模型专属 tokenizer 与长期 payload 限额属于 H4/运维治理，不在当前游戏热路径偷偷实现。

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

H0 已删除 active core/keeper Skill 中要求模型调用 `read_file` 的旧指令，并由受信引擎按 manifest/pin
注入内容。native 与 DSML 的模型调用都携带同一份**服务端签发、绑定当前 world/turn 的**请求快照；执行点
只使用该请求的精确 catalog，拒绝旧快照、伪造快照、私密工具和所有未下发能力。普通日志只记录元数据；
H1 的 pipeline 继续记录去敏 outcome、幂等和 deadline。

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
2. 对完整 provider wire payload（messages + tools JSON framing）做保守估算，以部署者配置的窗口比例
   作为预压缩触发线；`TRPG_CONTEXT_WINDOW_TOKENS` 必须由部署者按 provider/model 的已知总窗口配置，
   估算不是 tokenizer-perfect 的容量证明；
3. 先确定性裁剪过大的旧工具结果，保留有界 head + marker + tail，原结果仍留在事件日志；
4. 选择最旧、完整、call/result 配对平衡的 surface 范围；
5. 摘要 checkpoint 必须记录 source references，并验证输出比被替换范围更小；
6. 以 replace checkpoint 遮蔽旧 surface，不删除原始事件；
7. 摘要失败、超时、取消或不收敛时保持原 surface，不再使用“丢弃最早消息”兜底；
8. 只有发生了持久、可验证的 surface 缩减后，context-overflow 请求才可重试。

若 system core、工具 schema、最新不可拆消息或其他不可裁剪 section 在一次安全 prune/summary 后仍超过
容量，返回 metadata-only `irreducible_context` 诊断并停止自动重试；不得循环摘要或偷偷删除安全规则。
压缩性能应使用录制请求和构造的极限 context fixture 测量，不把公网模型延迟算作本地 pipeline p95。

摘要采用受限 JSON shape，只作为连续叙事的非权威提示。精确骰值、资源、线索、场景和规则结论绝不信任
摘要模型复述，而是在每个动作前从 `WorldState`/权威 TurnEvent 确定性重投影；摘要不能修改 `WorldState`，
不能把私密事实提升为玩家已知事实，也不会接收原始 tool result 或控制消息作为输入。

### 5.4 Skill Catalog，不再赌关键词

参考上游
[Skill 子系统](https://github.com/deepseek-ai/deepseek-harness/blob/99f6f02fecdb7dff40c3fbc9470f5907c29f74ca/docs/subsystems/skills.zh.md)，
当前已交付的 manifest 不是下列“理想字段清单”的超集，而是一个有限、可验证的
`SkillEntry`。`skills/catalog.json` / 运行时模组合成条目提供：

```text
id / path / version / trust(core | bundled-module) / residency
description / opening / model_invocable / max_context_tokens
required_tools / allowed_tools / dependencies / user_invocable / resources
activation: tools / combat_active / san_below / phases / scenes / scene_capabilities /
            module_capabilities / rulesets
diagnostic_keywords
```

首次 pin 时，`world_skill_pins` 另存正文、`content_digest`、version、trust、residency；其 1:1
sidecar `world_skill_pin_manifests.entry_snapshot` 冻结上述完整 `SkillEntry`，再加
`order`、`catalog_version` 与完整 `catalog_ids`。因此 `content_digest` 是**pin 完整性字段**，不是会被
当前 catalog 反读或热更新的 manifest 值。

`dependencies` 与 `scene_capabilities` 已有确定性校验/解析；`resources` 已有受信引擎的 manifest allowlist
读取路径，模型仍不能调用它。`required_tools`、`allowed_tools`、`user_invocable` 也已进入严格 schema，
但任何非空/`true` 声明会被 catalog fail-closed 拒绝——字段存在不代表权限生效。

以下是**未开始的 H3.1 manifest 扩展**，不能在设计文档中被误写成现有能力：被安全门禁的
`required_tools`/`allowed_tools`/`user_invocable` 的真实执行策略、`local-author` trust、catalog 版本迁移和
作者编辑 UI。工具权限目前由服务端签发的 request-scoped model catalog 管理；受信引擎的固定资源路径规则
不是给模型的任意文件读取能力。

加载分三类：

- **常驻核心**：信息边界、禁止代替玩家说话、权威状态优先、工具协议等安全规则始终存在。
- **确定性激活**：`ruleset`、`combat.active`、SAN 状态、`ActionResolution.intent`、场景 capability、
  模组声明和即将暴露的工具决定本回合必须加载哪些 Skill。这是安全关键路径。
- **模型按需**：模型只看到有界的 `id + description` catalog，可用 `load_skill(id)` 读取非关键或歧义
  Skill。加载器按 allowlist 取正文，不给模型绝对路径或通用 `read_file`。

关键词命中可以保留为召回信号和漏加载诊断，但不能成为唯一条件。Skill 内容按
`world + version + digest` 固定；正在进行的世界不因磁盘文件变化热更新。每次激活/加载都作为带来源的
模型上下文事件记录，压缩后若目录消失，应由 ContextBuilder 重新建立当前目录。若数据库世界存在完整的
pre-0011 content-only pin 集，则只用 pin 行能证明的保守 metadata 兼容读取（固定预算 core 正文、非 opening、
不可模型调用、无 activation），绝不查询当前 catalog；0011 sidecar 缺少仅限后来新增且默认安全字段时按空/
`false` 规范化，其他部分/空 sidecar、pin 损坏或 schema 不可读一律 fail-closed。

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
| H3 结构化候选 / fact | `memory_fact_candidates` / `memory_facts` | shadow-only | 仅内部 schema/service；不是 prompt、玩家功能或世界事实来源 |

H3 已把下列字段落入候选/fact 表，并为 accepted fact 提供 digest 去重、同一
`(world, subject, fact_type)` 的 partial-unique current 约束和 `supersedes_id` 版本链：

```text
world_id / root_world_id / source_turn_id
subject_id / fact_type / value
audience / owner_user_id / tier
revision / provenance / supersedes / created_at
```

但这只是 schema 与受限内部服务的 shadow foundation：正常 `GameEngine` 回合不会产生 candidate、
`ModelSession` 不会查询或注入它、模型没有工具可读写、HTTP/WS/前端也没有入口。即使测试或未来受信
维护代码调用 `StructuredMemoryService`，其结果也不能反写 `WorldState`、改变检定/叙事或直接显示给玩家。
这种不暴露正是 H3 的安全完成条件，不是遗漏了一个待补 UI 或协议。

时间线只应读取共同祖先与当前分支上的事实，不能读取兄弟分支的新事件；祖先 fact 同时必须落在分叉的
`source_world_revision` 和接受时间截点内（新分支写 `branch.memory_cutoff_at`，旧分支仅兼容其带时区的
`branch.created_at`）。因此分叉后才接受、即使引用旧 source turn 的 fact 也不能倒灌到子线；损坏时间线
metadata 一律 fail-closed。这是已被 service 单测覆盖的**未来接入前提**，不是已经启用的玩家记忆体验。未来接入时，模型至多提出 candidate；独立的受信接受者
必须验证来源、principal、权限、冲突和幂等。embedding 或全文检索只能召回候选，结果注入前仍要经过
world、branch、audience、TIER 和 clue gate，且不能成为事实来源。

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

### H0：安全边界与基线测试（已实施，2026-08-21）

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

实现落点：`src/model_request.py` 在最终 wire request 才生成 snapshot；
`src/tool_request_authority.py` 签发并绑定 world/turn/exact catalog；`src/tool_pipeline.py` 与
`src/agent_graph.py` 对 structured/DSML/legacy path 都只使用该已签发 catalog；
`src/model_tool_catalog.py` 生成递归 closed schema；`src/skill_activation.py` 只加载 pin 中允许的资源。
相关 golden、拒绝、重放、泄漏与轮次边界回归在 `tests/test_tool_policy.py`、
`tests/test_tool_pipeline.py` 与 `tests/test_skill_catalog.py`。

### H1：Context Envelope + Tool Pipeline V2（已实施，2026-08-21）

- `src/tool_pipeline.py` 定义 `ContextSection`、`RequestEnvelope`、`ToolDescriptor`、`ToolCall`、
  `ToolOutcome`、`ToolUnitOfWork` 和 `MutationPlan`。`RequestEnvelope` 把 H0 请求快照扩展为
  world/turn/step、provider/model、容量、context section 和工具目录的类型化不可变记录；持久审计只保留
  digest、计数和来源，不复制 prompt 或私密 tool output。
- 所有当前**模型可调用**写工具（线索、物品、NPC 揭示、SAN、战斗、心理特质、结局等）都由既有
  `WorldStore.turn_cache` 包裹，最终仍只在 `DatabaseTurnJournal.complete()` 中与 WorldState、Snapshot、
  SaveSlot 原子提交。`MutationPlan` 只描述这些缓存内 mutation；`create_character`、文件读取、素材展示和
  私密读取均不在模型目录中。今后新增跨介质副作用必须走 post-commit outbox 或具补偿动作的专用路径，不能
  直接注册为 V2 model tool。
- `ToolPipeline` 将 H0 的 request-scoped policy/schema 校验、descriptor visibility、semantic-turn
  幂等、协作式 deadline、取消检查、输出大小/类型校验和每调用必有一个 outcome 收敛为一条路径。取消会向上
  传播，外层 `turn_cache` 丢弃未提交状态；同一 turn 中同一语义的写调用返回既有 outcome 而不再次执行。
  同步 handler 不能被安全强杀，因此 deadline 仅阻断**尚未开始**的 handler；开始后的 handler 必须完成，
  随后由取消/失败的回合工作单元整体丢弃，避免产生“超时但已半写入”的假象。
- active turn 与 completed turn 都记录去敏的 `call_id/request_id/world_id/turn_id/step/name`、参数/输出
  digest、状态、耗时和 mutation plan；`ModelCall.details` 记录 typed request envelope、实际 usage 与
  provider cache hit/miss 元数据。
- `TRPG_TOOL_PIPELINE_V2=0` 保留 H0 旧执行环；此时 `TRPG_TOOL_PIPELINE_SHADOW=1` 仅预检 V2 descriptor
  和 policy、记录差异，不会二次调用 handler。`TRPG_TOOL_EXECUTION_TIMEOUT_MS`（1–120000，默认 5000）控制
  协作式 handler deadline。默认启用 V2。

验收证据：`tests/test_tool_pipeline.py` 覆盖类型化 envelope、语义幂等、取消、deadline、非法输出、旧路径
shadow、active/completed TurnJournal audit 以及 `ModelCall.details`；H0 的 native/DSML/伪造/跨 world-turn
重放 fixture 继续由 `tests/test_tool_policy.py` 覆盖。`PYTHONPATH=. ./venv/bin/python tools/benchmark_tool_pipeline.py --assert-budget`
以固定的本地 recorded-shaped handler fixture 在每次验证时输出 80 次样本的 p95；命令会对 pipeline 自身
`<10ms`、正常本地编排 `<=5%`（公网 LLM 未计入）两项预算失败关闭。

### H2：追加式 Context Event + 非破坏性压缩（已实施，2026-08-21）

- 双写模型上下文事件；
- 实现纯投影器并比较现有 messages digest；
- 固化 fork/save/resume 的 lineage、session epoch、checkpoint 范围和引用感知 GC；
- 上线 tool-result pruning、平衡边界、summary replace、真实 wire-capacity preflight 和 context-overflow
  一次性受控重试；Pi 的单 slot 流中断/overflow 重试会先释放 slot；
- 原始权威 Turn 按产品存档策略保留，旧存档仍可 seed；GC 默认只处理 archived worlds，活跃世界不会因
  请求时维护被删除；
- 摘要只接受有界 JSON 与确定性 private-fragment guard；它是非权威连续性缓存，关键事实由权威状态重投影。

验收：同一事件日志投影得到相同模型 surface；摘要失败/崩溃不丢历史；call/result 始终配对；可压缩的
请求会在 provider 调用前重建并重新评估，只有安全压缩后仍达到 hard 上限才返回诊断而非删规则；摘要不能
写入权威事实或回显已知私密正文，兄弟时间线不能读取彼此事件。摘要模型文本本身不被当作事实来源。

实施落点：`src/context_events.py` 新增
`project_with_refs`（投影逐条带 `(session_id, sequence)` provenance，replacement 位映射到
checkpoint 事件自身，支持嵌套引用）；`src/context_shadow.py` 新增 `replace_range` /
`prune_messages` 与 adapter `compact_engine` / `prune_engine`——压缩先校验投影==权威 messages
再发 `replace` checkpoint，失败只诊断且回退 rebase（回合内不回退）；`src/history_compactor.py`
的 summary/截断兜底改走 replace（raw events 保留），新增窗口外大 tool 结果原地修剪
（保留 `tool_call_id` 配对）；`src/model_streamer.py` 识别 provider 上下文溢出后强制压缩并重试
一次（不可约则诊断，不删规则；`messages_override` 请求不触发；回合内压缩同步更新
`_turn_context_surface`，rollback 落在压缩后表面）。语义分工：**rebase 换历史**（reset/load 兜底/
adopt/branch/rewrite），**replace 压历史**（summary/pruning），replace 事件一律不带 turn_id。
摘要安全补充：`src/context_summary.py` 在摘要输入侧剔除引擎控制消息，并对候选摘要执行确定性可见性
检查；私有 memory、未披露线索与 NPC secret 的已知正文不得被回写到摘要。摘要模型不可用、输出不合法
或未通过可见性检查时，`HistoryCompactor` 只记录去敏诊断并保留原有 model surface——不再以“已丢弃
最早消息”的伪摘要替换历史。`tests/test_context_summary.py` 固定公开、私有、未披露线索三类 gold
fixture，且验证检查器不会写入 WorldState。

管理面：`tools/maintain_context_events.py` 是唯一的显式 GC 入口，默认只处理已归档世界；`--all` 才会
参考引用关系清理活跃世界的关闭 epoch。生产和 staging 分别通过
`trpg-master[-staging]-context-gc.timer` 每日运行，GC 仅记录计数型 audit，不输出 context payload。
`tests/test_context_maintenance.py` 覆盖引用保护、幂等、归档范围、审计，以及物理删除 World 时
`context_sessions` / `model_context_events` 的外键级联清理。

验收证据：`tests/test_context_compaction.py`（replace 签发、边界不拆 call/result、摘要失败不改写
surface、diverge 回退 rebase、回合内 rollback 一致性、pruning 配对、overflow 重试一次/不可约不
重试）、`tests/test_context_events.py` 的 with_refs golden、`tests/test_context_summary.py` 和
`tests/test_context_maintenance.py`。PostgreSQL 迁移/并发检查由 CI 的 `TRPG_TEST_POSTGRES_URL` 运行；
本地无该数据库时对应集成用例按测试配置跳过，而不是以 SQLite 冒充 PostgreSQL。

### H3：Skill Catalog + 结构化记忆（Skill 已完成；记忆严格 shadow-only）

已交付：

- 官方 Skill 已迁移为 `skills/catalog.json` + 正文文件；关键规则由 action/ruleset/state/capability
  确定性激活，模型按需 loader 只覆盖被冻结、非关键且 `model_invocable` 的 on-demand Skill；
- 每个数据库世界首次解析 catalog 时冻结正文、digest、version、trust、residency、完整 `SkillEntry`
  snapshot、catalog order/version/ID 列表；分支复制源世界 pin，运行中的世界不因磁盘发布热更新；
- pre-0011 的完整 content-only pin 集可保守读取；部分 sidecar、损坏 digest、孤儿/循环分支 pin、不可读
  数据库或未通过 schema 检查的世界都拒绝继续，而不是回退活 catalog；
- `20260821_0012` 在升级时只做 fail-closed H3 schema integrity guard：严格验证 0009/0010/0011 的列、
  类型、NOT NULL、主键/唯一约束、外键 `ON DELETE` 和查询/partial index。它不猜测或修复未知旧库；健康的
  fresh 与 `Base.metadata.create_all` 数据库可原样接管；
- `MemoryFactCandidate` / `MemoryFact` 的候选—accepted 边界、branch/audience/tier 查询与数据库约束已作为
  shadow service 建好；分叉读取同时受 source revision 与 accepted-time cutoff 约束，SQLite 的重复 candidate
  提案会收敛到同一 winner，但没有任何模型工具、HTTP/WS、前端、prompt 或正常回合接线。

**H3.1（未开始）manifest 扩展**：被安全门禁的 `required_tools`、`allowed_tools`、`user_invocable` 的真实
执行策略、`local-author` trust、catalog version migration 与作者编辑 UI。`dependencies`、scene capability
解析和受信 `resources` allowlist 已在 H3 落地，不能再列作未实现。

另行规划的结构化记忆产品工作：principal/API、生产写入接受者、自动 candidate 抽取、模型/玩家查询、
向量检索、导出、管理 UI、并发生产监测与 gold precision 评测。它们完成前，`memory_fact_*` 不拥有世界事实，
也不能被宣传为可用的玩家长期记忆；不暴露给模型、HTTP、WS 或玩家正是当前 H3 的完成条件。

验收证据：`tests/test_skill_catalog.py` 覆盖 manifest resolver、pin/sidecar 完整性、branch inheritance、
load_skill digest 与来源事件；`tests/test_structured_memory.py` 覆盖 shadow service 的 candidate/fact、
branch/audience/tier 隔离和幂等约束；`tests/test_electron_packaging.py` 覆盖 fresh/create_all→Alembic 的
0012 接管与被削弱 schema 的 fail-closed 拒绝。

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

H0/H1/H2 已作为当前发布基线完成，H3 的 Skill freeze 与 schema integrity guard 也已接入。下一轮优先
做 H4：录制 provider stream fixture、离线 turn replay、模型错误分级和 Pi staging 灰度；不要把尚未有
principal/评测边界的结构化记忆接成模型能力，更不要启动长期记忆 MCP 或 Agent loop 替换。

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
