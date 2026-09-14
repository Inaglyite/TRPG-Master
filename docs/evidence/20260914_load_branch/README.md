# 生命周期用例证据：读档 / 重连 / 分支（2026-09-14）

本目录记录本轮新增的两条真实后端 E2E 与它抓到的问题。

## 1. 用例与结论

| 用例 | 文件 | 结论 |
|---|---|---|
| 主动读档：存档点之后的进度不复活；重连不等于读档 | `frontend/e2e/structured-load-branch.spec.ts:460` | **通过** |
| 分支：结构化世界可从当前进度分叉，且检索不到原世界分叉后的记忆 | 同文件 `:561` | **通过** |
| 追问：原交互保持存活，直到原动作真的执行 | `frontend/e2e/structured-interaction-duals.spec.ts:574` | **通过** |
| agent 缺 BYOK：实时帧必须让请求离开「处理中」 | 同文件 `:776` | **通过**（原 fixme，Kimi `cae8900` 修复落地后改回真测试） |

## 2. 读档用例到底验了什么

1. 存档点之前记录一条记忆 → 可查（对照组）。
2. `save`（快速存档）落 `slot_000`；此后的一切都属于「被回滚的未来」。
3. 存档点之后：再记一条记忆（此时可查）、挂两条「尚未执行」待办、真实移动一次。
4. **重连**（刷新）不是读档：位置停在移动后的场景，待办卡仍在，`scene_changed` 计数不增（不重放事件）。
5. **主动读档**（`save_load`→`restore_structured_save`）：场景回到存档点、被回滚的交互线程不再显示、
   非终态请求收成 failed（不是继续「等你回应」）、存档点之后的记忆**查不到**、存档点之前的仍在。
6. 全程零模型调用。

后端的权威实现（供对照）：`src/structured/branch.py:351 restore_structured_save` 在回滚 revision 的同一事务里
删除晚于存档点的事件、线程与记忆，并把开放线程 cancel、非终态请求置 failed。

## 3. 分支用例到底验了什么

1. 分叉点之前记录一条记忆（共同经历）。
2. 结构化世界没有旧回合——此前**时间线面板的分支入口要求 `latestBranchTurnId`，在结构化世界里恒为 null，
   入口不可达**。前端已改为：结构化 && 本地时以「当前已提交状态」为分支点开放入口
   （`panels.ts createBranchFromCurrentTurn`、`PanelLayers.tsx`）。
3. 点击「从当前进度创建分支」→ `turn_branched`，`source_turn_id` 为空字符串，新 `world_id` 与原世界不同。
4. 分支创建后回到原世界新增一条记忆（界面一次只显示一个世界，这一步用同一真实后端的命令服务写入，
   属真实数据而非 mock）。
5. 真实库断言：`SOURCE` 同时含共同经历与分叉后新增的记忆；`BRANCH` 含共同经历、**不含**分叉后新增的记忆
   （按 `world_id` 隔离，与 `branch.py` 的注释一致）。

## 4. 本轮抓到、已登记给后端的问题

### 4.1 移动已抵达后，关联请求的「尚未执行」明细仍挂着（失效待办）

- 现象：主持记录「尚未出发前往X」→ 玩家追问（主持只回答）→ 主持执行 `move_party` 到达 X。
  开放线程被 `domains.py auto_complete_move_threads` 自动收尾为 `completed`，**但关联的
  `awaiting_player` 请求没有被同步收尾**。
- 后果：线程卡消失后，旧的 awaiting 明细重新露出来，卡片上写着「尚未执行：尚未出发前往X」，
  而队伍已经站在 X ——世界状态与待办自相矛盾。
- 证据：`frontend/e2e/structured-interaction-duals.spec.ts:650`（`test.fixme`，附完整可复现步骤）。
  本轮把该断言临时改成 `test` 实测**确实失败**（不是「可能有问题」）：失败信息为
  `expect(waitingCard(page)).toBeHidden()` 得到 `visible`，元素正是那条 `structured-awaiting`
  （「尚未执行：尚未出发前往X」）。复现证据：`probe_fixme_awaiting/`（失败截图 + error-context）。
  改回 `test` 即会红，后端同步请求终态后应转绿。
- 建议（权威侧）：收尾线程时一并把关联请求置终态（`completed`，note「已抵达目的地」），
  与 `cancel_threads_for_request` 的联动同一形态。

### 4.2 分支入口在结构化世界曾经不可达（前端侧已修）

- 时间线面板的分支控件位于 `active && manage` 区块内，且要求 `latestBranchTurnId`；
  结构化世界没有 Turn 记录 → 该值恒为 null → 即便服务端支持（`world_timeline_ws.py:85`）
  也点不到按钮。
- 已修：结构化 && 本地世界用「当前已提交状态」为分支点。**云端不含在内**：
  `solo_branch_create` 服务端要求非空 `turn_id`（`src/multiplayer/solo_timeline_ws.py:459`），
  结构化世界没有回合 ID 可给 → 云端结构化世界仍无法建分支，登记为后端能力缺口。

### 4.3 Kimi 在途修复：BYOK 暂停事件投递（已由本轮验证）

- 原缺口：`agent_runtime._run_keeper_agent` 的 BYOK 分支只 `execute_command`，不投递事件 →
  玩家卡片永久停在「已提交，等待服务端确认」。
- 在途修复：把 `outcome["events"]` 按 `deliver`/`broadcast` 投递（与其它暂停路径一致）。
- 本轮验证：`structured-interaction-duals.spec.ts:776` 由 fixme 改回真测试并**通过**
  （实时帧到达 `paused`、输入可用、零模型调用）。该断言从此是回归信号：修复被回退就会红。
