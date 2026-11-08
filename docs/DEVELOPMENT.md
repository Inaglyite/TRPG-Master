# 开发、协作与文档维护

根目录 `AGENTS.md` 是本机私有协作约定，不纳入 Git；存在时先读。共享规则由本文与[运维](OPERATIONS.md)维护。默认本地开发与测试；真实模型调用、预发布、正式发布分别授权。工作区他人改动不得覆盖、stash 或顺手提交。

## 1. 本地环境

使用 Python 3.12+；CI 使用 Node.js 22，前端依赖以 lockfile 为准。
Windows 用户可从 [Releases](https://github.com/Inaglyite/TRPG-Master/releases/latest)
下载安装版或便携版；Linux 桌面在仓库根目录运行 `bash start_desktop.sh`。
启动器检查依赖与前端构建，选择本地游戏后才启动源码后端。
英文操作入口见 [English guide](en/README.md)。

终端入口为 `python3 start.py`，优先使用当前已激活的虚拟环境；模型路由与桌面
共用本地 `model_settings.local.json`，未自定义的角色回退 `.env.json` / 环境变量。
`python3 start.py --config` 配置默认服务；`TRPG_MODULE=猩红文档 python3 start.py`
可选择模组。有存档时先输入 `/load` 或 `/new`，启动不会重置旧世界或先调用模型。
`/retry` 重试失败的开场或原行动；`/save-new` 创建独立槽位，`/saves` 列出槽位，
`/load slot_001` 读取指定进度并显示上次回复，读档不生成新回合。终端支持经典
AI 叙事路径，结构化人类主持仍使用桌面/网页工作台。

手动准备后端环境（仓库根目录，`python3` 应为 3.12+）：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
```

构建前端，再执行数据库迁移并启动本地浏览器服务：

```bash
cd frontend
npm ci
npm run build
cd ..
python -m alembic upgrade head
python server.py
```

浏览器打开 `http://127.0.0.1:8765/?mode=local`。首次运行可选择模组、调查员，
再在选角页的“游玩方式”切换经典 AI 叙事、人类、AI 辅助或 AI 主持。
人类主持不需要模型 Key；AI 功能按模型设置页/BYOK 路由配置。
经典 AI 和结构化模式的能力与验收覆盖分别见 [STATUS](STATUS.md)。

前端热更新可另开终端运行 `cd frontend && npm run dev`，访问 Vite 输出的地址并加
`?mode=local`；后端保持在 8765。需要其他后端地址时设置
`VITE_TRPG_BACKEND_ORIGIN`，并同步核对后端允许的来源。
手动启动不替代桌面启动器的一次性旧文件世界导入；迁移已有数据前先备份。

独立开发或测试时，在迁移和启动前为 `TRPG_RUNTIME_ROOT`、`TRPG_DATABASE_URL`
设置对应的隔离目录与数据库，不继承正式环境 URL，也不使用真实玩家存档。
`.env.json`、Key 和运行数据不提交、不写进截图。模型请求仍需连接相应服务；
本地运行不表示 AI 可以离线推理。

桌面日志的 `[backend:stderr]` 表示后端标准错误输出流，不是日志严重级别：
Alembic/Uvicorn 正常的 INFO 也写入该流。Linux 上的 VA-API 初始化及 Wayland/Vulkan
诊断需结合页面加载、后端启动和实际 WebGL 功能判断，不能仅按 ERROR 字样判定启动失败。

### 按回合查看开发日志

后端保留普通运行日志 `trpg.log`，并默认写入 `diagnostics.jsonl`。
后者每行是一个独立 JSON 事件，带 UTC 时间、进程/运行标识、世界、模组、回合和请求编号。
记录范围包括经典引擎回合起止/提交、已有阶段校验记录、模型调用结果/重试、工具起止与有限的结果字段。
它不是前端渲染确认，也不是所有玩法模式的完整审计；详细正文与权威状态仍以数据库回合记录为准。

日志目录按以下优先级选择（启动时生效）：

1. 显式设置的 `TRPG_LOG_DIR`。
2. 设置了 `TRPG_RUNTIME_ROOT` 时，使用该目录下的 `logs/`。
3. 打包程序使用可执行文件旁的 `logs/`；源码开发默认使用仓库父目录
   `mid_product/<仓库目录名>/work/runtime-logs/`。不会搬动既有日志。

```bash
# 最近 100 条事件；默认自动查找上述目录并读取轮转文件
python tools/diagnose_turn.py
# 按世界/回合筛选；编号可从上一条命令输出中取得
python tools/diagnose_turn.py --world WORLD_ID --turn TURN_ID --limit 200
# 指定桌面客户端的日志目录，输出筛选后的 JSONL
python tools/diagnose_turn.py --log-dir /path/to/runtime/logs --turn TURN_ID --json
```

排障时先找 `turn.started`，再看 `turn.stage` 中的校验状态、`model.completed` 的
`status/error_class`、`model.retry` 的原因与退避、`tool.completed` 的 `ok/code`，
最后确认是否出现 `turn.committed` 或 `turn.incomplete`。若只有 `tool.started`
没有对应 `call_id` 的完成记录，说明截取时尚未完成或进程中断，不能直接断言工具失败。
`model.completed` 表示本次调用已结束，是否成功必须看 `status`；并不保证整个回合已提交。

JSONL 每份最多约 5 MiB，保留 3 份轮转备份；完整一行可能使文件短暂略超阈值。
默认不记录玩家正文、提示词、原始模型回复、任意工具参数/结果或异常消息；
工具异常只保留类型与最后 12 层代码位置。新目录权限为 `0700`，JSONL 新文件为 `0600`
（Windows 仍以账户 ACL 为准）。原始日志可能包含内部实体 ID，不应未经核对直接外发。
`TRPG_DIAGNOSTICS=0` 可关闭 JSONL，不影响原有日志。JSONL 写入失败仅提示一次并继续游戏。
同一目录按单个后端进程设计，多进程部署应分别设置 `TRPG_LOG_DIR`，避免轮转互相覆盖。

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
2. 机器可读正本保留在仓库内 schema/fixtures；较长历史字段说明及操作资料迁往同级 `mid_product/trpg-master/`，在 [INDEX](INDEX.md) 登记位置。
3. 过程报告、旧计划、事故取证、生成美术原稿与截图放 `mid_product`，注明版本与历史属性；已投入使用的前端素材仍留在 `frontend/src/assets/`。
4. 新计划标明“提案/已批准/实施中/已取代”；归档不表示所有待办完成，也不重新授予执行权限。
5. 原始证据按旧相对目录原样归档并保留 SHA256 清单，不修改 JSON/TXT 轨迹以让旧结果更好看。历史日志里的旧路径不再表示文件仍留在仓库。
6. 文档搬迁同步修复现行入口链接；历史命令/日志原文不静默改写，旧路径可在[INDEX](INDEX.md#归档资料)查找。
7. 架构台账并入架构与状态入口，避免额外维护一套重复“架构师知识库”。

不以“精简文档”为由删除未解决问题或完整契约。正式发布仍按[运维](OPERATIONS.md)执行。

本机 `test-results` 可用被本地 Git 排除的链接指向 `mid_product/trpg-master/work/test-results`，使新产物也写到仓库外；该链接不提交。其他机器与 CI 不依赖本机归档目录，测试仍可使用默认输出位置。归档及原始轨迹可能含私有测试上下文，目录需限制为本人访问。

新日志、trace、截图、临时诊断脚本、生成美术原稿及过程报告统一放仓库父目录的 `mid_product/<仓库目录名>/work/<任务标识>/`，不写死开发机绝对路径。完整验收冻结到独立的 `<日期>-<任务标识>/`，附版本、目录索引和校验和，后续任务不覆盖冻结资料。现有应用素材、业务代码、schema、fixtures 和测试源码继续在仓库内；真实配置、Key、数据库及玩家存档不得当作中间产物搬动。原始资料不整包推送或外发，共享前先脱敏并确认范围。

## 6. 模组工坊独立更新

桌面壳启动的本地后端启用 `TRPG_ENABLE_EDITOR_UPDATES=1`。编辑器打开后检查
`Inaglyite/TRPG_Mod_Editor` 的 `editor-web-v<版本>` Release，展示版本及更新说明；
用户确认后才下载。文件菜单的“检查编辑器更新”会主动重新检查。
源码后端单独启动时默认关闭；需要本地验收时显式设置该变量。

新版网页资源存放在运行目录 `editor-updates/versions/`，校验 SHA256、大小、
解压路径与 `editor_api=1` 后原子切换 `active.json`。当前页面不强制刷新，
下次访问 `/editor/` 使用新版；旧资源与安装包附带版本继续保留。新版入口
缺失或损坏时使用内置版，完整游戏升级附带更高版本时优先使用该内置版。
云端账号模式禁用这一更新接口，继续按正式部署流程更新。

编辑器仓库的 `editor-web-release` workflow 手动构建、验收并上传
`editor-web.zip` 与 `editor-web-update.json`；测试包默认预发布，不进入稳定通道。
隔离验收可用 `TRPG_EDITOR_UPDATE_TEST_TAG=editor-web-test-<标识>` 指向具体测试包。
内置快照仍由 `bash tools/sync_editor_bundle.sh ../TRPG_Mod_Editor` 同步。
旧游戏客户端需先升级一次以获得该机制，此后兼容的编辑器网页更新无需重发游戏安装包。

## 7. 中英文入口与展示截图

根 `README.md` 以中文为主，`README.en.md` 提供英文介绍；英文上手、主持方式和
创作操作集中在 `docs/en/README.md`。六份中文现行文档继续作为详细规格入口，
行为变化同步更新英文操作说明，避免维护一套过期的英文协议副本。

README 实际引用的公开截图保留在 `docs/screenshots/`，来源与用途见该目录的
`README.md`。重拍时先把源码构建、脚本、原图、日志放仓库外的任务目录，
使用隔离运行目录和演示世界，确认不含真实存档、密钥或未公开剧情后，
只复制最终选用的图片到文档目录。标注日期、开发版/发布版和演示数据范围，
实际打开中英文页面检查图片、说明及链接；截图不替代功能验收。

### 对话格式诊断

新回复的 `segments` 只承载旁白和台词，行动选项只能放入顶层 `choices`（非空单行、不带编号的字符串）。正文菜单在发布回调前拒绝；阶段日志中的 `embedded_choice_menu` 表示正文夹带菜单，`invalid_choice_format` 表示选项项格式无效。格式修复最多一次、禁用工具，避免重复执行检定；再次失败则不展示该回复并报告错误。历史记录仍使用兼容解码。叙事一致性校对也应用相同校验，违规重写被丢弃。
