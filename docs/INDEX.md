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

`7bd0e51d` 的后续完整 GitHub quality 记录冻结于
`mid_product/trpg-master/2026-10-13-agent-harness-memory-ci/`：2005后端/167子测试、
1339前端单测、95浏览器E2E/2门控skip。该结论不覆盖后续新增阶段摘要代码。
阶段摘要与完整事件回执的本地/模型验收冻结到
`mid_product/trpg-master/2026-10-13-agent-harness-long-memory/`：保留
`agent-harness-chapters-20261013/`、`agent-harness-receipts-20261013/` 的原相对结构，
包含ccf8290e源码指纹、2024项后端/1340项前端、三宽度真实后端截图、完整事件
回执/人类摘要更正/读档并发验收、所有真实模型轮次及共享费用账本。包含曾经
耗尽上下文预算的失败原件，不以其后的成功覆盖。最新CI结果另行记录，不能
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
