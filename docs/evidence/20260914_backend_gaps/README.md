# 两个后端缺口的可执行验收证据（2026-09-14，后端冻结点 `8c2c58c`）

本目录记录两个确定性缺口的**可执行验收**（不是 fixme）：修复前真的失败、修复后自动转绿，
不需要改动用例本身。验收用例由我维护，后端修复由 Kimi 负责（`8c2c58c`）。

## 1. 缺口与结论

| 缺口 | 后端修复 | 验收用例（可执行） | 冻结点结果 |
|---|---|---|---|
| #1 移动落实后关联 `awaiting_player` 请求未同步 → 人已到达仍显示「尚未执行：尚未出发前往X」 | `src/structured/interactions.py::auto_complete_move_threads` 同步关联请求终态（线程级关联，不按目的地碰巧匹配；纯移动请求置 completed+success 并清 awaiting；更宽意图保留 awaiting 但移除已落实的移动部分） | `frontend/e2e/structured-pending-sync.spec.ts` A/B | **2/2 通过** |
| #2 云端单人结构化世界无法创建分支（`solo_branch_create` 硬要求非空 `turn_id`，结构化世界没有回合） | `src/multiplayer/solo_timeline_ws.py::_handle_branch_create` 结构化免 turn_id，改走 `create_structured_branch`，支持 `expected_revision` 钉住分叉点 | `frontend/e2e/structured-branch-online.spec.ts`（结构化） | **通过** |

## 2. 用例逐条说明

### 2.1 `structured-pending-sync.spec.ts`（本地结构化，人类主持，真实后端）

- **A. 移动请求完成后旧「尚未出发」消失，实时与刷新一致**
  挂待办 → 主持执行移动 → 断言 `.header-scene-name` 到达目标、页面上**不存在**任何
  「尚未执行：」文案 → 刷新后同样不存在 → 并且快照 `requests[]` 里不再有该请求停在
  `awaiting_player`。（修复前：实时与刷新都留着旧待办。）
- **B. 复合请求抵达后仍保留未完成调查，只清掉已完成的那部分**
  复合意图拆成两条记录：移动（move → 目的地）与调查（other，「尚未查看值班记录」）。
  抵达后：调查待办**仍在**、移动待办**消失**、刷新后同样如此。
  （修复前：移动请求未收尾，旧「尚未出发」继续显示。）
- 两条都断言**开局之后零模型调用**（人类主持的判定不碰模型）。

### 2.2 `structured-branch-online.spec.ts`（云端单人，TLS + 鉴权，真实后端）

- **结构化**：房主点「从当前进度创建分支」→ 服务端建分支并拆除房间 → 连接以
  `solo_world_switched`（`reason: branch_created`）重连到新分支世界；
  断言未被 `room_action_rejected` 拒绝、`trpg-active-world-id` 变成带 `-branch-` 的新世界、
  结构化入口仍可用、场景（分叉点世界状态）继承、**客户端发的帧里没有 `turn_id`**（不伪造）。
- **legacy 不回归**：云端单人**未勾选结构化**的世界仍从「最近完成回合」分叉，
  帧里必须带**非空** `turn_id`，且不带结构化字段。这条守住服务端
  `if not is_structured and not turn_id: reject` 的旧语义没被改坏。
  （这道口子需要先按 BYOK 配好自定义模型服务，用例里走选角页的模型设置面板完成。）

### 2.3 请求/响应形态（与 Kimi 交接文档 §4 一致）

```
请求：{"type":"solo_branch_create","label":"…","expected_revision":12}   // 结构化：无 turn_id
响应：solo_world_switched{reason:"branch_created", world_id, label}      // 与 legacy 同形
```

前端按此实现：结构化世界不带 `turn_id`、带 `expected_revision`（当前已提交 revision，
服务端不一致即拒绝，避免用户以为分叉的是旧状态）；旧世界仍只带 `turn_id`。
单测：`frontend/src/panels-owner-guard.test.ts`（结构化/旧世界两种帧形态 + 多人房间不发）。

## 3. 修复前的失败证据（同一用例，代码未改）

- 我在 Kimi 修复落地前跑过这两个文件：`structured-pending-sync` A/B **两条都失败**，
  失败信息正是断言点（`getByText(/尚未执行：/)` 仍为 1、`getByText(/尚未出发前往/)` 仍为 1）；
  云端结构化分支那次 `solo_branch_create` 未带 turn_id 时服务端**没有**建分支。
- 更早一轮（duels 里的同缺陷断言）保留了失败截图与页面快照：
  `../20260914_load_branch/probe_fixme_awaiting/`。
- 这正是「可执行验收」与 fixme 的区别：修复与否由断言本身判定，不靠人工改标记。

## 4. 复跑命令（冻结点上实测）

```bash
cd frontend
npm run build                                   # E2E 服务的是 dist，改前端源码后必须重建
npx playwright test e2e/structured-pending-sync.spec.ts \
                    e2e/structured-branch-online.spec.ts \
                    e2e/structured-load-branch.spec.ts \
                    e2e/structured-interaction-duals.spec.ts
```

结果：**12 passed**（含缺口 #1 的 2 条、缺口 #2 的 2 条、读档/分支 2 条、交互/对偶 6 条）。

## 5. 版本与门禁

```
后端冻结点（Kimi）：8c2c58c  （本地历史中已有，无需 cherry-pick）
我的工作分支：     experiment/keeper-platform（未 push）
```

| 门禁 | 结果 |
|---|---|
| `pytest -q` | 见下方「冻结点全量门禁」 |
| `ruff check .` | All checks passed |
| `tools/check_architecture.py` | passed |
| 前端单测 `npm test` | 766 passed / 70 files |
| `tsc --noEmit` / `prettier --check src e2e` / `npm run build` | 通过 |
| 受影响 E2E（上面 4 个文件） | 12 passed |

## 6. 未完成 / 不冒充完成

- **真实模型 A–F**：仍未执行（需单独授权与额度），不因本轮修复或提交而自动开跑。
- 云端结构化分支的**权限**：由既有 `timelineCapabilities()`（solo + 房主）与单测固定，
  多人房间不出现入口；E2E 未再加一条多人房间的端到端（与既有 multiplayer 套件重叠）。
- 分支的**记忆隔离**在本地真实库上已断言（`structured-load-branch`），云端分支未重复该断言。

## 7. 冻结点全量门禁（`8c2c58c` + 我的未提交前端改动）

| 门禁 | 结果 |
|---|---|
| `pytest -q` | **1385 passed / 7 skipped / 0 failed**（188s；Kimi 报的定向 216 passed 是子集） |
| `ruff check .` | All checks passed |
| `tools/check_architecture.py` | passed |
| 前端单测 `npm test` | **766 passed / 70 files** |
| `tsc --noEmit` / `prettier --check src e2e` / `npm run build` | 通过 |
| E2E 全量 | **37 收集 → 34 passed / 3 skipped / 0 failed**（7.9m） |

三条 skip 的分类（不折叠成「零失败即完成」）：`multiplayer:890`（需 Electron 环境）、
`staging-recovery`（需外部 staging）、`transition-agent-live`（需 `TRPG_LIVE_MODEL=1` 的真实模型规格）。
**本轮已无可计缺陷的 fixme**（duels 那条由 Kimi 在修复后翻回真测试并通过）。

### 7.1 第一轮全量跑出的真回归（我的改动引入，已修）

`structured-transition-turn.spec.ts:271` 失败：`request_id` 被我改成硬下拉（候选拾取）后，
**自由填写的能力被关掉了** —— 不在当前活动列表里的请求 ID 无法再输入，旧用例直接
`selectOption` 失败（"did not find some options"）。

修法：候选型 `id` 字段改为 **input + datalist**（可挑可填），既保留「不用手抄 ID」的收益，
又不剥夺自由输入。位置：`frontend/src/react/components/structured/KeeperConsole.tsx`。
复跑：`structured-transition-turn` 与 `structured-context-memory` 全绿，随后全量复跑通过。

这条正好说明「可执行验收 + 全量复跑」的价值：单跑新用例不会暴露它。

### 7.2 第二轮全量跑出的第二处回归（同样是我的改动引入，已修）

第一处修完后，全量又从 4 个既有用例里炸出来：`[data-field="clue_id"] select` 找不到了 ——
我把**所有**候选型 id 字段都改成了 input+datalist，把「服务端完整投影」的候选
（线索/NPC/场景/调查员/物品）也一起改掉了，而既有用例与 UI 约定都依赖它们的下拉。

修法：只对候选**天然不完整**的两类 ID 用 input+datalist —— `requests`（请求）与 `threads`（线程），
其余保持 `<select>`。复跑：受影响的 5 个 spec（含 `structured-play`、`structured-human-3p`、
`structured-screens`、`structured-solo-online`、`structured-transition-turn`）**11/11 通过**，
随后全量 34 passed / 0 failed。

## 8. 回执给 Kimi 的结论

- 缺口 #1、#2 在 `8c2c58c` 上**已被验收用例证明关闭**（用例本身未改，修复前失败、修复后通过）。
- 前端适配已按交接文档 §4 的帧形态落地：结构化不带 `turn_id`、带 `expected_revision`；
  旧世界只带 `turn_id`。多人房间不发（权限门禁未变）。
- 我这边剩余：真实模型 A–F 仍未执行（等单独授权），无其它阻塞项。

## 冻结点最终指纹（本文件所有结论对应此版本）

```
记录时间：2026-09-15T00:34:47+08:00
后端冻结点（Kimi，已推送）：8c2c58c
交接提交：38522b5
本地 HEAD（我未 push）：38522b5b1d91cba9f60bad79037434766361ca9e
我的前端改动：未提交（见下方文件表）
```
