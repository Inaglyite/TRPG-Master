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
| B6a | 古董店找地下室 | 按场景构造走（假墙→厨房隐蔽区→活板门）；wicks_shop_searched / monster_manifested / 开战其一 |
| B6b | 古董店战斗 | 威胁响应式输入（不对非敌对开枪）；combat_state 建立过；monster_defeated（goal） |
| B6c | 找回文档 | witch_trial_documents 入册或 documents_recovered |
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

## 四、跑偏处理

- 模型没按剧本走：harness 会在 beat 内自动重试更直白的输入（inputs 列表
  按序降级为 steering 句）；超过 max_turns 记 fail 继续跑。
- 新增 beat 时：goal 用世界状态（可判定），不要匹配叙事文本（措辞不稳）。
- 模型守门拦下剧本动作（如白天硬搜有人看守的店）时，先看它给出的
  「你可以——」选项并写进剧本，不要硬顶——六跑死锁 7 回合就是硬顶的结果。

## 五、事故案例（别再犯）

- 2026-08 案件时钟全程为 0： pacing 规则写在模组非 spine skill 里，hybrid 档
  只加载 spine 内容，模型从未见过时钟表；且叙事模型无工具、审计负载里也没有
  case_clocks，时钟双通道都不可达。修复：keeper_pressure 加 spine 标记 +
  commit_turn 加 clocks_set（审计可见、只增不减）。**改提示装配/审计 schema
  后必须跑本验收**。
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
