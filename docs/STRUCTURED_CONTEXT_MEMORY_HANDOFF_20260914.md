# 给 zcode：后端接口增量与前端待适配点（2026-09-14 第二轮）

基线：552672c（检查点）之后的「修复与验收」提交。只列**新增/变化**，既有契约见
`docs/STRUCTURED_PLAY_PROTOCOL_V1.md` §10/§11 与本目录 fixtures。

## 1. 需要你适配/验证的接口变化

1. **快照 `requests[]` 新增可选 `detail`**（schema 已加，request_status_entry）：
   `paused`/`failed`/`awaiting_player` 时带可操作原因（如「守秘人助手未配置模型
   服务（BYOK）…可接管继续」）。建议暂停卡直接显示该文案。
2. **缺 BYOK 的暂停现在有实时帧**：降级路径会投递 `action_status{status:"paused",
   detail}`（audience public→全员可见状态变化）。你登记的 fixme 用例
   `structured-interaction-duals`「agent 缺 BYOK：实时帧必须让请求离开处理中」
   应可启用；通过后请去掉 fixme 标记。
3. **交互线程自动收尾**（resolve_intent 终态联动，无需手动关闭按钮）：
   - 原意图请求 `completed+success` → 其线程自动 completed；
   - 原意图请求 `cancelled` → 自动 cancelled；
   - `declined` / `completed+(failure|not_executed)` → 线程保留；
   - 追问收尾（origin 不匹配）永远不关原线程；
   - `interaction_updated` 事件照常推送，快照 `interactions[]` 同步消失。
   前端无需新增逻辑，只需确认「当前交互卡」随事件/快照正确消失。
4. **未知世界错误合成信封**：`request_error` 可能带 `event_id: 0, sequence: 0`
   （世界不存在/存储故障时的回退，不落库）。sequencer 请按内容处理，
   不要把 event_id=0 当成需要重放的游标回退信号。
5. **Agent 纯查询步**不再触发 `no_progress` 暂停：模型用 `queries` 查记忆时
   运行会正常继续到下一步。

## 2. 本轮不需要前端做的事

- 不需要为线程加「关闭」按钮（状态联动已覆盖落实/取消/替换/抵达）；
- 不需要在前端推断意图归属（候选绑定由后端 `candidate_thread_ids` 给出）；
- 玩家侧仍无全量记忆接口（`memory_query` 维持 keeper 专用）。

## 3. 联合验收请求

请在同一版本（最终提交 SHA 见交付报告）复跑：前端单测 + E2E 全量，尤其
`structured-interaction-duals` 的暂停回帧用例与 `structured-context-memory`
的交互卡用例；结果连同环境条件 skip 一起回执给我记入联合验收。
