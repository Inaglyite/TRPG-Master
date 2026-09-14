# 结构化操作跑团平台：前端阶段交付记录

日期：2026-09-13。分支：`experiment/keeper-platform`（Kimi 冻结的实验基线 + 双方未提交改动）。
契约版本：**structured-play 协议 v1**（`schemas/structured-play/v1/`，M0 冻结）。
状态：**前端阶段完成；联合验收已完成**——后端负责人收工后定稿复跑（`HEAD=75516a4`）：
整仓 `pytest -q` **1305 passed / 7 skipped / 0 failed**、`ruff check .` 与 `tools/check_architecture.py` 全绿、
前端 **715 单测**与 **E2E 20 passed / 1 skipped**（含三客户端与云端单人结构化闭环，全程零模型调用）。
**已验收范围限于人类主持闭环**；Agent 真模型闭环尚未验证，见 4.1。证据收口（59 失败分类、生产/夹具拆分、
对偶验证、打包升级、指纹与日志）见 `docs/evidence/20260913_joint_acceptance/README.md`。

## 1. 交付范围与方法

按主规格分工，主体改动在 `frontend/` 与前端验收产物。为收尾联合验收，另有两处跨边界改动：
`src/structured/room_integration.py` 的开局名册物化（已随后端负责人提交 `09db2f9` 落地，见 6.2），
以及 `packaging/pyinstaller_runtime_hook.py` 的升级表集补全（真实产品缺陷，见第 6 节末行）。

原则：按钮提交**明确对象与操作**的结构请求，主持（人类或 Agent）决定成立与否；前端不判成败、
不扣数量、不从正文推断位置或发言人；协议不支持时明确提示，不静默退回自然语言通道。

## 2. 文件清单

### 新增（frontend，25 个文件）

协议与传输：

| 文件 | 行数 | 职责 |
|---|---|---|
| `src/protocol/structured.ts` | 792 | 协议 v1 类型/校验/构造/事件游标/能力协商/错误码 |
| `src/protocol/keeper-commands.ts` | 877 | 主持命令字段表（逐项对照 `command_request.json`） |
| `src/protocol/structured-fixtures.ts` | 281 | 测试夹具（spec 派生，标注来源） |
| `src/structured-transport.ts` | 469 | 发送/接收适配、超时同 ID 重发、冲突用新版本重提 |
| `src/structured-effects.ts` | 285 | 结构化事件 → 既有 UI（消息/骰子/场景/角色） |
| `src/state/structured-store.ts` | 1027 | 请求登记、待检定、草稿、主持状态 |
| `src/state/structured-editor-store.ts` | 228 | 出示/使用编辑器草稿 |
| `src/investigator-panel-view.ts` | 122 | 面板双路径视图模型（legacy / structured） |
| `src/investigator-structured-actions.ts` | 284 | 出示/使用双路径决策点与提交 |

组件与样式：

| 文件 | 行数 |
|---|---|
| `src/react/components/investigator/StructuredActionDialog.tsx` | 455 |
| `src/react/components/structured/StructuredCards.tsx` | 约 400（ActionStatusCard / CheckRequestCard / 抽屉 / 掷骰面板） |
| `src/react/components/structured/MoveDialog.tsx` | 121 |
| `src/react/components/structured/KeeperConsole.tsx` | 约 560 |
| `src/react/components/structured/StructuredToolRow.tsx` | 57 |
| `src/react/components/structured/AssistedAgentCards.tsx` | 114 |
| `src/styles/components/structured.css` | 344 |

测试（前端 8 个文件 + E2E 6 个）：

| 文件 | 行数 |
|---|---|
| `src/protocol/structured.test.ts` | 436 |
| `src/protocol/keeper-commands.test.ts` | 332 |
| `src/protocol/m0-fixtures.test.ts` | 249 |
| `src/structured-transport.test.ts` | 674 |
| `src/structured-routing.test.ts` | 148 |
| `src/room-structured.test.ts` | 449 |
| `src/react/components/structured/StructuredCards.test.tsx` | 约 400 |
| `src/react/components/structured/KeeperConsole.test.tsx` | 248 |
| `src/react/components/investigator/StructuredActionDialog.test.tsx` | 386 |
| `e2e/structured-play.spec.ts` | 397 |
| `e2e/structured-screens.spec.ts` | 127 |
| `e2e/structured-real-backend.spec.ts` | 240 |
| `e2e/structured-real-integration.spec.ts` | 333 |
| `e2e/structured-solo-online.spec.ts` | 331 |
| `e2e/structured-human-3p.spec.ts` | 522 |

### 修改（frontend，21 个文件）

`ws.ts`(+68)、`room-ws.ts`(+9)、`options.ts`(+20)、`api/worlds.ts`(+11)、`protocol/server-message.ts`(+22)、
`react/GameShell.tsx`(+4)、`react/components/AppHeader.tsx`(+49/-2)、`GameControls.tsx`(+26/-2)、
`ModelSettingsPanel.tsx`(+18)、`ModelSettingsPanel` 相关、`investigator/{ClueCard,InventoryCard,InvestigatorPanel}`、
`online/{LobbyScreen,RoomScreen,SoloLobbyScreen}`（各含测试）、`styles/components/{investigator-panel,responsive}.css`、`styles/index.css`。

### 联合验收修复（跨边界，需后端负责人复核）

| 文件 | 改动 |
|---|---|
| `requirements.txt` | +2：声明 `jsonschema==4.26.0`、`referencing==0.37.0` |
| `packaging/pyinstaller_runtime_hook.py` | `LATER_TABLES` 补 0015 的 5 张表；列豁免泛化为 `LATER_COLUMNS`（产品缺陷，见第 6 节末行） |
| `src/structured/{agent,agent_runtime}.py` | 清 ruff F401 × 5（仅删未使用导入） |
| 后端测试夹具 | `test_world_branches`/`test_turn_journal`/`test_context_compaction`/`test_context_shadow`/`test_action_adjudication`/`test_test_runtime_isolation`/`test_model_timeout`（patch 目标迁移）、`test_solo_play_mode`/`test_multiplayer_membership`/`test_multiplayer_combat_routing`/`test_settlement_actor_reset`（真实夹具库）、`test_database_persistence`（legacy 原生 SQL 插入） |

### 文档与产物

- `docs/STRUCTURED_PROTOCOL_FRONTEND_CONTRACT_20260913.md`：前端所需字段、M0/M1 对账结论、三种入口验收矩阵。
- `docs/screenshots/structured-{desktop,narrow,present-editor,move-dialog,keeper-console}.png`。

## 3. 契约对账（后端 M0 官方 fixtures）

`frontend/src/protocol/m0-fixtures.test.ts` 直接读 `schemas/structured-play/v1/fixtures/`：
valid 必须被接受、invalid 必须被拒绝。对账抓出并修掉 4 处前端缺口：

1. `presentation=original` 缺 `physical_item_id` 曾被放行；
2. `free_roll_request.spec` 只校验长度未校验正则；
3. `command_request.kind` 接受任意字符串（`execute_arbitrary_sql` 本应前端即拒）；
4. `handout_presented` 未登记 → 会被当成“无法识别的协议消息”丢掉。

前端命令字段表与 M0 命令集合有防漂移断言（`resolve_draft` 加入后同步）。

## 4. 测试与构建结果（最终）

| 项目 | 命令 | 结果 |
|---|---|---|
| 前端单测 | `cd frontend && npx vitest run` | **715 passed / 65 files** |
| 前端类型 | `npx tsc --noEmit` | 通过 |
| 前端格式 | `npx prettier --check "src/**/*.{ts,tsx}" "e2e/**/*.ts"` | 通过 |
| 前端构建 | `npm run build` | 通过 |
| E2E（全量） | `npx playwright test` | **20 passed / 1 skipped**（5.4m） |
| 后端整仓 | `.venv/bin/python -m pytest -q` | **1305 passed / 7 skipped / 0 failed** |
| 后端 lint | `.venv/bin/python -m ruff check .` | **All checks passed** |
| 后端架构 | `tools/check_architecture.py` | 通过 |

> 上表是**后端负责人收工后**在 `HEAD=75516a4` 上的定稿复跑（默认用例顺序）；同一棵树上
> `git diff` 指纹与逐文件哈希见 `docs/evidence/20260913_joint_acceptance/state_fingerprint.txt`，
> 原始日志位置见同一目录的 `README.md` 第 5 节。整仓 pytest 起初是 **59 failed**，
> 逐类修完后为 0 failed，诊断与证据见第 6 节。

E2E 21 条用例中唯一 skipped 的是既有的 `staging-recovery.spec.ts`（需要外部 staging 服务器）。
结构化相关共 12 条（structured-play 6、structured-screens 2、real-backend 1、real-integration 1、
solo-online 1、human-3p 1）全部通过，其中：

- `structured-real-integration.spec.ts`：真实本地后端 + structured_v1 世界，快照驱动界面、主持命令落账、普通掷骰服务端结算、**零模型调用**。
- `structured-solo-online.spec.ts`：**云端单人**（账号 + TLS）勾选结构化模式 → 无 Key 开局 → 前往发 `action_request` 且不自动执行 → 主持 `move_party` 抵达医学院且不发线索 → 掷骰 `free_roll_request` → 主持 `command_request`，零模型调用。
- `structured-human-3p.spec.ts`：**三客户端**（1 主持 + 2 玩家）私发线索（甲收到、乙 0 条）→ 检定（甲独占响应）→ SAN（事件驱动面板）→ 整队移动（两端一致）→ 存档入口 + 刷新重连，零模型调用。
- `structured-play.spec.ts`：按钮 payload 取证（测试替身）、冲突恢复、跨世界与同 revision 事件语义。
- `structured-real-backend.spec.ts`：真实后端未启用结构化时不出新入口、不发结构帧、旧模式可用。

### 4.1 「已验收」的边界：人主持闭环 vs Agent 真模型闭环

| 路径 | 状态 | 依据 | 模型调用 |
|---|---|---|---|
| **人类主持 · 无模型闭环** | **已验证** | 上表 human-3p / solo-online / real-integration / structured-play 四条真机用例 | **零**：human-3p 与 solo-online 的 `OPENAI_BASE_URL` 指向关闭端口 `http://127.0.0.1:9/v1`；real-integration 用 `startModelStub` 并断言 `modelRequests` 计数不变 |
| **Agent 模式 · 真实模型闭环** | **尚未验证** | 后端仅脚本化调用者单测（`tests/test_structured_agent.py` 全用 `_ScriptedCaller`，无真实 SDK 调用）；前端 E2E 全程无真实模型 | — |

即：**「人类主持、不用 Key、不建模型会话」这条闭环有真机证据**；
**「Agent 用真实模型完成判断→命令→叙事→读结果」尚无任何真机证据**，需在取得 BYOK 授权后单独验证，
不能由脚本化调用者的通过来替代。

## 5. 联调证据（实际 outgoing payload）

三客户端实测抓到的关键帧（脱敏，来自 `structured-human-3p` 与 `structured-solo-online`）：

```
{"type":"action_request","protocol_version":1,"world_id":"world-…",
 "expected_revision":2,"investigator_id":"default:霍华德",
 "action":{"kind":"move","destination_scene_id":"miskatonic_medical"}}

{"type":"check_response","protocol_version":1,"request_id":"…",
 "world_id":"world-…","check_request_id":"chk-…","decision":"roll"}      ← 不带 skill/target_value

{"type":"command_request","protocol_version":1,"command_id":"cmd-…",
 "world_id":"world-…","expected_revision":2,"kind":"grant_clue",
 "payload":{"clue_id":"clue_001","recipient_investigator_ids":["default:霍华德"],"basis":"医生当面说明"}}

{"type":"command_request",…,"kind":"advance_time","payload":{"minutes":30,"reason":"驱车前往医学院"}}
```

服务端回应：`clue_granted`（仅走被授权成员的连接）、`check_requested`/`check_resolved`、
`action_status{status:"completed",outcome:"success"}`、`scene_changed`、`session_snapshot`（含 13 键
`server_capabilities`）。全程模型 base URL 指向关闭端口，**零模型调用**。

## 6. 联合验收收尾记录（2026-09-13 晚）

### 6.0 起因：范围化跑测低估了基线

前端阶段收尾时我只按范围跑了结构化相关用例，得出「3 个后端阻塞项」。本轮改为跑**整仓**
`pytest -q`，实际是 **59 failed / 1213 passed / 7 skipped**。先证明了它们不是本轮前端工作引入的：

```bash
git worktree add /tmp/trpg-head HEAD --detach     # 干净工作树，不含任何未提交改动
cd /tmp/trpg-head && pytest tests/test_world_branches.py -q
# → 14 failed（与带改动的工作树一致）
```

59 个失败按 **node ID 去重后逐条归因**（清单见
`docs/evidence/20260913_joint_acceptance/failures_59_by_class.txt`），得到 **42 / 14 / 1 / 1 / 1**：

> 早先本表写成 43 / 15 / 1 / 1 / 1（合计 61），与 pytest 的 59 对不上，**是本表的计数错误**，
> 差异来源有两处：① A 类把 `test_model_timeout.py::test_engine_openai_client_uses_configured_timeout`
> 重复计了一次（该用例的 `monkeypatch.setattr` 形式抛的是短句式 `has no attribute`，
> 41 + 1 = 42 已含它）；② B 类把当时被 `--deselect` 的挂起用例也算了进去，而 pytest 的
> “59 failed” 不含被 deselect 的那条。61 = 59 + 1（deselect 的挂起）+ 1（重复计数）。

| 类别 | 数量 | 根因 | 处理 | 改动性质 |
|---|---|---|---|---|
| A. 陈旧 patch 目标 `src.app.engine.OpenAI` | 42 | M1（`0e10105`）把客户端构造迁到 `src.structured.engine_gate.build_engine_client`，该 commit 已声明「测试 patch 目标迁至 `src.structured.engine_gate.OpenAI`」，但只改了 `test_outcome_contract_gates.py` 一个文件 | 按同一写法补齐 9 处（`test_world_branches`/`context_compaction`/`context_shadow`/`turn_journal`/`action_adjudication`/`test_runtime_isolation`）+ `test_model_timeout` 的 `monkeypatch.setattr` 形式 | 仅测试 |
| B. 夹具库无表 → 世界探针抛错（12 条 `no such table: worlds` + 1 条库打不开 + 1 条循环崩溃表现为挂起） | 14 | 连接循环开头会按 world 元数据探测结构化模式（`gateway.is_structured`、`structured_frame_gate_reason`→`room_world_modes`），而老夹具用 `database_url=lambda: "sqlite://"`（无表库）→ 探针抛错、**连接循环任务结束**；用例仍在 `await lease_waiting.wait()` → 表现为永久挂起 | 给夹具接真实临时库（`Base.metadata.create_all`）。涉及 `test_solo_play_mode`/`test_multiplayer_membership`/`test_multiplayer_combat_routing`/`test_settlement_actor_reset`；`FakeEngine.context` 补 `database_url` | 仅测试 |
| C. `test_readiness_db_error_fails_closed` | 1 | 老夹具用「打不开的库路径」表达「配置存储故障」，但该路径现在会先在探针处抛错 | 改为只建 `worlds` 表的库：世界元数据可读、模型配置表缺失——用一次真实存储故障保留原断言（`model_readiness_unavailable`、不提交、房间留 lobby） | 仅测试 |
| D. `world_members` 无 `can_keeper` 列 | 1 | 0004 旧库上插入 ORM `WorldMember`，而模型已含 0015 新增列 | 按该测试自身先例（`worlds` 也是原生 SQL）改用原生 SQL 插入 | 仅测试 |
| E. 打包 `LATER_TABLES` 缺 0015 表 | 1 | **真实产品缺陷**：`packaging/pyinstaller_runtime_hook.py` 的 `LATER_TABLES` 停在 0013，未纳入 0015 的 `player_requests`/`game_commands`/`check_requests`/`event_outbox`/`keeper_control`，且 `world_members.can_keeper` 未列入列豁免 → 旧的无版本号桌面库会被「无法接管未版本化数据库」拒绝，**打包版升级路径不可用** | 补全 `LATER_TABLES`，并把列豁免泛化为 `LATER_COLUMNS`（`worlds.root_world_id`、`world_members.can_keeper`）；fail-closed 契约仍全绿，并新增 `tests/test_packaged_upgrade_evidence.py` 钉住数据保留/新表与 `can_keeper`/重复启动/残缺库仍拒绝 | **产品代码** |

**更正上一版的表述**：不能笼统说「全部是陈旧夹具问题」。上表里 A–D 是测试夹具（12 个测试文件），
E 是产品逻辑修复；此外为收尾联合验收还改了 3 处非测试文件（见下表与 6.1/6.2）：

| 生产/配置代码 | 改动 | 是否改变运行时行为 |
|---|---|---|
| `packaging/pyinstaller_runtime_hook.py`（+23/−4） | `LATER_TABLES` 补 0015 的 5 张表；新增 `LATER_COLUMNS` 列豁免（`worlds.root_world_id`、`world_members.can_keeper`） | **是**（扩大可接管的旧库集合；安全方向：只是让本该升级的旧库能升级） |
| `requirements.txt`（+2） | 声明 `jsonschema==4.26.0`、`referencing==0.37.0` | 否（补齐 import 期依赖的声明） |
| `src/structured/{agent,agent_runtime}.py`（−5 导入） | 删未使用导入（ruff F401） | 否（已随后端负责人 `f6722a6` 一并落地） |
| `src/structured/room_integration.py`（+54/−3） | 结构化房间开局物化认领调查员名册（见 6.2） | **是**（新增行为，已由后端负责人提交 `09db2f9` 接纳） |

另：`src/structured/agent.py`、`agent_runtime.py` 的 5 处 ruff F401 与 1 处 UP034 已清
（CI `quality.yml` 的门禁是 `ruff check .` → `pytest -q` → `npm test`，lint 不绿会挡住整条链）。

`tests/test_structured_play_protocol.py::test_every_command_kind_has_a_fixture` 由后端负责人在
23:16 自行修好（内联 `COMMAND_KINDS` 补 `resolve_draft`），本轮未改该文件。

### 6.1 依赖声明（已补）

`src/structured/validation.py` 在 import 期使用 `jsonschema` 与 `referencing`，此前
`requirements.txt` 未声明（venv 里也只是临时装过）→ 干净环境任何 `uvicorn server:app`
都会 `ModuleNotFoundError`。现已声明 `jsonschema==4.26.0`、`referencing==0.37.0`。

### 6.2 跨边界改动（名册物化已由后端负责人接纳）

- `src/structured/room_integration.py`：`handle_structured_room_start` 原先只翻转房间状态，
  未把玩家认领的调查员物化进世界状态，导致命令服务看不到任何调查员
  （`object_not_found: 调查员不存在：legacy-pc`）。改为按 **character_key**（结构化层标识空间）
  物化名册、复用既有 `initialize_investigator_roster`，**未绕过任何授权**（服务端仍逐条校验命令）。
  该改动已随后端负责人提交 **`09db2f9`（M1 修补：结构化房间开局物化调查员名册）** 落地，
  附带 `tests/test_structured_room.py` 的名册/拒绝路径测试，视为已复核。
- `packaging/pyinstaller_runtime_hook.py` 的 `LATER_TABLES`/`LATER_COLUMNS` 补全（见上表末行）：
  语义是「基线指纹之后新增的表/列」，0015 之后再有新表需同步维护——这一处仍请后端负责人过目。

### 6.3 已接受的前端侧限制

- 主持专属资料（秘密 NPC 设定/场景文档）需服务端 keeper 查询出口：控制台读可选的
  `keeper_material` 投影，缺失时如实显示“未提供”，不假装可读。
- 房间尚未把 `can_keeper` 下发给客户端：前端以“结构化房间 + 房主”作为客户端可见的 keeper 判据，
  服务端 `keeper_required` 仍是最终边界。
- “重新编辑/用最新版本重新提交”在主持台浮层打开时需先收起浮层（浮层遮挡聊天区状态卡）。
- 分队后每个玩家各自的位置不在本轮（当前为共享场景的队伍位置）。

### 6.4 世界缺失 / 库故障的对偶验证（证据收口）

按交付收口要求新增 `tests/test_structured_world_probe_duals.py`（5 条），把「世界行缺失时按 legacy 处理」
这一**模式判定**的 fail-soft 与**访问权**切开：缺失世界不授权（认证用户 `not_authorized`、本地路径
fail-closed 且不留孤儿成员行、房间 4403 关闭且引擎零提交）；legacy 世界收到结构化帧得
`profile_mismatch` 且不落回旧回合；库确实打不开时零提交、零 ack、房间状态不变。
`tests/test_packaged_upgrade_evidence.py`（3 条）补齐打包升级的数据保留 / 新表与 `can_keeper` /
重复启动 / 残缺库仍拒绝。逐条对应关系与全部指纹见
`docs/evidence/20260913_joint_acceptance/README.md`。

### 6.5 由此发现的残余缺口（后端域，未自行修改）


由 `tests/test_structured_world_probe_duals.py` 的对偶用例暴露，安全方向都是 fail-closed
（无执行、无落账），但反馈不完整，属 `src/structured/gateway.py` / `src/multiplayer/messages.py` 范围：

1. **缺失世界的拒绝帧落不了库**：`_persist_request_error` 往 `event_outbox` 写 `world_id`
   外键指向 `worlds`，世界行不存在时 IntegrityError → 调用方拿到异常、客户端收不到拒绝帧。
   对**已存在**的 legacy 世界不受影响（`profile_mismatch` 正常回帧，已有用例覆盖）。
2. **探针遇库故障时连接循环直接结束**：客户端既无拒绝帧也无断开语义说明（`room_action_rejected`
   不会发出）。安全不变量成立（无提交、无 ack、房间状态不变），已由上述对偶用例钉住。

## 7. 复跑清单

```bash
cd /home/inaglyite/MyProjects/trpg-master
.venv/bin/python -m ruff check .                 # 期望 All checks passed
.venv/bin/python -m pytest -q                    # 期望 1305 passed / 7 skipped
.venv/bin/python tools/check_architecture.py     # 期望通过
cd frontend && npx vitest run                    # 期望 715 passed / 65 files
npx tsc --noEmit && npx prettier --check “src/**/*.{ts,tsx}” “e2e/**/*.ts” && npm run build
npx playwright test                              # 期望 20 passed / 1 skipped
```
