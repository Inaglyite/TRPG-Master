# 过渡节拍专项：最终两项检查收口（2026-09-13）

承接 ADVISORY_TRANSITION_FIX_20260911.md（"查看遗体被再次劝留"事故专项）。
本轮只做两项收口检查与对应修复；不扩展多日监视机制，未触碰生产与 Pi。

## 一、组合验证：旧快照分支 + 法伦不在场

### 结论：矛盾成立，已修复

从旧快照建分支的世界（`seed_from_snapshot`，模组文件版本已一致）只享受条目级
升级，而 09-11 的条目级迁移只覆盖 `action_advisories`。组合后果：

- advisory 被升级：法伦不在场时回落节拍是新 `keeper_text`
  （"停尸房不对外开放：按程序，你得先经院方值班人员许可才能查看遗体。"——
  前置事实是"还没有安排"）；
- `miskatonic_medical.entry_beat` 仍是旧措辞
  （"惠特克罗夫特医生正攥着病历夹**等候**……向你示意"——隐含"有人替你安排过"）。

两条文本在同一回合相继播出，模型必须解释"没有安排，医生却在等你"，正是原事故
里它补出"法伦主任打过招呼了"的同款臆测压力。组合矛盾成立。

### 修复：entry_beat 精确旧值匹配升级

- `SceneEntryBeatDefinition` 新增 `supersedes`（最多 3 条，每条是历史版本的完整
  `entry_beat` 对象，校验非空对象）；猩红文档模组把旧措辞逐字存档进
  `miskatonic_medical.entry_beat.supersedes`。
- 新增 `transition_prelude.upgrade_legacy_entry_beats(state, template)`：与
  advisory 同一套语义——世界里只有与声明旧载荷**逐字一致**的入场节拍才被替换；
  改写措辞、换 NPC、增删字段一律视为手改而保留；模板未声明 `supersedes` 时不动；
  升级后世界副本不带 `supersedes`，幂等。
- `RuntimeContext.sync_module_metadata()` 现在同时驱动两类条目级升级，返回
  `scene_id/advisory_id` 与 `scene_id/entry_beat` 标识。
- 两个 JSON Schema（v1/v2）随模型重新生成，`tools/check_architecture.py` 通过。

对偶验证（`tests/test_action_preflight.py::LegacyEntryBeatUpgradeTests`，
`tests/test_discovery.py` 组合用例）：

- 逐字命中才升级、幂等、手改保留、模板未声明不动、世界没有的场景不动；
- 真实模组数据：旧载荷在 `supersedes` 里逐字命中；
- **组合用例** `test_legacy_branch_world_with_absent_fallon_announces_no_arrangement`：
  同时降级 advisory 与 entry_beat、清空法伦在场，升级后跑回合——播出的是守秘人
  回落节拍 + 新接待节拍（"攥着病历夹站在那里"），流式文本与模型提示词里都没有
  "等候/拨通内线/知会/打过招呼"，且移动正常结算。

## 二、arrival 计划建立后、移动结算前的拒绝路径

### 修复前的问题

`agent_graph._prepare_turn_inner` 旧的顺序是"先宣布、后结算"：非阻塞过渡节拍
（含"法伦已替你拨通内线"这类通知事实）在 `_resolve_scene_transition` **之前**
流入叙事流，而 `_resolve_scene_transition` 有三条静默返回 `None` 的拒绝路径
（世界不可读、目的地不在目录、`state_set` 未确认）以及一条异常路径（遭遇解析/
掷骰失败），调用方原本忽略返回值。后果：通知已向玩家宣布、赶路文本已播，
权威状态却没有移动；异常路径下回合失败重试还会把节拍再播一次。

### 拒绝路径清单（计划建立 → 结算完成）

| 路径 | 位置 | 修复前 | 修复后 |
| --- | --- | --- | --- |
| 目的地归属校验失败 | 规划期 `adjudicate_player_action` → `DestinationGroundingError` | 计划本身降级为 clarify/INTERACTION，从未成为 arrival：无节拍、不移动 | 不变（一致） |
| 危机抢占（伏击/显形） | prepare 开头替换为 INTERACTION 决议 | preview 匹配在替换之后：无节拍、不移动 | 不变（一致） |
| 阻塞卡取消/超时 | `skip_agent` ⇒ `transition_id=None` | 只播卡片文案，不播节拍、不移动 | 不变（既有测试覆盖） |
| 阻塞卡 replace 重计划 | 新计划不重匹配 preview | 新计划若命中别的 advisory 不播其节拍——无错误宣布，但节拍可能缺失 | 不变（登记为已知限制，无矛盾） |
| 结算静默拒绝 | `_resolve_scene_transition` 返回 `None` | **节拍已宣布、状态未动** | 不播任何节拍，回合失败（`RuntimeError`），可原样重试 |
| 结算抛异常 | 遭遇解析/掷骰失败 | **节拍已流入叙事**，失败重试会再播一次 | 节拍在结算后才宣布，失败回合干净，重试只播一次 |
| 结算后场景改写 | 后置审计 `turn_reconciler` 有场景写权限 | 既有通道，不在本轮范围 | 登记为边界，未改 |

### 修复：先结算，后宣布

`agent_graph` 现在先执行 `_resolve_scene_transition`，确认返回的目的地与计划一致
后才按"过渡节拍 → 赶路 → 遭遇 → 抵达接待"的既有展示顺序播出；任何结算失败
（返回 `None` 或抛异常）都直接让回合失败收口，节拍、赶路、抵达描述一律不播，
并记录诊断日志（`场景移动未结算 | 计划抵达=… 结算结果=…`）。

**通知与移动的执行顺序契约**：移动（含遭遇与在场 NPC）先落账，"已通知/已安排"
类事实后宣布；展示顺序不变。`entry_beat` 照旧只在对应 NPC 抵达结算后确实在场时
显示。场景 handout 由结算内部的 `state_set` 触发，且只在 `state_set` 返回
`ok: true` 时派发（本轮补的守卫，与 `state_add_clue` 对齐）。

**保证范围（按独立复核修正，见
ADVISORY_TRANSITION_INDEPENDENT_REVIEW_20260913.md）**：这里的"先结算"是回合
**工作缓存**（`world_store.turn_cache()`）内的结算，不是数据库持久提交。持久提交
发生在 finalize 的 `journal.complete`（ WorldState+Turn+Snapshot+自动存档一个事务），
之后才 `accept_turn_commit`。因此本轮保证的是：**过渡事实被宣布 ⇒ 移动已在工作
缓存中结算成功**（不再出现"宣布成功但移动被静默拒绝"的矛盾）；它**不**保证
"宣布 ⇒ 已持久化"——提交前故障（如 `journal.complete` 抛错）会丢弃工作状态，
而节拍/handout 已通过流式回调送达玩家。这是整个流式回合管道的既有边界（模型叙事
本身也是提交前边生成边推送），不是本轮新引入的窗口。该边界下的补偿性质已钉成
契约测试：提交失败 ⇒ 持久状态不变、回合记录失败、重试恰好结算一次
（`test_arrival_commit_failure_rolls_back_and_retry_settles_once`，真实
handle_action + 临时数据库 + 独立连接验证）。提交成功后再推送失败由既有
journal/recovery 重放覆盖（不重结算），未新增测试。

后续方向（本轮不做，复核建议 2/3）：在既有原子回合事务边界内缓冲权威过渡事件与
素材、提交成功后按序发布；或设计明确的暂定/作废协议。不得用提前 `flush_turn`
拆阶段提交的方式当局部补丁——那需要同时处理时间、遭遇、回合记录、快照与重试
语义的独立设计。

对偶验证（`tests/test_discovery.py` 新增）：

- 结算返回 `None`：不播节拍、不发 handout、状态不动、回合 `RuntimeError`；
- 结算抛异常：异常原样上抛、不播节拍、状态不动；
- 顺序用例：节拍到达玩家回调时权威状态已完成移动（`settle → beat → 已在新场景`）；
- 阻塞卡选择继续后结算失败同样 fail-closed。

## 三、交付声明

- **事故专项通过**：停尸房路径两轮全流程均 ✓，事故状态副本真实模型单回合复测
  无劝留、无矛盾（09-11 记录）；本轮两项组合/顺序检查均转为确定性测试。
- **全流程两轮各 32/33，未达到全绿**：失败 beat 不同（B5b_clock、B4_sanatorium），
  四个全局检查（保密、未知线索、弹药、结局）两轮均通过，详见 09-11 文档第四节。
  本轮改动后未重跑全流程真机（费用约束），发布前需按 scarlet-playthrough-check
  在冻结提交上补一次。
- **整体静态刷新覆盖自定义 `scene_catalog` 是既有未解决限制**：模组文件版本变化时
  `refresh_static_handout_config` 仍整体替换场景目录，世界内手改（含自写 advisory、
  自写 entry_beat）在该路径下没有保护。`supersedes` 逐字匹配只覆盖"版本已一致"的
  分支/存档路径——这次 entry_beat 升级**没有**改变整体刷新路径的行为，两部分不能
  混为一谈。契约测试 `test_stale_module_revision_refresh_replaces_the_whole_scene_catalog`
  继续钉住该行为。
- 本轮测试：定向 349 项通过（discovery/action_preflight/module_packages/
  world_branches/runtime_integration/turn_resolution/action_adjudication/
  destination_grounding/handouts/module_v2/module_migrations/context_shadow/
  game_application/multiplayer_membership/outcome_contract_gates/turn_journal/
  ws_turn_gate），ruff 与架构门禁通过。应费用约束未跑全量。
- **独立复核（ADVISORY_TRANSITION_INDEPENDENT_REVIEW_20260913.md）已处理**：
  其主要发现（"先结算后宣布"只覆盖工作缓存、不覆盖持久提交）属实，保证范围已按
  上文修正；本轮补了 `state_set` handout 的 `ok` 守卫与提交失败回滚/重试契约测试；
  "提交成功后按序发布权威事件"的缓冲方案按其建议 3 留作独立设计，本轮不做局部
  补丁。

## 四、问题登记（各自独立，不以"运行波动"结案）

### R1 处所式异地行动拒行（既有机制缺口，本轮登记、未实现）

"接下来几天，我白天在古董店对面的咖啡馆监视……"这类**处所式**输入：句子没有
移动动词 ⇒ 不是移动；提到其他场景且耗时 >60 分钟 ⇒ `validate_proposal` 拒绝原地
结算。结果是死路：只能玩家改口或模型给选项。取证见 09-11 文档第六节（六回合
`not_executed`、时间 355→355）。涉及组件（`action_checks.infer_scene_transition`、
`action_adjudication.validate_proposal`）本轮未动。设计建议见第五节。

### R2 亨特复制件取得停滞（登记）

09-11 最终版 run 第 13 回合起，"接过复制件"被裁决 `executed_failure`（亨特拒绝、
护理结束探视）后，harness 连续 7 回合重复同一做法，裁决每次维持拒绝，
`discovery_refs` 为空 ⇒ `hunter_copy` 未入册。拒绝本身是有效裁决（机制链自洽），
但引擎缺少"僵局出口"：当同一互动被连续拒绝时，没有向玩家给出换做法/付出代价/
放弃的具体通道。与 A06 重复检定闸门相邻但不等同（A06 管重复掷骰，这里是重复
社交索取）。登记为独立问题，后续单独定性，不归入"运行波动"。

## 五、处所式异地行动设计建议（本轮不实现）

### 原则：依据玩家意图规划移动，禁止"时长>60分钟+唯一地点"硬触发

时长与地点提及都不是移动意图的充分条件。若用"`>60 分钟` + 恰好点名一个场景"
直接触发移动，下列反例全部误伤：

- **否定移动**："我才不去那家古董店，太邪门了。"（地点 + 否定）
- **讨论异地**："聊聊古董店吧，我怀疑老板有问题。"（谈论 ≠ 前往）
- **历史回顾**："上周我在古董店对面蹲了两天，什么都没看到。"（过去时 + 长时长）
- **假设/计划**："如果我去古董店蹲点，你觉得能发现什么？"（虚拟语气）
- **多目的地**："先回旅馆放行李，然后到古董店对面守着。"（只有第一段是移动）
- **远程交互**："给报社打个电话，问问古董店的来历。"（涉及异地但不移动）

### 建议形态

1. **意图结构化**：由裁决层（非正则）输出 `relocation` 意图：
   `relocate_and_act`（去那里做）/ `discuss_place` / `negate_move` / `recall` /
   `hypothetical` / `remote_interact`。只有 `relocate_and_act` 规划 arrival；
   目的地归属复用 `destination_grounding` 的唯一性/歧义校验。正则守卫
   （`_MOVE_ACTION` 等）保留为确定性兜底，但不再独自决定"处所式"句的去留。
2. **分步执行**：抵达与后续活动分两拍。第一拍 arrival 回合只结算移动 + 赶路时间 +
   抵达描述（沿用现有 `arrival_only` 语义），后续活动记为权威状态里的
   `pending_intent`；第二拍（玩家确认或下一回合）才结算监视/蹲守本身。
   绝不在同一回合既移动又结算多日停留——`validate_proposal` 的"未抵达不得结算
   异地停留"硬约束保留。
3. **时间结算**：停留类活动的时长由模型提议、引擎结算（`advance_time` +
   时间型案件时钟），设上限与代价；`narrative_consistency` 的时间一致性检查需
   扩展覆盖"几天/数日/一连几天"等中文时段措辞，堵住"结算 4 小时、叙事过了三天"
   的漂移。
4. **剩余行动提示**：抵达回合的选项区固定给出下一步（如"开始监视（耗时数小时）"），
   让玩家不必猜魔法措辞；`pending_intent` 存在时优先列出对应动作。
5. **澄清而非死路**：意图无法归类时，裁决给 `clarify` 并在选项里固定包含
   "前往<该场景>"与"留在原地谈论它"两条，替代目前纯文本澄清。

实现落点（下一轮）：`ActionProposal` 增加结构化 `relocation` 字段；
`validate_proposal` 校验；arrival 结算后把 `on_arrival_intent` 写入权威状态，
下回合优先消费。harness 不需要为此改输入。
