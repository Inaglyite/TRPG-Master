# 给 zcode：后端接口增量与前端待适配点（2026-09-14 第二轮）

> 历史归档（2026-09-16 整理）：正文保留当时的结论与适用版本，不作为当前完成状态或执行授权。现行入口见 [README](../../../../README.md)，未完成事项见 [STATUS](../../../STATUS.md)。正文中的仓库路径按仓库根目录理解，`/tmp` 产物不保证仍存在。

基线：552672c（检查点）之后的「修复与验收」提交。只列**新增/变化**，既有契约见
`docs/reference/STRUCTURED_PROTOCOL_V1.md` §10/§11 与本目录 fixtures。

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

## 2.5 第二轮修复（本次提交新增）

1. **移动落实同步请求终态**：`move_party` 抵达时，除线程自动 completed 外，
   线程关联的 awaiting 请求在其待办「就是这次移动」（pending_action.kind=move
   且目的地一致）时置 completed+success 并清除 awaiting 明细——你复位的
   fixme 用例「移动已抵达后…」应直接通过。更宽意图（待办=调查等）保持等待，
   由主持后续更新。带 awaiting 的 action_status 事件 audience 收窄为本人定向
   （不再向其他玩家广播待办明细）。
2. **云端 structured solo 分支**：`solo_branch_create` 对 structured_v1 世界
   不再要求 turn_id，从当前已提交状态分叉；可传 `expected_revision` 钉住分叉点，
   不匹配拒绝（branch_failed + 版本提示）。legacy 路径不变（仍需 turn_id）。
   请求形态：`{"type":"solo_branch_create","label":"...","expected_revision":12}`
   （structured 世界省略 turn_id）；响应/广播与 legacy 相同
   （`solo_world_switched` reason=branch_created）。

## 3. 联合验收请求

请在同一版本（最终提交 SHA 见交付报告）复跑：前端单测 + E2E 全量，尤其
`structured-interaction-duals` 的暂停回帧用例与 `structured-context-memory`
的交互卡用例；结果连同环境条件 skip 一起回执给我记入联合验收。

---

## 4. 正式交接（后端冻结点）

**后端冻结 SHA：`8c2c58c`（experiment/keeper-platform 已推送）。**
后端代码与协议冻结，可以开始联合验收。我不再写入相关代码，直到你的验收结果返回。

1. 接口变化：
   - `session_snapshot.requests[]` 可选 `detail`（paused/failed/awaiting_player）。
   - 带 awaiting 的 action_status 事件 audience 收窄为本人定向。
   - `resolve_intent` 终态线程联动（§10.1）；移动落实同步请求终态（同上）。
   - `request_error` 合成信封可能带 `event_id: 0`（不落库回退）。
   - `solo_branch_create`：structured 世界免 turn_id、可传 expected_revision；
     请求/响应 fixture 即 `{"type":"solo_branch_create","label":"...","expected_revision":12}` →
     `solo_world_switched{reason:"branch_created"}`（与 legacy 同形）。
   - schema 变更仅 events.json 的 request_status_entry 可选 detail；其余为行为语义。
2. 定向测试（8c2c58c 上实测）：
   - 后端结构化+时间线+打包证据：216 passed / 1 skipped / 88 subtests。
   - E2E `structured-interaction-duals`：6/6（真实后端+真实前端）。
3. 回归测试落点：
   - 移动落实同步：`tests/test_structured_pause_and_thread_lifecycle.py::MoveSettlementTests`（4 项）
     + E2E `structured-interaction-duals.spec.ts:650`（fixme 已恢复为真测试）。
   - 云端结构化分支：`tests/test_solo_timeline_ws.py` 三个新用例（免 turn_id /
     revision_conflict 拒绝 / legacy 不变）+ `tests/test_structured_branch.py::
     StructuredBranchRevisionTests`（expected_revision 真实校验）。
4. 联合验收请覆盖：`structured-pending-sync`（你在途）、`structured-load-branch`、
   `structured-branch-online`（你在途）与既有三客户端套件；结果回执后我核对
   最终联合版本并补记录推送。
