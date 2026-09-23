# 结构化操作平台后端：验收与交付记录

> 历史归档（2026-09-16 整理）：正文保留当时的结论与适用版本，不作为当前完成状态或执行授权。现行入口见 [README](../../../../README.md)，未完成事项见 [STATUS](../../../STATUS.md)。正文中的仓库路径按仓库根目录理解，`/tmp` 产物不保证仍存在。

日期：2026-09-14；分支：`experiment/keeper-platform`；范围：`src/`、`server.py`、`migrations/`、
`schemas/structured-play/v1/`、后端 tests、协议文档与 Agent 提示词（前端由 zcode 负责）。

主规格：`docs/archive/2026-09/plans/STRUCTURED_PLAY_PLATFORM_PLAN_20260913.md`；协议正本：
`docs/reference/STRUCTURED_PROTOCOL_V1.md` + `schemas/structured-play/v1/`（字段级以 JSON Schema 为准）。

## 1. 分阶段完成项（冻结提交链）

| 提交 | 内容 |
|---|---|
| bf0c269 | 实验基线（承接 master 未提交改动，单独成 commit 便于对照回退） |
| 912e11c | M0 协议冻结：schema + 50 正例/7 反例 fixtures + 权限矩阵 + 协议文档 |
| 39db0c2 | M1 命令服务核心：迁移 0015（5 表 + can_keeper）、`src/structured/` 服务/领域/检定/注册表 |
| 418c6bd | M1 接线：本地 /ws + 房间分派、快照下发、旧回合门禁、结构化房间开局（无模型） |
| 0e10105 | M1 引擎门禁：structured_v1+human 无 Key 开局（`build_engine_client` 返回 None） |
| 09db2f9 | M1 修补：房间开局物化调查员名册（character_key 标识空间），无人认领拒开局 |
| 45a0352 | M2：出示素材授权、孤注一掷（push_for/push_pending/pushed）、time_cost 结算、玩家取消、failed 重发 |
| f6722a6 | M3：KeeperAgentRunner 决策循环（预算/epoch/接管）、assisted 草稿 + resolve_draft、BYOK fail-closed、触发接线 |
| f1acafb | M3 修补：房间 agent 事件不排除发起玩家；BYOK 缺失降级先绑 epoch（人类在控不抢占） |
| 5ff7241 | M4：结构化分支（当前已提交状态）/读档 reconcile/续团列表可见性 |

## 2. 协议与 fixtures 冻结

- `schemas/structured-play/v1/`：common / action_request / cancel_request / free_roll_request /
  check_response / command_request（15 种命令）/ events（29 定义）/ permission-matrix。
- fixtures：50 正例 + 7 反例；`tests/test_structured_play_protocol.py` 双向校验
  （每个命令/事件有 fixture、权限矩阵覆盖全部命令与角色、fixture 通过 schema 校验）。
- 协议文档 §9 按里程碑维护给前端的差异/增补清单（M1 修正、M2/M3/M4 增补）。

## 3. 测试证据（分层标注）

**mock/单元与数据库集成（离线）：**
- 后端全量：1297 passed, 7 skipped, 116 subtests（2026-09-14，约 3 分钟）。
- 结构化定向 9 个文件 73 项 + 79 子项全绿，含：
  - 提交边界故障验证用**独立数据库连接**（非同一 store 缓存）确认提交前故障零可见变化；
  - 幂等（同 ID 同载荷重放/异载荷拒绝）、revision CAS、权限矩阵、epoch 接管失效；
  - Agent 循环（fake caller 脚本化决策）：模型失败保留已提交命令、预算耗尽 paused、
    无进展停止、解析失败 2 次 paused、人类接管 takeover_stopped。

**真实服务（非 mock）：**
- `tests/test_structured_ws_smoke.py`：真实 `server.app` + TestClient WebSocket 全闭环
  （快照→行动请求→命令→事件→非法帧→读档恢复快照）。
- `tests/test_structured_room.py` / `test_structured_ws.py`：真实 SQLite 库 + 房间枢纽假连接。

**三客户端 UI（zcode 前端 + 真实后端，2026-09-14 实测）：**
- Playwright 结构化 E2E 全组 **12/12 通过**（约 2.3 分钟）：
  - `structured-human-3p`：一位主持 + 两位玩家真实后端闭环——建房（勾选结构化）→
    邀请 → 选角（主持不占角色）→ 开局 → 定向私发线索（乙不可见）→ 持久检定卡掷骰 →
    SAN 调整 → 整队移动 → 存档入口 + 刷新重连恢复；全程零模型调用。
  - `structured-real-integration`：真实后端 + structured_v1，快照驱动界面、按钮结构
    请求落账、零模型调用。
  - `structured-solo-online`：云端单人勾选结构化后无 Key 开局，按钮发结构请求。
  - `structured-play`：出示/检定/前往/掷骰均发 M0 信封；freeform 不退回文字通道；
    提交冲突位置不变、可同 ID 重试；协议不支持的世界不出入口也不退回文字发送。
  - `structured-real-backend`：legacy 世界无结构化能力时不出新入口、旧模式不受影响。
  - `structured-screens`：桌面/窄屏/长名称/主持台截图（`docs/screenshots/structured-*.png`）。
- 前端协议层单测 **114/114 通过**：`structured.test.ts`、`keeper-commands.test.ts`、
  `m0-fixtures.test.ts`（后端 fixtures 互测）、`structured-transport`/`structured-routing`/
  `room-structured`/`StructuredActionDialog`。

**真实模型：** 未执行（需用户额度授权；本批未获授权，遵守"没有适用授权则先完成离线工作"）。

## 4. 主规格 §12 验收矩阵逐行状态（后端侧）

| 类别 | 状态 | 证据/说明 |
|---|---|---|
| 意图 | 机制就绪，真机待验 | 表达意愿不过关键词直接抵达（结构化世界无 infer 自动移动）；明确前往才由 move_party 落账；"那就过去"承接/歧义澄清属 Agent 决策职责，已写入 `agent_prompts.py` 契约，真实模型验证未做 |
| 出示 | 通过 | M2：无原件可说明、展示不转移、缺原件/缺素材/无权对象拒绝（test_structured_m2） |
| 道具 | 通过 | use_item 数量/目标事实检查；重复点击幂等不双扣；失败保留真实代价 |
| 骰子 | 通过 | free_roll 不触发剧情（触发测试）；指定玩家限制、代点拒绝、重复响应返回原结果（幂等） |
| 检定 | 通过 | 创建即持久化；恰好结算一次；相关条件变化作废；取消/超时不自动掷；孤注一掷规则约束 |
| 移动 | 通过 | 成功才提交并发布；拒绝无权威事件保持原地；抵达不自动检查；结构化世界关闭关键词自动移动（效果所有权） |
| 隐私 | 通过（一处已知限制） | keeper/owner 分离；定向事件服务端过滤；快照按 principal 投影；素材授权后发布。已知：云端 keeper 同时是玩家时连接 principal keeper 优先，会看到主持秘密（见 §7） |
| 并发 | 通过 | 请求/命令幂等；revision 冲突；控制权每次复核；接管旧 epoch controller_epoch_stale |
| 持久化 | 通过 | 提交前故障无权威结果（独立连接验证）；推送失败按游标重放；模型失败不重掷/重扣/重移动 |
| 生命周期 | 通过 | 无 Key 人类开局/续团（引擎门禁 + 列表可见性）；模型故障 paused 不影响主持台；分支恢复待办并绑定新世界权限（test_structured_branch） |
| 兼容 | 通过 | 旧快照一次性迁移稳定 ID 注册表并持久化；旧模式全量回归绿；分支/读档不覆盖自定义内容 |
| UI | 通过（zcode 侧实测） | E2E 12/12（桌面/窄屏/长名称/主持台截图、三客户端闭环）；前端协议单测 114/114 |

## 5. 迁移与恢复说明

- **迁移 0015**（`migrations/versions/20260913_0015_structured_play.py`）：新增
  `player_requests`/`game_commands`/`check_requests`/`event_outbox`/`keeper_control`
  五表 + `world_members.can_keeper` 列；纯扩展兼容，旧库升级不影响既有表。
  回滚程序版本≠降级数据库：旧版本读取含新表的库无冲突（不读不写），但
  `can_keeper` 以外的结构化状态对旧版本不可见。
- **稳定 ID 迁移**：旧世界的字符串背包/线索在首个结构化命令/请求时一次性迁移为
  `item_registry`/`clue_registry` 并随状态持久化；幂等；只新增不重置。
- **分支**：结构化世界从当前已提交状态分叉（无 Turn）；复制控制面 metadata、
  成员授权、调查员认领（复制非搬走）、非终态待办、pending 检定与幂等账本；
  不复制 outbox/keeper_control；写 slot_000 兼容旧列表。源世界 pin 不可复制时
  创建整体失败（不生成会按新磁盘独立 pin 的分支）。
- **读档**：`restore_structured_save` CAS 回滚 + 同事务 reconcile（非终态请求
  failed 可原 ID 重发、pending 检定做废、晚于存档点的 outbox 事件删除）；
  keeper_control 保留（epoch 使迟到调用失效）。
- **续团**：结构化世界重连即快照全量重同步；有结构化活动即列入存档位且
  resumable，不依赖 SaveSlot。

## 6. 给前端（zcode）的联调说明

1. 协议字段一律以 `schemas/structured-play/v1/` 为准；里程碑增补见协议文档 §9
   （M2 cancel_request/push_for/image 出示授权；M3 resolve_draft/keeper_draft 事件/
   触发语义；M4 分支/读档/切换的快照重同步约定）。
2. 本地时间线操作已分流：`turn_branch_create`（结构化世界忽略 turn_id）、
   `world_switch`、`load`/`save_load` 后都会收到新的 `session_snapshot`，
   前端不得沿用旧 revision/游标。
3. 房间结构化帧事件按连接 principal 过滤；agent 产生的事件发起玩家也会收到。
4. assisted 模式：`keeper_draft` 事件仅 keeper 可见；批准/拒绝用
   `resolve_draft`；批准不代执行，主持以各自 command_request 幂等提交。
5. agent/assisted 世界 BYOK 未配置时：触发请求被置 paused（action_status 可见），
   前端应提示房主配置模型或改人类主持，不应静默重试。

## 7. 未解决问题（明确登记，不以"运行波动"代替）

1. **真实模型验收未跑**：Agent 决策质量（意图承接/歧义澄清/不擅改按钮 ID/
   无进展停止）只有 fake caller 验证；需用户授权额度后在独立测试世界验收。
2. **云端 keeper 兼玩家的秘密泄露**：连接 principal keeper 优先，若同一人
   兼玩家会看到主持秘密；需要"主持视角/玩家视角"显式分离（后续里程碑）。
3. **rate_limited 未实现**：错误码预留，限流策略未接；request_error 无频率限制。
4. **云端分支/读档入口未接**：本地 WS 已分流；云端 solo_timeline_ws 的分支
   仍走旧 Turn 路径（结构化世界会失败），房间模式 save_load 仍拒绝。
5. **agent 模式无主动开场**：首次触发依赖玩家首个 action_request/check_response；
   如需开场叙述，另需 start 触发设计（与前端约定）。
6. **读档 reconcile 是 fail-closed 语义**：读档后所有非终态待办一律 failed
   （可原 ID 重发恢复），不尝试恢复存档点时刻的请求状态（无历史快照可查）。
7. **既有基线改动归属**：工作区仍有他人/历史未提交改动（部分 tests、
   packaging、requirements.txt），本交付未提交这些文件。
