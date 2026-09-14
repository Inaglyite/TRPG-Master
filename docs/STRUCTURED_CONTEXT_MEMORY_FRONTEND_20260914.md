# 上下文与记忆改造：前端适配、独立取证与联合验收（2026-09-14）

分工与边界（本轮）：**Kimi** 负责后端/存储/上下文组装/检索工具/提示词与后端测试；
**zcode** 负责前端、真实请求轨迹核查、E2E 与联合验收。本轮**未修改任何 Kimi 的后端文件**
（`src/structured/*.py`、`migrations/`、`schemas/`、后端 tests、提示词均未改动），
发现的问题都以复现证据交回（见 §4）。协议与 fixtures 由 Kimi 先提出并冻结后再各自实现——
**本轮尚无「上下文/记忆」相关的协议增量或 fixtures 落地**（我核对过 `src/structured/` 没有新增
context/memory/retrieval 模块），因此前端只按既有 streaming 协议（structured-play v1）适配。

## 1. 验收基线（第一条要务）

### 1.1 保留的真实模型轨迹

沿用上一轮归档的验收轨迹：`docs/evidence/transition_real_model/trace_acceptance_frozen.json`
（冻结版代码 `75516a4+dirty`，逐文件 sha256 在报告里；`deepseek-flash`，生效预算 32768）。

### 1.2 S3「明确出发」的逐项核查（结论：**输入问题，不是协议/执行问题**）

完整取证：`docs/evidence/transition_real_model/S3_ROOT_CAUSE_FOR_BACKEND.md`。摘要：

| 核查项 | 结果 |
|---|---|
| 当次请求是否包含已讨论目的地及**稳定 ID** | 包含但**无绑定**：`snapshot.destinations=[{miskatonic_medical, 医学院},{wright_office, 莱特的办公室}]`，当前场景 `miskatonic_university`；没有任何字段把「过去」绑定到其中之一 |
| 最新玩家回应是否关联到正确待办 | `trigger_request_id=trace-3` 正确；但 `pending_requests` 里**只有本次触发**——S1 已 `declined`、S2 已 `completed`，**没有跨回合保留的「已讨论目标/未执行行动」** |
| 当前场景与目标场景是否混淆 | 无混淆；但「已讨论的目的地」无处安放 |
| 是否有互相冲突的上下文 | **有**：最近 6 条公开对话刚说过「遗体已移交、校方钥匙开不了停尸房的门」「这间屋里没有一位值班医生能对上号」，而结构化层仍把医学院列为已知目的地，且没有任何命令把这两句话落成事实 |

**定位**：失败在**上下文组装（第 2 层「当前交互状态」缺失）**。不是执行层（S3 没有任何 `move_party`
被提交或被拒）、不是协议层（稳定 ID 齐全、帧校验全过），也**不能**用「历史未截断」当成关键上下文完整——
`truncated_recent_messages=0`、6 条对话完整送达，而关键上下文根本没有结构化形式。

**修复归属**：由 Kimi 在后端补（会话级「已讨论/已约定目标 + 稳定 ID」、玩家回应与未执行行动的绑定候选、
主持叙事与权威状态的冲突标注）；**前端不替模型决定出发**，不新增关键词规则或用自动选目的地掩盖。

### 1.3 S6 用例重设计

原 S6 在队伍**已在**目标场景后仍期望 `scene_changed`（等于测原地移动）——那是**我的用例缺陷**。
已重设计（`tools/transition_real_model_check.py`）：

- 用例拆成两个隔离世界：`chain`（意愿→追问→明确出发）与 `duals`（换说法 / 普通直接移动 / 取消）；
- 每个移动用例声明前置条件 `require_different_scene`，起点已等于目标时记 **`INAPPLICABLE`**
  （既不算通过也不算失败，并写明原因）；
- 新增 `expect_destination` 校验（移动了但没到期望目的地也算失败）；
- `--dry-run` 可在**不调用模型**的情况下自检世界、前置条件与目的地是否可用（本轮已跑通）。

## 2. 前端改动（本轮）

| 文件 | 改动 |
|---|---|
| `frontend/src/ws.ts` | **读档后清理陈旧内容**：结构化世界读档是 CAS 回滚、没有消息历史，原先只加一条系统提示、聊天区仍显示存档点之后的「未来事件」；现在清空消息并提示「已回到存档点，聊天区已重置」。legacy 路径行为不变 |
| `frontend/src/state/structured-committed-projection.test.ts`（新） | 位置只由已提交投影更新（叙事里写「你们到了」不移动；`scene_changed` 才移动）；前端无文本关键词解释器（含「取消/继续/现在过去」的消息事件不动待办） |
| `frontend/src/structured-load-reset.test.ts`（新） | 结构化读档清空聊天、legacy 保持原行为、读档失败不清空 |
| `frontend/src/react/components/structured/StructuredCards.test.tsx` | 取消后不再有可执行按钮；暂停可见且无转圈；等待确认时给出可重试提示而非无声转圈 |

协议版本：仍是 **structured-play 协议 v1**（`schemas/structured-play/v1/`），本轮前端**未新增任何协议字段**，
也未新增记忆编辑/删除类 UI（按要求不做大型记忆管理面板）。

既有保障复核（本轮以测试与 E2E 复核，未改行为）：过渡走正常消息流与人物气泡；等待时卡片离开
「处理中」、输入可用；待办提示只显示玩家可知的「尚未执行/已告知」且无强制确认弹窗；玩家可追问/改计划/
明确出发且不要求口令；当前场景只由服务端已提交投影更新；刷新/重连从快照恢复公开待办；
取消/替换后旧卡片不再表现为可执行；暂停与错误都可见。

## 3. 本地真实后端 E2E（人类主持，无模型；明确标注）

新增 `frontend/e2e/structured-interaction-duals.spec.ts`（临时 runtime root；开局那一步用**会计数的模型桩**，
判定部分**断言**不再调用模型）：

| 用例 | 覆盖 | 结果 |
|---|---|---|
| 对偶：换目的地 / 普通直接移动 / 取消 | 目的地由「前往」对话框**实时**列表挑选（快照列表移动后会滞后，不能用它当准）；请求提交时位置不变、主持执行后才变；换目的地时旧待办被取消且不会随后执行；取消后卡片变「已取消」且不再可执行 | **PASS** |
| 刷新不丢公开待办，且同一动作只落账一次 | 挂待办 → 刷新 → 卡片与「尚未执行」文案恢复、位置未变；主持执行后 `scene_changed` **恰好 1 次**；再刷新不重放 | **PASS** |
| agent 缺 BYOK：暂停可见、输入可用、可接管 | 刷新后能从快照恢复「已暂停（可恢复）」、输入可用、主持台入口在 | **PASS** |
| agent 缺 BYOK：**实时帧**必须让请求离开「处理中」 | 后端缺陷，见 §4.1 | **`test.fixme` 登记**（不冒充通过） |

截图：`docs/screenshots/structured-dual-plain-move.png`、`structured-dual-replace-destination.png`、
`structured-refresh-pending.png`、`structured-agent-paused.png`。

脱敏网络证据（帧级，不只看 DOM）：

- 既有 `structured-human-3p.spec.ts` 已断言**帧级隔离**：玩家 A 收到 `clue_granted`，玩家 B 的 WS 帧里
  `clue_granted` 计数为 **0**；本轮沿用该断言作为秘密隔离的帧级证据。
- 新增用例断言位置变化只来自已提交命令：`scene_changed` 帧计数与实际位移一一对应（刷新不重放）。

## 4. 交给 Kimi 的问题清单（附复现证据）

### 4.1 agent 世界缺 BYOK 时，暂停状态没有回帧（阻塞一条 E2E）

复现与根因：`docs/evidence/transition_real_model/BYOK_PAUSE_EVENT_DROPPED_FOR_BACKEND.md`。
`_run_keeper_agent` 的「模型路由不可用」分支把 `resolve_intent` 提交为 `paused`（世界状态正确），
但**丢弃了 `outcome["events"]`** → 客户端收不到 `action_status{paused}` → 卡片永久停在
「已提交，等待服务端确认」。探针实测事件只有 `['action_ack','intent_pending']`。

### 4.2 暂停/失败原因不在快照投影里

`session_snapshot.requests[]` 只有 `request_id/status/summary/awaiting`，没有 `detail`（或公开安全的
`reason`）→ 刷新后前端只能显示「已暂停（可恢复）」，看不到「缺 BYOK / 输出被截断」这类可操作说明。

### 4.3 需要后端补的第 2 层上下文（S3 根因）

见 §1.2 与 `S3_ROOT_CAUSE_FOR_BACKEND.md`：会话级「已讨论/已约定目标 + 稳定 ID」、玩家回应与未执行
行动的绑定候选、主持叙事与权威状态的冲突标注。**前端不会**用文本关键词或自动选目的地绕过它。

### 4.4 lint 门禁（在 Kimi 本轮新文件里）

`ruff check .` 当前有 1 处错误，在 **`src/structured/memories.py:22`**（`from typing import Any` 未使用，
F401）。不是我的文件，未改动；**CI 的 quality 门禁会因此变红**，请 Kimi 顺手清掉。

## 5. 短期衔接与长期回忆：分开的验收结果

| 层 | 状态 | 证据 |
|---|---|---|
| 1 权威世界状态 | 已有权威投影与「只由已提交事件更新」的保证 | 新增用例（叙事文本不移动场景）；快照驱动位置；E2E 位移只在主持命令后 |
| 2 当前交互状态（已讨论目标/未执行行动/已提醒/等谁回应） | **部分**：待办与「已告知」在等待期可用（`deferred_player_intent` + `disclosed`，且标注非授权）；**跨回合的「已讨论目标」缺失**（S3 根因） | 本轮取证 + 上一轮真实轨迹；待 Kimi 补会话级记录 |
| 3 近期对话 | 有界且已核（最多 8 条 × 400 字符，实测未截断） | 轨迹 `diagnostics` |
| 4 长期记忆检索 | **未实现**（Kimi 未交付该层） | 无代码、无协议、无 fixtures；本轮**不做**假设性验收 |

因此真实模型专项里的 **B 长期回忆、D 传闻与事实、E 缺失信息、F 无关记忆** 依赖第 4 层，
本轮**未验收**；**A 短期衔接**与 **C 知识隔离**可先用现有层做，但需先确认授权（见 §6）。

## 6. 真实模型专项（A–F）：本轮**未执行**，先确认授权与范围

按要求先声明，再做：

- **目的地与数据范围**：只在**临时 runtime root 的隔离测试世界**里跑（真实猩红文档模组），
  不触碰任何真实存档与生产环境；日志脱敏，只记 `api_key_present`。
- **模型与配置**：`deepseek-flash`（`.env.json`）、BYOK 生效预算 32768、同一冻结代码
  （跑前记录 `code_revision` + 逐文件 sha256）。
- **运行次数与额度**：计划 A–F 各 1 次 + 失败场景不重跑凑样本；按上一轮实测，单场景约 5–20k tokens，
  整轮预计 < 10 万 tokens（账户本轮前可用 161.36 CNY）。若需要重复运行，会先约定次数并同时报告
  总次数与失败次数，不挑成功样本。
- **A/C 可以先用现有层做**（短期衔接、知识隔离），**B/D/E/F 需等第 4 层落地**（Kimi 交付后）。

未获确认前，本轮只完成离线与本地无模型测试（§2、§3）。

## 6.5 轮次中的状态变化：Kimi 的上下文/记忆后端已开始落地

本轮工作过程中（12:13–12:22）出现了 Kimi 的新实现，**晚于**我这次验证所对应的代码：

- `migrations/versions/20260914_0016_structured_context_memory.py`
- `src/structured/interactions.py`（`InteractionThread`：`thread_id/status/pending_action/disclosed/waiting_on/last_request_id`，
  `public_projection`，以及新事件 **`interaction_updated`**，owner 定向）
- `src/structured/memories.py`（`CharacterMemory`：`insert/supersede/derive_from_commit/retrieve/project`）
- `service.py` / `domains.py` / `storage/database.py` 相应改动

结论与影响（**不拼接**）：

1. 本文 §1–§3 的验证对应的是**这些文件落地之前**的修订（HEAD `75516a4` + 我当时的未提交前端/工具改动）；
   它**不能**代表合并了 0016/interactions/memories 之后的行为。
2. `schemas/` 本轮**尚未**出现对应增量（我核对了文件 mtime），即**协议与 fixtures 还没冻结**。
   按约定「协议与 fixtures 由 Kimi 先提出并冻结，两边确认后各自实现」，前端**不猜** `interaction_updated`
   的载荷语义、也不提前适配。
3. 前端已做的一件事（不依赖协议冻结）：**未适配的结构化事件不再静默丢弃**。
   `applyStructuredEffects` 对未知类型记录到 `unknownEventTypes`，并在待办区显示
   「收到未适配的结构化事件：… （前端待按冻结协议适配；内容不会被静默丢弃）」。
   已有测试覆盖（`interaction_updated` 会被记录、已登记类型不会被误记）。
4. 第二步（等冻结后）：前端按协议把 `interaction_updated` 的公开投影接到「当前交互状态」展示，
   并在主持台按需做**主持只读**诊断（选用记忆来源/查询失败提示），且必须服务端授权。

## 7. 最终状态与未解决问题

- **HEAD**：`75516a4`（未提交改动 96 个文件，`diff_sha256=889425c9…`，见
  `docs/evidence/transition_real_model/BASELINE_20260914.txt`）；前端关键文件 sha256 同文件。
- 前端门禁：`vitest` **736 passed / 67 files**；`tsc --noEmit`、`prettier --check` 通过。
- E2E 全量：**27 收集 → 23 passed / 4 skipped**（6.1m）。四条 skip 全部有明确原因，不是失败：
  `staging-recovery`（需外部 staging 服务器）、`multiplayer:890`（需 Electron 运行环境）、
  `structured-transition-agent-live`（按需触发的真实模型规格，默认 skip）、
  `structured-interaction-duals` 里的 `test.fixme`（§4.1 的后端暂停回帧缺陷）。
  本轮新增的 `structured-interaction-duals`：**3 passed / 1 fixme-skip**，并在三个用例末尾断言
  **判定过程零模型调用**（`modelRequests.length` 不再增长）。
- 后端门禁（未改后端，仅复跑确认）：`ruff check .` 与 `tools/check_architecture.py` 通过；
  后端全量沿用上一轮 1336 passed / 7 skipped / 0 failed。

未解决问题：

1. 第 2 层「已讨论/已约定目标」缺失（§1.2、§4.3）——由 Kimi 补，前端不绕过。
2. 暂停回帧缺失导致实时「处理中」不消失（§4.1）——由 Kimi 修；对应 E2E 现为 `test.fixme`。
3. 快照投影缺 `detail`（§4.2）。
4. **读档/分支的 UI 端到端未验证**：前端侧已有确定性用例（读档清空聊天、终态清掉待办指纹），
   后端侧由 Kimi 的 `tests/test_structured_branch.py` 覆盖 reconcile；但我**没有**跑通
   「存档 → 推进 → 读档」的浏览器用例（存档面板的 DOM 钩子未稳定，需要先补测试钩子），
   **不声称该场景已通过**。
5. 长期记忆层未实现，B/D/E/F 未验收。
6. 结构化世界里仍有一条旧文案「已连接到守秘人……」（`frontend/src/ws.ts`，与记忆改造无关）；
   主持台枚举仍显示英文原值。
7. **协议未冻结期间不动前端适配**：`interaction_updated` 目前只做「不静默丢弃 + 明确提示」，
   载荷语义、公开投影字段与主持诊断接口等 Kimi 冻结协议/fixtures 后再实现（见 §6.5）。
8. 本轮验证的修订与 Kimi 刚落地模块**不同源**，联合验收需在协议冻结、双方实现到位后**整体重跑**
   （届时同时报告短期衔接与长期回忆两层的通过/失败，不拼接本轮结果）。

## 8. 联合验收（Kimi 的 M5 上下文/记忆落地后，2026-09-14）

### 8.1 Kimi 交付了什么

- 迁移 `20260914_0016_structured_context_memory`：`interaction_threads`、`character_memories`。
- `src/structured/interactions.py`：交互线程（第 2 层）——`thread_id/status(open|completed|cancelled|superseded)/
  pending_action/disclosed/waiting_on/origin_request_id/last_request_id`，`public_projection`，
  以及事件 **`interaction_updated`**（owner + 主持定向）。
- `src/structured/memories.py`：角色记忆（第 4 层）——`insert/supersede/derive_from_commit/retrieve/project`。
- 协议增量（`schemas/structured-play/v1/`）：新事件 `interaction_updated` / `memory_recorded` /
  `memory_query_result`；快照 `interactions[]` 与能力 `memory_query`；新帧 `memory_query.json`；
  新命令 `record_memory`；`resolve_intent.payload.thread{action,pending_action,disclosed,waiting_on,thread_id}`。
  配套 50+ fixtures 已含三种新事件与 `memory_query`。

### 8.2 前端适配（本轮，全部在我的区域）

| 层 | 改动 |
|---|---|
| 协议 | `STRUCTURED_EVENT_TYPES` 登记 3 个新事件；`InteractionThread`/`MemoryEntry` 解析器；能力 `memoryQuery`；`buildMemoryQuery` 帧构造 |
| 入口白名单 | `protocol/server-message.ts` 的 `serverMessageTypes` 登记 3 个新类型（**漏登记会在入口被丢**，`handout_presented` 与本次 `interaction_updated` 都踩过）；`ws.ts` 去掉重复的事件类型清单，改为引用协议层同一份 |
| store | `interactions`/`interactionOrder` + `memoryQuery` 状态；`applySnapshot` 读 `interactions[]`；`openInteractions` 只返回未结束线程 |
| effects | `interaction_updated` → upsert 线程；`memory_query_result` → 只进主持台状态；`memory_recorded` → 不改玩家视图；只接受本次 `query_id` 的结果 |
| UI | 待办区新增「当前交互」卡（状态 + 尚未执行 + 已告知 + “直接说话回应就行”，**无按钮**）；同一条待办被线程卡覆盖时不再重复渲染 awaiting 明细；主持台新增**只读记忆查询**区（能力门控 + 服务端授权） |
| 命令表 | `record_memory`（第 16 条）与 `resolve_intent.thread*` 字段；**可选枚举不再默认选第一项**（否则每次收尾都会凭空带上 `thread.action`）；枚举值本地校验（官方 invalid fixture `knowledge_type=fact` 现在前端也拒） |

### 8.3 对账中发现并修掉的漂移

1. **`resolve_intent` 顶层多了 `thread_action` 等复合字段**（E2E 抓到，服务端以 `invalid_action` 拒绝）：
   builder 未排除复合字段 → 已修。
2. **`interaction_updated` 在入口被丢**：`serverMessageTypes` 白名单未登记 → 已修（并合并重复清单）。
3. **同一待办显示两张卡**：新线程卡与旧 awaiting 明细重复 → 已按请求去重。
4. **打包接管表集漏 0016 的两张表**（与上一轮同类）：`LATER_TABLES` 未同步 → 已补，并新增
   **防漂移守卫测试**（从真实迁移链推导「head − 基线」表集，必须 ⊆ `LATER_TABLES` 且无冗余项），
   以后新增迁移不会再靠人记得。
5. 官方 fixtures 抓到的两处：`resolve_intent.thread` 字段未声明（已加 `thread_action/thread_id/waiting_on`）、
   `record_memory` 非法枚举未被前端拒绝（已加枚举校验）。

### 8.4 验收结果（本轮，同一工作树）

| 层 | 结果 |
|---|---|
| 后端全量 | **1367 passed / 7 skipped / 0 failed**（含她的 M5 与我的打包修复 + 新守卫）；`ruff check .` 全绿；`tools/check_architecture.py` 通过 |
| 前端单测 | **746 passed / 69 files**；`tsc --noEmit`、`prettier --check` 通过 |
| 前端 E2E 全量 | **29 收集 → 25 passed / 4 skipped / 0 failed**（6.3m）。四条 skip 都有明确原因：`staging-recovery`（需外部 staging）、`multiplayer:890`（需 Electron 环境）、`transition-agent-live`（按需真实模型规格）、`interaction-duals` 的 `test.fixme`（后端暂停回帧缺口） |
| 新增 M5 E2E | `structured-context-memory.spec.ts`：**交互线程 2/2 通过**——①主持开线程 → 待办区出现「当前交互」卡（无按钮）→ 刷新由快照恢复 → 位置不变；②`record_memory` → 主持只读查询命中并显示过滤条件/命中数 → **记忆内容不出现在聊天区** |
| 玩家侧隔离（帧级） | 三客户端 spec 新增断言：两名玩家的 WS 帧中 `memory_recorded`/`memory_query_result` 计数为 **0**，且玩家页面无 `keeper-memory-query` 入口 |

截图：`docs/screenshots/structured-interaction-card.png`、`structured-memory-query.png`。

### 8.5 交回 Kimi 的问题

1. **`cancel_threads_for_request` 定义了但没有调用方**（`grep -rn "cancel_threads_for_request" src/` 只命中定义）：
   主持只把请求置 `cancelled/completed` 时，由 `awaiting_player` 自动创建的交互线程**仍停在 `open`**，
   玩家侧会一直看到「当前交互 · 进行中」。E2E 实测需要显式 `resolve_intent{thread:{action:"close", thread_id}}`
   才能收尾（且 `thread_id` 必须从事件里取，控制台卡片上不显示 id）。建议：终态 resolution 时自动收尾
   关联线程，或在命令层允许省略 `thread_id` 时按 `last_request_id` 反查。
2. 控制台要关闭线程需要 `thread_id`，而公开卡片不展示它 → 若希望人类主持可用，建议在
   `interaction_updated`/快照投影里给出可直接复制的最小标识，或提供「关闭本条」的主持动作。
3. 第 5 节（`document-skills` 之外）的其余条目仍有效：真实模型 A–F 未执行（等授权确认范围与额度）、
   读档/分支的浏览器端到端仍未覆盖。

### 8.6 最终复跑与指纹

- E2E 全量：`npx playwright test` → **29 收集 / 25 passed / 4 skipped / 0 failed**（见 §8.4）。
- HEAD 与未提交指纹：见 `docs/evidence/transition_real_model/BASELINE_20260914.txt` 的最后一节。

## 9. 第二轮联合验收：提交授权后的协作与冻结版本（2026-09-14）

### 9.1 交付与提交（Part 一）

- 开发检查点 **`96ce488`**（同一共享分支 `experiment/keeper-platform`，**无需 cherry-pick**）：
  113 个文件 / +27962 −130，基线 `75516a4`。
- 范围：前端适配 + 验收测试 + 文档证据 + 两处**跨边界**改动（`packaging/pyinstaller_runtime_hook.py` 补
  0016 两张表、`requirements.txt` 补 `jsonschema`/`referencing`）+ 12 个共享测试文件的夹具修正。
- 边界纪律：**没有提交任何 Kimi 的在途文件**（`src/**`、`schemas/**`、迁移、她的 docs 与测试全部留在工作区）；
  未入库 `Key`/`.env.json`/真实存档/数据库/未脱敏轨迹（暂存前逐项扫过，证据目录为脱敏后副本）。
- 提交信息写明「真实模型专项未执行」，**不写「全部完成」**。

### 9.2 协作验收（Part 二）

六个协作行为，前端这一侧改为可执行的断言（`frontend/src/state/structured-interaction-behaviors.test.ts`，12 条）：

| 行为 | 断言位置 | 结果 |
|---|---|---|
| 追问不打断原交互（正常回答并等待是合法完成） | 单测 ①② + E2E `interaction-duals:574` | 通过 |
| 原动作完成/取消/替换只关「对应那一条」线程 | 单测（三种终态 + 多线程） | 通过 |
| 其他玩家/其他动作的线程不受影响 | 单测（三条线程只关一条） | 通过 |
| 刷新（快照）与实时投影逐字段一致、无幽灵待办 | 单测（快照 vs 实时 deep-equal） | 通过 |
| 暂停/失败离开「处理中」、给出原因与重试入口 | 单测 + E2E `interaction-duals:738/776` | 通过 |
| 线程是记录不是执行授权（卡片无执行入口） | 单测 + 卡片组件测试 | 通过 |

本轮前端侧修掉的漂移（都是对账出来的，不是猜测后端状态）：

1. **`awaiting` 的存活条件与后端不一致**：后端只在 `awaiting_player` 时保留 `awaiting`，前端原先只在终态清掉 →
   暂停/失败后实时态与刷新态不一致（刷新后为空、实时留着）。已按后端同一规则对齐（`structured-store.ts` 两处）。
2. **主持台的候选 ID 一直是空的**：`request_id`/`thread_id` 字段声明了候选来源，但 `requests: []` 是硬编码 →
   主持只能手抄卡片上不显示的 ID。已接上真实投影（待处理请求 + 开放线程），并新增 `threads` 候选源；
   `thread_id` 现在可在控制台直接挑（§8.5 第 2 条的一半由此关闭：ID 可挑，但仍不在玩家卡片上显示）。
3. **结构化世界没有分支入口**：控件要求 `latestBranchTurnId`，结构化世界恒为 null（详见 §9.4.2）。

### 9.3 冻结版本联合验收（Part 三）

**验收复核过两次版本**（Kimi 在我跑门禁期间又落了 `cae8900`，按「后端有改动就记录并复跑」的要求重跑）：

| 轮次 | 版本 | 结果 |
|---|---|---|
| 第一次 | HEAD `552672c` + 她在途改动（聚合指纹 `c5191e40…`） | pytest 1376/7/0；E2E 33→29+4；`ruff` 有 1 error（她的在途新文件） |
| 第二次（认证版本） | **HEAD `cae8900`**（她的「交互线程生命周期收口 + 暂停/错误回帧」提交，聚合指纹 `f111a7d6…`）+ 我这轮未提交的前端改动 | 见下表，全绿 |

第二次（认证）结果：

| 门禁 | 结果 |
|---|---|
| 后端 `pytest -q` | **1376 passed / 7 skipped / 0 failed**（214s） |
| 后端 `ruff check .` | **All checks passed**（第一次跑出的那 1 个 error 是她在途文件，她已在 `cae8900` 修掉） |
| 后端架构门禁 | `tools/check_architecture.py` 通过 |
| 前端单测 | **763 passed / 70 files**（+12 行为断言 +1 主持台候选 +2 合成信封 +2 快照 detail/awaiting） |
| 前端类型/格式/构建 | `tsc --noEmit`、`prettier --check src e2e`、`npm run build` 全通过 |
| 前端 E2E 全量 | **33 收集 → 29 passed / 4 skipped / 0 failed**（7.2m） |

四条 skip 的分类（**不折叠成「零失败即完成」**）：

| skip | 类别 | 原因 |
|---|---|---|
| `staging-recovery` | 环境跳过 | 需要外部 staging 环境 |
| `multiplayer:890` | 环境跳过 | 需要 Electron 源码进程环境 |
| `transition-agent-live` | 未执行的真实模型 | 需 `TRPG_LIVE_MODEL=1` 与单独授权 |
| `interaction-duals:650` | **已知缺陷 fixme** | 移动已抵达后关联请求的「尚未执行」明细仍挂着（§9.4.1），等后端同步请求终态 |

**真实模型专项（A–F）本轮仍未执行**：提交/推送授权不包含真实模型调用额度，未因提交授权自动开跑。

按 Kimi 的《给 zcode：后端接口增量与前端待适配点》，本轮又适配了两处（都属我的区域，且都有测试）：

| 接口变化 | 我的改动 | 断言 |
|---|---|---|
| 快照 `requests[]` 新增可选 `detail`（暂停/失败/等待的可操作原因） | 快照恢复路径读 `detail`；与实时路径统一「只有 `awaiting_player` 才有尚未执行明细」的规则（两处） | 单测（暂停原因刷新后仍在、终态不复活明细）+ E2E：BYOK 暂停刷新后能看到原因 |
| `request_error` 可能带 `event_id: 0, sequence: 0`（合成信封，不落库） | 定序器把 `event_id <= 0` 视为合成信封：**不参与去重**（否则游标 ≥0 时会被判重复而丢掉，错误提示到不了界面）、**不推进游标**（否则断线补发起点被拉回） | 单测两条（合成信封必到达、不移动游标、之后正常事件照旧去重） |

Kimi 提到的「追问收尾永不关原线程」与「原意图 `completed+success`/`cancelled` 自动收尾」两条语义，
本轮的追问 E2E 与单测正好覆盖（追问的 `origin_request_id` 不匹配，天然不关）。

### 9.4 交回 Kimi 的问题（本轮新增/更新）

0. **（2026-09-14 晚，已关闭）** 下面第 1、2 条都已由 Kimi 在冻结点 `8c2c58c` 修掉，
   并由我的**可执行验收**用例证明关闭（修复前失败、修复后通过，用例未改）：
   - 缺口 #1 → `frontend/e2e/structured-pending-sync.spec.ts`（2 条：移动收尾 + 实时/刷新一致、
     复合请求只清已落实部分）；
   - 缺口 #2 → `frontend/e2e/structured-branch-online.spec.ts`（结构化免 turn_id 建分支成功 +
     legacy 不回归仍带真实 turn_id）。
   逐条说明、门禁数字与复跑命令见 `docs/evidence/20260914_backend_gaps/README.md`；
   回执给 Kimi 的版本见 `docs/STRUCTURED_BACKEND_FREEZE_RECEIPT_20260914.md`。

1. ~~**移动已抵达后，关联的 `awaiting_player` 请求没有同步收尾（失效待办）**~~（已关闭，见上）：
   `domains.py auto_complete_move_threads` 会把目标一致的开放线程收尾为 `completed`，但请求仍是
   `awaiting_player`。线程卡消失后，旧的 awaiting 明细重新露出，卡片上写着「尚未执行：尚未出发前往X」，
   而队伍已经站在 X。证据与被否掉的断言：`frontend/e2e/structured-interaction-duals.spec.ts:650`（`test.fixme`，
   改回 `test` 即红）。建议在收尾线程时一并把关联请求置终态（与 `cancel_threads_for_request` 同形态）。
2. ~~**云端结构化世界仍然无法创建分支**~~（已关闭，见上）：`solo_branch_create` 要求非空 `turn_id`
   （`src/multiplayer/solo_timeline_ws.py:459`），结构化世界没有回合 ID。本地路径已通
   （`world_timeline_ws.py:85` 从当前已提交状态分叉，前端入口本轮打通），云端请给出等价入口
   （如允许空 `turn_id` 表示「当前进度」）。
3. ~~`tests/test_structured_pause_and_thread_lifecycle.py:27` 的 `StructuredError` 未使用 → `ruff` 红~~：
   你在 `cae8900` 已修，`ruff check .` 现在全绿。
6. **「抵达」只覆盖了线程，没覆盖请求**：你的交接文档写「状态联动已覆盖落实/取消/替换/抵达」，
   但实测「抵达」只把**线程**收尾，关联的 `awaiting_player` 请求仍悬着 → 玩家卡片继续显示
   「尚未执行：尚未出发前往X」（§9.4.1，fixme 已实测确实失败，复现证据在
   `docs/evidence/20260914_load_branch/probe_fixme_awaiting/`）。这条需要你确认是后端补收尾，
   还是协议上认定「抵达不等于请求终态」——若是后者，请给前端一个可见信号（例如请求也进终态、
   或在快照里明确该请求已由世界事实满足），否则界面会一直自相矛盾。
4. **§8.5 第 1 条的关闭确认**：`cancel_threads_for_request` 已有调用方（你在 `552672c` 补的玩家取消路径），
   该条关闭。剩余的是「主持把请求置终态时不自动收尾线程」，与第 1 条同源。
5. §8.5 第 2 条部分关闭：`thread_id` 现在可在控制台候选里直接挑（不再需要手抄），但公开卡片仍不展示 ID。

### 9.5 本轮新增用例与证据

| 产物 | 说明 |
|---|---|
| `frontend/e2e/structured-load-branch.spec.ts` | 读档回滚 / 重连 ≠ 读档 / 分支与记忆隔离（2 条，真实后端、零模型） |
| `frontend/src/state/structured-interaction-behaviors.test.ts` | 六个协作行为的前端契约（12 条） |
| `frontend/e2e/structured-interaction-duals.spec.ts` | 新增「追问保活」；BYOK 实时回帧由 fixme 改回真测试；新增失效待办 fixme |
| `docs/evidence/20260914_load_branch/` | README（逐项断言说明与问题分类）+ 版本指纹 + `probe_fixme_awaiting/`（fixme 确实失败的复现证据） |
| `frontend/src/protocol/structured.ts` | 合成信封（`event_id=0`）不进去重、不动游标（+2 单测） |
| `frontend/src/state/structured-store.ts` | 快照 `detail` 恢复；快照与实时统一「仅 awaiting_player 有尚未执行明细」 |
| 截图 | `structured-load-before/after.png`、`structured-branch-created.png`、`structured-dual-followup-alive.png` |

### 9.6 剩余未完成（不许并入「完成」）

- 真实模型 A–F（短期衔接 / 长期回忆 / 知识隔离 / 传闻与事实 / 缺失信息 / 无关记忆）：等单独授权与额度。
- 上面 §9.4.1 的失效待办、§9.4.2 的云端分支：后端未修前对应的 E2E 保持 fixme / 未覆盖。
- ~~BYOK 暂停原因不在快照投影里~~：后端已补 `detail`，前端已恢复显示（本轮关闭，E2E 断言刷新后可见原因）。
