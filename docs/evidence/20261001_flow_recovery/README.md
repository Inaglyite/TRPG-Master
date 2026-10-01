# 交互修复的验证与边界（2026-10-01）

基线 `1c916a9` + 本地修复。工作区原有并行改动、截图、模组加载修复均保留；未提交、未推送、未部署。仅本地独立测试库/临时世界，没有测试正式环境或修改用户存档。

## 可复现的关键验收

- `tests/test_platform_flow_recovery.py`：批准后移动/叙事/幂等、批次失败全回滚、拒绝不执行、生成后及生成中版本变化拒绝批准、复合行动待办保留、暂停事件落库与投递、人类接管隔离旧 Agent、显式重试不重复调度、房主与主持权限分离、HTTP 授权及成员投影。
- 前端组件测试：完整角色卡编辑保留、角色库 Escape、认领失败不能开始、输入法 Enter、普通选项输入解锁、模型设置超时/断线/晚确认、云端主持方式、授权确认与离线玩家跳过。
- `structured-real-integration.spec.ts`：真实后端，刷新恢复草稿、按钮批准实际移动、刷新后不重放；模型暂停后显式接管并用主持台继续。
- `structured-human-3p.spec.ts`：真实三客户端私发隔离/检定/SAN/移动/刷新；大厅、房间授权和主持控制在 1280/939/640 宽度的截图及几何检查。无真实模型调用。
- 图像在 `/tmp/trpg-flow-ui-{lobby,room,control}-{1280,939,640}.png`。检查加载层已卸载、按钮内边距/不挤压/不换行、无页面水平溢出，并人工复核。

最终后端全量：`python -m pytest -q` **1484 passed / 7 skipped / 127 subtests passed**（一条既有 SQLAlchemy warning）。前端 `npm test` **804 passed / 72 files**；build（含 tsc）、format:check、ruff 与架构门禁通过。协议专项 **7 passed / 90 subtests passed**。日志分别在 `/tmp/trpg-flow-final-{pytest,vitest,build,format}.log`、`/tmp/trpg-flow-schema-final.log`。

关键后端源文件的最终 SHA256（便于区分基线 HEAD 与未提交修复）：

```text
src/structured/drafts.py       2cb1c9157f5792d76dd657949f28c54725a21a3ffb9e959facbc36e0bfa496dd
src/structured/control.py      e83ac3993f3db67944a89bda75e110af1e32124eb867719b2220e0df027ff417
src/structured/agent.py        ef541d78d31657d05c66a3da9493fe7ffcdf8f52faebc4fc9fe8b3838859a1d8
src/structured/service.py      8cdffab2aaa0742bfe90996109a80c6ce81b48812d5208cbeb4534caf66bae46
```

浏览器全量 `npm run test:e2e`：**43 收集 → 41 passed / 2 skipped / 0 failed**（10.3 分钟），日志 `/tmp/trpg-flow-final-e2e.log`。两条跳过分别是外部 staging 参数未提供、按需真实模型规格未启用；没有已知缺陷 fixme，没有新增 skip 或 retry。Electron 本地联机与后端进程生命周期用例本次实际通过。

## 一次真实模型主线（非全绿）

授权：仅 `api.deepseek.com` / `deepseek-flash`，猩红文档测试角色、模组与试玩输入，费用上限 ¥20。关闭其他模型目的地，在独立运行目录使用一次主线。

- 临时入口 `/tmp/trpg-flow-live-budget.py`；运行目录 `/tmp/trpg-flow-live-207lbzr4`；原始报告 `/tmp/trpg-flow-live-report.json`。
- 109 次调用，保守费用上界 ¥17.88753（不是账单实付）；下一次调用的预留费用会超限，故在发请求前停止。没有另起主线或额外真实调用。
- 完成 15 个 beat、36 回合，已执行的 25 项检查中 24 项通过。取文档、取徽章与封印已落账；B7 疯狂、B8 最终结局及最终汇总检查没有执行。
- 原始报告 `status=running` 是预算退出前的中间文件；真实终态是 **budget_stopped**，不得引用为完整通过。
- B5b 多日监视失败：回合 14 抵达只结算 30 分钟（280→310）；15/16 裁决被“超过一小时的行动需明确为等待或旅行”拦下并回退；叙事却演出了数日监视。17/18 汇总为零分钟，19 仅推进至 340；怪物/人类压力时钟仍为 0。属于长时调查裁决与叙事状态契约的待处理缺口，不能仅归因模型强度或骰运。本轮未改这些组件，也没有证据证明是本轮引入的回归。
- 该主线走 legacy，不能替代 structured assisted/agent 的完整真实模型验收；新增批准/恢复链路目前由确定性测试和真实浏览器+后端验证。

## 尚未承诺

未实现完整 structured 战斗/结局、档案自助恢复、本地结构化新建入口、所有未保存编辑的退出确认。没有用文案或按钮模拟这些能力。历史限制见 [STATUS](../../STATUS.md)，本轮不关闭未验证项。
