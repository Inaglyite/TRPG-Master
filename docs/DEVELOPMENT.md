# 开发、协作与文档维护

先读根目录 [AGENTS.md](../AGENTS.md)。默认本地开发与测试；真实模型调用、预发布、正式发布分别授权。工作区他人改动不得覆盖、stash 或顺手提交。

## 1. 本地环境

Python 3.12；CI 使用 Node.js 22，前端依赖以 lockfile 为准。Linux 桌面可运行 `bash start_desktop.sh`。手动开发示例（仓库根目录）：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
```

前端另一个终端执行 `cd frontend`、`npm ci`、`npm run dev`；后端执行 `python server.py`。确保运行数据库与正式环境隔离；不要继承生产数据库 URL 跑测试。模型配置按设置页/BYOK 路由准备，不把真实配置提交到 Git。

## 2. 本地门禁

在仓库根目录、已激活开发虚拟环境下：

```bash
ruff check .
python tools/check_architecture.py
python -m pytest -q
```

在 `frontend/`：

```bash
npm test
npm run format:check
npm run build
npm run test:e2e
```

浏览器依赖与 Electron 环境按 `.github/workflows/quality.yml`、Playwright 配置及用例要求准备；不能仅因本地缺环境就说相关能力已验收。PostgreSQL 迁移/集成、Windows 打包和隔离恢复有独立条件，按变更范围补验。

涉及场景、线索、战斗、SAN、结局、handout、检定或大版本发版时，按仓库 `scarlet-playthrough-check` 技能检查真实主线；涉及按钮按 `ui-button-check`。模型验收先确认服务目的地、测试数据、预算与重试上限；只用独立测试世界。legacy 主线不得代替 structured Agent 验收。

## 3. 分工与交接

- 架构负责人：边界、协议、风险、验收方案；默认不抢实现。
- 后端实现者：服务、存储、模型装配、迁移、后端测试；先提供 schema 与正反例。
- 前端实现者：UI、协议消费、错误恢复、真实后端 E2E；发现后端问题交复现，不用 UI 猜测兜底。
- 集成人：核对版本、共享文件和证据，唯一负责该轮集成推送；具体人员在任务开始时确定，不写死在永久协议中。

优先独立 worktree；共享工作目录时串行暂存/提交，不并行操作索引。任务必须声明负责人、文件边界、依赖 SHA、非目标、验收与授权。后端以明确冻结 SHA/fixture 交接；前端回执直接给集成人，不反复等用户转述。

先保存开发检查点，再做修复和联合验收；检查点不叫“产品完成”。验收期间新增相关代码须更新认证目标。只改文档时不机械重跑业务全量，但检查引用、文件完整性和业务代码未被误改。

## 4. 验收证据

报告必须区分：静态检查、脚本化模型、真实后端、人类主持、真实 Agent、旧模式主线、staging、生产发布。每项写明版本与实际路径；不能把不同版本的成功片段拼成完整通过。

环境 skip、已知缺陷 fixme、未授权模型测试分别列出。CI 超时不能仅凭失败位置认定机器慢；先看 trace、浏览器错误、网络与服务日志。测试通过不代表不存在漏洞。

只提交脱敏证据。Key、Cookie、真实玩家存档、数据库及私人模型上下文不得进入仓库；临时绝对路径只作取证位置，不保证别人可复现。截图只提交有意义的变化，不批量带入其他人的重渲染产物。

## 5. 文档制度

日常只读根 README 与六份现行文档：ARCHITECTURE、PROTOCOL、DEVELOPMENT、MODULE_FORMAT、OPERATIONS、STATUS。维护规则：

1. 架构/行为变化更新对应现行文档，未完成事项更新 STATUS，不新建一个“最终最终报告”充当状态正本。
2. 完整字段与较长操作说明放 `docs/reference/`，由现行入口链接；schema/fixtures 仍是机器可读正本。
3. 过程报告、旧计划、事故取证放 `docs/archive/YYYY-MM/{plans,reports,investigations}/`，注明版本与历史属性。
4. 新计划标明“提案/已批准/实施中/已取代”；归档不表示所有待办完成，也不重新授予执行权限。
5. 原始证据 `docs/evidence/` 和截图保留原路径，不修改 JSON/TXT 轨迹以让旧结果更好看。
6. 文档搬迁同步修复 Markdown 相对链接与现行导航；历史命令/日志原文不静默改写，旧路径可在[归档索引](archive/README.md)查找。
7. 架构台账并入架构与状态入口，避免额外维护一套重复“架构师知识库”。

不以“精简文档”为由删除未解决问题或完整契约。正式发布仍按[运维](OPERATIONS.md)执行。
