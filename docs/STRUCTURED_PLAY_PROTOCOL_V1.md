# 结构化操作协议 v1（M0 冻结）

日期：2026-09-13；分支：`experiment/keeper-platform`；状态：M0 协议冻结，后端实现依 M1→M4 推进。

本文是 `docs/STRUCTURED_PLAY_PLATFORM_PLAN_20260913.md` 第 5 节的正式化；出现差异时以主规格为准，
但**字段级定义以 `schemas/structured-play/v1/` 的 JSON Schema 为唯一正本**——前端
（zcode）按 schema + fixtures 对齐，不口头约定。

## 1. 可校验文件清单

| 文件 | 内容 |
|---|---|
| `schemas/structured-play/v1/common.json` | id/revision/target/audience/speaker/domain_outcome/error_code/dice_spec 公共定义 |
| `schemas/structured-play/v1/action_request.json` | 玩家行动尝试请求（present_clue / use_item / move / freeform） |
| `schemas/structured-play/v1/cancel_request.json` | 玩家取消自己 queued 的行动请求（M2 新增帧类型） |
| `schemas/structured-play/v1/memory_query.json` | 主持侧只读记忆查询帧（keeper 专用；结果以 memory_query_result 事件返回） |
| `schemas/structured-play/v1/free_roll_request.json` | 普通掷骰请求（受限表达式） |
| `schemas/structured-play/v1/check_response.json` | 待检定卡回应（roll / decline，不携带参数） |
| `schemas/structured-play/v1/command_request.json` | 主持命令信封 + 16 种命令的严格载荷（M3 新增 resolve_draft；上下文/记忆批新增 record_memory） |
| `schemas/structured-play/v1/events.json` | 事件信封 + 23 种事件载荷（含 session_snapshot / server_capabilities / handout_presented；上下文/记忆批新增 interaction_updated / memory_recorded / memory_query_result） |
| `schemas/structured-play/v1/permission-matrix.json` | 权限矩阵机器可读正本 |
| `schemas/structured-play/v1/fixtures/` | 55 个正例 + 9 个反例 |
| `tests/test_structured_play_protocol.py` | 双向校验与一致性门禁（7 项 + 74 子项） |

校验方式：后端 `pytest tests/test_structured_play_protocol.py`；前端用同一目录的
JSON Schema（Ajv 2020-12，需支持 `unevaluatedProperties`）或直接把 fixtures 与
`frontend/src/protocol/structured.ts` 的 zod 解析互测。

## 2. 版本协商与运行模式

### 2.1 execution_profile 与 keeper_mode

- 世界元数据新增 `execution_profile`: `legacy`（默认）| `structured_v1`；`keeper_mode`:
  `human` | `assisted` | `agent`。两者都持久化在服务端，前端切换不冒充授权。
- `legacy` 世界完全走现有路径：旧 speaker_parser、旧回合、旧工具策略不变。
  structured 请求打到 legacy 世界返回 `profile_mismatch`。
- `structured_v1` 世界启用本协议：结构化请求、命令服务、逐命令提交。
  新模式下旧的关键词自动执行通路对对应效果关闭（见第 8 节效果所有权）。

### 2.2 模式策略

| 模式 | 模型要求 | 行为 |
|---|---|---|
| human | 不初始化模型会话、不要求 Key、不自动摘要 | 玩家请求进主持待办；人类守秘人用同一组命令结算 |
| assisted | 仅显式请求时调用；BYOK | AI 产物为草稿（`keeper_draft`），仅主持可见，批准前按当前 revision 复核 |
| agent | 开始前验证 BYOK、能力与预算 | Agent 在授权内自主调用命令；预算/无进展/断开进入 paused，人类可接管 |

human 房间的模型就绪门禁：structured_v1 + human 不经 `_room_model_readiness`；
agent/assisted 仍走 BYOK 校验，**不**因此放开平台 Key 兜底。

Agent 触发语义（M3 实现冻结）：`action_request` / `check_response` 提交成功后调度一次
守秘人运行；`free_roll_request`（普通骰）与主持自己的 `command_request` 不触发剧情。
同一世界同一时刻至多一个活动运行；运行中到达的新触发由下一轮上下文重建吸收。
模型路由不可用（BYOK 未配置/被阻断）时不消耗模型、触发请求置 paused 并给主持可见提示；
人类在控时运行返回 blocked 不抢占。

### 2.3 server_capabilities 协商

`session_snapshot.payload.server_capabilities` 是能力唯一来源：`structured_protocol`、
`protocol_version`、`execution_profile`、`keeper_modes`、`commands` 及若干布尔开关。
前端 fail-closed：缺字段=不支持；协议版本不符显示明确提示并保留草稿，不回退旧协议。
结构化协议不可用时前端不得偷偷退回文字路径。

## 3. 状态机

### 3.1 行动请求（player_requests）

```
queued → processing → completed | declined
   ↑         ↓  ↘ awaiting_player（等待检定/选择，由 check_requested/决策驱动回 processing）
   │         ↘ paused（Agent 不可用/预算/接管）→ processing（恢复，不重跑已提交命令）
   │         ↘ failed（可恢复失败；客户端用同一 request_id 重发 ⇒ 回到 queued）
   └─（同 request_id 同载荷重复提交：返回当前状态，不新建）
cancelled：仅由主持 resolve_intent 或玩家取消未开始请求进入。
completed = 主持处理完毕，**不代表故事行动成功**；领域结果另列 success/failure/not_executed。
```

### 3.2 检定（check_requests）

```
pending → resolved（服务端恰好结算一次）
        → declined（指定玩家点“放弃”）
        → cancelled（主持撤回）
        → expired（超时；超时不默认掷骰，多人由主持取消或延期）
```
创建即持久化（角色、技能、目标、难度、奖惩骰、代价、可见性、规则版本、依赖条件）。
执行时复核控制权与相关条件：无关聊天不使检定失效；场景/物品等相关条件变化 ⇒
`check_conditions_changed` 失效并由主持重新请求，不偷偷换参数。断线/重启/接管后可恢复。
秘密检定（visibility=keeper）结果仅按权限投递。

孤注一掷（M2）：主持对已失败的原卡发一张带 `push_for` 的新检定卡，写明升级代价
（`known_cost`）。服务端强制：原卡必须已失败、同调查员/技能/目标/场景、非战斗、
luck/sanity/dodge/克苏鲁神话/战斗技能不可；原卡同一时刻至多一张孤注一掷卡
（`push_pending` 占位），玩家实际掷骰后原卡永久标记 `pushed`；放弃孤注一掷卡
不消耗原卡。`time_cost_minutes` 在结算时（成败都）推进世界时钟并随 revision 落账。

### 3.3 命令（game_commands）

命令是即时事务，无异步状态：`submitted → committed | rejected | conflict`。
committed 附带领域 result（success/failure/not_executed 及明细）与新 revision；
rejected/conflict 不改变状态、不发布权威事件（只回 `request_error` 给调用者）。

命令事件的因果标签（M1 定稿）：

- 由玩家请求引发的命令（`cause_id` 非空）：事件 `cause_request_id` = 上游玩家
  request_id，玩家请求卡随之更新。
- 无上游的命令：`cause_request_id` = command_id 自身。
- 每条 committed 命令额外追加一条 `action_status{request_id: command_id,
  status: "completed", outcome}` 作为命令卡收尾，audience 定向发起方
  （keeper/agent 或该玩家），让发起端的“主持操作/发言”卡离开等待态。
- `request_error` 同样持久化到 event_outbox（真实 event_id/sequence，
  前端游标可去重），但不推进世界 revision，audience 定向发起方。

### 3.4 keeper_draft（assisted 草稿，M3）

assisted 模式的 AI 产物**不是命令执行**，而是草稿：占用 `player_requests`
（`request_type=keeper_draft`），仅主持可见（事件 audience=keeper）。

```
queued → completed（主持 resolve_draft decision=approved/edited）
       → declined（decision=rejected）
```

- 草稿携带 `summary`、`proposed_commands`（无 command_id 的建议命令列表）、
  `narration`；批准本身**不执行**任何命令——主持批准后以各自的
  `command_request` 单独提交（幂等），edited 表示主持改过内容再发。
- 终态草稿不能重复收尾（invalid_action）；未知 draft_id 报 request_not_found。
- 模型不可用时不产草稿、不消耗后续调用，运行记 draft_unavailable 暂停。

## 4. 错误码

正本在 `common.json#/$defs/error_code`。前端已知 13 码（REQUEST_ERROR_TEXTS）不得改名；
后端新增码前端用 `message` 兜底。

| code | 含义 / 典型触发 | retryable |
|---|---|---|
| revision_conflict | expected_revision 与世界当前版本不符 | 刷新候选后重提 |
| duplicate_request_conflict | 同 ID 不同载荷 | 否（换新 ID） |
| unknown_target / stale_target | 目标不存在 / 已失效（离场、状态变化） | 刷新候选 |
| target_unresolved | unresolved 文本目标未获主持解析 | 等主持澄清 |
| unsupported_protocol / profile_mismatch | 服务端/世界不支持结构化协议 | 否 |
| not_authorized / keeper_required / not_investigator_controller | 权限/主持权/调查员控制权不足 | 否 |
| not_actor | 未轮到行动（旧回合门禁沿用场景） | 是 |
| check_not_pending / check_already_resolved / check_conditions_changed | 检定生命周期冲突 | 否 |
| controller_epoch_stale | 接管后旧 epoch 的迟到调用 | 否 |
| object_not_found / object_not_held | 物品/线索不存在或不持有 | 刷新候选 |
| presentation_requires_item / presentation_requires_asset | 出示方式缺原件/已授权素材 | 改出示方式 |
| invalid_action | 请求内容不合法（schema 或语义） | 否 |
| rate_limited / budget_exceeded / keeper_unavailable | 限流 / Agent 预算耗尽 / 主持不可用 | 稍后 |
| request_not_found / unknown_world | 原请求/世界不存在 | 否 |
| commit_failed / internal_error | 事务提交失败 / 内部错误 | 是 |

## 5. 权限矩阵

机器可读正本：`schemas/structured-play/v1/permission-matrix.json`（
`tests/test_structured_play_protocol.py` 校验其覆盖全部命令与角色）。要点：

- 房间管理角色（owner/player/viewer）与玩法授权（keeper、调查员控制权）是两个维度；
  owner 不自动看秘密，keeper 不必占调查员名额。
- Agent 是“被委派的主持”：命令子集 + 每次调用绑定当前 `controller_epoch`；
  接管递增 epoch，旧 epoch 迟到调用返回 `controller_epoch_stale`。
- principal 一律服务端解析；客户端自报身份/epoch 仅作参考，不作授权依据。

## 6. 去重与幂等

- `request_id`（玩家意图）/ `command_id`（一次副作用）/ `check_request_id`（一次被请求
  检定）是三种 ID，不得混用；一个意图可派生多个命令。
- 去重键 = world_id + ID + 身份 + 载荷摘要（canonical JSON 的 sha256，键序与空白规范化）。
  同内容重发返回已保存结果；同 ID 不同内容返回 `duplicate_request_conflict`。
- Agent 重试不依赖供应商 tool_call_id；harness 为已计划步骤保存稳定 command_id。
- 数据库唯一约束兜底：`player_requests(world_id, request_id)`、
  `game_commands(world_id, command_id)`、`check_requests(world_id, check_request_id)` 唯一。

## 7. 稳定物品/线索 ID：迁移与兼容

问题：物品是字符串标签、线索 key 可由类别/文本拼接，不能作为新协议的可写实体 ID。

方案（M1 实现，方案本里程碑冻结）：

1. 世界状态新增 `item_registry` 与 `clue_registry`（带 `schema_version`）：
   - 物品条目：`item_id`（`item_` + 12 位稳定散列/序号）、`label`（原标签）、`quantity`、
     `stack_key`（同一持有者的同名旧字符串折叠为同一堆叠，数量累加；不同持有者
     绝不合并；拆分/转移产生的新堆叠由命令层生成新 ID）、`legacy_label`、`operations`。
   - 线索条目：`clue_id`（模组目录有 catalog_id 用之，否则 `clue_` + 稳定散列）、
     `legacy_key`（category+text 拼接原键）、`category`、`text`、`granted_to`
     （知情授权列表）。
2. 迁移在打开旧世界时**一次性**执行并随状态持久化（不是每次读取重算）；
   幂等：已迁移世界不再改动。迁移只新增注册表与映射，不重置进度、不覆盖自定义内容。
3. 兼容投影：旧前端/旧模式继续看到字符串背包与按类别的线索列表（由注册表投影生成）；
   新协议只接受稳定 ID；旧值通过 `legacy_label`/`legacy_key` 映射解析一次，之后以 ID 为准。
4. 快照/分支：同一分支内快照恢复保留同一标识（注册表随状态走）；跨世界请求一律校验
   world_id，旧 ID 不跨界复用。

## 8. 每命令提交与事件发布边界（避开旧整轮 turn_cache）

旧路径 `GameEngine.handle_action` 的 `turn_cache` 覆盖整轮（含模型生成），权威结果可能在
最终持久化前公开——**structured_v1 不复用该路径**。

新路径（命令服务，M1 实现）：

1. 受理：schema 校验 → 身份/权限/epoch → 去重（同 ID 同载荷直接返回旧结果）。
2. 短事务（不跨模型生成持事务/世界锁）：读取最新状态 → 校验前置条件与
   `expected_revision`（CAS）→ 领域函数计算新状态 → 同一事务写入：
   世界状态（revision+1）、`game_commands` 行（含 result）、`check_requests` 变更、
   `event_outbox` 待发布事件（含 audience）。
3. 提交成功后才发布权威事件；断线按 `event_id` 游标补发；推送失败只重放，不重掷/
   重扣/重移动。
4. 模型失败不回滚已提交命令：Agent 回应可记 partial/paused，恢复从最后完成命令的
   游标继续。
5. 固定原子组合由应用层显式提供（如 use_item 消耗+结果、grant_clue+授权图片展示），
   不开放任意脚本事务。

存储表名冻结（M1 建迁移）：`player_requests`、`game_commands`、`check_requests`、
`event_outbox`、`keeper_control`（当前控制器 + epoch）。复用现有 `WorldState` 行与
revision CAS；`turn_events` 不承担命令事件（它依附旧回合，失败回合会被清理）。

### 效果所有权（防双结算）

structured_v1 世界：移动只由 `move_party` 落账（禁用 `infer_scene_transition`/
`infer_discovery_target_destination` 的自动移动）；线索只由 `grant_clue`
（禁用 discovery 自动发放）；SAN/HP 只由 `adjust_stat` 与检定结算；时间只由
`advance_time` 与明确命令；出示不产生所有权变更（transfer_item 独立确认）。
legacy 世界保持现有自动化。每个效果同一时刻只有一个执行所有者。

## 9. 与 zcode spec-derived 草案的已知差异（后端冻结版为准）

zcode 已声明其 `frontend/src/protocol/structured-fixtures.ts` 为过渡草案，M0 后替换为
后端 fixtures。需要 zcode 适配的两处：

1. `session_snapshot.payload` 新增必填 `keeper_mode`（世界级设置；`keeper` 字段仍表示
   当前控制器，可为 null）。
2. `roll_resolved.payload` 新增必填 `note: "普通掷骰"`（主规格 §3.4 的固定标注）。

其余字段（信封、请求、错误码、状态枚举、事件类型）与 zcode 草案一致；后端错误码
集合是其已知集的超集，前端未知码用 `message` 兜底即可。

M1 实现期的两处 schema 修正（均为放宽/归位，已有 fixtures 不受影响）：

1. `state_changed.payload` 归位新增可选 `targets`（在场目标列表，供 set_npc_presence
   等命令推送）；此前误置于事件层级，任何载荷都过不了校验。
2. `session_snapshot.payload.investigator_id` 允许 null（keeper/旁观连接无行动
   调查员；玩家连接仍为非空字符串）。

另：上线信封不携带路由 `audience`（envelope_base 无此字段，且定向接收者列表本身
即私密信息）；`message_started`/`message_completed` 载荷内的 `audience` 是消息自身
属性，照常下发。

M2 协议增补（zcode 侧需要跟进）：

1. 新帧类型 `cancel_request`（玩家取消自己 queued 的请求）；前端无需新事件类型，
   取消结果由既有 `action_status{cancelled}` 承载。
2. `request_check` 载荷新增可选 `push_for`（孤注一掷）；`check_requested` /
   `pending_check` / `check_resolved` 同步携带可选 `push_for`，前端检定卡可据此
   显示“孤注一掷”徽标与升级代价。
3. 出示 `presentation=image` 需要已授权素材（`presentation_requires_asset`）；
   快照 `known_clue.presentation` 现在按接收者投放可用出示方式（describe 恒有，
   image 仅在素材已授权后出现）。

M3 协议增补（zcode 侧需要跟进）：

1. 第 15 种主持命令 `resolve_draft`：`{draft_id, decision: approved|rejected|edited,
   note?}`，收尾 assisted 草稿（见 §3.4）。权限矩阵已含 `command.resolve_draft`
   （keeper/agent）。
2. 新事件 `keeper_draft`（assisted 产出，audience=keeper；载荷含 draft_id/summary/
   proposed_commands/narration/可选 request_id）与 `keeper_draft_resolved`
   （draft_id/decision/note?）。两者在 M0 冻结的 events.json 中已定义，本轮起真正产生。
3. Agent 触发语义见 §2.2 末段：仅 action_request / check_response 触发；
   agent 运行产生的事件在房间场景只经 broadcast 按各连接 principal 过滤投递，
   不回溯发起玩家的连接。

M4 协议增补（生命周期：分支 / 读档 / 续团）：

1. 分支：本地 `turn_branch_create` 对 structured_v1 世界忽略 `turn_id`，
   从**当前已提交状态**分叉（无 Turn 概念）。响应仍为 `turn_branched`
   （`source_turn_id=""`、`history=[]`），随后必收到新的 `session_snapshot`。
   分支复制控制面（execution_profile/keeper_mode/成员 can_keeper/调查员认领）、
   非终态待办与幂等账本；**不复制** outbox（游标从 0，前端按快照重同步）与
   keeper_control（分支以无人掌控开始，旧 agent epoch 不跨界）。
2. 读档：本地 `load` / `save_load` 对结构化世界走 CAS 回滚 + 同事务 reconcile
   （非终态请求一律 `failed` 可用原 request_id 重发、pending 检定做废、晚于
   存档点的 outbox 事件删除），随后下发 `loaded` + 新 `session_snapshot`。
   **不得**沿用读档前的 revision/游标。房间模式 `save_load` 仍拒绝
   （`structured_required`），云端房间读档入口待与前端另行约定。
3. 续团：结构化世界有任意结构化活动（请求/命令/检定）即在存档位列表
   可见且 `resumable`（重连取快照即续团，不依赖 SaveSlot）；分支写入
   slot_000 使时间线列表兼容旧 UI。
4. `world_switch` 到结构化世界：`world_switched.history=[]`，随后收到
   `session_snapshot`；不会有消息历史帧。

## 10. 当前交互与角色长期记忆（2026-09-14 扩展）

背景与根因：`docs/evidence/transition_real_model/S3_ROOT_CAUSE_FOR_BACKEND.md`——
请求进入终态后「已讨论的目标」消失，模型只能从自由文本重新推断。本扩展补第 2 层
（当前交互）与第 4 层（角色长期记忆），均属**记录**，不构成执行授权。

### 10.1 交互线程（interaction_threads）

- 创建路径只有两条：`resolve_intent{resolution:"awaiting_player"}` 自动开/续线程
  （awaiting 记录带 `thread_id`）；或任何 resolve_intent 显式携带
  `thread{action:open|continue|close|replace, thread_id?, pending_action?, disclosed?, waiting_on?, note?}`。
- `pending_action` 与 awaiting 同形（kind/note/target/destination_scene_id）；
  `waiting_on` 为调查员 ID / `party` / `keeper` / 空。
- 请求进入终态**不会**自动关闭线程（这是与 awaiting 待办的刻意差异）；
  线程只能由主持显式收尾，或：移动命令抵达其 `destination_scene_id` 时自动
  收尾为 completed（记录收尾，方向永远是命令→线程）；玩家取消请求时联动取消
  其关联线程（状态联动，非文本推断）。
- 写入线程的 resolve_intent 会推进世界 revision（读档截止依据）；不碰线程的
  resolve_intent 维持不推进。
- 事件：`interaction_updated`（audience 定向所属调查员，主持可见）。
  快照 `session_snapshot.payload.interactions[]` 投影开放线程（本人/主持可见）。
- 分支复制开放线程；读档 fail-closed——存档点后创建的线程删除，其余开放线程
  一律 cancel（与 player_requests 的读档契约一致）。

### 10.2 角色记忆（character_memories）

- 每行 = 某角色的一条记忆：`character_id/character_kind(investigator|npc)` +
  `knowledge_type(experienced|told|rumor|belief)` + 内容/场景/主题/来源 +
  `superseded_by`（更正链）。**传闻与推测永远不是事实**。
- 确定性派生：仅来自已提交的命令/事件（move_party→全员亲历抵达、
  grant_clue→被授予者被告知、handout_presented→查看、check_resolved→检定经历、
  use_item/transfer_item/adjust_stat→对应经历）。派生在命令事务提交后独立运行，
  失败只记日志不丢已提交事实；`derivation_key` 幂等，可用
  `memories.repair_derivation` 补建。未执行的计划、被拒命令、未落账叙述不进记忆。
- 主持显式记录：`record_memory{character_id, knowledge_type, content, ...}`，
  `supersedes` 更正旧记忆；事件 `memory_recorded` 为 keeper 定向（不进玩家投影）。
- 主持侧只读查询：`memory_query` 帧（keeper 专用；玩家调用一律
  `not_authorized`），结果以 `memory_query_result` 事件返回；
  服务层 `query_memories` 对玩家仅放行其控制的调查员。
- 读档：`created_revision` 晚于存档点的记忆删除（不泄漏未来知识）；存档点之后
  被取代的记忆还原为 active（更正发生在被回滚的未来）。分支复制分叉点全部记忆，
  之后按 world_id 隔离。

### 10.3 Agent 上下文与预算

Agent 上下文新增 `open_threads`（含 `candidate_for_trigger` 状态匹配候选）、
`trigger_context`（无候选时 `candidate_thread_ids` 为显式空数组）与
`character_memories`（自动小预算注入：在场角色 × 当前场景，≤8 条 ≤800 字符）。
决策契约新增只读 `queries[{kind:"memory", ...}]`：每运行 ≤3 次、结果 ≤800 字符，
超预算明确拒绝并回喂。必需区（权威状态/当前交互）不依赖检索、不被记忆挤占。
