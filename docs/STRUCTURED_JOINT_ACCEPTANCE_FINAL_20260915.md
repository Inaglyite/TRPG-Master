# 联合验收最终记录：experiment/keeper-platform（2026-09-15）

本轮集成收口。冻结点 `8c2c58c`（后端修复）→ zcode 验收 `29be620`/`ee86446`/`805537d`
→ 本记录。全部提交在共享实验分支历史中，无 cherry-pick、无 force。

## 1. 版本与祖先关系

| 提交 | 内容 | 位置 |
|---|---|---|
| `8c2c58c` | 后端两项修复（移动落实同步、云端结构化分支） | 冻结点 |
| `38522b5` | 交接文档（接口增量与验收请求） | 其后 |
| `29be620` | zcode 前端验收与分支帧形态适配 | 其后 |
| `ee86446` | zcode 验收回执 | 其后 |
| `805537d` | zcode 交付文档缺口标注关闭 | 其后 |
| 本提交 | 最终联合验收记录 + 线上 audience 对偶测试 | HEAD |

## 2. 验收覆盖核对（以文件差异为证，非仅引用回执数字）

- 后端代码自 `8c2c58c` 起无变化（`git diff 8c2c58c..HEAD -- src/` 仅本记录新增
  一个测试文件）；后端全量数字沿用 zcode 在同一树上的实测：1385 passed/7 skipped。
- 前端代码差异全部在 `29be620`（zcode 自有区域）：我**独立复跑**了其中两条
  新验收 spec——`structured-pending-sync` 与 `structured-branch-online`，
  **4/4 通过**（真实后端 + 真实前端，43s）。
- 我在最终 HEAD 上复跑后端结构化+时间线定向：**213 passed / 1 skipped /
  88 subtests**；架构门禁通过。
- 移动落实同步与云端分支两项缺陷的 E2E 均已从 fixme 恢复为真测试并通过。

## 3. 双方门禁汇总（同一联合版本）

| 门禁 | 结果 | 实测方 |
|---|---|---|
| 后端全量 | 1385 passed / 7 skipped / 0 failed | zcode（回执），我复跑定向 213 绿 |
| 前端单测 | 766 passed / 70 files | zcode |
| tsc / prettier / npm build | 通过 | zcode |
| E2E 全量 | 34 passed / 3 skipped / 0 failed | zcode；关键两条 spec 我独立复跑 4/4 |
| ruff / check_architecture | 通过 | 双方 |

E2E 三条 skip 均为环境条件：Electron 运行环境、外部 staging 服务器、
按需真实模型 live 规格（`TRPG_LIVE_MODEL=1`）。无 fixme 计入完成。

## 4. action_status 定向 audience 的对偶验证

带 awaiting 明细的 `action_status` 收窄为本人定向后：
主持（keeper/agent）按 audience 规则仍可见全部待办（需要主持处理的不会被隐藏），
本人可见，其他玩家收不到私密明细。钉死在
`tests/test_structured_pause_and_thread_lifecycle.py::AwaitingEventAudienceTests`
（实时帧层）；快照层既有 `test_awaiting_todo_visible_only_to_owner_and_keeper`
与房间帧用例继续通过。

## 4.5 CI 状态（quality workflow，不提前报绿）

- 本分支五次推送的 quality 运行**全部为红**（含最终 66a3da2，run 34874213367）：
  backend 绿；frontend 的 E2E 步骤红。
- 失败形态一致且与产品逻辑无关：6 条本地全绿的 structured spec 在 CI 里全部
  停在开局引导 `module-select-trigger`（30s 默认超时）——真实后端+重量级模组
  在共享 runner 上冷启动超过 30s，尚未执行到任何产品断言。master 最近的
  quality 失败也是同类 E2E 环境性超时（model-settings-online 5 分钟超时）。
- 结论：CI 红 = CI 环境的 E2E 启动预算不足，不是本轮代码缺陷；本地真实
  前后端验收（双方实测，见 §2/§3）为准。
- 待办（建议下一轮，不属本轮授权范围）：CI 的 E2E 开局引导超时从 30s 提到
  60–90s 或加 retry；这是前端测试基建，留给 zcode 评估。

## 5. 未提交内容与剩余事项

- 未提交：E2E 重渲染截图（`structured-*.png`/`scene-indicator-*.png`）——
  按约定不批量加入、不删除、不 stash，留待 zcode 决定。
- 真实模型 A–F 专项：**未执行**（独立待授权任务，不阻塞本轮实验分支备份）。
- `rate_limited` 仍未实现（既有登记）。
- 本轮不合并 master、不部署、不触碰生产。
