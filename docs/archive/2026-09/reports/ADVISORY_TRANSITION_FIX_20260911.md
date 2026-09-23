# 事故修复：过渡节拍被当成决策卡重播（2026-09-11）

> 历史归档（2026-09-16 整理）：正文保留当时的结论与适用版本，不作为当前完成状态或执行授权。现行入口见 [README](../../../../README.md)，未完成事项见 [STATUS](../../../STATUS.md)。正文中的仓库路径按仓库根目录理解，`/tmp` 产物不保证仍存在。

## 一、事故

世界 `local-猩红文档-recovery-e4963b`，回合
`turn_20260911T054445727977Z_e45e9387`，输入
「起身前往密斯卡托尼克大学医学院，交涉查看莱特的遗体。」

最终叙事在玩家**已经明确出发**之后，又出现法伦的劝留台词
（"进去之前，你也许愿意先听听我对这件事的看法。"），随后同一段叙事先说
"他没有立刻打电话"、抵达后医生又说"法伦主任打过招呼了"。

实际状态并无异常：`current_scene=miskatonic_medical`，
`body_examined=false`，SAN 未变，未发放 `wright_body_evidence`
（裁决只结算了 `time_advanced 0→20`，`arrival_only=true`）。问题全部出在
"出发素材"这条通道上。

## 二、根因

四个环节叠加，缺一不可：

1. **模组** `wright_body_handoff` 是 `blocking: false`，但 `npc_text` 写成有条件的
   劝留（"我得先替你知会惠特克罗夫特医生……进去之前，你也许愿意先听听我对这件事
   的看法。"），`public_hint` 又是"『你可以立即前往，也可以先向法伦了解更多情况。』"
   —— 这条分支提示的本质是**再选一次**。
2. **`src/gameplay/action_preflight.py`** 把 `npc_text`（或 `keeper_text`）和
   `public_hint` 拼成唯一的 `narrative`，决策卡与过渡素材共用同一个字段。
3. **`src/app/agent_graph.py`** 的非阻塞分支把整段 `narrative` 塞进
   `preview_material`，以"玩家表明意图后、出发前，在场人物的回应大意"注入，并要求
   模型"先让该人物在原地以其性格完整回应玩家……再写赶路"。于是引擎亲手把一张
   已经被玩家跨过的决策卡，重新演成了出发前的对话；模型又必须解释医生为何在等，
   于是补出"法伦主任打过招呼了"——而同一回合里它刚写过"他没有立刻打电话"。
4. **旧快照**：场景目录（含 `action_advisories`）随世界状态存档。分支续玩
   （`WorldBranchService.create` → `seed_from_snapshot`）直接继承源回合快照，
   只改模组文件覆盖不到这类世界。

事实来源核对：本回合**没有任何**记录联系医生（裁决 `npc_direction` 为空、
`events` 只有赶路时间），医生的"打过招呼"是模型为弥合 `entry_beat`
（"医生正攥着病历夹等候"）与缺失的前置事实自己补的。模组其实已经声明了正确的
事实来源：`miskatonic_university.action_routes.contact_whitcroft` 的
`departure_text`（"法伦拨通医学院的内线……示意惠特克罗夫特医生会在停尸房等候。"）
——但只有玩家显式说"请法伦联系医生"才会走到那条路线，`discovery_target` 这条
入口没有任何已结算的通知事实。

## 三、修复

### 3.1 引擎：两种形态彻底分开

`action_preflight.ActionPreview` 新增 `transition_text`：

- `blocking: true`（默认）＝决策卡，行为不变：`npc_text`/`keeper_text`/`public_hint`
  + 选项，`cancel_action` 仍是超时默认，产出与之前逐字一致。
- `blocking: false` ＝**过渡节拍**：不生成卡片、不生成选项（`options=()`），只取
  `transition_text`；`narrative` 也只等于这条节拍。声明了 `npc_id` 而该 NPC 不在场时
  改用 `keeper_text` 作为回落节拍。
- 迁移前形态（`blocking: false` 且没有 `transition_text`）在匹配阶段**直接跳过**：
  宁可不提醒，也绝不把卡片文案降级成出发素材。
- 决策卡文案绝不可能再进入非阻塞通道：`public_hint`、选项标签、`prepare_options`
  在非阻塞分支里根本不读取。

`agent_graph` 的非阻塞分支把节拍**预播**给玩家（与 `departure_text` /
`travel_text` / `entry_beat` 同一类作者节拍），不再注入为"模型素材"：

```
过渡节拍（作者态，已结算） → 赶路 → 抵达描述 → entry_beat → 故事模型续写
```

预置叙事段的提示词框架也从"已向玩家展示"明确为
**"引擎已结算的既定事实"**：不得改写或否认其中"谁在场、说没说过、联系没联系、
去过哪里"，只从节拍之后继续。这是一条通用的框架收口，不是给猩红文档打的补丁，
也没有屏蔽任何普通词语——NPC 仍然可以给建议、可以表达保留意见，只是不能把
已经做出的决定再要一次。

### 3.2 模组：过渡文案改为已成立的事实

`wright_body_handoff`：`blocking: false` 保留，新增 `transition_text`
（法伦当场替你拨通医学院内线、并提示死亡证明是惠特克罗夫特签的），
`npc_text` 去掉劝留句（只保留"进去之前我得先替你知会"这一必要条件），
`public_hint` 改为事实性提示"『进入停尸房需要院方值班医生的许可。』"，
`keeper_text` 改为未完成现状"停尸房不对外开放：按程序，你得先经院方值班人员许可"
——即法伦不在场时，引擎给出的前置事实是"还没有安排"，医生就不能被断言"已收到通知"。
阻塞卡字段（`continue_label` / `prepare_options` / `cancel_label`）保留，
"先问法伦"仍然是一条合法选择（回到原场景交谈，不移动）。

联系医生的事实来源由此明确：本回合**完成联系**（法伦在场，节拍已预播）时前后一致；
法伦不在场时前置事实是"还需现场交涉"。

### 3.3 旧快照兼容

新增 `action_preflight.upgrade_legacy_advisories(state, template)` 与
`RuntimeContext.sync_module_metadata()`：

- 只升级**仍未声明 `transition_text` 的 `blocking: false` 条目**；
- 且要求该条目与模组模板里同名 advisory 声明的 `supersedes` **逐字完全一致**
  （`supersedes` 是作者在模组里存档的官方旧载荷，猩红文档的那份直接从事故快照导出）；
- 阻塞卡不动（作者显式要的决策门）、手改过的条目不动（一个字、一个字段的增删都算改）、
  模板里没有的条目不动；
- 幂等：升级后条目自带 `transition_text`，第二次运行不再改动、不写盘
  （`world_store.transaction()` 只在内容变化时提交，不推 revision）；
- 只改 advisory 文案，不碰 `current_scene`、flags、线索、SAN、历史回合与快照；
- 升级进世界的那份不带 `supersedes`，世界副本保持干净。

**加载顺序与保护范围（两条路径不同，不能互相代入）**：

| 路径 | 触发条件 | 行为 | 保护范围 |
| --- | --- | --- | --- |
| 整体静态刷新 | 世界记录的 `initial_state_revision` ≠ 当前模组文件版本（直接续玩、恢复旧存档） | `refresh_static_handout_config` 把 `scene_catalog` 等**整体替换**为模组当前内容，随后条目级步骤成为 no-op | **无条目级保护**：世界内对场景目录的改写（含自写 advisory、新增 advisory）会被覆盖 |
| 条目级迁移 | 版本已一致，但状态来自更早快照（`WorldBranchService.create` → `seed_from_snapshot`） | 只替换与 `supersedes` 逐字一致的非阻塞条目 | 手改过的条目、阻塞卡、模板未声明的条目全部保留 |

事故世界（`local-猩红文档-recovery-e4963b`）走的是第一条路径：它记录的版本是旧文件版本，
模组文件一改就会整体刷新。分支续玩才走第二条。测试
`test_stale_module_revision_refresh_replaces_the_whole_scene_catalog` 把第一条路径的
行为（用户改写与用户新增条目都被替换）固定成契约，避免把"局部用例通过"说成
"所有自定义内容都不受影响"。

`entry_beat` 的文案（第四节）原本只随整体刷新更新；2026-09-13 起入场节拍也支持
`supersedes` 逐字匹配的条目级升级（见 ADVISORY_TRANSITION_FINAL_CHECKS_20260913.md）。

覆盖路径：

| 世界来源 | 机制 |
| --- | --- |
| 直接续玩已有世界（本事故世界） | 模组文件改动 → `initial_state_revision` 变化 → `refresh_static_handout_config` 全量刷新场景目录 |
| 从旧回合/存档建分支、恢复旧存档 | `seed_from_snapshot` / `restore_snapshot` 之后按条目升级（新钩子） |
| 新开局 | 模板本身就是新文案，无需升级 |

已知边界（本轮未改动）：

- `refresh_static_handout_config` 在模组文件版本变化时**整体替换 `scene_catalog`**，
  这是既有设计（把场景目录视为模组权威元数据）。因此手改过世界内场景目录的世界，
  在模组文件更新后仍会被整体刷新覆盖；本轮新增的按条目升级是更窄的一条通道
  （只动与 `supersedes` 逐字一致、未被改写的非阻塞 advisory），用于不触发整体刷新的
  分支/存档路径。两条路径的保护范围已在 3.3 的表格中分开列出。
- 把 `blocking` 翻成 `true` 的旧快照不会被按条目升级（避免覆盖作者显式声明的决策门），
  其卡片文案只随整体刷新更新。

### 3.4 模组格式

`ActionAdvisoryDefinition` 新增 `blocking`（默认 `true`）、`transition_text`
（≤1600 字）与 `supersedes`（≤3 条旧载荷对象），并加校验：非阻塞预演必须提供
`transition_text`（否则安装即失败）、`supersedes` 每项必须是非空对象；阻塞卡维持原有
"必须提供 NPC/守秘人提醒/公开提示"的约束。两个 JSON Schema（v1/v2）随模型重新生成，
`tools/check_architecture.py` 的 schema 复现门禁通过。

## 四、验证

复现与验收脚本（离线，不调模型）：`/tmp/trpg-advisory-20260911/repro.py`，
状态取自事故回合**之前**的快照副本 `snapshot_933d47e9c929ebdb9ea3c3356dfec150`
（导出于 `/tmp/trpg-advisory-20260911/evidence/pre_turn_state.json`），
输入与事故回合逐字一致。原始事故存档与 trpg-master.db 只读访问，未做任何修改。

见 `logs/` 与 `after/` 目录：修复前 `repro-before/`，修复后 `after/`。

单元/集成回归（本地）：

- `tests/test_action_preflight.py`：非阻塞=节拍（无卡片、无选项、卡片文案不入素材）、
  旧形态跳过、NPC 不在场回落 `keeper_text`、阻塞卡语义不变；升级的**对偶用例**：
  官方旧载荷逐字命中才升级，改过 `npc_text`/`keeper_text`/`title` 或增删字段的一律不升级，
  模板未声明 `supersedes` 时不升级；真实模组数据的命中用例。
- `tests/test_discovery.py`：到达回合只播节拍并按"节拍→赶路→抵达→接待"排序、节拍只出现一次、
  模型提示词里没有劝留与分支提示、到达不等于验尸（flag/SAN/线索不变）、
  先问法伦留在原场景、法伦不在场时回落节拍且不出现"拨通/知会/打过招呼"、
  接待节拍不隐含预先安排、取消阻塞卡不播节拍、被拒绝的跨场景停留不移动也不结算时间、
  旧快照（官方旧载荷）升级后不重播卡片文案、**手改过的同名旧条目保留原样**、
  未升级快照降级为无提醒、以及整体刷新路径的边界契约。
- `tests/test_module_packages.py`：非阻塞缺 `transition_text` 校验失败、
  `supersedes` 非对象校验失败、非阻塞条目编译进运行时场景且不带卡片字段、
  `supersedes` 编译进世界后能驱动条目级迁移。

真实模型主线（`tools/playthrough.py`，独立 runtime root，独立世界）：

| run | 世界 | 回合 | 断言 | 失败 beat | 说明 |
| --- | --- | --- | --- | --- | --- |
| 第一版 | `local-猩红文档-20260911-060601-a291` | 38 | 32/33 | B5b_clock | 玩家卡在洛奇门口，六回合无时间结算 |
| 最终版 | `local-猩红文档-20260911-063507-7b24` | 44 | 32/33 | B4_sanatorium | 亨特拒绝交出复制件，harness 未换做法 |

两次都是四个全局检查（保密、不提前说出未知线索、弹药递减、结局结算）全通过、主线走到
`truth_and_seal`；两次失败的 beat 不同、机制链自洽。**B1 停尸房（本轮修复的直接路径）
两次都 ✓**：回合 2 用事故同款输入「我想先看看莱特教授的尸体。」→ 场景切到
`miskatonic_medical`、惠特克罗夫特在场、handout 发放；回合 3 的明确验尸动作才发放
`wright_body_evidence`。第一版回合 2 的叙事逐字先播过渡节拍（法伦替你拨通内线），
医生的话是"法伦主任说你要来看看。"——**没有劝留、没有"没有打电话"式矛盾**；
`narrative_segments`（流式记录）与最终叙事一致，节拍只出现一次。
- **B5b_clock ✗（第一版的唯一失败）**：取证见下面"六、B5b 连续拒行取证"。结论是
  **既有机制缺口 + 剧本刚性问题，不是本改动引起的回归**，本轮未修（不改 harness 掩盖）。
  在最终版的第二次主线 run（世界 `local-猩红文档-20260911-063507-7b24`，44 回合）里，
  同样的代码 **B5b ✓**（玩家在 B5b 开始前已经在古董店），换成 **B4_sanatorium ✗**：
  该 beat 在第 13 回合请亨特交出复制件被裁决判 `executed_failure`（亨特拒绝、护理结束探视），
  之后 harness 连续 7 回合重复"接过那份复制件…"而没有换做法，裁决每次都维持"他不会交出"，
  `discovery_refs` 为空 ⇒ `hunter_copy` 未入册（世界内线索停在 5）。
  两次 run 都是 32/33、失败的 beat 不同、机制链都自洽 ⇒ 这是**模型自由度 + 剧本刚性**的
  运行间波动，不是机制回归；`B5b` 的时钟机制本身由 `test_case_clock_time.py` 覆盖。
- 报告另记若干 `adjudication_fallback`（第一版 5 次、第二版 4 次）：裁决模型输出不可用时由
  引擎的确定性兜底接管，相关 beat 断言仍全部通过，属既有行为（本轮未触碰裁决通道）。

**事故状态副本 + 真实模型单回合复测**（`/tmp/trpg-advisory-20260911/incident_replay.py`，
独立 runtime root `/tmp/trpg-advisory-20260911/runtime-incident`）：
世界 `local-猩红文档-20260911-063044-5149`（supersedes 精确匹配版本），报告
`logs/incident-replay.json`：

- 世界由事故回合**之前**的状态副本 seed 而成，`sync_module_metadata()` 返回
  `miskatonic_university/wright_body_handoff`（逐字指纹在真实世界副本上命中）。
- 用原始输入跑一次真实模型：`decisions: []`（无决策卡）、无劝留；
  流式文本依次是"过渡节拍（法伦拨通内线）→ 赶路 → 抵达 → entry_beat → 模型续写"，
  节拍只出现一次。
- 医生的台词是 **"法伦主任在电话里说过了——你就是为查尔斯来的那位。"**，
  与本回合已完成的通知一致（后续叙事有依据引用该事实），没有出现"他没有立刻打电话"
  那类前后矛盾。
- 终态：`scene=miskatonic_medical`、`body_examined=false`、`san=70`、无新线索、
  `world_clock.elapsed_minutes=15`（只结算了赶路）；选项是抵达后的动作
  （询问死亡证明 / 查看遗体），遗体未被打开。
- 上一轮（`local-猩红文档-20260911-062136-82c8`）是同一路径的早期版本，结论相同：
  "法伦在电话里说过了。你是为查尔斯·莱特的事来的……"。
- 残余（cosmetic，技能文档已记录）：`entry_beat` 与赶路文案仍会被模型回显一次，
  措辞不同、事实一致。

## 六、B5b 连续拒行取证

数据来源：主线 run 的隔离 runtime DB
（`/tmp/trpg-advisory-20260911/runtime/trpg-master.db`，世界
`local-猩红文档-20260911-060601-a291`，回合 14–19 的完整 turn record）与
规划探针（`/tmp/trpg-advisory-20260911/b5b_planning.py`，状态取 B5b 开始前的世界快照）。

### 6.1 六回合事实

| 回合 | 玩家输入 | 当前场景 | 裁决 status | 裁决理由（模型原文摘要） | 结算时间 | 待处理门禁 |
| --- | --- | --- | --- | --- | --- | --- |
| 14 | 接下来几天，我白天在古董店对面的咖啡馆监视进出的人，晚上回旅馆整理三个买家的线索 | miskatonic_lodge_office | not_executed | "当前场景为洛奇办公室，且未说明如何离开、监视地点与'三个买家'线索的具体所指，需要先澄清行动起点与时间跨度" | 355→355 | 洛奇堵在门缝后等回答 |
| 15 | 继续监视：记录维克、费德曼兄妹和任何可疑访客的规律…… | 同上 | not_executed | "洛奇正堵在门缝后等回答。玩家并未声明离开此楼前往轻率琐事古董店" | 355→355 | 同上 |
| 16 | 又盯了几天：留意店里夜里的异常动静…… | 同上 | not_executed | "当前场景仍是洛奇的历史系办公室，玩家尚未离开这栋楼，也未抵达轻率琐事古董店或任何可打听传闻的场所" | 355→355 | 同上 |
| 17 | 把这些天的监视结果汇总：谁最可能已经把文档弄到手了？ | 同上 | not_executed | "监视行动从未实际发生……不存在可供汇总的监视记录" | 355→355 | 同上 |
| 18 | 同 17 | 同上 | not_executed | "角色从未进行过任何监视蹲守……不足以回答'谁已把文档弄到手'" | 355→355 | 同上 |
| 19 | 同 17 | 同上 | not_executed | "这是基于不存在前提的询问，需要澄清" | 355→355 | 同上 |

- 六个回合的裁决 **都是有效裁决**（`success: true`，`status: not_executed`，
  `description` 由裁决模型撰写），**不是** `adjudication_fallback`；`npc_direction` 在第 15
  回合明确写了洛奇会收拢门缝甚至关门。
- `not_executed` 的具体来源是**裁决模型自己的分类**：它选择 `clarify` + `time_minutes: 0`
  （"先澄清行动起点"），所以本回合既没有时间、也没有时钟推进。
- 门禁不是引擎硬门，而是**叙事状态**：上一拍（B5_buyers）在洛奇门口结束，洛奇正在等回答；
  harness 没有回答，也没有离开，直接跳到"监视几天"。
- 模型在每一回合给出的选项里都写了出路（回合 14："直接说明你打算去监视维克古董店"；
  回合 15–18："离开历史系办公楼，前往轻率琐事古董店一带"），harness 未采纳。

### 6.2 A/B 对照与规则依据

规划探针（无模型）结果：

| 输入 | `infer_scene_transition` | 计划 | 原地停留 >60 分钟的验证 |
| --- | --- | --- | --- |
| A：接下来几天，我白天在古董店对面的咖啡馆监视…… | `None` | interaction（无目的地） | `DestinationGroundingError`：必须先 move 抵达 |
| A2：继续监视：记录维克…… | `None`（未点名任何场景） | interaction | 接受（可原地停留） |
| B：离开洛奇办公室，前往古董店对面监视 | `trivial_pursuits` | arrival（explicit_move） | ——（已按移动结算） |
| B2：离开洛奇办公室，前往轻率琐事古董店 | `trivial_pursuits` | arrival | —— |
| D/E：我去古董店 / 前往古董店 | `trivial_pursuits` | arrival | —— |

- **语义差异**：A 用"**在**……监视"的处所结构描述活动地点，句子本身没有任何移动动词
  （没有 去/前往/进入），`_MOVE_ACTION`（`src/gameplay/action_checks.py`）本来就要求
  移动动词出现在开头或标点之后，所以 A 不是一次移动；A2/A3 连场景名都没点到。
  B 用"离开……前往……"，移动动词 + 场景名齐全，引擎立刻规划为抵达。
  **引擎不要求任何魔法措辞**：最短的"我去古董店"也能离开。
- **为什么 A 不能直接结算**：`validate_proposal`（上一轮新增）规定"非 move 且超过 60 分钟
  且提到其他场景"必须先行抵达——否则权威场景停在洛奇办公室，而叙事在别处度过数日，
  时间与时钟都会被记到错误地点。
- **登记为真实机制问题（既有，非本轮回归）**：A 这种"处所式多日监视"在引擎里是**死路**——
  既不移动（没有移动动词），又不允许原地消耗时间（提到了别的场景），只能靠玩家改口或
  模型给选项。涉及的两个组件（`action_checks.infer_scene_transition`、
  `action_adjudication.validate_proposal`）本轮都没有改动，其中守卫是上一轮引入的。
  建议的后续修法（本轮未做，会改变节奏语义，需要单独确认）：当"非 move + >60 分钟 +
  恰好点名一个其他场景"时，把该场景当作隐含目的地**自动规划为抵达回合**（抵达后再结算停留），
  而不是拒绝；或至少在澄清选项里固定给出"前往<该场景>"这一条。
- 连续六回合的僵局另有剧本因素：harness 的 B5b 输入列表里没有任何带移动动词的句子，
  且 `choose_beat_input` 在越界后重复最后一条。按验收规范，**不通过修改 harness 掩盖**，
  B5b 在本轮报告中记为 ✗。

## 七、过渡事实的执行边界

| 边界 | 机制 | 证据 |
| --- | --- | --- |
| 法伦不在场 | `match_action_preview` 判定 `npc_absent` ⇒ 节拍改用 `keeper_text`（"停尸房不对外开放：按程序，你得先经院方值班人员许可"），不出现"拨通/知会/打过招呼" | `test_absent_fallon_never_announces_a_completed_contact`；`test_absent_npc_uses_keeper_fallback_beat` |
| 玩家取消（阻塞卡） | 取消回合 `skip_agent`，只播卡片文案；过渡节拍与"已结算的既定事实"框架都不出现在流式文本里 | `test_cancelling_the_blocking_card_never_plays_a_transition_beat` |
| 行动被拒绝 | 非移动计划不会命中预演（`match_action_preview` 要求 `is_arrival`），因此不预播、不移动、不结算时间 | `test_refused_cross_scene_stay_settles_nothing_and_plays_no_beat`；B5b 六回合实测 355→355 |
| 通知只发生一次 | 节拍在流式事件里计数为 1，在模型提示词里计数为 1；主线真机复测中模型只引用该事实、不重演 | 上述计数断言 + `logs/incident-replay.json` |
| 后续叙事有依据引用 | 节拍被写进"[本轮已向玩家展示的前置叙事｜引擎已结算的既定事实]"并明确"不得改写或否认……联系没联系" | `logs/incident-replay.json`（"法伦主任在电话里说过了"） |
| 无通知时不得补出 | 法伦不在场分支的节拍与提示词都不含通知陈述；`miskatonic_medical.entry_beat` 去掉了"等候"措辞（改为"攥着病历夹站在那里"），不再隐含"有人替你安排过" | `test_arrival_beat_does_not_imply_a_prior_arrangement`；`test_absent_fallon_never_announces_a_completed_contact` |

框架提示词只能降低模型改写/臆测的概率，确定性的部分只有：节拍文案本身、卡片不再重播、
以及状态结算（移动、时间、线索、SAN、flag）。`entry_beat` 的新措辞自 2026-09-13 起
也按 `supersedes` 逐字匹配进入"版本已一致"的旧快照世界（见 3.3 表格与后续收口文档）。


## 五、残余

- **B5b 类输入（处所式多日监视）在引擎里是死路**：见第六节。既有机制缺口（守卫与移动匹配
  都是既有代码），本轮未改；不改 harness 掩盖，B5b 记为 ✗。
- **旧存档保护范围分两条路径**：整体刷新覆盖世界内对场景目录的所有改写（含用户自写的
  advisory）；只有"版本已一致"的分支/存档路径享受 `supersedes` 逐字保护。见 3.3 与
  第七节的测试契约。
- ~~`entry_beat` 文案只随整体刷新更新：从旧快照建分支的世界仍沿用旧措辞。~~
  已于 2026-09-13 收口：入场节拍支持 `supersedes` 逐字匹配升级，见
  ADVISORY_TRANSITION_FINAL_CHECKS_20260913.md。
- 阻塞卡路径仍然是"模型按卡片含义展开"（玩家已看过卡片，联系是否发生在叙述里由模型
  写出）。本轮未改阻塞流程；需要确定性联系事实的模组应使用 `blocking: false` +
  `transition_text`。
- 提示词框架把预播节拍声明为既定事实，能显著降低被改写/否认的概率，但**不是**确定性
  保证；确定性部分只覆盖节拍文案本身、卡片不再重播、以及状态结算（见第七节表）。
- 未实现（需要单独确认产品语义）：按场景状态条件化的 `entry_beat`；把"处所式多日监视"
  自动规划为抵达回合。
