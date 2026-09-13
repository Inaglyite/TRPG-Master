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
| `schemas/structured-play/v1/free_roll_request.json` | 普通掷骰请求（受限表达式） |
| `schemas/structured-play/v1/check_response.json` | 待检定卡回应（roll / decline，不携带参数） |
| `schemas/structured-play/v1/command_request.json` | 主持命令信封 + 14 种命令的严格载荷 |
| `schemas/structured-play/v1/events.json` | 事件信封 + 19 种事件载荷（含 session_snapshot / server_capabilities） |
| `schemas/structured-play/v1/permission-matrix.json` | 权限矩阵机器可读正本 |
| `schemas/structured-play/v1/fixtures/` | 46 个正例 + 7 个反例 |
| `tests/test_structured_play_protocol.py` | 双向校验与一致性门禁（7 项 + 73 子项） |

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

### 3.3 命令（game_commands）

命令是即时事务，无异步状态：`submitted → committed | rejected | conflict`。
committed 附带领域 result（success/failure/not_executed 及明细）与新 revision；
rejected/conflict 不改变状态、不发布权威事件（只回 `request_error` 给调用者）。

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
     `stack_key`（重名物品按持有者与获取次序区分）、`legacy_label`、`operations`。
   - 线索条目：`clue_id`（模组目录有 catalog_id 用之，否则 `clue_` + 稳定散列）、
     `legacy_key`（category+text 拼接原键）、`category`、`text`、`grants`（知情授权列表）。
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
