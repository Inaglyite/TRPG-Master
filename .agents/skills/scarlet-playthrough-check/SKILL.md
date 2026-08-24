---
name: scarlet-playthrough-check
description: 猩红文档全真机全流程游玩验收——改动场景切换/线索分发/战斗/SAN/结局/handout/检定相关代码后，用真实模型跑完整主线并核对 10 个验收面
type: prompt
whenToUse: 改动 src/ 中场景切换、线索分发、handout、战斗、SAN/疯狂、结局结算、检定匹配相关代码后；或大版本发版前
---

# 猩红文档全流程游玩验收

模组行为事故反复发生（场景切换漏匹配、审计 thinking 截断、handout 停发、
上下文毒化），根因都是**单测覆盖了局部逻辑但没人真玩一遍**。按下面流程做。

## 一、跑 harness

```bash
env -u PYTHONPATH .venv/bin/python tools/playthrough.py
# 指定报告路径：
env -u PYTHONPATH .venv/bin/python tools/playthrough.py --report /tmp/pt.json
```

- 全程**真实模型调用**：约 40-70 回合、15-40 分钟、消耗 DeepSeek 额度
  （有 context cache，前缀命中便宜）。报告含回合数与事件统计。
- harness 自建独立世界（world_id 记在报告里），**绝不碰已有存档**；
  profiles/player_profile.json 跑前备份、跑后恢复。
- 疯狂验证用注入（sanity_loss catastrophic），只发生在 harness 世界。

## 二、主线 beats 与通过标准

| beat | 内容 | 关键断言 |
|------|------|---------|
| B0 | 开局法伦办公室 | 场景=miskatonic_university；拿到两把钥匙 |
| B1 | 停尸房验尸 | 场景=miskatonic_medical；惠特克罗夫特在场；**照片发放**；wright_body_evidence 入册；有检定 |
| B1b | 威胁无辜者 | 弹确认门；放弃后无战斗状态 |
| B2 | 两个研究生 | 到过历史系/学生公社 |
| B3a | 莱特办公室 | wright_private_diary 入册（模组 source=wright_office，不在小屋） |
| B3b | 莱特小屋 | 场景=wright_cottage；cottage_searched flag |
| B4 | 精神病院 | 场景=arkham_sanatorium；hunter_copy 入册（确定性 discovery_rules talk/examine 双路 + 模型分发） |
| B5 | 分别调查买家 | 至少两个买家地点 |
| B5b | 多日监视古董店 | 案件时钟推进（monster_manifestation/human_pressure/clue_clarity 其一 >0）；为终局危机留出 pacing |
| B6a | 古董店找地下室 | 按场景构造走（隔断/厨房→活板门）；wicks_shop_searched / deep_basement_found / 开战其一 |
| B6b | 古董店伏击战 | 威胁响应式输入；goal=伏击战发生且已结束；终局危机成立（战斗/显形/解决其一） |
| B6c | 找回文档+徽章 | witch_trial_documents 入册或 documents_recovered；abner_seal 同回合多匹配入册（seal_obtained） |
| B6d | 徽章封印怪物 | 「我用银质徽章覆上图示」→ monster_defeated 确定性落定（end_game 前置 flag） |
| B7 | 疯狂注入 | SAN 压低（循环注到 san<30）；症状写入心理特质 |
| B8 | 结局 | on_game_over 触发且为 truth_and_seal |

剧本要点：每个 beat 只认**状态目标**（场景 id / flag / clue id / combat_state），
输入列表按序降级为 steering；不要在目标未达成时按固定顺序硬推进
（三跑教训：combat_start 后下一拍脚本就喊「战斗结束后搜查」，节奏全乱）。

## 三、10 个验收面 → 报告核对位置

1. **判定执行**：报告 dice_count > 0；beat 内「检定有执行」断言。
2. **场景切换**：每 beat 的「场景切换到 X」断言 + turn_snapshots 的 scene 序列。
3. **线索分发**：各 beat 的 clue 断言 + handout_count。
4. **威胁无辜者**：B1b 的确认门断言（decision_count ≥ 1）。
5. **结局进行**：B8 结局断言。
6. **结局后加成**：结局结算断言；profile 已回滚，如需验证加成内容看
   `settle_case` 的单测。
7. **保密**：全局「叙事未泄露 NPC secret 原文」。
8. **不提前说未知线索**：全局「叙事不提前说出未知线索原文」。
9. **弹药消耗**：引擎机制由 `tests/test_combat.py` 确定性覆盖；报告的
   「枪械弹药随射击递减」是 e2e 证据——模型走徽章封印等非战斗路径时不开枪，
   不计为引擎缺陷。
10. **疯狂**：B7 两条断言。

任何一项 fail：先看报告里对应 beat 的 turn_snapshots 与 narratives（世界还在，
world_id 在报告头部，可用 WorldBranchService.open 复查），再定位代码。
看 turn_snapshots 的 clocks/flags **逐回合演进**最快定位「哪一拍该发生没发生」。

**先定性再修**：fail 分两类，修法完全不同——
- *机制缺陷*：通道本身不通（模型从未见过数据 / 没有写通道 / 确定性规则缺失）。修引擎或装配。
- *模型自由度*：通道已验证可用，但真机里模型没选这条路。只能调提示词/职责授权，
  **不要去改引擎**。判别用最小探针：绕过叙事模型，直接向审计喂一段含目标事实的
  叙事文本，看 commit_turn 是否记账（2026-08 征兆探针：授权前 clocks_set 恒空，
  授权后 0→1，证明通道通、缺的是叙事模型写征兆的习惯）。

当前基线 **30/30**（七跑：truth_and_seal 全链确认——搜店→伏击真打
（dice 18、弹药 6→0、PC hp 10→1）→文档+徽章同回合入册→显形→B6d
确定性封印→结局结算）。终局机制链：crisis_triggers（feldman_ambush
带战斗/ink_manifestation 不带战斗）+ requires_flags 发现链 +
monster_sealed use 阀门。已知残余（不算回归）：
- 伏击战难度偏高（兄妹合并实体 hp 14、PC 手枪 45%），骰运差时 12 回合
  打不完且 PC 濒死——真实玩家应谈判/逃跑（三跑证明 combat_end 谈判路
  存在），harness 空枪后仍反复开枪是剧本不真实，不是引擎缺陷。
- approach_text 在叙事中重复 2-3 次（prelude 注入+模型回显），cosmetic。
跑分波动时先对照本清单，别把模型自由度/骰运当红 bug 修引擎。

## 四、跑偏处理

- 模型没按剧本走：harness 会在 beat 内自动重试更直白的输入（inputs 列表
  按序降级为 steering 句）；超过 max_turns 记 fail 继续跑。
- 新增 beat 时：goal 用世界状态（可判定），不要匹配叙事文本（措辞不稳）。
- 模型守门拦下剧本动作（如白天硬搜有人看守的店）时，先看它给出的
  「你可以——」选项并写进剧本，不要硬顶——六跑死锁 7 回合就是硬顶的结果。

## 五、事故案例（别再犯）

- 2026-08 危机战斗被暴力确认门逐枪取消：crisis 点燃的伏击战里，参战 NPC
  disposition=submissive → hostile_to_pc=false → 玩家每枪都触发
  「攻击非敌对者」确认门，harness 默认选取消，12 回合枪声不响
  （combat_action 记录全是 action_cancelled）。修复：`_participant` 支持
  spec 声明 hostile_to_pc，crisis 机制对危机战斗参战者默认注入。
  **教训：危机/伏击类战斗的 NPC 敌意必须机制化，不能只看 disposition
  社交面具；战斗空转先查 action_cancelled 而不是先怪模型。**
- 2026-08 好结局差一步永远够不到：end_game 同回合校验前置 flag，而
  「封印成功」只有叙事没有机制记录——模型先 end_game(good) 被拒
  （missing monster_defeated）→ 叙封印搏斗 → SAN 清零 → end_game(bad)
  被接受。修复：monster_sealed use 阀门（requires_flags 门控 +
  flag_effects monster_defeated），flag 在玩家行动回合开始前落定。
  **教训：结局前置条件所依赖的状态必须有确定性落账通道，不能靠审计
  事后追认；审计追认永远比同回合的 end_game 校验慢一步。**
- 2026-08 审计门时钟信号配额事故：`_clock_keywords` 全局 40 词段配额被
  monster_manifestation 的 levels 征兆词吃满，human_pressure 的「枪战」等
  行动词从未入表；真机 16/47 回合有征兆叙事但审计一次未跑，时钟全程 0。
  修复：每时钟独立配额 + advance_when 优先收割 + 周期性末日钟审计
  （声明时钟表的模组每 3 回合强制对账）。**教训：信号配额必须按来源隔离；
  时间发酵型机制（拖延也推进）不能只有关键词触发，必须有周期性兜底。**
- 2026-08 否定守卫跨标点误判：「趁店员不注意，检查隔断」被 `_NEGATED`
  当成否定检查（`不.{0,8}检查` 跨逗号），wick_shop_secrets 漏匹配，
  推进链差点断在第一步（周期审计从叙事里 flags_set 救回）。修复：否定
  词与动作词之间不得跨标点。**改守卫类正则必须配「真否定仍拦截」对偶测试。**
- 2026-08 skill 文案引导模型调已移除工具：keeper_pressure 等 7 个 skill
  让模型调 state_set/update_private_memory/get_npc_secret/sanity_loss/
  sanity_trigger/cache_scene——全是 H0 引擎专属，模型目录里不存在。
  修复：统一改 sanity_event/审计记账/权威状态直读，并加
  test_skill_catalog 扫描回归。**H0/H3 类工具目录变更后必须全量扫 skill 文案。**
- 2026-08 案件时钟全程为 0： pacing 规则写在模组非 spine skill 里，hybrid 档
  只加载 spine 内容，模型从未见过时钟表；且叙事模型无工具、审计负载里也没有
  case_clocks，时钟双通道都不可达。修复：keeper_pressure 加 spine 标记 +
  commit_turn 加 clocks_set（审计可见、只增不减）。**改提示装配/审计 schema
  后必须跑本验收**。
- 2026-08 时钟表进审计负载后仍然不记账：审计的工具契约是「只提交正文中
  已完成的事实」，时钟推进作为推断被它自己的保守契约否决（探针：同一段
  含征兆叙事，授权前 clocks_set 恒空，授权后立刻 0→1）。凡是让审计做
  推断类记账，必须在系统提示里显式授权「这是你的固定职责，不算虚构」。
  配套：时钟表数据化（case_clock_definitions）、每轮状态注入 next_level、
  clue_clarity 由 cmd_add_clue 确定性推进。
- 2026-08 B8 结局断言误读字段：on_game_over 回调给的是
  ending_type(good/neutral/bad/secret)+标题，不是结局 id；断言 id 永远 fail。
- 2026-08「拿着便签前往停尸房」场景漏匹配：移动动词前出现携行短语导致
  `_MOVE_ACTION` 不命中；修复加闭集携行白名单。**改 matcher 后必须跑本验收**。
- 2026-08「莱特教授的办公室」切不了场景：场景名「莱特的办公室」子串匹配被
  「教授」截断。场景别名要覆盖自然说法（"X教授的办公室"）。
- 2026-08 审计 commit_turn 不可解析：deepseek-v4 默认 thinking，推理 token
  吃掉 max_tokens 预算截断工具参数。结构化调用必须显式关 thinking。
- 2026-08 handout 停发：场景没切过去 → npcs_present 过期 → 照片只发给在场
  NPC。现象是「玩家见到 NPC 但没照片」，根因在场景状态。
- 2026-08 上下文毒化：show_handout 的 base64 图片留在消息历史（单条 223KB），
  容量熔断。图片载荷只走 WS 投递，不进模型历史。
