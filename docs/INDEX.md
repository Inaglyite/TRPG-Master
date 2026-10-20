# 文档入口

仓库仅保留下面六份现行文档。代码、schema、fixtures、模组与正式前端素材仍在仓库内；详细历史资料不是运行依赖。

| 文档 | 用途 |
| --- | --- |
| [架构](ARCHITECTURE.md) | 模块、权威状态、两种执行路径、事务与上下文边界 |
| [协议](PROTOCOL.md) | 请求、命令、事件、权限与恢复语义 |
| [开发](DEVELOPMENT.md) | 本地环境、门禁、协作与文档维护 |
| [模组](MODULE_FORMAT.md) | 作者流程、格式正本、安装与迁移边界 |
| [运维](OPERATIONS.md) | 环境保护、BYOK、发布、备份与恢复要求 |
| [状态](STATUS.md) | 当前发布、已验收范围和未解决事项 |

## 归档资料

2026-10-20 的对话复读与裁决纠错验收冻结于
`mid_product/trpg-master-dialogue-recovery/2026-10-20-dialogue-recovery/`：
业务版本 f43b71ed、最终真实五回合专项、34 回合猩红 legacy 主线 33/33、
单次时钟结算人工复核、全量 2142 通过/8 跳过/19 既有失败、代码评审与本地集成回执。
`README.md` 登记范围与两次安全回退，`SHA256SUMS.txt` 校验原件；首轮 32/33、
第二轮自动通过但人工发现重复时钟的报告均保留。私有上下文不随源码提交或推送。

2026-10-18 的无模型本地读档验收位于
`mid_product/trpg-master/2026-10-18-local-save-restore/`：f7e3ef40 的真实后端读档
点击流程、零模型调用记录、前后端检查和源码指纹。

2026-10-14 的结构化发言、出发待决与模型状态修正验收冻结于
`mid_product/trpg-master/2026-10-14-speaker-transition-attribution/`：最终源码指纹、
2111项后端/8环境skip、1340前端、完整96 E2E/2门控skip及最终发言专项、
人类三客户端18/18、最终真实DeepSeek legacy主线33/33与状态修复等专项。
`README.md`登记版本、范围、共享费用保守计算与原始失败，`SHA256SUMS.txt`校验完整原件。
旧错误存档未改，场景修复仅覆盖本轮投影；原始私有上下文不随源码推送。


Linux桌面GPU启动诊断与后端stderr标签核对位于
`mid_product/trpg-master/2026-10-14-electron-gpu-startup/`：包含本机Wayland原始
复现、不同参数的原始诊断、WebGL2渲染与GPU功能状态、隔离Electron/本地后端桩
启动日志及31项Electron测试。GPU诊断仍保留，未宣称修复系统显卡驱动。

2026-10-14 的256K默认窗口与Agent累计预算移除验收位于
`mid_product/trpg-master/2026-10-14-context-window-256k/`：保留本地后端/前端日志、
模型设置真实后端E2E、多步大上下文查询及单次小窗口/输出预留测试、源码指纹和
参考编程Agent的官方资料入口。本轮没有付费模型调用或生产、正式Release发布。

最终业务代码94621558的完整验收冻结于
`mid_product/trpg-master/2026-10-14-agent-harness-delivery/`：quality38384606990
全绿原始日志（2033后端/167子测试、1340前端、95 E2E/2门控skip、PostgreSQL0021），
最新代码两个真实模型长历史/人类更正专项、三宽度真实后端UI截图、逐项审计、
源码指纹及最终共享费用账本（保守上界¥18.847708）。后续交付只更新文档，
不覆盖旧归档或旧失败；原始私有上下文不随源码推送。

原始玩家请求档案及分支长期记忆验收冻结于
`mid_product/trpg-master/2026-10-13-agent-harness-request-memory/`：f7ea6908源码指纹、
2025项本地后端、两轮真实模型请求专项及共享费用账本，保留首轮误称“公开记录”
的原件与修正后证据。包含733f7302的完整CI日志（2032后端、1340前端、95 E2E），
该CI不覆盖新增0021；完整的最终代码CI在上面的delivery归档。原件不随源码推送。

`7bd0e51d` 的后续完整 GitHub quality 记录冻结于
`mid_product/trpg-master/2026-10-13-agent-harness-memory-ci/`：2005后端/167子测试、
1339前端单测、95浏览器E2E/2门控skip。该结论不覆盖后续新增阶段摘要代码。
阶段摘要与完整事件回执的本地/模型验收冻结到
`mid_product/trpg-master/2026-10-13-agent-harness-long-memory/`：保留
`agent-harness-chapters-20261013/`、`agent-harness-receipts-20261013/` 的原相对结构，
包含ccf8290e源码指纹、2024项后端/1340项前端、三宽度真实后端截图、完整事件
回执/人类摘要更正/读档并发验收、所有真实模型轮次及共享费用账本。包含曾经
耗尽上下文预算的失败原件，不以其后的成功覆盖。最终CI结果见上面的delivery归档，不能
用7bd0e51d的CI替代后续提交；归档不是公开上传包。

2026-10-13 的主持资料、私有笔记与可恢复回执验收位于
`mid_product/trpg-master/2026-10-13-agent-harness-memory/`（相对仓库父目录）：
包含版本及源码哈希、后端/前端日志、三宽度本地真实后端截图、全部真实模型轮次与
共享费用账本，以及本轮 legacy 28/33 的失败原件。`README.md` 区分脚本断言、
人工复核和未闭合问题，`SHA256SUMS.txt` 校验归档；原始私有测试上下文不随源码推送。

2026-10-12 的模组工坊独立更新验收位于
`mid_product/trpg-master/2026-10-12-editor-component-update/`：包含 GitHub 预发布包标识、
真实桌面入口与更新弹窗截图、主动检查/拒绝/下载/下次打开记录、测试日志及源码校验和。
测试运行数据属于隔离本地用户目录，归档不作为可公开上传的发布资源。

2026-10-12 的模组工坊内嵌构建同步与入口验收位于
`mid_product/trpg-master/2026-10-12-editor-bundle-sync/`（相对仓库父目录）：包含构建日志、
“主游戏 → 模组工坊 → /editor/”点击截图、API 隔离记录，以及独立桌面构建未被覆盖的 SHA256 核对。

本机归档与项目同级，不纳入源码提交：

```text
MyProjects/
├── trpg-master/
│   └── docs/                 六份现行文档、本文及 README 展示图
└── mid_product/
    ├── README.md             归档入口
    └── trpg-master/
        ├── 2026-10-10-archive-AUAUX0/
        │   ├── README.md
        │   ├── SHA256SUMS.txt
        │   ├── ORIGINAL_PATHS.zlist
        │   ├── STATUS_BEFORE.zlist
        │   ├── TRACKED_SCREENSHOTS_BEFORE.patch
        │   └── files/        按原仓库相对路径保留的完整原件
        └── work/test-results/  后续本机产物，不改动冻结归档
```

任一旧相对路径 `P` 的归档位置为 `mid_product/trpg-master/2026-10-10-archive-AUAUX0/files/P`。例如：

- `docs/reference/`：完整 API、模组格式、部署操作、旧引擎详解与模块地图。
- `docs/archive/`：旧计划、事故分析、阶段交付报告。
- `docs/design/platform-ui/`：UI 提示词、生成参考、实现截图与完整验收矩阵。
- `docs/evidence/`、`test-results/`：原始验收记录、指纹、trace、截图及日志。
- `docs/deliverables/`：需求分析 DOC/DOCX/PDF/HTML。
- `frontend/character/`：人物图片原稿；应用中的已用素材未搬动。

9744 个原始文件已逐一核对 SHA256。六份核心文档的整理前版本也保留在 `files/docs/`，不能将其历史措辞当作当前状态。

本机归档不保证在其他开发机或 GitHub 检出中存在。现行契约查源码/schema；需要历史资料时取归档副本，或按归档基线 `2b66a582` 查 Git 中曾提交的资料。未提交的原稿与运行产物只在本机归档中，须另行备份；归档不是独立灾备。
