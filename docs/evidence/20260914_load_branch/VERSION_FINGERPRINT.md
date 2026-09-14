# 验收版本指纹（Part 三 冻结版本联合验收）

记录时间：2026-09-14T15:16:08+08:00

## Git
```
HEAD: 552672c60dea8340d6a3fe2c534e90fc99257e5d
branch: experiment/keeper-platform
我的开发检查点: 96ce488
已提交工作树脏文件数（含 Kimi 在途）: 27
```

## 后端关键文件 sha256（含 Kimi 在途改动）
```
backend_aggregate_sha256_16: c5191e405026a53eb102f1eec4239e2a
```

## 在途未提交文件（快照，Kimi 仍在改）
```
 M docs/screenshots/scene-indicator-after-move.png
 M docs/screenshots/scene-indicator-desktop.png
 M docs/screenshots/scene-indicator-long-name.png
 M docs/screenshots/scene-indicator-narrow.png
 M frontend/e2e/structured-interaction-duals.spec.ts
 M frontend/src/panels.ts
 M frontend/src/protocol/keeper-commands.ts
 M frontend/src/react/components/PanelLayers.tsx
 M frontend/src/react/components/structured/KeeperConsole.test.tsx
 M frontend/src/react/components/structured/KeeperConsole.tsx
 M frontend/src/state/structured-store.ts
 M schemas/structured-play/v1/events.json
 M src/structured/agent.py
 M src/structured/agent_prompts.py
 M src/structured/agent_runtime.py
 M src/structured/domains.py
 M src/structured/gateway.py
 M src/structured/interactions.py
 M src/structured/service.py
 M tests/test_structured_character_memory.py
?? docs/evidence/20260914_load_branch/
?? docs/screenshots/structured-branch-created.png
?? docs/screenshots/structured-load-after.png
?? docs/screenshots/structured-load-before.png
?? frontend/e2e/structured-load-branch.spec.ts
?? frontend/src/state/structured-interaction-behaviors.test.ts
?? tests/test_structured_pause_and_thread_lifecycle.py
```

## 验收时的后端在途指纹（Kimi 仍在改）

```
记录时间：2026-09-14T15:43:08+08:00
HEAD: cae8900338d32f1a0c8245461c34390915705366
后端聚合 sha256_16: f111a7d6d4000f4afaf92bf412aef433
后端在途未提交文件：
```

门禁结果（本轮，同一工作树）：pytest 1376 passed / 7 skipped / 0 failed；前端单测 759 passed；
E2E 33 收集 → 29 passed / 4 skipped / 0 failed；tsc / prettier / build 通过；ruff 1 error 位于 Kimi 在途文件。

## 认证版本（第二轮复跑）

```
记录时间：2026-09-14T16:0x+08:00
HEAD: cae8900（Kimi「修复：交互线程生命周期收口 + 暂停/错误回帧（遗留两项）」）
后端聚合 sha256_16: f111a7d6d4000f4afaf92bf412aef433
后端在途未提交文件：无（src/schemas/migrations/server.py 全部已提交）
我这一轮的前端改动：未提交（见交付记录 §9.5 文件表）
```

门禁结果（认证版本，同一工作树）：

| 门禁 | 结果 |
|---|---|
| `pytest -q` | 1376 passed / 7 skipped / 0 failed（194s） |
| `ruff check .` | All checks passed |
| `tools/check_architecture.py` | passed |
| 前端单测 `npm test` | 763 passed / 70 files |
| `tsc --noEmit` / `prettier --check src e2e` / `npm run build` | 通过 |
| E2E `npx playwright test` | 33 收集 → 29 passed / 4 skipped / 0 failed（7.3m） |

四条 skip：`staging-recovery`（需外部 staging）、`multiplayer:890`（需 Electron 环境）、
`transition-agent-live`（需 `TRPG_LIVE_MODEL=1` 的真实模型规格）、`interaction-duals:650`
（已知后端缺陷 fixme，已实测确实失败，证据在 `probe_fixme_awaiting/`）。
