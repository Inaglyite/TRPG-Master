# 联合验收证据收口 · 结构化操作跑团平台（2026-09-13 → 09-14）

本文件对应交付记录 [STRUCTURED_PLAY_FRONTEND_DELIVERY_20260913.md](../../STRUCTURED_PLAY_FRONTEND_DELIVERY_20260913.md)
的第 6 节，只做**证据收口，不新增功能**。

## 0. 边界

- 未修改后端负责人正在维护的文件：`src/structured/**`、`src/multiplayer/**`、`tests/test_structured_*.py`、
  `schemas/**`、`docs/STRUCTURED_PLAY_PROTOCOL_V1.md`。
- 本轮新增（前端侧）：`tests/test_structured_world_probe_duals.py`（5 条对偶）、
  `tests/test_packaged_upgrade_evidence.py`（3 条打包升级）、本目录。
- 未提交、未发布；未在生产环境（`https://trpggame.xyz`）做过任何测试。

## 1. 59 个失败按 node ID 去重后的分类；61 的差异解释

基线（工作树快照，`HEAD=45a0352`，2026-09-13 23:2x）：
`59 failed, 1213 passed, 7 skipped, 1 deselected, 116 subtests passed`。
59 条 `FAILED` 行的 node ID **互不重复**（`sort -u` 后仍为 59），逐条归因结果见
[`failures_59_by_class.txt`](failures_59_by_class.txt)。

| 类 | 数量 | 归类依据（该 section 的首个 `E` 行） |
|---|---|---|
| A. 陈旧 patch 目标 `src.app.engine.OpenAI` | 42 | `AttributeError: ... does not have the attribute 'OpenAI'`（41 条）+ `has no attribute 'OpenAI'`（1 条，`test_model_timeout`） |
| B. 世界探针在夹具库上抛错 | 14 | 12 条 `no such table: worlds` + 1 条 `unable to open database file` + 1 条 `CancelledError`（循环任务已结束，用例仍在等待） |
| C. legacy 夹具库缺 `can_keeper` 列 | 1 | `table world_members has no column named can_keeper` |
| D. 打包 `LATER_TABLES` 缺 0015 表 | 1 | `RuntimeError: 无法接管未版本化数据库：缺少基础表 check_requests, event_outbox, game_commands, keeper_control, player_requests` |
| E. 后端在途 | 1 | `AssertionError: Lists differ: ['playing'] != []`（`test_structured_room.py`，当时后端负责人正在改写） |
| **合计** | **59** | 与 pytest 计数一致 |

**61 的差异来自本文件上一版的两处计数错误，`61 = 59 + 1 + 1`：**

1. **A 类重复计数 1 条**：上一版把 `tests/test_model_timeout.py::test_engine_openai_client_uses_configured_timeout`
   单独列了一次，同时又把它算进了「`has no attribute`」那一桶。两种措辞（`does not have the attribute` /
   `has no attribute`）来自同一批 42 条用例——按 section 统计为 41 + 1 = 42，不存在第 43 条。
2. **B 类多算 1 条**：上一版的 15 里包含被 `--deselect` 的挂起用例
   `tests/test_multiplayer_membership.py::test_start_revalidates_roster_after_room_action_lease`。
   它当时被显式 deselect（会永久挂起），因此**不在** “59 failed” 之中，属额外第 60 个问题。

那条挂起用例的机制不是猜测，而是 asyncio 任务栈实测（见 [`hang_asyncio_task_dump.txt`](hang_asyncio_task_dump.txt)）：
`run_room_message_loop` 的任务已经 `done` 且异常为
`OperationalError('(sqlite3.OperationalError) no such table: worlds')`，而用例的 `scenario` 仍停在
`await lease_waiting.wait()` —— 循环在世界探针处就已结束，从未走到用例要验证的租约边界。

## 2. 实际修改的生产代码 vs 仅改测试夹具

上一版写的「全部为陈旧夹具或迁移表集滞后」**不准确**，现按性质分开列出。

### 2.1 生产 / 配置代码（4 项）

| 文件 | 改动量 | 是否改变运行时行为 | 说明 |
|---|---|---|---|
| `packaging/pyinstaller_runtime_hook.py` | +23/−4 | **是** | `LATER_TABLES` 补 0015 的 5 张表；列豁免泛化为 `LATER_COLUMNS`（`worlds.root_world_id`、`world_members.can_keeper`）。安全方向：只是让本该升级的旧库能升级；残缺 schema 仍 fail-closed |
| `src/structured/room_integration.py` | +54/−3 | **是** | 结构化房间开局物化认领调查员名册（按 `character_key`）。**已随后端负责人提交 `09db2f9` 落地**，非本轮遗留 |
| `requirements.txt` | +2 | 否 | 声明 import 期依赖 `jsonschema==4.26.0`、`referencing==0.37.0` |
| `src/structured/{agent,agent_runtime}.py` | −5 导入 | 否 | 删未使用导入（ruff F401）；已随后端负责人 `f6722a6` 落地，当前 ruff 全绿 |

### 2.2 仅测试夹具（12 个文件）

| 文件 | 改动性质 |
|---|---|
| `tests/test_world_branches.py`、`test_turn_journal.py`、`test_context_compaction.py`、`test_context_shadow.py`、`test_action_adjudication.py`、`test_test_runtime_isolation.py`、`test_model_timeout.py` | 陈旧 patch 目标迁移到 `src.structured.engine_gate.OpenAI`（A 类） |
| `tests/test_solo_play_mode.py`、`test_multiplayer_membership.py`、`test_multiplayer_combat_routing.py`、`test_settlement_actor_reset.py` | 夹具库由无表 `sqlite://` 改为真实临时库（B 类）；`_guard_controller`/`_Controller`/`FakeEngine.context` 接真实 `database_url` |
| `tests/test_database_persistence.py` | legacy 库插入改原生 SQL（C 类） |

未放松任何断言：A 类是替换 patch 目标（被测语义不变）、B/C 类是让夹具库真实可用，
`test_readiness_db_error_fails_closed` 由「打不开的库」改为「只有 `worlds` 表、模型配置表缺失」，
仍断言同一条 fail-closed 行为。

## 3. 对偶验证：世界缺失 / 数据库故障不得放行

背景：`room_world_modes` 在世界行缺失时返回 legacy，这只是**模式判定**的 fail-soft。
新增 `tests/test_structured_world_probe_duals.py`（5 条）把它与「访问权」切开：

| 用例 | 断言 | 覆盖的要求 |
|---|---|---|
| `test_missing_world_probe_is_mode_detection_only` | `structured_frame_gate_reason=None`、`room_world_modes=("legacy","human")`；但同帧走网关被拒（认证用户 `not_authorized`）；`worlds`/`world_states`/`player_requests`/`game_commands`/`event_outbox` 全无该世界记录 | 不存在的世界不能取得访问权 |
| `test_missing_world_rejects_local_operator_frame_without_executing` | 本地无账号路径同样拒绝（fail-closed 码集合）；且**不留孤儿成员行**（本地引导建的成员行随拒绝落库失败一并回滚） | 本地路径同样不授权 |
| `test_missing_world_room_connection_gets_no_access` | 房间路径 `authorize_world` 403 → 连接 4403 关闭；引擎未收到任何提交；房间仍 `lobby` | 房间路径不授权 |
| `test_legacy_world_structured_frame_never_falls_back_to_legacy_turn` | legacy 世界（真实成员 + 已认领调查员）收到 `action_request` → `profile_mismatch`；引擎未收到提交；未建世界状态；outbox 只允许 `request_error` | 结构化请求不能降级绕过门禁 |
| `test_database_failure_during_probe_executes_nothing` | 库确实打不开（正控制）；无提交、无 ack、`action_active=False`、房间状态不变 | 真实数据库故障仍 fail-closed |

既有覆盖（未重复实现，直接引用）：

- legacy 世界的结构化帧 → `profile_mismatch`：`tests/test_structured_ws.py::test_legacy_world_gets_profile_mismatch`。
- 结构化世界关闭旧文字通道 → `structured_required`：`tests/test_structured_ws_smoke.py`（真实 server + TestClient WS）。
- 配置存储故障 fail-closed：`tests/test_solo_play_mode.py::test_readiness_db_error_fails_closed`。

**由此发现的两处残余缺口**（后端域，未自行修改，已登记在交付记录 6.4）：
① 缺失世界的拒绝帧因外键（`event_outbox.world_id → worlds`）无法落库，客户端收不到拒绝帧；
② 探针遇库故障时连接循环直接结束，客户端既无拒绝帧也无断开说明——安全不变量成立，但反馈不完整。

## 4. 打包升级：四项属性的证据

| 属性 | 证据 |
|---|---|
| 支持的旧库升级后**数据保留** | `tests/test_packaged_upgrade_evidence.py::test_supported_old_database_upgrade_retains_data_and_builds_structured_schema`：0002 库写入 users/worlds/world_members/turns 后删版本号 → 跑打包 hook → 逐字段比对升级前后行完全一致（含 `metadata_json` 原文） |
| 结构化表与 `can_keeper` **正确建立** | 同一用例：`player_requests`/`game_commands`/`check_requests`/`event_outbox`/`keeper_control` 全部存在；`world_members.can_keeper` 存在且旧成员默认 `0/False`（不凭空获得 keeper 授权） |
| **重复启动**幂等 | `test_repeated_packaged_startup_is_idempotent`：第二次跑 hook 无异常，revision 仍为 head，数据与列集合不变 |
| 未知残缺 schema **仍拒绝** | 新增 `test_missing_base_table_is_still_rejected`（只有 `users` 表 → `RuntimeError: 无法接管未版本化数据库`）；另引用既有 `test_electron_packaging.py::test_packaged_migrations_reject_unknown_partial_schema` 与 guard 用例族 |

**修复必要性的反证**（来自修复前基线日志 `/tmp/full_pytest_before.log`）：
`tests/test_electron_packaging.py::test_packaged_migrations_upgrade_unversioned_revision_0002` 报

```
E  RuntimeError: 无法接管未版本化数据库：缺少基础表 check_requests, event_outbox, game_commands, keeper_control, player_requests
```

即旧的无版本号桌面库（升级路径上真实存在的用户数据库）会被拒绝启动。补全 `LATER_TABLES` 后该用例通过。
注：本仓库没有 0002 时代之前的任何真实用户存档被用于测试——所有数据都是临时目录里现造的。

## 5. 最终测试所对应的状态、指纹与日志位置

### 5.1 HEAD 演进（后端负责人并发提交）

`45a0352`（基线，59 失败）→ `09db2f9`（M1 修补：接纳名册物化）→ `f6722a6`（M3 Agent 运行器）
→ `5ff7241`（M4 分支/读档）→ `f1acafb`（M3 修补）→ **`75516a4`（收工后定稿）**。

期间她的提交会推移 HEAD：本轮共记录 6 次整仓复跑，明细如下。

| 运行 | HEAD | 结果 | 原始日志 |
|---|---|---|---|
| 基线（59 失败） | `45a0352` | 59 failed / 1213 passed / 7 skipped / 1 deselected | `/tmp/full_pytest_before.log` |
| M3 后 | `f6722a6` | 1297 passed / 7 skipped / 0 failed | `/tmp/full_pytest_evidence.log` |
| M4 后 | `5ff7241` | 1 failed / 1296 passed（membership WS 用例；单跑通过、repeat 未复现 → 非确定性） | `/tmp/full_pytest_post_m4.log` |
| 并发写入期 repeat | `f1acafb` | 5 failed / 1299 passed（全部在她当时新建的 `test_structured_trigger.py`） | `/tmp/full_pytest_repeat.log` |
| trigger 文件排前 | `f1acafb` | 1304 passed / 7 skipped / 0 failed（证明是顺序/污染效应） | `/tmp/full_pytest_trigger_first.log` |
| **定稿（默认顺序）** | **`75516a4`** | **1305 passed / 7 skipped / 0 failed** | `/tmp/full_pytest_final2.log` |
| 前端 E2E 定稿 | `75516a4` | **20 passed / 1 skipped**（5.5m，21 条） | `/tmp/e2e_final_state.log` |
| 前端单测定稿 | `75516a4` | 65 files / 715 tests passed | 00:42:30 终端输出 |

同期门禁：`ruff check .` → All checks passed；`tools/check_architecture.py` → architecture checks passed；
`npx tsc --noEmit` / `npx prettier --check` / `npm run build` 均通过。

> 后续（过渡回合功能，2026-09-14）按 list reporter 逐条核对，E2E 稳定态是 **22 收集 → 20 passed /
> 2 skipped**：两条 skip 都是环境门控的既有用例（`multiplayer.spec.ts:890` 需要 Electron 运行环境、
> `staging-recovery.spec.ts:8` 需要外部 staging 服务器）。上表写「1 skipped」是因为当时只有其中一条
> 触发了 skip；以后续实测为准，见 `docs/STRUCTURED_TRANSITION_TURN_20260914.md` §5。

关于那次顺序敏感：`test_structured_trigger.py` 单跑 7 passed，且与我的两个新测试文件同跑 14 passed；
在后端负责人收工后（`75516a4`）的默认顺序全量跑中已不再复现，故不再作为未决项。

### 5.2 未提交差异指纹

见 [`state_fingerprint.txt`](state_fingerprint.txt)：`HEAD=75516a4…`、`status_lines=74`、
`diff_sha256=c288ca62d406ed9e167fb81ba3ba5e7cefdf808a96d7595d0226e0d417672248`、
`diffstat=38 files changed, 606 insertions(+), 125 deletions(-)`、`untracked_new=46`，以及我改动的
16 个文件逐个 sha256 前缀。注意 `git diff` 不含未跟踪文件，故新增文件以逐文件哈希单独记录。

### 5.3 人主持闭环 vs Agent 真模型闭环（分开声明）

| 路径 | 状态 | 证据 | 模型调用 |
|---|---|---|---|
| **人类主持 · 无模型闭环** | **已验证** | `e2e/structured-human-3p.spec.ts`（1 主持 + 2 玩家：私发线索 → 检定 → SAN → 整队移动 → 存档入口 + 刷新重连）；`e2e/structured-solo-online.spec.ts`（云端单人）；`e2e/structured-real-integration.spec.ts`（真实本地后端 + structured_v1）；`e2e/structured-play.spec.ts`（按钮 payload 取证） | 零：human-3p 与 solo-online 把 `OPENAI_BASE_URL` 指到关闭端口 `http://127.0.0.1:9/v1`；real-integration 用 `startModelStub` 并断言 `modelRequests` 计数不变 |
| **Agent 模式 · 真实模型闭环** | **尚未验证** | 后端只有脚本化调用者的单测：`tests/test_structured_agent.py` 全部用 `_ScriptedCaller`（无真实 SDK 调用）；前端 E2E 全程无真实模型 | — |

结论：**「human keeper 无模型」这条闭环有真机证据；「Agent 用真实模型跑通判断→命令→叙事→读结果」还没有任何真机证据**，
需在取得 BYOK 授权后单独验证（主规格 M3/M4 的真机部分），不能由脚本化调用者的通过来替代。

## 6. 复跑命令

```bash
cd /home/inaglyite/MyProjects/trpg-master
.venv/bin/python -m ruff check .                 # All checks passed
.venv/bin/python -m pytest -q                    # 1305 passed / 7 skipped
.venv/bin/python tools/check_architecture.py     # architecture checks passed
cd frontend
npx vitest run                                   # 715 passed / 65 files
npx tsc --noEmit && npx prettier --check "src/**/*.{ts,tsx}" "e2e/**/*.ts" && npm run build
npx playwright test                              # 20 passed / 1 skipped
```
