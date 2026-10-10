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
