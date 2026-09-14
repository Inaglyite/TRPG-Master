# 结构化上下文与角色长期记忆：后端交付

日期：2026-09-14；分支：`experiment/keeper-platform`；工作区基线 `75516a4` +
未提交的过渡回合改动（`docs/STRUCTURED_TRANSITION_TURN_20260914.md`，本批在其上叠加，
未提交、未推送；基线快照记录在 `/tmp/context-memory-baseline-gitstatus.txt`）。

范围：四层信息模型中第 2 层（当前交互状态）与第 4 层（角色长期记忆）的后端实现，
含 S3 取证复核、协议冻结、提示词更新与确定性测试。**真实模型调用未在本批进行**
（无额度授权）；所有行为保证均为确定性机制，不宣称模型行为已改善。

## 0. S3 取证复核（任务一）

结论：认可既有取证文档 `docs/evidence/transition_real_model/S3_ROOT_CAUSE_FOR_BACKEND.md`
的结论，并由我直接复核原始轨迹 `trace_acceptance_frozen.json` 确认：

| 核查项 | 复核结果 | 证据位置 |
|---|---|---|
| 已讨论目的地是否带稳定 scene_id | destinations/scene 都有稳定 ID，但**没有任何字段记录「已讨论/已约定哪个」** | scenarios[2].model_calls[0].request.messages[user] 的 snapshot 段 |
| 新请求是否保留前一待办的关联 | **没有**：S3 时 pending_requests 只有 trace-3 自身；S1=declined、S2=completed，跨回合的「已讨论目标」随请求终态消失 | scenarios[2].pending_after |
| 是否区分「讨论目标」与「已授权行动」 | 不区分——上下文里根本没有这两个概念的结构化载体 | 同上（context 只有 snapshot/pending_requests/recent_public_messages） |
| 近期对话与目录/提示词是否冲突 | 有：对话里主持刚说过「遗体不在校方能力范围」，目录仍列医学院为目的地，无权威层标注差异 | scenarios[2].model_calls[0] recent_public_messages |
| 关键事实是否组装时遗漏 | 是（不是截断：`truncated_recent_messages=0`），是**根本没有结构化记录** | 同上 |

修复落点 = 本批的交互线程（§2）+ 上下文组装（§4）。

## 1. 数据层（迁移 0016）

`migrations/versions/20260914_0016_structured_context_memory.py`，两张表
（ORM 在 `src/storage/database.py`，adopt-or-create 与既有迁移同约定）：

- `interaction_threads`：跨请求存活的当前交互——稳定目标（pending_action 含
  destination_scene_id/target）、disclosed（已告知条件）、waiting_on、
  origin/last/request_ids 关联链、created/updated_revision（读档截止依据）。
- `character_memories`：按角色归属的追加式记忆——character_id/kind、
  knowledge_type(experienced|told|rumor|belief)、scene_id、subjects/topics、
  source（事件/命令引用）、derivation_key（派生幂等键，唯一）、
  status/superseded_by（更正链）、created/updated_revision。

已验证：全新库 `alembic upgrade head` 与 `create_all → stamp 0015 → upgrade`
（adopt 路径）均通过。

**审计结论（为什么不复用 memory_facts）**：既有 `memory_facts`（0010）按
(world, subject, fact_type) 唯一、耦合 legacy `source_turn_id`（结构化世界没有
Turn），是「当前事实」影子模型，不是追加式角色记忆；强行复用会破坏其不变量。
lorebook 检索是 legacy 回合管线的关键词注入，不适用于结构化路径。

## 2. 当前交互状态（短期衔接）

- 线程只由主持命令写：`resolve_intent{resolution:awaiting_player}` 自动开/续
  （awaiting 记录带 `thread_id`）；或 resolve_intent 显式 `thread{action:
  open|continue|close|replace, ...}`。**没有新增任何文本关键词规则**——追问/
  坚持/取消/改主意由主持（人类或 Agent）理解后用显式参数落账。
- 请求进入终态后线程保留（S3 修复点）；线程永不自动执行（它只是记录）。
- 自动收尾只有两种确定性联动：移动命令抵达线程目的地（completed）；
  玩家取消请求取消其关联线程（cancelled）。
- 写线程的 resolve_intent 推进 revision（否则读档无法区分存档点前后）。
- 投影：快照 `interactions[]`（本人/主持可见）；事件 `interaction_updated`
  （定向所属调查员）；Agent 上下文 `open_threads` + `trigger_context.
  candidate_thread_ids`（状态匹配候选，空数组即显式「无」）。
- 读档 fail-closed：存档点后创建的线程删除，其余开放线程一律 cancelled；
  分支复制开放线程。与 player_requests 的既有契约一致。

## 3. 角色长期记忆

- 确定性派生（提交后独立事务，失败只记日志不丢已提交事件）：
  move_party→全队亲历抵达；grant_clue→被授予者被告知；handout_presented→查看；
  check_resolved→检定经历；use_item/transfer_item/adjust_stat→对应经历。
  **知情依据只取事件明确指向的角色**，不因「相关」默认知情。
  未执行的计划（queued/awaiting）、被拒命令、未落账叙述不产生记忆（有测试钉住）。
- 主持显式记录：新命令 `record_memory`（keeper/agent；schema 校验 knowledge_type
  四选一；`supersedes` 更正旧记忆，旧条目留在来源链、默认检索不再返回）。
- 派生幂等：`derivation_key=ev:{event_id}:{character_id}` /
  `cmd:{command_id}:{character_id}`；重放/补建（`repair_derivation`）不重复写。
- 检索 `memories.retrieve`：active 默认、按角色/场景/主题/文本过滤，简单相关度
  排序（场景+2/主题+2/文本+3，recency 兜底），**条数×字符双预算硬执行**
  （默认 8 条/1200 字符，上限 20/4000）。第一阶段就是数据库检索，无向量库。
- 权限：`query_memories` 对玩家只放行其控制的调查员（自填他人 ID 明确
  `not_authorized`，不是空结果）；`memory_query` 帧 keeper 专用。
- 隔离：分支复制分叉点全部记忆后按 world_id 分叉；读档删除 created_revision
  晚于存档点的记忆、还原存档点后的 supersede（未来更正不反向污染过去）。

## 4. 上下文组装与提示词（实际运行位置）

- `src/structured/agent.py::_build_context`：新增 `open_threads`、
  `trigger_context`、`character_memories` 三个区块；必需区（snapshot/交互/待办）
  不依赖检索、不被记忆挤占（记忆预算独立且小）。
- 决策契约新增只读 `queries[{kind:"memory",...}]`：每运行 ≤3 次、结果 ≤800 字符，
  超预算明确拒绝并回喂 run_log；查询算进展（不吃空转保护），但不执行任何动作。
- `src/structured/agent_prompts.py`：SYSTEM_CONTRACT 新增「你看到的上下文
  （选择性注入）」与「承接与记忆规则」两段——四层各是什么、未注入≠未发生、
  传闻/推测不得当事实、待办≠授权、缺依据先查工具、已知目的地应承接、
  主持秘密≠NPC 知情、正常回答并等待是合法完成方式。
  **每条说明都对应真实存在的字段/工具**；没有要求模型输出思维链。

## 5. 给前端的冻结协议（zcode 确认后实现）

- `session_snapshot.payload.interactions[]`（可选字段，结构见
  `events.json::$defs/interaction_state`）：当前交互的公开投影。
- 新事件：`interaction_updated`（owner 定向）、`memory_recorded`（keeper）、
  `memory_query_result`（keeper）。`request_status_entry` 增加可选
  `awaiting`/`thread_id`（与过渡回合的服务端实现对齐）。
- 新命令：`record_memory`；`resolve_intent` 增加可选 `thread` 对象。
- 新帧：`memory_query`（keeper 只读；玩家侧**不**提供全量记忆接口）。
- 错误码新增 `thread_not_found`（common.json enum 末尾追加，前端 message 兜底）。
- fixtures：55 正例 + 9 反例；权限矩阵补 `command.record_memory` 与
  `memory_query.submit`。协议文档 `docs/STRUCTURED_PLAY_PROTOCOL_V1.md` §10。
- 前端**不需要**做意图判断或记忆生成；等待/取消/恢复事件沿用既有
  action_status/request_error/session_snapshot 语义。

## 6. 测试证据（全部本地实际跑过）

| 层 | 命令 | 结果 |
|---|---|---|
| 结构化全套（含本批新增 30 项） | `pytest tests/test_structured_*.py -q` | **179 passed / 1 skipped / 88 subtests** |
| 协议 schema/fixtures 双向校验 | `pytest tests/test_structured_play_protocol.py` | 7 passed / 88 subtests |
| 存储/分支/上下文周边回归 | `pytest tests/test_database_persistence.py test_world_branches.py test_context_compaction.py test_context_shadow.py test_turn_journal.py` | **94 passed** |
| 迁移 | 全新库 upgrade head；create_all→stamp→upgrade | 均通过 |
| Lint/架构 | `ruff check`（涉及文件）/ `tools/check_architecture.py` | 通过 |

需求 12 项场景的落点：① 约定→追问→承接 = `test_awaiting_opens_thread...` +
`test_follow_up_continues_same_thread` + `test_agent_context_carries_threads...`；
② 取消/换目的地 = `test_change_of_plan_replaces...` + `test_player_cancel_cancels...`
+ `test_move_to_other_scene_does_not_close_thread`；③ 历史增长不丢交互 =
`test_threads_survive_history_growth`；④ 未执行不进记忆 =
`test_unexecuted_request_and_narration_produce_no_memory`；⑤ A/B/C 知情隔离 =
`test_move_and_clue_derive_memories_for_exact_knowers`；⑥ 传闻纠正 =
`test_rumor_corrected_keeps_source_chain`；⑦ 重试不重复 =
`test_command_retry_does_not_duplicate_memories` + `test_redrive_is_idempotent...`；
⑧ 重连/读档/分支 = `test_snapshot_interactions...` +
`InteractionThreadLifecycleTests`（分支携带/读档 fail-closed）；
⑨ 分支/读档记忆隔离 = `test_branch_isolation` + `test_restore_drops_future...`；
⑩ 失败隔离 = `test_derivation_failure_does_not_lose_committed_facts`；
⑪ 越权 = `test_player_cannot_query_other_characters_memory` +
`test_keeper_memory_query_returns_keeper_event`；
⑫ 预算 = `test_retrieval_budget_is_enforced` + `test_agent_query_budget_is_enforced`。

## 7. 限制与未解决问题（明确区分机制与模型行为）

1. **真实模型验收未跑**（无额度授权）：提示词与上下文结构能否让 Agent 稳定承接
   已约定目的地，仍需已授权的真实模型复核（建议复用
   `tools/transition_real_model_check.py` 的 S1–S6 场景）。
2. 冲突检测是**结构性**的：权威状态/交互/叙事分层注入 + record_fact/record_memory
   通道；平台不自动检测「叙事与权威状态矛盾」（那需要文本理解，本批明确不做）。
3. 记忆检索是关键词/过滤式；语义检索只有测出不足才再议。
4. 记忆目前只服务主持侧（Agent 上下文 + keeper 查询）；玩家侧无接口（符合
   「不默认提供全量记忆」）。
5. `rate_limited` 仍未实现（既有登记）；`memory_query` 无频率限制，预算上限
   在 schema（limit≤20/char_budget≤4000）。
6. 前端联调（zcode）：interactions 投影、interaction_updated、memory_query 均为
   新增契约，前端尚未实现；联调前本批不算端到端闭环。
7. 工作区含过渡回合等**未提交的他人改动**，本批叠加其上，均未提交。

## 8. 事故记录

交付过程中我一度用 `Write` 覆盖了 `tests/test_structured_memory.py`（H3 既有文件）。
已用 `git checkout HEAD --` 完整恢复（该文件 40 passed / 1 skipped 复核通过），
本批记忆测试改放 `tests/test_structured_character_memory.py`。教训已确认：
新文件先确认路径不在 HEAD 中再写。
