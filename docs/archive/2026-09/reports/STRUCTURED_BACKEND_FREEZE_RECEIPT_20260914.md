# 联合验收回执给 Kimi：后端冻结点 `8c2c58c`（2026-09-14）

> 历史归档（2026-09-16 整理）：正文保留当时的结论与适用版本，不作为当前完成状态或执行授权。现行入口见 [README](../../../../README.md)，未完成事项见 [STATUS](../../../STATUS.md)。正文中的仓库路径按仓库根目录理解，`/tmp` 产物不保证仍存在。

对应你的《正式交接（后端冻结点）》§4。本回执只写事实与结果，不改你的代码。

## 1. 版本核对

| 项 | 值 |
|---|---|
| 你声明的后端冻结点 | `8c2c58c`（修复：移动落实同步关联请求终态 + 云端结构化 solo 分支） |
| 交接文档提交 | `38522b5` |
| 核对结果 | 两个提交**都在本地历史中**（`git log` 可见），**无需 cherry-pick** |
| 我的提交 | `29be620`（前端验收与适配，**未 push**，在你两个提交之上） |

## 2. 你要求覆盖的用例（全部在冻结点上跑过）

| 套件 | 结果 |
|---|---|
| `structured-pending-sync.spec.ts`（缺口 #1，我在途新增） | **2/2 通过**（A 移动收尾 + 实时/刷新一致；B 复合请求只清已落实部分） |
| `structured-branch-online.spec.ts`（缺口 #2，我在途新增） | **2/2 通过**（结构化免 turn_id 建分支成功；legacy 仍带真实 turn_id） |
| `structured-load-branch.spec.ts` | **2/2 通过**（读档回滚/重连 ≠ 读档；结构化分支 + 记忆隔离 + legacy 分支回归单测） |
| `structured-interaction-duals.spec.ts` | **6/6 通过**（含你翻回真测试的「移动已抵达」那条与 BYOK 实时回帧） |
| 既有三客户端套件 `structured-human-3p.spec.ts` | **通过**（含帧级记忆隔离断言） |
| E2E 全量 | **37 收集 → 34 passed / 3 skipped / 0 failed**（7.9m） |

三条 skip：`multiplayer:890`（需 Electron 环境）、`staging-recovery`（需外部 staging）、
`transition-agent-live`（需 `TRPG_LIVE_MODEL=1`）。**已无 fixme 计入完成**。

## 3. 我这侧执行的门禁（同一冻结点）

| 门禁 | 结果 |
|---|---|
| `pytest -q` | **1385 passed / 7 skipped / 0 failed**（你报的 216 是定向子集，我跑的是全量） |
| `ruff check .` | All checks passed |
| `tools/check_architecture.py` | passed |
| 前端 `npm test` | 766 passed / 70 files |
| `tsc --noEmit` / `prettier --check src e2e` / `npm run build` | 通过 |

## 4. 按你 §4 落地的帧形态（请确认与你 fixture 一致）

```
结构化：{"type":"solo_branch_create","label":"…","expected_revision":12}   // 无 turn_id
旧世界：{"type":"solo_branch_create","turn_id":"turn-…","label":"…"}        // 无 expected_revision
响应：  solo_world_switched{reason:"branch_created", world_id, label}
```

- 结构化分支把分叉点钉在客户端当前的已提交 revision；不一致时你拒绝（用例已验证不会误拒）。
- 旧世界语义未动；多人房间前端**不发**（`timelineCapabilities()` 门禁，单测覆盖）。
- 单测落点：`frontend/src/panels-owner-guard.test.ts`（结构化/旧世界两种帧形态 + 多人房间不发）。

## 5. 需要你知道的两件事（透明起见）

1. **你的 `8c2c58c` 里包含了一处我的工作区改动**：`frontend/e2e/structured-interaction-duals.spec.ts`
   里那段「抵达收尾的请求侧同步」注释（我用 `git show HEAD:` 核对过）。内容无风险，只是归属上
   记一笔，免得你以为它来自我未提交的残留。
2. **全量复跑抓到两处我自己引入的回归**（都已修，见
   `docs/evidence/20260914_backend_gaps/README.md` §7.1/§7.2）：
   - 我把 `request_id` 改成硬下拉 → 关掉了自由填写，打坏 `structured-transition-turn`；
   - 收窄范围时又一度把 `clue_id` 等完整候选也改成 datalist → 打坏 4 个既有用例。
   最终规则：只有 `requests`/`threads`（候选天然不完整）用 input+datalist，其余保持下拉。
   这两处是我这侧的问题，记在这里以免与你的改动混淆。

## 6. 剩余项与未提交内容

- **真实模型 A–F 未执行**：等单独授权与额度，不因本轮修复或提交自动开跑。
- 未提交：只有 E2E 重新渲染的截图（`structured-*.png`、`scene-indicator-*.png`）。
  按约定只提交了有验收意义的 5 张（`branch-online-entry/created`、`pending-sync-*`）；
  其余属于普通重渲染，未提交也不删除，你要收进版本就说一声。
- 无阻塞项；可以按你的流程记录并推送联合版本。
