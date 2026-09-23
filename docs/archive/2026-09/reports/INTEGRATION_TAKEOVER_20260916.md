# Codex 接手集成：初次复核

日期：2026-09-16；代码基线 `18fecb3`。本记录不授予生产发布或新增模型调用权限。

## 分工与状态

- Codex 接手 Kimi 的集成和后端复核；zcode 的前端修复尚未交接，不抢写其文件。
- 独立查询 GitHub run 35060615190：head 为 18fecb3，backend 成功、frontend 失败。失败日志确有 boot-loader intercepts pointer events。
- 保留全部未提交文档整理与他人截图；不合并 master、不发布、不使用模型额度。

## B3a：应分开的两个结论

原始证据：`docs/evidence/20260916_real_model/legacy_playthrough_report.json`，首个“搜查办公室：书桌抽屉、夹层、文件柜，找到莱特的私人日记”快照。

1. 权威结算为 executed_failure，success=false；clues_found 没有 wright_private_diary，office_searched=false。后续重复检定拒绝及叙事中的孤注一掷选项，支持“失败后 harness 没有走替代路径”的局部判断，但未实际证明该替代路径本轮通过。
2. 同回合 narrative 开头重复宣告从暗格找到私人日记，与权威结算冲突。不能将这一点仅称为重复文案的 cosmetic 问题。

源码路径：模组 world_state_initial.json 中发现规则 approach_text 已写成取得结果；transition_prelude.build_transition_prelude 原样拼入所有匹配规则；agent_graph 在检定/裁决效果结算之前播出，并在提示词中列为已结算前置事实。因此这是作者态文本与执行阶段契约的问题，不能归咎于模型强度或只增加去重。

下一步需分开接近描述与结果描述的用途，覆盖成功、失败、拒行、重复请求、历史模组的兼容边界；未获新模型授权前只做确定性验证，不能声称真实主线已复验。

## 其余复核注意

### 启动屏交接进展

已接收 zcode 的 127fb36 与 86168a6，二者已在共享分支，不重复 cherry-pick。独立复跑 preload/BootLoader 单测：17 passed，退出码 0。普通推送至 origin/experiment/keeper-platform，目标 SHA 86168a64f28741346903728d2ed18e9edd231b98；CI run 35067934053，结果待回填。没有暂存工作区文档或截图。

接受产品变更：非关键图片转后台预载，覆盖层有界退出，不回退到 180 秒全量图片阻塞。实际预算为 6 秒启动退场，另有 250ms 停顿与 900ms 卸载动画，不能声称 DOM 严格在 6 秒内移除。当前说明随覆盖层卸载，不是持久的后台资源状态提示。

归因范围：zcode 的网络取证排除了当页请求仍在等待；代码存在无界 decode 等待且已加上限，但缺少该次 CI 内 decode 的直接状态轨迹，不将其写成唯一已证根因。pending 请求的反证用例验证同类阻塞风险，不等同原 CI 的全部资源 200 场景。

本轮不重跑模型；A–F 沿用 Kimi 已交付证据，不能把 zcode 的“本轮未执行”理解成整个项目从未执行。

补验：独立执行 `npm run test:e2e -- e2e/boot-loader-readiness.spec.ts`，3 passed (22.4s)，退出码 0；临时运行目录、本地模型桩，无真实模型调用。远端 run 35067934053 后端 3m32s 完成并成功；约 16 分钟时前端仍在 E2E，API 明确运行中 job 无可下载日志。尚无全绿结论，未取消远端任务。

独立离线定向复跑：`tests/test_structured_agent.py`、`tests/test_structured_commands.py`、`tests/test_structured_trigger.py`，51 passed in 12.13s，退出码 0。受限沙箱首次运行在异步用例停住；单文件限时诊断退出 124，同代码解除沙箱限制后上述三文件通过。本轮未重新运行全量或真实模型，不能用该定向结果替代完整门禁。

- 模型报告中的按钮验收先以 legacy 开场，再切换 structured：证明按钮后半链路，不证明本地结构化创建/BYOK 入口。
- 报告中的直接 downgrade 会删除记忆与线程表，不能当作无损生产回滚方案；发布前按 OPERATIONS 制定备份与兼容方案。
- 现行状态统一更新到 docs/STATUS.md，原始轨迹不改写。
