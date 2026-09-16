# 真实模型验收报告（2026-09-16，DeepSeek deepseek-flash）

> 授权范围：本地已配置 DeepSeek Key → https://api.deepseek.com；模型
> deepseek-flash；累计 API 消费上限 ¥20；只用隔离临时运行目录与测试世界；
> 不触碰生产、不合并 master、不部署。本报告不含任何凭据。

## 1. 验收对象与配置

- 代码基线：`experiment/keeper-platform` @ `46d5760` + 本轮验收中产生的修复
  （最终 SHA 见 §10；A9/BF8 两轮全绿对应的提示词与代码即最终版）。
- 模型配置（harness 世界）：BYOK world-scope，deepseek / deepseek-flash，
  narrative 与 judgement 同绑定；structured agent 输出 max_tokens=8000–16000。
- 环境注意：本机代理解析 `api.deepseek.com` 为 fake-ip（198.18.x），会触发云端
  出站校验的非公网拒绝。验收运行使用部署者级开关
  `TRPG_EGRESS_ALLOWED_PRIVATE_HOSTS=api.deepseek.com`（白名单主机跳过公网
  检查、仍解析并钉扎连接地址），这是该环境变量的设计用途；生产配置不受影响。
- 预算台账（账户余额差值，CNY）：基线 ¥141.48 → 验收结束 ¥131.87，
  **合计 ¥9.61**（含全部修复后复测），上限 ¥20 未触及。
  大项：legacy 全流程 ≈¥7.3；A 组 9 轮 ≈¥1.6；B–F 7 轮+探针 ≈¥0.7。

## 2. 结果总览

| 验收 | 模式 | 结果 |
|---|---|---|
| A 过渡承接 7 场景（transition_real_model_check） | structured/agent | 见 §3 |
| B–F 记忆与上下文 5 场景（memory_real_model_check） | structured/agent | 见 §4 |
| 按钮级：出示/使用/检定（真实 UI + 真实后端 + 真实模型） | structured/agent | 见 §5 |
| 猩红文档全流程主线（tools/playthrough.py） | legacy | 32/33，见 §6 |
| structured 全流程游玩 | structured | **能力缺失，已登记**（§6.3） |

## 3. A 组：短期行动衔接（意愿→追问→明确决定→执行）

同一世界连续三轮的完整链路 + 四个对偶场景。运行历史（同一代码逐轮修复后复跑）：

| 轮次 | 代码状态 | 结果 |
|---|---|---|
| A1 | 基线 | 4/7（C 组因 harness 缩进缺陷未执行；D1 ✓ / D2 ✗ / D4 ✓） |
| A2 | +物品可见性、+thinking 关闭、+harness 修复 | 4/7（归一化缺失暴露） |
| A3 | +扁平命令归一化 | 5/7（D2/D3 语义判定分歧） |
| A4 | +愿望式/提议句规则、D2 期望对齐产品契约 | **7/7** |
| A5 | +queries few-shot | 5/7（C1 跑题「下葬」、D3 方差） |
| A6 | +空结果放宽重查、+NPC 记忆查询规则 | 5/7（C1 pending_action 字符串撞 schema 空转暂停） |
| A7 | +scene_notes、+pending_action/disclosed 形状归一 | 5/7（C1 意愿直接执行、D2 挂起后空转） |
| A8 | +await 停止信号 | 7/7 |
| A9 | +检定栏位澄清、+角色卡注入（最终定稿） | **7/7**（最终联合版本复验） |

### 3.1 机制结论（全部有确定性测试回归）

1. 触发请求被 resolve_intent(awaiting_player) 挂起后，运行**必须**停止——
   修复前该信号被丢弃，模型被继续空调用直至预算耗尽（A7-D2 实测 6 步空转）。
2. 模型输出在扁平/嵌套命令形态间摇摆；Agent 边界归一化（顶层字段收进
   payload、pending_action 字符串→对象、disclosed 字符串→数组、audience
   investigators 省略 ids→全体调查员）。协议 schema 保持严格不变。
3. 主持/Agent 快照此前看不到任何物品（_visible_items 只看 own）→ 已修，
   keeper 视角带 holder_id；玩家投影不变。
4. 模组场景描述此前不进 Agent 上下文（destinations 只有名称）→ 模型编造
   「遗体已下葬」。已加 scene_notes（当前场景+已知目的地的模组描述，
   主持级、不进公开投影、协议不变）。修复后 A7-C1 的叙事正确使用了
   「遗体在医学院冷柜」。
5. thinking 显式关闭（deepseek provider/host 判定）：推理 token 曾把
   max_tokens 预算耗尽致整轮暂停（A1-D3，66K 字符 reasoning）。
6. C3 链式承接（意愿→追问→「那麻烦你联系一下，我现在过去」→移动落账）
   在 A4 完整通过；线程关联、deferred≠授权、取消联动均有确定性测试。

### 3.2 模型行为方差（不掩盖，如实登记）

以下不是机制缺陷（通道均已验证可用），是 deepseek-flash 在边界措辞上的
判断方差（temperature=0.4）：

- D3「我想回医学院那边再看看」：A3/A4 正确不移动，A2/A5/A6 移动。
  「回+目的地」处于意愿与指令的边界；提示词已含愿望式规则，模型仍不稳定。
- C1「我想先看看莱特教授的尸体」：A4 讨论后挂起（符合预期），A7 直接执行
  完整验尸流程（ keeper 判断后执行，属另一种合理主持风格，但与
  「意愿不直接抵达」的产品契约相左）。
- C3 在 C1 跑偏（编造下葬）的链条上无法承接——scene_notes 修复后该编造
  不再出现（A7-C1 叙事与模组事实一致）。

### 3.3 按钮级（出示/使用/检定等待与恢复）

见 §5。

## 4. B–F 组：长期记忆与上下文（memory_real_model_check.py）

| 场景 | 内容 | BF3 | BF5 | BF6 | BF7 | BF8（最终） |
|---|---|---|---|---|---|---|
| B 长期回忆 | 事实挤出近期窗口后凭记忆回答 | ✗ 不查询 | ✗ 查错角色 | ✓ | ✓ | ✓ |
| C 知识隔离 | 不在场 NPC 记忆不注入、不泄露 | ✓* | ✓ | ✓ | ✓ | ✓ |
| D 传闻与事实 | rumor 带不确定性、experienced 直述 | ✓ | ✓ | ✓ | ✓ | ✓ |
| E 缺失信息 | 查询无果后承认不确定、不编造事实 | ✓ | ✗ 判据措辞漏配 | ✓ | ✓ | ✓ |
| F 预算与检索 | 8 条/字符双预算生效，必需区不挤占 | ✗ 不查询 | ✗ | ✓ | ✓ | ✓ |

（C 的 BF2「FAIL」是验收判据误报：Agent 自己的新记忆记录合法包含物品名，
判据已改为按 character_id 精确判定。）

行为备注：B 组模型首轮把查询限定在「说话者本人」（查 fallon 而非全部角色），
空结果后按新规则放宽重查命中 pc 的记忆。提示词的两条新增规则（先查再答 /
NPC 问题查该 NPC 的记忆 / 空结果放宽重查）是 B/E/F 通过的必要条件。

## 5. 按钮级真实链路（真实后端 + 真实 UI + 真实模型）

方法：`/tmp` 脚本起真实 uvicorn 服务（隔离 runtime root），Playwright 驱动真实
前端；先正常开局（legacy 开场白为真实模型生成），再把世界切成
structured_v1+agent 并写入 BYOK，重连后全部通过按钮/输入框驱动。
证据：`buttons/`（截图 5 张 + 帧日志 + 控制台日志 + 聊天区转储）。

| 流程 | 结果 | 证据 |
|---|---|---|
| 面板「出示」线索 | ✓ 发出 action_request{kind:present_clue, clue_id, target:npc}；Agent 多步回应，请求 completed+success；叙事与模组事实一致（「遗体锁在地下停尸房冷柜」，scene_notes 生效） | 04_present_answered.png、帧日志 |
| 面板「使用」道具 | ✓ action_request{kind:use_item, item_id, approach}；法伦确认钥匙归属，completed+success；**未发生移动** | 05/06、帧日志 |
| 检定等待→点击→结算 | ✓ 自由文本搜查 → Agent 提交 request_check（skill=spot_hidden 精确键）→ UI 检定卡 → 点击掷骰 → check_response → check_resolved（92 vs 38 失败，目标值=角色卡技能值，数学正确） | 07_check_waiting.png、08_check_resolved.png、帧日志 |
| 断线/恢复 | 恢复语义由结构化 E2E 与单测覆盖（本轮未重复跑前端 E2E 全量） | — |

按钮级验收抓出并已修复 4 个真缺陷（§7 第 7–10 条）。
UI 观察项（转 zcode）：道具卡「使用」按钮在 Playwright 行动性检查下持续报
「outside of the viewport」（截图中按钮可见可点；疑似面板重渲染导致驱动层
失稳，建议按 ui-button-check 在小视口复核一次）。

## 6. 主线验收（猩红文档）

### 6.1 legacy 全流程（tools/playthrough.py，真实模型）

**32/33**，41 回合，world=local-猩红文档-20260916-030406-2024，
dice=22，handout=11，decision=5，结局 truth_and_seal。

唯一失败：B3a 莱特办公室私人日记未入册。定性：**骰运 + harness 剧本限制**，
不是引擎缺陷——检定通道正常（spot_hidden 检定执行并失败）；失败后的重复
检定被正确拒绝（RepeatCheckError）；harness 剧本只会重复同一输入，从未尝试
「孤注一掷」（push）——真实玩家有此通道。已知 cosmetic 残余：approach_text
在失败回合被重复注入叙事（既有登记）。

其余全部通过，含 B1b 威胁确认门、B6b 伏击战（7 回合，弹药 6→0）、B6d 封印、
B7 疯狂注入、B8 结局、全局保密与不提前泄露线索。

### 6.2 验收面 × 模式对照（10 面各自实际走哪条链）

| 验收面 | legacy（本轮实测） | structured |
|---|---|---|
| 判定执行 | ✓ dice=22 | ✓ 命令事务+A/BF 组（按钮级见 §5） |
| 场景切换 | ✓ 逐 beat | ✓ move_party 落账（A 组） |
| 线索分发 | ✓ handout=11 | ✓ grant_clue 通道（单测+E2E），主线未跑 |
| 威胁无辜者 | ✓ coercive_threat | 未覆盖（无结构化战斗） |
| 结局进行 | ✓ truth_and_seal | **不可用**（无结局命令） |
| 结局后加成 | ✓ profile 回滚 | 不可用 |
| 保密 | ✓ 全局通过 | ✓ C 组（NPC 记忆隔离） |
| 不提前泄露未知线索 | ✓ | ✓ D 组（传闻不当事实） |
| 弹药消耗 | ✓ 6→0 | 不可用（无战斗命令） |
| 疯狂 | ✓ 注入验证 | 部分（adjust_stat 可扣 SAN；无疯狂状态机） |

### 6.3 已登记：structured 模式暂无完整游玩能力

structured_v1 命令目录（schemas/structured-play/v1/command_request.json）当前
没有战斗域命令、没有结局结算命令。**新模式不能宣称可发布完整主线**；它的
当前能力边界是调查/社交/探索回合（移动、检定、线索、物品、状态、时间、
记忆、handout）。战斗与结局要么补结构化命令域，要么明确由人类主持接手。

## 7. 本轮发现并修复的问题清单

机制修复（全部带回归测试）：
1. `_visible_items` 主持/Agent 盲视全队物品 → keeper 视角含 holder_id
   （tests/test_structured_commands.py::test_keeper_snapshot_sees_all_party_items）；
   schema：`known_item` 增加**可选** holder_id（仅 keeper/agent 投影携带，
   玩家投影不变——协议增量，前端无需改动）。
2. agent BYOK caller 不关 thinking → DeepSeek 显式 disabled
   （tests/test_structured_trigger.py::ByokCallerThinkingTests 三项）。
3. 扁平/嵌套命令形态归一化（tests/test_structured_agent.py::
   NormalizeCommandsTests 四项，含 pending_action/disclosed 形状）。
4. audience `{"kind":"investigators"}` 省略 ids → Agent 边界补全全体调查员
   （修复前该写法连撞 schema 致玩家端静默）。
5. run() 丢弃 "await" 停止信号 → 挂起即停（test_awaiting_resolve_stops_run_without_wait_flag）。
6. Agent 上下文无模组场景事实 → scene_notes_for_agent（不进公开投影）。
7. 快照把 granted_to 为空的模组初始线索展示给全队、行动校验却拒绝任何人
   出示 → 校验对齐投影口径：空 granted_to = 全队共享，可出示
   （test_party_known_clue_presentable_by_any_investigator；
   按钮级真机验收实测「UI 给按钮、提交被拒」）。
8. 物品/线索稳定 ID 注册表在快照路径只在内存迁移不落库 → 快照里的随机
   item_id 在首次提交时不存在（object_not_held）。修复：session_snapshot
   首迁移即落库（不推进 revision）
   （test_snapshot_item_ids_are_stable_and_persisted；按钮级实测）。
9. resolve_intent(awaiting_player) 与同一步被拒之命令并存时运行直接结束
   → 玩家永远等一张没创建成功的检定卡。修复：同步有拒绝则不停在 await，
   带驳回理由续跑一步（test_await_with_same_step_rejection_continues_run）。
10. Agent 上下文看不到权威角色卡 → request_check 的 skill 只能猜
    （实测写「侦查」被拒；真实键 spot_hidden）。修复：investigator_sheets
    注入 Agent 上下文（主持级，不进公开投影），提示词要求用精确键名
    （test_investigator_sheets_for_agent_carry_skill_keys）。
    修复后探针实测：模型一次调用即提交 skill=spot_hidden 的检定并正确挂起。

验收工具修复：transition harness 组循环缩进缺陷（C 组曾被静默跳过）、
对偶场景共享世界导致起点漂移、paused/failed 误判 PASS；memory harness
种子后 revision 过期。

## 8. 已知限制与登记项（未修）

1. **本地结构化世界的模型配置链路**：agent_runtime 只用 room_route_resolver
   （云端语义、BYOK-only、allow_private=False），不读本地
   model_settings.local.json；且本地目前**没有**创建结构化世界的 UI 入口。
   fake-ip 代理环境下本地结构化 agent 会被出站校验拒绝。当前不影响用户
   （功能不可达），发布结构化模式前必须决定本地解析路径。
2. **模组 lore 访问**：Agent 除 scene_notes 外没有 lorebook/线索库正文通道；
   模组事实类问题仍可能编造（scene_notes 只覆盖场景级描述）。
3. D3 类边界措辞的移动判断方差（§3.2）。
4. B3a 类骰运失败后的推进体验依赖玩家主动「孤注一掷」；approach_text
   失败回合重放为既有 cosmetic 登记。
5. `rate_limited` 仍未实现（既有登记）。
6. CI quality 在实验分支持续红：frontend E2E 开局引导 30s 超时（共享 runner
   冷启动重量级模组）。backend 全绿。修复建议见 §9。

## 9. CI 协调（给 zcode）

- 现象：CI 共享 runner 冷启动时开局引导 `.module-select-trigger` 30s 超时，
  本地与 Pi 均不复现。master 亦有同类环境性失败。
- **zcode 已修复并提交（本分支本地 HEAD）**：`24bbf69`（E2E 就绪判据改为语义
  就绪 + CI 失败产物上传）、`0eac7b9`（产品级：StructuredActionDialog 遗留
  关闭定时器清掉重新编辑的草稿——UI 生命周期修复，不触协议/模型路径）。
  根因与证据见 `docs/evidence/20260914_ci_module_select_timeout/README.md`。
  其本机 CI 条件复跑：37 收集 → 35 passed / 2 skipped / 0 failed。
- **CI 实证结果（35058831650 / 35059395902）：仍红**，backend 绿、frontend 红。
  失败签名已变：按钮在 DOM 但 `boot-loader` 覆盖层拦截点击（CI 上首次预载
  超过 30s 点击超时；loader 预载兜底 180s）。这正是新就绪判据缺的一层：
  「页面已挂载」≠「覆盖层已退场」。修复建议与产品层判断已交 zcode：
  `docs/CI_BOOT_LOADER_HANDOFF_20260916.md`。

## 10. 交付与回滚

- 最终提交 SHA：`528ed7b`（本报告与证据即包含于该提交）。
- 测试（最终联合版本）：
  - 后端全量：**1401 passed / 7 skipped**（较基线 +16 项，全部是本轮回归测试）。
  - 架构门禁 `tools/check_architecture.py`：通过；ruff：通过。
  - 前端协议单测（structured/m0-fixtures，受 schema 增量影响的面）：
    36 passed（vitest 定向）。
  - 真实模型：A9 7/7、BF8 5/5、按钮级三链路通过（同一代码指纹）。
- 数据库迁移：`20260914_0016_structured_context_memory`（interaction_threads
  + character_memories，adopt-or-create 约定）。回滚：`alembic downgrade
  20260913_0015` drop 两张新表；旧代码不认识这两张表，天然兼容。
  备份：沿用现有 deploy/backup-trpg-master.sh 的数据库备份（新表随库备份）。
- 预算消耗：**¥9.61 / ¥20**（DeepSeek 账户余额 141.48 → 131.87）。
- 证据目录：`docs/evidence/20260916_real_model/`（A/BF 最终轮完整轨迹 +
  legacy playthrough 报告 + 按钮级截图/帧日志；轨迹只含
  api_key_present 布尔位，无任何凭据）。
- 协议增量（给 zcode）：`known_item` 增加**可选** `holder_id`
  （仅 keeper/agent 投影携带；玩家投影与 fixture 不变）。

## 11. 结论：是否建议进入预发布

**建议进入 Pi staging 预发布**，范围与条件如下：

可以进预发布的依据：legacy 主线 32/33 全真机通过（含结局）；structured
调查/社交链路 A 7/7 + B–F 5/5 + 按钮级三链路真实 UI 通过；本轮 10 个真实
缺陷全部修复并有回归测试；后端 1401 全绿。

必须随结论一起公开的限制：
1. **structured 模式不能完整游玩**：无战斗/结局命令域（§6.3）。只能以
   「实验模式（调查与社交阶段）」出现，不得宣称完整主线可玩。
2. **本地结构化世界的模型配置链路缺口**（§8.1）：当前不可达所以无用户影响；
   开放本地结构化入口之前必须先接通。
3. 模型在边界措辞上的判断方差仍在（§3.2）：D3 类「愿望式表达」偶尔被直接
   执行。机制兜底（待办记录、取消、纠正）都在，但真实玩家会遇到。
4. CI quality 实证仍红（backend 绿）：boot-loader 覆盖层在 CI 慢机上拦截开局
   点击（E2E 设施问题，非产品逻辑缺陷；修复建议已交 zcode，见 §9）。**quality
   未绿之前不得推 master。**
5. 云端 BYOK-only 强制与注册开放属另一条发布线（另有安全清单），不在本轮范围。

不建议的事：直接开放注册 / 直接推 master / 把 structured 当完整模式宣传。
