# 守秘人主持的叙事型过渡回合：根因记录与实现方案

> 历史归档（2026-09-16 整理）：正文保留当时的结论与适用版本，不作为当前完成状态或执行授权。现行入口见 [README](../../../../README.md)，未完成事项见 [STATUS](../../../STATUS.md)。正文中的仓库路径按仓库根目录理解，`/tmp` 产物不保证仍存在。

日期：2026-09-14；分支：`experiment/keeper-platform`（工作区基线 `75516a4`，改动未提交）。
范围：结构化跑团平台（`execution_profile=structured_v1`）的过渡回合能力。

**当前状态（一句话）**：人类主持的「意愿 → 叙事等待 → 追问 → 决定 → 执行」已用真实后端 +
真实前端验收通过；平台侧不变量（不自动移动、待办不自动执行、位置只由已提交命令改变、
失败可诊断）在真实模型下也逐项成立；**Agent 主持的同一条链条在冻结代码的一次完整运行里未完整通过**
（卡在「明确出发」那一步，见 §7.5）——不声称已通过，也不跨版本拼接通过结果。

阅读顺序：§1 根因 → §2/§3 实现与文件/协议 → §4 提示词接入 → §5 确定性测试与界面记录 →
§6 覆盖范围 → §7 真实模型验收（含修正后的验收声明）→ §8 未解决问题。

## 1. 根因记录（对照现有代码实测）

### 1.1 当前相关世界如何区分 legacy 与 structured？

世界元数据 `world.metadata_json` 的 `execution_profile`（`legacy|structured_v1`）与
`keeper_mode`（`human|assisted|agent`），由 `src/structured/gateway.py:world_modes` 读取。
三处入口判定：`StructuredGateway.is_structured`（连接/帧分派）、
`room_integration.structured_frame_gate_reason`（房间旧帧门禁）、
`agent_runtime.keeper_agent_needed`（是否调度 Agent）。

- legacy 世界：完全保留旧回合管线（`GameEngine.handle_action` + 关键词/发现推断移动）。
- structured_v1 世界：`src/structured/server_integration.py:_LEGACY_TURN_MESSAGES` 把
  `start`/`action`/`continue` 拦成 `structured_required`，行动只走命令服务
  （效果所有权：防止新旧通路双结算）。

### 1.2 自由输入与移动按钮分别经过哪些路径？

| 入口 | 前端 | 协议帧 | 后端路径 | 结果 |
|---|---|---|---|---|
| 自由输入 | `sendPlayerText`（结构化世界分支） | `action_request{kind:freeform}` | gateway → `submit_action_request` | `player_requests(queued)` + `action_ack`/`intent_pending`，触发 Agent（assisted/agent） |
| 前往按钮 | `MoveDialog` | `action_request{kind:move}` | 同上 | 同上，**不改位置** |
| 位置变更 | — | 主持 `command_request{kind:move_party}` | `execute_command` → `cmd_move_party` | 提交状态 + `scene_changed`，前端场景栏随之更新 |

即玩家的两种入口都只是「请求」，位置只由 `move_party` 命令落账。

### 1.3 谁决定移动、谁决定结束本轮、谁保存待办？

- 决定移动：只有主持（人类 KeeperConsole 或 Agent）发出 `move_party`。
- 结束本轮：Agent runner 由决策字段 `wait_for_player` / `stop_reason` 决定停止模型循环
  （`src/structured/agent.py:383`）；玩家请求本身的收尾由主持的
  `resolve_intent`（completed/declined/cancelled/paused）完成。
- 保存待办：`player_requests`（协议 §3.1 状态机）+ `check_requests` + `keeper_control`。

### 1.4 当前是否存在「先移动，再写出发前对话」的路径？

**存在，而且等待决策完全不留痕。** `agent.py:351-362` 把本步决策的 `commands` 顺序提交，
把 narration 作为 `publish_message` **追加在最后**。于是模型若输出
`[move_party, …]` + 「等你决定」的 narration，位置先改变、再播出等待文本——正是要避免的反模式。

同时 `wait_for_player` 只让 runner `return`（`agent.py:383-389`），**不写任何等待状态**：
触发的 `player_request` 停在 `queued`/`processing`，前端只能看到「守秘人处理中」，
既不知道「在等你回应」，也不知道「哪项行动尚未执行」。

### 1.5 已有等待机制是否只支持检定？

**只有检定。** `awaiting_player` 在协议 §3.1 有定义，但全仓唯一写入点是
`src/structured/checks.py:207`（创建 `request_check` 时）。此外只有 `paused`
（模型失败/预算/接管）。**不存在**「等待普通玩家自由回应」的持久状态，
也没有字段承载「未执行的待办」。

附带缺口：`_build_context`（`agent.py:95`）只带快照 + 未决请求 + 本次 run_log，
而 `session_snapshot` 不投影消息历史 → 下一轮主持看不到自己上一轮说了什么，
追问衔接只能靠世界状态与待办，不足以支撑「继续交谈」。

### 1.6 结论：需要补的不是文案

要满足「意愿 → 正常叙事 → 等待自由回应 → 追问/改主意/坚持」，缺的是三件事：

1. 一个**显式的等待动作**，把触发请求置为 `awaiting_player` 并持久化未执行待办；
2. runner 在等待后**真正停止**（不执行后续命令、不开后台续跑）；
3. 下一轮主持能看到：玩家原本想做什么、目标/目的地、已经发生了什么、
   哪项未执行、已告知哪些条件、在等谁——本实现额外补上**最近的公开对话**。

## 2. 实现方案（最小改动，复用既有机制）

| # | 改动 | 位置 |
|---|---|---|
| B1 | `resolve_intent` 新增 `resolution="awaiting_player"` 与可选 `pending_action` / `disclosed` / `waiting_on`；写入请求行与 `action_status` 事件 | `src/structured/domains.py`、`schemas/structured-play/v1/command_request.json` |
| B2 | runner：决策 `wait_for_player`（或 `stop_reason=wait_player/clarify`）时调用 B1 落等待并 `return`；等待后的命令不执行、不后台续跑 | `src/structured/agent.py` |
| B3 | Agent 上下文：携带未决待办（含 awaiting 明细）与**最近公开对话** | `src/structured/agent.py` |
| B4 | 快照投影 `requests[]` 暴露公开待办字段（仅本人/主持可见） | `src/structured/service.py` |
| B5 | 提示词：意愿≠执行、过渡是正常回合、有据可依才展开、已提醒后坚持不阻拦、只陈述工具结果、等待要用正式能力 | `src/structured/agent_prompts.py` |
| F1 | 前端：读取 `awaiting` 明细并在卡片上呈现「等你回应 + 尚未执行」；输入保持可用、无强制弹窗；刷新由快照恢复 | `frontend/src/state/structured-store.ts`、`.../structured/StructuredCards.tsx` |

## 3. 实际落地的文件与协议变化

### 3.1 后端

| 文件 | 变化 |
|---|---|
| `src/structured/domains.py` | `cmd_resolve_intent` 新增 `resolution="awaiting_player"`；`_awaiting_record()` 校验并归一化 `pending_action{kind,note,target,destination_scene_id}` 与 `disclosed[]`；等待中的请求忽略 `outcome`（不把「尚未执行」记成成功/失败）；终态时清掉 `payload.awaiting` |
| `src/structured/agent.py` | `_order_commands()`：`awaiting_player` 是**屏障**（其后的命令一律丢弃），叙述插在屏障之前；其余情况维持「工具→叙述→resolve_intent」。新增 `_await_trigger()`/`_awaiting_payload()`/`_pending_from_action()`（决策声明等待 → 落持久待办，幂等 command_id `await-<request_id>`）、`_resolve_trigger()`（`done` 时补收尾，避免请求永远停在 queued）、`_recent_transcript()`（最近公开对话进上下文）；`_build_context()` 携带 `awaiting` 与 `recent_public_messages`；`AgentRunResult.awaiting_parked` |
| `src/structured/service.py` | `session_snapshot` 的 `requests[]` 暴露 `awaiting`（仅本人或主持可见；终态不带） |
| `src/structured/agent_prompts.py` | 过渡回合规则与新决策字段（见 §4） |

### 3.2 协议（`schemas/structured-play/v1/`）

| 文件 | 变化 | 兼容性 |
|---|---|---|
| `command_request.json` | `resolve_intent.payload.resolution` 增加 `awaiting_player`；新增可选 `pending_action{kind,note,target,destination_scene_id}` 与 `disclosed[]`（≤8×200 字） | 纯新增：旧请求全部仍合法；未改任何既有字段语义 |
| `events.json` | `action_status.payload` 增加可选 `awaiting` | 同上；`action_status_kind` 早已含 `awaiting_player`，无需改 |

没有新增命令、没有新增数据表、没有新迁移：待办存放在既有 `player_requests`（`status=awaiting_player` + `payload.awaiting`）。

### 3.3 前端

| 文件 | 变化 |
|---|---|
| `src/state/structured-store.ts` | `AwaitingTodo` 类型 + `readAwaitingTodo()`；`applyRequestUpdate()` 写入/在终态清除待办；**快照 `requests[]` 恢复**（刷新后待办可见）；内部等待命令的 `request_id` 不会生成幽灵卡片 |
| `src/react/components/structured/StructuredCards.tsx` | 「等你回应」卡片区块：尚未执行 / 已告知 / 直接说话就行；无强制按钮 |
| `src/protocol/keeper-commands.ts` | 主持台 `resolve_intent` 表单新增等待字段并组装 `pending_action`/`disclosed`；`resolution=awaiting_player` 时不发 `outcome`；缺「尚未执行什么」本地即拦 |
| `src/styles/components/structured.css` | `.structured-awaiting` / `.structured-card-hint` 样式 |

## 4. 提示词的实际接入位置

`src/structured/agent_prompts.py` 的 `SYSTEM_CONTRACT`（由 `build_system_prompt()` 拼上命令目录）：

- 调用点：`KeeperAgentRunner.run()` 与 `run_assisted()` 每次模型调用都传 `build_system_prompt()` —— 这是**实际参与运行**的提示词，不是文档。
- 新增段落「过渡回合（正常叙事里的等待）」：意愿≠执行；明确移动请求**也可能**先处理有依据的重要情境（未告知风险/准备/接待条件/分歧），依据必须来自模组事实或已提交结果，**不得为凑过渡编造门槛/敌意/通知/秘密**；普通且无重要未告知条件的明确移动直接执行、不机械重复确认；已提醒且玩家坚持就先推进（真实硬约束除外）；过渡可以是对话/观察/遭遇；要等待就用 `wait_for_player: true` + `awaiting.pending_action` 正式收尾，**不要只在叙述里写“你是否……”**；`awaiting.disclosed` 写清已告知条件供下一轮避免重复劝留；抵达≠获准接见≠说服≠取得线索；待办只是记录，下一轮按当时情境与权限重新判断。
- 决策契约新增可选字段 `awaiting{pending_action,disclosed,note}`，并注明它是**记录而非执行授权**。

## 5. 测试命令、结果与产物

| 层 | 命令 | 结果 |
|---|---|---|
| 后端过渡回合（确定性） | `pytest tests/test_structured_transition_turn.py -q` | **28 passed** |
| 后端全量 | `pytest -q` | **1336 passed / 7 skipped / 0 failed**（3:19）；`ruff check .` All checks passed；`tools/check_architecture.py` 通过 |
| 前端单测 | `npx vitest run` | **727 passed / 66 files**；`tsc --noEmit`、`prettier --check`、`npm run build` 通过 |
| 真实后端 + 真实前端 E2E（本功能） | `npm run build && npx playwright test e2e/structured-transition-turn.spec.ts` | **1 passed**（16.9s，人类主持、零模型调用） |
| 前端 E2E 全量（不含 live） | `npm run build && npx playwright test` | **23 收集 → 20 passed / 3 skipped** |

E2E 的三条 skip 都不是失败：`e2e/multiplayer.spec.ts:890`（需要 Electron 运行环境）、
`e2e/staging-recovery.spec.ts:8`（需要外部 staging 服务器）是既有环境门控用例；
`e2e/structured-transition-agent-live.spec.ts` 是**按需触发的真实模型 live 规格**（默认跳过，见 §7.2）。其余 20 条全过，含三客户端
`structured-human-3p`、云端单人 `structured-solo-online` 与本功能的
`structured-transition-turn`。

> 说明：更早的两次全量 E2E 记录写作「20 passed / 1 skipped（21 条）」，当时只有其中一条
> 环境门控用例触发了 skip；本批新增本功能用例后按 list reporter 逐条核对，确认 skip 恒为上述两条。
> 以后续这次实测为准。

`tests/test_structured_transition_turn.py` 覆盖对偶（17 条）：

| # | 用例 | 钉住的性质 |
|---|---|---|
| 1 | `test_intent_narrates_and_parks_without_moving` | 意愿 → 只叙事 + 持久待办，**位置不变**；事件含 `action_status{awaiting_player, awaiting}` |
| 2 | `test_follow_up_reply_context_carries_todo_and_transcript` | 追问时上下文带「未执行项/已告知」与**上一轮对白** |
| 3 | `test_player_decides_then_keeper_executes_once` | 决定 → 执行移动，旧待办收尾且清除指纹（不会二次触发） |
| 4 | `test_pending_todo_never_auto_executes` | 待办不是指令：纯叙事运行不移动、仍等待 |
| 5 | `test_change_of_mind_replaces_todo` | 改主意 → 旧待办收尾，不随后执行 |
| 6 | `test_commands_after_await_are_not_executed` | 结构保证：`awaiting` 屏障之后的命令不执行 |
| 7 | `test_completed_intent_is_not_re_parked` | 已终态请求不会被再次挂起 |
| 8 | `test_plain_clear_move_executes_without_extra_confirmation` | 普通明确移动**不**多出确认回合（`awaiting_parked=false`、一轮完成） |
| 9 | `test_arrival_grants_nothing_by_itself` | 抵达 ≠ 接见 ≠ 线索：移动只改位置，无 `clue_granted`/`handout_presented` |
| 10 | `test_model_failure_after_committed_fact_keeps_facts_and_todo` | 已提交叙事保留、待办保留、未执行的不发生、位置不变 |
| 11 | `test_check_does_not_disturb_awaiting_todo` | 与检定的关系：两条待办各自独立，互不覆盖 |
| 12 | `test_human_takeover_stops_agent_and_keeps_todo` | 人工接管：agent 记 `human_in_control`、0 条命令、待办留给人类 |
| 13 | `test_awaiting_todo_visible_only_to_owner_and_keeper` | 隐私：待办只对本人与主持可见 |
| 14 | `test_repeat_run_does_not_double_park` | 幂等：重复运行不重复挂起、不重复事件 |
| 15 | `test_human_keeper_can_park_without_pending_action_is_rejected` | 人类主持同能力：缺「尚未执行什么」被拒 |
| 16 | `test_human_keeper_parks_and_player_reply_resolves` | 人类主持：挂起 → 玩家回应 → 主持收尾（无需口令） |
| 17 | `test_room_keeper_frame_parks_and_other_players_cannot_see_it` | 云端/房间路径：`command_request` 过 schema 后落账，乙看不到甲的待办 |

验收 12 项对偶的对应：①→1、②→2、③→3（“不重复劝留”属叙事质量，真模型待验）、
④→5、⑤→8、⑥→结构性部分「不新增属性门槛」由“未引入任何新门槛”+8/9 保证（叙事质量真模型待验）、
⑦→9、⑧→10、⑨→14+E2E 第 7 步、⑩→12、⑪→13+17、⑫→本地 E2E + 17（云端）+ 读档对 `awaiting_player`
的处理引用既有 `tests/test_structured_branch.py::...` 断言（非终态请求读档后 `failed`，fail-closed）。

产物：

- 界面记录：`docs/screenshots/structured-transition-awaiting.png`（等待态：等你回应 + 尚未执行 + 已告知 + 输入可用 + 场景未变）、
  `structured-transition-awaiting-narrow.png`（窄屏 390×780：等待块不挤压、无横向溢出）、
  `structured-transition-decided.png`（执行后：场景已变、等待块消失）。
- 原始日志：`/tmp/full_pytest_transition3.log`（后端定稿）、`/tmp/e2e_transition_final.log`（E2E 定稿）。
- 按钮/控件验收（仓库 skill `ui-button-check`）：本次**没有新增按钮**（等待块刻意不给「继续/取消」，
  并有用例断言不存在此类按钮），改动落在卡片区块与主持台表单字段；按该 skill 的意图补了窄屏断言
  （`document.scrollWidth - clientWidth <= 1`）与窄屏截图，未发现挤压或换行事故。
- 模组全流程游玩验收（仓库 skill `scarlet-playthrough-check`）：**未执行**——该流程需要真实模型额度
  （40–70 回合），本批无授权；本功能也未改动其触发清单里的场景切换/线索分发/战斗/SAN/结局结算逻辑
  （仅新增了「抵达不发线索」的断言）。

### 5.1 实际 UI 记录（摘自 E2E，真实本地后端 + 真实前端）

| 步 | 玩家/主持动作 | 界面事实 |
|---|---|---|
| 1 | 玩家输入「说实话，我想先看看莱特教授的尸体。」 | 顶栏仍是 **当前场景 · 密斯卡托尼克大学**；出现请求卡（守秘人待办）；发出的是 `action_request{kind:"freeform"}` |
| 2 | 主持：`publish_message`（法伦谈停尸房要医生放行）+ `resolve_intent{resolution:"awaiting_player", pending_action{kind:"move",note:"尚未出发前往停尸房"}, disclosed:["停尸房需要值班医生放行"]}` | 卡片变 **等你回应**：尚未执行：尚未出发前往停尸房 / 已告知：停尸房需要值班医生放行 / 「直接说话回应就行…」；**位置没有变化**；输入框可用 |
| 3 | 玩家输入「那位医生和我们熟吗？」 | 正常对话气泡；等待卡仍在；**位置仍未变化**（待办没有被自动执行） |
| 4 | 玩家输入「那麻烦你联系一下，我现在过去。」→ 主持 `publish_message` + `move_party` + 两条 `resolve_intent{completed}` | 顶栏场景名变为目的地；等待块消失；刷新后场景仍是目的地、不再出现等待块 |

全程人类主持、零模型调用（模型桩计数在切换成 human 之后不变）。

## 6. 覆盖范围

| 维度 | 状态 |
|---|---|
| `execution_profile` | **只影响 structured_v1**。legacy 世界未改一行代码，旧回合管线与关键词/发现推断移动保持原行为；本功能在 legacy 世界不出现（也不会被误触发） |
| `keeper_mode` | human / assisted / agent 共用同一条命令与状态：human 用主持台表单、agent 用决策字段 `awaiting`（runner 转成同一命令）、assisted 草稿同样可建议 `resolve_intent{awaiting_player}`（批准后由主持提交） |
| 入口 | 本地 `/ws`（E2E 实测）与云端房间 `command_request`（后端帧级测试）共用同一命令服务与同一前端组件；房间事件按 principal 过滤 |
| 存档 | 待办在 `player_requests`，无新迁移。**分支**复制非终态请求（含 `awaiting_player` 与 `payload.awaiting`）。**读档**按既有契约把非终态请求置 `failed`（fail-closed，可同 ID 重发）——即读档**不会**把待办静默恢复成「等你回应」，这是与普通刷新/重连的明确差异 |
| 刷新/重连 | 快照 `requests[]` 恢复公开待办（前端单测 + E2E 第 7 步） |
| 幂等 | 等待命令固定 `command_id=await-<request_id>`；重复运行不重复挂起（测试 9）；同载荷重发仍按既有幂等键处理 |
| 真实模型 | **未验证**（见 §7） |

## 7. 真实模型验收（2026-09-14；额度：账户可用 161.36 CNY；模型 `deepseek-flash`）

### 7.0 验收声明（修正）

上一版把「T1→T2→T3 全通过」写得含糊。**修正后的准确说法**：

- **平台侧机制**在真实模型下逐项验证成立：意愿回合不移动、等待落成持久待办、待办不会被
  自动执行、位置只由已提交命令改变、异常 payload 不再让运行崩溃、失败进入可诊断的暂停态。
- **Agent 主持的完整链条「意愿 → 追问 → 明确出发」在同一份代码 + 同一配置 + 同一模型的一次
  运行中未完整通过**（明细见 7.5）。因此**不声称该链条已通过**，也不把不同轮次的结果拼起来凑结论。
- **人类主持的同一条链条**此前已用真实后端 + 真实前端验收通过（§5、§7.2 的界面证据）。
- 每轮结果都带 `code_revision` 与逐文件 `code_fingerprint`（sha256 前 16 位），
  证明结论对应哪一份代码。

### 7.1 轮次与代码版本（不拼接）

| 轮 | 代码/配置差异 | S1 意愿 | S2 追问 | S3 明确出发 | 说明 |
|---|---|---|---|---|---|
| 1 | 硬顶 `max_tokens=4000` | 通过 | 中止 | 中止 | 两条 `finish_reason=length`、content 为空：推理吃光预算；同一长度白重试 |
| 2 | 尊重配置预算 + 空输出分类 | 通过 | 通过 | 失败 | 玩家已明确坚持，主持仍重复劝留、未执行移动 |
| 3 | 目录补 `outcome` 枚举 + 全拒重试 | 通过 | **失败** | 中止 | 玩家只是提问，主持却提交 `move_party` 把队伍带走 |
| 4 | 停等待办标记 + 长预算 | 中止（空输出） | 通过 | 中止（过慢） | 空输出在长上下文下仍偶发 |
| 5 | 收口版（schema 校验/四分类/派生/悬空修复），预算 16000 | 通过 | 通过 | 通过 | S4 截断中止、S6 因截断失败；命令层留痕当时还没包到运行器内部 |
| 6 | **冻结版**（+command_id 冲突修复 + 代码指纹），预算 32768 | 通过 | 通过 | **失败（见 7.5）** | 一次跑完 S1–S6；对偶见 7.5 |

### 7.2 轨迹证据（脱敏，已入库）

`tools/transition_real_model_check.py` 在**隔离测试世界**（`TRPG_RUNTIME_ROOT` 指向临时目录，
真实猩红文档模组）里，走完整生产链路（`action_request` → 自动调度 → BYOK 路由 → 真实模型 →
命令 → 事件），逐次记录：

- **实际模型输入**：`system` 提示词全文 + `user` 上下文（`snapshot`、`pending_requests`
  含 `deferred_player_intent`、`recent_public_messages`、`trigger_request_id`、`run_log`）；
- **请求参数**：`model`、**实际生效** `max_tokens`、`temperature`、`response_format`、`messages`；
- **模型响应**：`content`、`reasoning_chars`、`finish_reason`、`usage`（prompt/completion/total）；
- **命令尝试**：`kind`、`payload`、`command_id`、提交结果或**拒绝原因**（含 schema 拒绝）；
- **每回合**：最新玩家消息、场景前后、`moved`、请求状态与待办、服务端投递的全部事件、未决请求列表。

脱敏：报告只记 `api_key_present` 布尔值，不含明文密钥；世界是隔离测试世界，不碰任何真实存档。

入库位置：`docs/evidence/transition_real_model/`
（`trace_acceptance_frozen.json` = 验收轮、`trace_budget16000.json` = 第 5 轮、`trace_early_iteration.json` = 早期轮）。

**四项复核结论**（轨迹里的 `diagnostics` 字段逐次记录）：

| 检查 | 实测 |
|---|---|
| 历史截断 | 最近对话上限「最多 8 条、每条 400 字符」；各回合 `recent_message_count` 0→7，`truncated_recent_messages` 全为 0（未发生截断） |
| 角色顺序 | 每次调用都是 `["system","user"]` |
| 提示词冲突 | 发现并修复 4 处（见 7.3）；system 里「提问不移动」「坚持即执行」「待办只是记录」三条规则都在 |
| 待办误表示为已授权任务 | 上下文键为 `deferred_player_intent` 且带 `deferred_player_intent_is_authorization=false`；只要存在待办，该标记逐次为 true；提示词措辞同步 |

### 7.3 模型可见命令定义 vs 执行层校验（一致性）

对照脚本（已固化为测试 `tests/test_structured_command_catalog.py`）逐字段比对了
`COMMAND_CATALOG_BRIEF` 与冻结 schema，发现 4 处不一致并修复：

| # | 不一致 | 修复 |
|---|---|---|
| 1 | `present_information.target` 在目录里没写（schema 必填） | 目录写为 `target(必填)` |
| 2 | `advance_time.reason` 目录标成可选（schema 必填） | 改为 `reason(必填)` |
| 3 | `record_fact.audience` 目录标成可选（schema 必填） | 改为 `audience(必填)`，并修正脚本化测试里不合 schema 的 payload |
| 4 | 目录完全没有 `resolve_draft`（第 15 条命令） | 补上 `draft_id/decision/note?` |

更根本的一条：**Agent 生成的命令原先不过任何 schema**（只靠各领域函数零散校验）。现在
`src/structured/validation.py::validate_command()` 让 Agent 命令走与客户端帧**同一份**
`command_request.json`，payload 字段与枚举在进入执行层前就被拦下，拒绝原因会回喂给模型。
命令层与 Agent 运行器对「等待但没写 `pending_action`」也统一为**从原请求派生待办**，不再一边派生一边拒。

### 7.4 空输出与失败分类（含生效预算）

`agent_runtime.build_byok_caller` 现在把 `finish_reason`、`usage` 与**实际生效** `max_tokens`
交给运行器，运行器按四类分别记录（并写进暂停说明，玩家/房主能看到可操作提示）：

| 类别 | 判据 | stop_reason | 本次实测 |
|---|---|---|---|
| 长度截断 | `finish_reason=length` 且 content 为空 | `model_output_truncated` | 第 5 轮 S4/S6：`reasoning_chars=62880/64531`，`completion≈17k/19.8k`，预算 16000 不够 |
| 非截断空响应 | `finish_reason=stop` 且 content 为空 | `model_output_empty` | 第 4 轮出现 |
| 解析错误 | content 非空但 JSON 不可解析（重试 2 次） | `unparseable_decision` | 第 1 轮出现 |
| 调用超时 | `APITimeoutError` | `model_timeout` | 验收轮未出现（有确定性用例钉住） |

生效预算 = `min(房主配置, 32768)`，每次调用的 `request.max_tokens` 与暂停说明里的
`生效 max_tokens=` 都能核对；**不能把这些失败一律称作「推理预算问题」**——表中四类各有判据。

本轮另外修掉两个真实模型暴露的平台缺陷：

1. **兜底 `command_id` 冲突**：原 `f"{run_id}-cmd-{已提交数}"`，某一步全部被拒时下一步会重用
   同一 id → `duplicate_request_conflict`，整轮空转（第 5 轮 S1 实测）。现改为按尝试序号
   `f"{run_id}-s{step}-c{index}"`，并有用例钉住。
2. **无进展/连续零提交悬空**：空决策或连续两步零提交时，请求原先会停在 `queued`
   （玩家一直看到「处理中」）。现在停在明确的 `paused` 状态并可接管。

### 7.5 验收结果（冻结代码、同一模型、一次运行）

`code_revision=75516a4+dirty` + 逐文件 `code_fingerprint`（8 个文件已记入报告）；
BYOK 生效预算 32768。机械判定 + 人工复核如下：

| 场景 | 移动 | 机械判定 | 证据与人工复核 |
|---|---|---|---|
| S1 意愿（想先看看遗体） | 否 | **PASS** | 3 次模型调用，发布叙事并挂起；`awaiting_player`，位置未变 |
| S2 追问（那位医生和我们熟吗） | 否 | **PASS** | 主持以对话回答，未移动 |
| S3 明确出发（麻烦你联系一下，我现在过去） | 否 | **FAIL** | 主持**改成澄清目的地**（“是去哪儿？我这串钥匙只认两扇门…”）并挂起。需求允许「歧义时问一次」，但「承接已约定目的地」这条没有做到 → 该步**未通过** |
| S4 换说法（想去莱特的办公室看看） | 是 | RECORDED | `move_party` 提交并抵达 `wright_office`，叙述与门锁细节一致；属正确的承接与执行 |
| S5 取消（医学院先不去了） | 否 | **PASS** | 请求 `cancelled`，位置未变 |
| S6 普通直接移动（出发去办公室） | 否 | FAIL（机械） | 队伍**已在**办公室，主持明确说明「门不用再走一趟」并等待 → 行为正确，是我的机械期望（必须移动）在此前提不适用 |

**结论**：链条「意愿 → 追问 → 明确出发」在这一次运行里**卡在 S3**：主持选择了澄清而非承接已约定
的目的地；这是**主持的判断**（提交的命令里没有 `move_party`，没有任何平台拒绝），不是平台机制失败，
也**不会**用新增关键词判断器或「为了清掉待办而自动移动」去绕过它——那两件事恰好是需求禁止的。
人工主持的同一条链条有通过记录（§5）；Agent 侧的这一步属于主持决策质量问题，需继续调提示词或用
更合适的模型/配置复核（对照实验见 7.6）。

### 7.6 未做：模型对照实验

按要求，**在排除上述因素（schema 一致性、失败分类、预算、平台缺陷）之前不做模型对照**。
本节记录：更强模型通过只能说明它对当前接口更适应，**不能自动证明平台没有问题**；本轮未做对照实验。

### 7.7 旧模式与模组行为（与上一条分开标注）

`tools/playthrough.py`（仓库 skill `scarlet-playthrough-check`）用真实模型跑完整**旧模式**主线：
38 回合、17 个 beat、**33/33 断言通过**、结局 `truth_and_seal`。它验证的是 legacy 路径与模组行为
未受本轮改动影响，**不是**结构化过渡回合的验收证据。

## 8. 未解决问题（本轮收口后）

1. **Agent 主持链条卡在「明确出发」**（§7.5）：主持选择澄清而非承接已约定目的地，该步未通过。
   平台不会、也不应该用关键词判断器或自动执行待办来绕过它；需要继续调提示词或用更合适的模型/配置复核。
2. **主持判断在同一提示词下不稳定**：同一场景不同轮次出现「坚持时继续劝留」「提问时移动」「明确出发时改澄清」
   「换说法时移动」等不一致行为；已把每次决策与命令留痕（§7.2），便于逐轮对照。
3. **推理型模型的输出预算**：`deepseek-flash` 的 reasoning 会占用 `max_tokens`，长上下文下单次可超 16k；
   绑定里建议 ≥32768（本轮验收用 32768）。截断/空响应/超时现在分别可诊断（§7.4）。
4. **读档 vs 刷新**：读档按既有契约把等待中的待办置 `failed`（fail-closed），玩家侧只看到「处理失败」。
5. **live 界面规格只跑过 T1**：`e2e/structured-transition-agent-live.spec.ts`（默认 skip）在浏览器里
   验过意愿回合（等你回应 + 场景未变 + 截图），T2/T3 未在浏览器里跑完。
6. 结构化世界里连接时仍插一条旧文案「已连接到守秘人……」（`frontend/src/ws.ts:433`）；
   主持台枚举仍显示英文原值。
7. `rate_limited` 仍未实现（Kimi 既有登记，与本功能无关）。
