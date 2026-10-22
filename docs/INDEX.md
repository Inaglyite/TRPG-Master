# 文档入口

详细规格以以下六份中文现行文档为主。产品介绍见根 [中文 README](../README.md)
与 [English README](../README.en.md)，英文操作说明见 [English guide](en/README.md)。
代码、schema、fixtures、模组与正式前端素材仍在仓库内；详细历史资料不是运行依赖。

| 文档 | 用途 |
| --- | --- |
| [架构](ARCHITECTURE.md) | 模块、权威状态、两种执行路径、事务与上下文边界 |
| [协议](PROTOCOL.md) | 请求、命令、事件、权限与恢复语义 |
| [开发](DEVELOPMENT.md) | 本地环境、门禁、协作与文档维护 |
| [模组](MODULE_FORMAT.md) | 作者流程、格式正本、安装与迁移边界 |
| [运维](OPERATIONS.md) | 环境保护、BYOK、发布、备份与恢复要求 |
| [状态](STATUS.md) | 当前发布、已验收范围和未解决事项 |

## 归档资料

2026-10-22 的 [Windows 1.0.3-rc.1 候选包交付](../../mid_product/trpg-master/2026-10-22-windows-1.0.3-rc.1/PACKAGING.md)
冻结于 `mid_product/trpg-master/2026-10-22-windows-1.0.3-rc.1/`：构建固定标签 c329e227，
后端隔离启动/迁移、便携引导、安装版静默安装/启动/卸载通过；安装包、便携包及更新/校验
文件已上传 Release 草稿，远程文件摘要一致。记录 CI 后端2329项通过、前端E2E仍在运行的
快照，不把未结束检查记作通过。原始构建后检查误将自建 venv 当作源码修改的失败保留，
清理该环境后源码干净；不修改此前源码验收归档，不部署正式服务器。

2026-10-22 的 [Kimi → Codex 接手实现与统一验收](../../mid_product/trpg-master/2026-10-22-kimi-codex-delivery/ACCEPTANCE.md)
冻结于 `mid_product/trpg-master/2026-10-22-kimi-codex-delivery/`：T01–T11 的审计与修正，
2321 项后端、1388 项前端、6 项本地 E2E、三宽度 UI 与实际 GPU、旧存档/连续压缩/
分支及真实回合内压缩专项；真实自动主线 38 回合 33/33，人类三客户端 18/18、零模型。
保留早期失败与脚本修正，`api-usage-ledger.json` 区分完整捕获与历史已记录下限，
不伪报总费用。含更新后的任务书工作副本、源码指纹、索引与 `SHA256SUMS.txt`。
原始运行数据库位置及校验另册登记，私有上下文不随源码推送。

2026-10-22 的中英文 README、英文上手文档与五张展示截图更新记录冻结于
`mid_product/trpg-master/2026-10-22-readme-refresh/`：保留旧文档/截图、隔离源码构建、
演示对白与真实本地后端取图脚本、零模型请求记录、中英文页面预览、90 处本地引用
检查，以及模板编译/打包/校验结果。只覆盖文档和展示图，不将同时进行的功能实现
算入本次验收；源码快照与当前工作区版本边界见归档索引，原始运行目录留在私有工作目录。

2026-10-22 的 [Kimi 逐项实现任务书](../../mid_product/trpg-master/2026-10-22-kimi-handoff/TRPG-Master-Kimi实现任务书-2026-10-22.md)
冻结于 `mid_product/trpg-master/2026-10-22-kimi-handoff/`：11 项任务覆盖离场人物导致
叙述失败、3D 骰子初始化等待、技能选项提示，以及目标上下文、检定后备、场景修正、
对白历史/compact、Skill 职责、选项一致性、结案与诊断；含只读事故证据、相关源码快照
和前端 24 项基线。该交接原件只记录当时诊断与待办；后续实现见上方接手续做验收。骰子缺口
与 Electron 原生 Button 动画日志的因果尚未证实，`SHA256SUMS.txt` 校验交接原件。

配套 [DiceFrame 源码调研](../../mid_product/trpg-master/2026-10-22-diceframe-research/DiceFrame技术调研与借鉴报告.md)
冻结于 `mid_product/trpg-master/2026-10-22-diceframe-research/`：上游固定提交
`bfc0305d75e1fd76aa73c7f4055ef7298c01b5a7`，455 项所选上游测试与 9 项函数探针，
记录机制、边界和对本项目的借鉴建议；没有上游真实模型游玩，不代表上述改进已完成。
两个归档均含私有上下文，不作为公开上传包；桌面另有 Markdown 与离线 HTML 副本。

2026-10-21 的离场连续性、发现结算与玩家存档回滚验收冻结于
`mid_product/trpg-master/2026-10-21-scene-continuity-discovery/`：323 项相关后端测试，
真实模型失败/付代价/孤注一掷回放，以及原存档备份、实际恢复和重启核验。
本次猩红主线 36 回合完成封印与结局，32/33；随机 SAN 注入未达测试阈值，隔离
固定损失边界补测通过。`ACCEPTANCE.md` 区分原始主线结果、补测、取消操作与已完成回合，
保留早期失败及旧脚本误读上一回合的原件；备份大文件留在私有工作目录，归档登记
其位置和校验和，不重复复制。`SHA256SUMS.txt` 校验冻结证据。

2026-10-21 的 CoC 裁决与检定节奏改进验收冻结于
`mid_product/trpg-master/2026-10-21-coc-adjudication/`：255 项相关测试、最终真实模型
书房专项 8/8、询问身份重复 3/3、成功目标沿用专项，以及最终猩红完整主线 33/33。
`ACCEPTANCE.md` 登记规则来源、旧世界 pin 兼容、主线保守回退与模型语义裁量边界；
包含最终源码指纹和早期失败输出，`SHA256SUMS.txt` 校验冻结证据。

2026-10-21 的读档后分支按钮恢复验收冻结于
`mid_product/trpg-master/2026-10-21-restore-turn-branches/`：前端1355项、后端相关61项，
三个宽度的真实本地读档、创建分支并重读流程，零模型调用及按钮截图。
`work/restore-turn-branches-20261020/ACCEPTANCE.md` 登记版本和纯文本旧存档的能力边界。

2026-10-20 的读档加载反馈与玩家发言边界验收冻结于
`mid_product/trpg-master/2026-10-20-save-load-feedback/`：01a3761d 后端版本的真实
五回合专项和 33 回合猩红 legacy 主线 33/33、前端兼容修正、1352 项前端与 57 项
后端检查、三个宽度的慢读档截图及完整耗时日志。`work/save-load-feedback-20261020/ACCEPTANCE.md`
登记版本、模拟延迟范围与原存档未改动边界，`SHA256SUMS.txt` 校验原件。

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
