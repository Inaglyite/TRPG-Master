# 结构化操作协议 v1：前端所需字段与联调说明

> 历史归档（2026-09-16 整理）：正文保留当时的结论与适用版本，不作为当前完成状态或执行授权。现行入口见 [README](../../../../README.md)，未完成事项见 [STATUS](../../../STATUS.md)。正文中的仓库路径按仓库根目录理解，`/tmp` 产物不保证仍存在。

日期：2026-09-13。面向：后端（Kimi）。状态：**M0 已冻结并与前端完成 fixtures 对账**；
M1（真实后端命令与 human 闭环）尚未落地，联调仍待进行。

对应代码：`frontend/src/protocol/structured.ts`（协议类型与校验）、
`frontend/src/protocol/keeper-commands.ts`（主持命令字段表，逐项对照
`schemas/structured-play/v1/command_request.json`）、
`frontend/src/protocol/structured-fixtures.ts`（当前为 spec-derived 夹具）。

## 1. 已对齐的部分（与 M0 schema 一致）

前端实现已按 M0 冻结文件逐字对齐，不需要你再改：

- `action_request` / `free_roll_request` / `check_response` / `command_request`
  的字段名、required、枚举、数值边界（含 `use_item` 的
  `operation=custom ⇒ approach 必填`、`presentation=original ⇒ physical_item_id 必填`）。
- `common.json` 的 `target`（npc/investigator/scene_object | unresolved）、
  `domain_outcome`、`error_code` 全量枚举、`dice_spec` 正则与限幅。
- 事件信封（protocol_version / event_id / world_id / sequence / revision /
  type / cause_request_id / payload）与 `speaker` 的 `{kind, id}` 形态。
- 14 个主持命令的 `payload` 字段表（`keeper-commands.ts` 逐字段标注 required/边界）。

## 1.1 与 M0 冻结产物的对账结果（2026-09-13，commit 912e11c）

前端已用后端官方 fixtures 跑对账测试（`frontend/src/protocol/m0-fixtures.test.ts`）：
valid 夹具必须被接受，invalid 夹具必须被拒绝。对账发现并修复了 4 处前端缺口：

| 缺口 | 修复 |
|---|---|
| `presentation=original` 但 `physical_item_id=null` 曾被接受 | zod 加 `superRefine`，对齐 M0 的 `allOf` |
| `free_roll_request.spec` 只校验长度，未校验正则 | 加 `DICE_SPEC_PATTERN`（M0 common.json 正则） |
| `command_request.kind` 接受任意字符串 | 加 `KEEPER_COMMAND_KINDS` 白名单（14 个）；`execute_arbitrary_sql` 这类请求前端即拒 |
| `handout_presented` 事件未登记 → 会被当成“无法识别的协议消息” | 加入事件集合并接入既有 handouts 展示链 |

对账中同时确认一致的部分：`server_capabilities` 的全部键、`session_snapshot.payload`
的 13 个必填投影字段、`common.json` 的 target/domain_outcome/error_code 全量枚举、
14 条命令的 payload 字段（前端字段表逐项覆盖，含 `from`/`to`/`audience`/`speaker`
的复合形态）。

## 1.2 与 M1 后端实现的复核（2026-09-13，`src/structured/`）

前端已逐项复核 `src/structured/service.py` 的实际输出，结论：

| 项 | 复核结果 |
|---|---|
| `server_capabilities` 的 13 个键 | 与前端读取器**完全一致**（含 `keeper_console`/`free_roll`/`assisted_draft`/`agent_takeover`/`check_request`/`move_action`/`present_clue`/`use_item`） |
| `session_snapshot.payload` 的 14 个键 | 一致（`keeper` 可为 `null`，前端已按可空处理） |
| `known_clue` | `additionalProperties: false`，只有 `id/category/text/presentation`。前端不再假设存在“关联实物 ID”，因此「展示原件」在没有该字段时**明确禁用并说明原因**，不会伪造物品 ID |
| `_visible_items` 的 `operations: []` | 前端在用法列表为空时自动落到 `custom` + `approach`，仍然满足 schema 的 `operation=custom ⇒ approach 必填` |
| 事件信封与 `speaker: {kind, id}` | 一致 |
| **WebSocket 接入** | **未接入**：`grep structured server.py src/multiplayer/*.py` 无引用，真实后端不发送也不会处理 `action_request`。前端据此保持“无结构化入口 + 旧模式可用”，并有真实后端回归用例证明（`frontend/e2e/structured-real-backend.spec.ts`） |

## 2. 前端需要、但 M0 目前还没有定义的字段（请补齐或确认）

前端按“缺字段 = 不支持（fail-closed）”处理，因此下面的字段缺失时对应入口会
禁用并显示明确原因，不会静默降级：

### 2.1 `server_capabilities`（必需，用于能力协商）

前端需要的键（`frontend/src/protocol/structured.ts: readServerCapabilities`）：

| 键 | 类型 | 用途 |
|---|---|---|
| `protocol_version` | int | 必须等于 1，否则前端停止结构化提交 |
| `structured_protocol` | bool | 总开关 |
| `execution_profile` | `"legacy" \| "structured_v1"` | 决定走结构化还是旧文字通道 |
| `keeper_console` | bool | 是否显示主持台 |
| `free_roll` | bool | 普通掷骰入口 |
| `check_request` | bool | 检定卡与“掷骰/放弃” |
| `move_action` | bool | 顶栏“前往…” |
| `present_clue` / `use_item` | bool | 面板“出示/使用”结构化提交 |
| `assisted_draft` / `agent_takeover` | bool | assisted 草稿批准、agent 暂停/接管 UI |
| `keeper_modes` | string[] | `human`/`assisted`/`agent` |
| `commands` | string[] | 主持命令白名单；也可替代上面若干布尔开关 |
| `protocol_version` | int | 版本协商 |

注意：`readServerCapabilities` 只认 snake_case 线上形态，**不认识已转换过的
camelCase 对象**，避免用伪造对象绕过版本校验。

### 2.2 `session_snapshot.payload` 的公开投影（必需）

| 键 | 类型 | 用途 |
|---|---|---|
| `revision` | int | `expected_revision` 来源；为 0 时前端拒绝提交 |
| `scene` | `{id, name}` | 顶栏“当前场景”（只显示 `name`，不下发描述） |
| `destinations` | `{id, name}[]` | “前往…”候选；**只给玩家已知目的地** |
| `targets` | `{kind, id, name}[]` | 出示/使用/主持命令的目标候选（npc/investigator/scene_object） |
| `clues` | `{id, category, text, presentation[], allowed_physical_item_ids[], asset_label?}[]` | 线索卡在结构化模式下**只渲染这份投影**；`presentation` 决定“展示图片/原件”是否可选 |
| `items` | `{id, label, quantity, operations[]}[]` | 道具卡同上；`operations` 是常见用法枚举（即兴用 `custom`+approach） |
| `investigator_id` | id | 当前玩家控制的调查员（`check_response` 的授权判据） |
| `pending_checks` | `{check_request_id, investigator_id, skill, difficulty, bonus_penalty?, attempt, known_cost?, visibility}[]` | 刷新后恢复待检定卡 |
| `keeper` | `{user_id?, mode}` | 主持台授权；**房主 ≠ keeper**，只有这里标了才显示控制台 |
| `server_capabilities` | 见 2.1 | 首连协商 |

结构化模式下：`clues`/`items`/`destinations`/`targets` 为空时，前端显示
“等待服务端投影”，**不会**用旧的文字标签冒充 ID（这是刻意的：不用文本当实体标识）。

### 2.3 事件侧

- `scene_changed.payload.scene.name`：只有它更新顶栏位置；`destinations` 可选随发。
- `check_requested` / `check_resolved` / `check_cancelled`：见 fixtures 里的字段。
- `roll_resolved.payload`：`{expression, dice:[{sides, values[]}], total, modifier}`。
  前端把 `sides===100` 映射成十位/个位骰面复用既有 3D 动画；缺字段则只显示文字。
- `message_started/chunk/completed` 的 `speaker`：`{kind, id?}`；`name` 可选
  （没有时前端用“守秘人/系统”兜底，绝不去正文里猜）。
- `request_error.payload`：`{request_id?, code, message, retryable}`；未知 code 前端用
  `message` 兜底显示，不会空白也不会丢事件。
- 事件去重只按 **event_id**（同 world 内单调）；`revision` 只用于状态更新，
  **不参与丢弃**（同 revision 的多条聊天事件必须全部渲染）。

### 2.4 尚未定义的出口（前端已按“能力缺失”处理）

- **主持专属资料**（秘密 NPC 设定、场景文档、规则检索）：`permission-matrix.json`
  声明了 `keeper.read_module_secrets` / `keeper.read_private_knowledge`，但没有对应的
  命令或投影字段。前端已实现显示路径：快照里出现可选的
  `keeper_material: [{title, text}]` 就直接渲染；没有时控制台明确写“服务端未提供，
  不显示也不假装可读”。建议 M1 二选一：把它加进 `session_snapshot`（按 keeper 过滤），
  或给出 `query_module`/`read_entity` 出口。
- **请求状态查询帧**：M0 未定义单独的 query。前端把“查询原请求状态”实现为
  **用同一 request_id 重发同一载荷**（依赖 M0 §5.3 的幂等去重），并显示
  “正在查询原请求状态”。若你要提供独立 query 帧，请给出帧名与字段，前端改一处即可。
- **`assets` 候选**（`present_handout` 的素材列表）：需要服务端投影素材 ID 与名称。

## 3. 前端的行为约定（联调时需要知道）

1. `execution_profile = structured_v1` 的世界**只**走结构化通道；协议不可用时
   明确报错并保留草稿，**绝不**退回 `{type:"action"}` 文字通道。
   `legacy` 世界完全保留旧路径（旧编辑器 + 文字回合）。
2. 提交成功 ≠ 行动成功：`sendStructuredAction` 只表示“已发出”，UI 用
   `action_ack`/`action_status` 显示处理中/等待/完成/拒绝/暂停；领域结果
   （`outcome`）单独显示。
3. 超时（8s 无 ack）自动同 ID 重发一次并标记“正在查询原请求状态”；用户也可
   手动“重试（同一请求 ID）”。拒绝后可用“重新编辑”用原载荷回填表单。
4. 切换世界会清空请求/待检定/候选绑定，旧世界迟到事件按 `world_id` 丢弃。
5. `room_event_id` 仍然由 `room-ws.ts` 的游标负责；结构化事件同时带
   `event_id`，前端两套游标都按单调递增处理。如果房间里的结构化事件不带
   `room_event_id`，它们会绕过房间游标直接进入结构化游标（当前实现）。

## 4. 联调步骤（M1 接入 WebSocket 后）

0. **先接 WebSocket**：当前 `src/structured/` 服务模块还没有被 `server.py` /
   `src/multiplayer/` 调用，所以真实后端不会下发任何结构化帧。前端的
   `frontend/e2e/structured-real-backend.spec.ts` 直接抓 WS 帧验证了这一点，
   接入后该用例会立刻变红（断言“不应出现 session_snapshot”），正好当作接入信号。
1. 后端在 `/ws` 与 `/ws/room` 首连时下发 `session_snapshot`（含 2.1+2.2）。
2. 用 `frontend/src/protocol/structured-fixtures.ts` 对账你的实际帧：
   `npx vitest run src/protocol` 会跑信封/枚举/边界校验。
3. 起真实后端，跑 `npx playwright test e2e/structured-play.spec.ts`：
   把 `e2e/helpers/structured-stub-server.cjs` 换成真实后端地址即可复用全部断言
   （断言核对的是 outgoing payload 与 UI 状态，不依赖替身内部实现）。
4. 三客户端 human 闭环（keeper + 2 玩家、无 Key）：
   - 本地：三个浏览器上下文连同一 `?mode=local` 后端（同一进程）；
   - 云端：`/?mode=online` 房间 + 房间内 keeper/receiver 授权。
   前端需要的正是 2.1/2.2 的投影；没有它们控制台与卡片会显示“等待服务端投影”。

## 4.0 M1 已接入 WebSocket 之后的实测结论（2026-09-13 晚）

**已跑通的真实后端链路**（`frontend/e2e/structured-real-integration.spec.ts`，通过）：

- 把测试世界切成 `structured_v1` + `human` 后，真实 `/ws` 首连即下发
  `session_snapshot`（`server_capabilities` 13 个键齐全），前端出现结构化入口；
- 顶栏位置来自真实快照；主持台的 `adjust_stat` 以真实 `command_request`
  被受理（无 revision 冲突）；`free_roll_request` 返回真实 `roll_resolved`；
- 全程 **零模型调用**（模型桩请求计数为 0）。

联调中由前端修复的真实缺陷（都不是靠改后端绕过）：

| 缺陷 | 修复 |
|---|---|
| 结构化请求被旧的“轮到你行动”门禁挡住，人类房间无法提交 | 结构化提交不再依赖回合制 `inputEnabled`；房间开启后同步 `gameStarted` |
| 房间从大厅进入 playing 只广播 `room_state`，`gameStarted` 不翻转 → 顶栏位置/前往入口不出现 | `applyRoomStateFields` 同步启动态 |
| 主持命令被要求提供 `investigator_id`（主持不必认领调查员） | `command_request` 不要求调查员身份 |
| 主持台在房间里拿不到调查员候选 | 候选并入房间调查员名册与成员信息 |
| 房间开局门禁仍要求房主选角（规格 §7 明确主持不必） | 结构化房间里房主豁免选角要求 |
| 主持台没有 Escape/焦点处理 | 补齐键盘可用性 |
| 主持台没有存档/续团入口、没有授权资料视图 | 补齐（含 `keeper_material` 可选投影） |
| M0 后新增的 `resolve_draft` 未登记 | 字段表 + assisted 卡片的批准/拒绝都走 `resolve_draft` |

**三客户端房间闭环已通过**（`frontend/e2e/structured-human-3p.spec.ts`，15 秒左右，
连跑 3 次稳定）：一位人类主持 + 两位玩家，无模型 Key 完成
① 主持定向私发线索给甲（乙全程收不到）→ ② 主持请求检定、甲在服务端持久检定卡上
掷骰（按钮不带任何可影响结果的参数）→ ③ 主持调整 SAN、甲的数值面板按事件更新 →
④ 主持 `move_party` 整队移动、两端场景一致 → ⑤ 存档入口可用 + 甲刷新重连后位置与
结构化入口恢复。全程零模型调用（模型 base URL 指向关闭端口）。

打通它需要补的三处（都在这一轮完成并有对应测试）：

1. **后端（跨边界，已标注）**：`handle_structured_room_start` 原本只翻转房间状态，
   没有把玩家认领的调查员物化进世界状态的 `investigators`，命令服务因此看不到
   任何调查员（`object_not_found: 调查员不存在：legacy-pc`），玩家 principal 也拿不到
   自己的 `investigator_id`。已在开局时按 **character_key**（结构化层的标识空间，
   与 `controlled_investigators`/`local_player_investigator_ids` 一致）物化名册。
   这个改动在 `src/structured/room_integration.py`，后端同学可直接复核/替换。
2. **前端**：`session_snapshot` 不参与 `event_id` 去重（服务端按 M1 约定复用当前
   最大 event_id 下发快照，去重会把开局后的首份快照丢掉 → 客户端一直用过 revision）。
3. **前端**：事件信封的 `revision` 必须让客户端游标前进（含 `request_error` 这类
   早返回分支），否则 `revision_conflict` 之后的重试永远还是旧版本。
   另：主持命令的错误按 `command_id` 归位（此前命令失败在界面上是静默的）。

## 4.0.1 三种入口的结构化模式暴露与验收现状

| 入口 | 在哪里开 | 验收用例 |
|---|---|---|
| 本地单机 | 世界 metadata 切档（`bootstrap.apply_profile_metadata`） | `e2e/structured-real-integration.spec.ts` |
| 云端单人（账号化私密世界） | 「我的冒险 → 开始新冒险」勾选**结构化操作模式（人类主持）**（`createSoloWorld(..., {structured:true})` → `execution_profile=structured_v1`） | `e2e/structured-solo-online.spec.ts` |
| 云端多人房间 | 「联机大厅 → 创建房间」勾选**结构化操作模式（人类主持）**（`createRoom(..., {structured:true})`） | `e2e/structured-human-3p.spec.ts` |

云端单人验收细节（真实后端 + 账号 + TLS，无任何模型 Key）：

- 勾选后创建冒险 → 选角确认 → **直接开局**，页面上没有门禁引导（`.model-gate-cta`）与“尚未配置”；
- 服务端下发 `session_snapshot` + `server_capabilities`，结构化入口出现；
- 前往 → `action_request{kind:move, destination_scene_id}`（无自然语言行动句），且
  **不会自动执行**：human 主持世界里它进入主持待办（这一步也验证了“玩家请求不等
  于出发”）；
- 主持台 `move_party` → 公开场景变为医学院地下停尸房所在场景，且**抵达不发线索**
  （`clue_granted` 为 0），验证“抵达不等于调查”；
- 掷骰 → `free_roll_request` + 服务端 `roll_resolved`；主持 `advance_time` →
  `command_request{kind:"advance_time", command_id, payload}`；
- 全程零模型调用，且没有回退到 legacy 文字回合（`action` 帧为 0）。

## 4.1 云端多人路径的验证现状（前端侧已完成）

房间路径与本地路径共用同一套事件处理与传输适配，前端已用真实传输栈验证
（`frontend/src/room-structured.test.ts`，8 例）：

- 房间 `session_snapshot` 进入同一 store 与投影，驱动顶栏位置与候选；
- 房间里的按钮发出的就是 M0 信封，**且房间传输不会注入 `action_id`**
  （M0 是 `additionalProperties: false`，多一个字段就是协议不兼容）；
- 房间镜像到达前的提交按房间队列排队，镜像到达后立即发出；
- `room_event_id` 游标与结构化 `event_id` 同时生效：重复帧只 ACK 不重复分发；
- 同 revision 的多条聊天事件全部渲染；
- 换世界（新快照）后旧世界事件被丢弃。

**仍未验证的部分**：真实云端后端（`/ws/room` 由服务器进程提供）上的三客户端
闭环，同样卡在 M1 未接入 WebSocket。前端侧只差把替身换成真实地址。

## 5. 当前夹具的来源标注（避免误读）

`structured-fixtures.ts` 与 `e2e/helpers/structured-stub-server.cjs` 是
**前端按 M0 schema 手写的 spec-derived 替身数据**，不是后端产物。
所有基于它们的测试（含 `e2e/structured-play.spec.ts` 与截图）都标注了
“测试替身后端”，不能当作与后端联调完成的证据。
真实后端联调完成后，请把 fixtures 换成后端导出的文件（或让前端从它生成）。
