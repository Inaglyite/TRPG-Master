# 叙事语义标记：人物介绍 `【intro:id】` 与时间语义分层（2026-09-30）

## 背景

无声编钟申报馆开场事故：两名在场 NPC（苏晚晴、伊芙琳）同回合登场，公开人物卡只发了一张——
模型正文用"我姓苏/苏小姐"称呼，确定性兜底只认全名/`·` 短名，苏晚晴迟一回合才补发。
同一场次还暴露一致性闸门把 NPC 台词里转述的日期/病程（"三月十日""我盯了半个月"）
误判为时间越界，每回合误触重写且"重写后仍有争议"。

## 冻结的最小契约

### 人物介绍

- 模型叙事用点标记 `【intro:<npc_id>】` 声明"此处向玩家公开介绍了该人物"（一回合可多个）。
- 解析与 `【npc:id】` 发言标签同一状态机（`speaker_parser.parse_segments_full`），
  流式与定稿同路径；标记不产生文本，玩家可见流/消息历史/存档均无标记。
- 落账唯一通道 `npc_reveal`（与确定性兜底、审计 commit、模组 discovery rules 同账本，
  `revealed.level` + 条目判重去重）。校验：id 在权威 NPC 表 ∧ 在场 ∧ 未揭示；
  失败剥离 + 诊断日志，不中断回合。
- 权威转移：本回合有 ≥1 个可识别标记 → 关键词兜底（全名/姓氏称谓/特征 quorum）关闭；
  无标记或全未知 id → 旧兜底照旧。旧文本/旧存档行为不变。
- 公开身份：模组 NPC 可选 `display_name`（schema v1/v2 + 编译器，空值不落盘）；
  声明后人物卡条目与发言气泡一律用公开称呼，真名不进玩家可见载荷。
- 玩家输入的同款标记无效（解析只跑模型叙事）。

### 时间

- 世界时间只由裁决通道（`time_advanced`）推进；叙事/对白/日期文字不产生时间变更。
- `_time_semantics_note()` 注入故事模型：有结算报分钟数，无结算明说未推进；
  两种情况都区分"现实耗时 vs 台词/回忆中被谈论的时间"。
- 一致性闸门保留为诊断与兜底：跨度检查只看叙述层（剔除弯/直引号对白）；
  重写未消除任何越界时保留原文。

## 改动文件

- `src/gameplay/speaker_parser.py`：intro piece + `parse_segments_full`（`parse_segments` 签名不变）。
- `src/gameplay/turn_reconciler.py`：`apply_narrative_introductions`、
  `reconcile_narrative_entities(npc_backstop=)`、姓氏称谓别名与特征 quorum、`_npc_public_entry`。
- `src/app/agent_graph.py`：`_parse_final_narrative` 返回 intro_ids；finalize 接线
  （标记落账 → 可识别时关闭兜底）；`_time_semantics_note`；`[输出格式]` 加标记说明。
- `src/app/engine.py`：开场/改写契约字符串各加一句（标记用法/保留）；`_reconcile_narrative_entities` 透传开关。
- `src/gameplay/narrative_consistency.py`：对白剔除（弯/直引号）、重写零修复保原文、重写提示保留标记。
- `src/modules/module_format.py` + `module_compiler.py` + `schemas/trpgmod/module-v{1,2}.schema.json`：`display_name`。
- `src/web/asset_payload.py`：发言气泡载荷用 `display_name or name`。

## 验证

确定性（本地，全过）：
- `test_speaker_parser.py` IntroMarkerTests：剥离/收集/去重/发言段内标记/半角括号/旧右括号/空标记/逐字符流式一致。
- `test_turn_resolution.py` NarrativeIntroductionTests：双人同回合、简称自我介绍、不在场拒绝、
  未知 id 拒绝、重复幂等、display_name 不露真名、标记抑制兜底、finalize 全链路、玩家伪造无效。
- `test_outcome_contract_gates.py`：对白转述不触闸（弯/直引号）、叙述层越界仍命中、
  重写零修复保原文、`_time_semantics_note` 两类时间。
- `test_module_v2.py`：display_name 编译进世界状态/缺省不落字段。`test_asset_payload.py`：气泡载荷别名。
- 全量 1462 过 / 1 失败（`test_context_capacity` 为本机 `.env.json` 污染的既有失败，CI 不受影响）；
  `tools/check_architecture.py` 与 ruff 全过。
- 真实存档复现：无声编钟真实开场叙事（无标记）兜底双发；注入标记版本只发标记人物且无残留。

真机（真实 DeepSeek，与确定性测试分开记录）：
- 无声编钟开场探针 `/tmp/intro_probe.py` → `/tmp/intro_probe_report.json`：PASS。
  双卡同回合 tier-1、无标记/标签泄漏、无错误。本次模型未写标记（兜底发卡），
  标记采纳率属提示词调优空间，行为正确性不依赖它。
- 猩红文档全流程 harness 两轮：r1 30/33（B8：模型连写 8 回合收尾文本但未调 `end_game`，
  `game_over` 未落定；结局工具在目录内、无拒绝记录、通道完整）；r2 32/33
  （B8 3 回合正常落定 truth_and_seal；B5b 失败：裁决模型对"又盯了几天"只结算了分钟级
  耗时（350→485），未触发时间发酵，时钟持平——同一 build 的 r1 对相同输入结算了
  335→3215（两天）且时钟 0→2，机制通道完整）。两轮失败落在不同 beat、机制均在
  同 build 的另一轮被证明可用，定性为模型自由度波动，不按引擎缺陷修。
  裁决模型对"多日监视"类输入的耗时口径值得后续在裁决提示词侧观察调优。

## 已知限制 / 后续

- `docs/reference/MODULE_FORMAT.md` 的 `display_name` 文档待 Reasonix 的并行改动落定后补
  （避免混入未提交文件）。
- 模型对 `【intro:】` 的采纳率需要在更多模组上观察；若不达预期，下一步是提示词调优
  （开场契约已含用法说明），不动引擎。
- 旧档重放安全：历史/存档中的文本永远无标记；标记只在产生它的当回合生效。
