# TRPG Game

一个由 AI 担任守秘人的中文 TRPG 游戏。模型负责叙事与理解玩家意图，d100 检定、战斗、伤害、SAN、线索、世界状态和存档由确定性的 Python 规则引擎结算。

支持本地 Electron 单机、浏览器云端单人和 2–4 人联机；仓库内置「疯狂宅邸」与「猩红文档」两个可游玩模组。

<p align="center">
  <img src="docs/screenshots/menu.png" alt="模组选择" width="48%"/>
  <img src="docs/screenshots/character-select.png" alt="调查员选择" width="48%"/>
  <img src="docs/screenshots/gameplay.png" alt="守秘人叙事回合" width="48%"/>
  <img src="docs/screenshots/character-panel.png" alt="调查员面板" width="48%"/>
</p>

## 核心能力

- **模型叙事，代码裁决**：模型不能自行编造骰值、伤害、SAN 或世界状态；关键结果由服务端工具提交。
- **行动预演与自然转场**：自由输入和推荐选项先经过确定性预检，在真正移动前给出 NPC 劝告、风险提示和可撤回机会。
- **服务端权威战斗**：先攻、对抗检定、伤害、弹药和防御选择由状态机处理；不可逆的高风险行动需要玩家确认。
- **场景、NPC 与线索闭环**：模组可以声明发现规则、失败保底、危机、时钟和结局条件，避免调查因一次失败永久卡死。
- **Lorebook 与 Skill**：按当前场景、权威状态和规则集确定性注入相关材料；模型不能读取任意项目文件或越权调用内部工具。
- **存档与时间线**：世界、存档位、不可变快照和回合日志持久化；支持从历史决策点创建分支并恢复未完成回合。
- **模组工具链**：`.trpgmod` 使用 JSON、Markdown 和素材文件，支持 Schema 校验、编译诊断、版本并存及浏览器模组工坊。
- **桌面与云端**：Electron 保留完整单机体验；云端提供账号、可撤销 Session、房间权限、私密事件过滤和 PostgreSQL 持久化。

## 快速开始

### 环境要求

- Python 3.12+
- Node.js 20+
- 本地游玩需要 OpenAI 兼容接口的 API Key；仅加入云端游戏不需要个人 Key
- 云端部署使用 PostgreSQL；本地单机默认使用 SQLite

### Linux 一键启动

```bash
git clone https://github.com/Inaglyite/TRPG-Master.git
cd TRPG-Master
bash start_desktop.sh
```

脚本会在首次需要本地后端时创建 `venv`、安装 Python 依赖、迁移 SQLite，并自动安装与构建前端。只选择云端模式时不会创建本地数据库或安装后端依赖。

### Windows 构建

在安装 Python 3.12+、Node.js LTS 和 Git 后，用 PowerShell 构建安装版与便携版：

```powershell
powershell -ExecutionPolicy Bypass -File packaging/build_windows.ps1 -UseChinaMirrors
```

输出位于 `frontend/release/`，API Key 和本地运行数据不会进入安装包。

### 手动安装（开发者）

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cd frontend
npm ci
npm run build
cd ..
```

交互式配置模型，配置会写入已被 Git 忽略的 `.env.json`：

```bash
python3 start.py --config
```

最小配置示例：

```json
{
  "api_key": "your-api-key",
  "base_url": "https://api.deepseek.com",
  "flash_model": "deepseek-v4-flash",
  "pro_model": "deepseek-v4-pro"
}
```

环境变量会覆盖文件配置。常用项包括 `OPENAI_API_KEY`、`OPENAI_BASE_URL`、
`TRPG_FLASH_MODEL`、`TRPG_PRO_MODEL`、`TRPG_DATABASE_URL`、
`TRPG_REQUIRE_AUTH`、`TRPG_ALLOWED_ORIGINS` 和 `TRPG_LLM_MAX_CONCURRENCY`。
部署所需的完整配置见[部署与恢复](docs/DEPLOYMENT.md)。

### 启动桌面版

```bash
bash start_desktop.sh
```

启动后选择：

- **单机游戏**：按需启动本地后端，自动应用数据库迁移；首次数据库化启动会导入旧版 `worlds/` 数据。
- **多人游戏**：直接连接云端，不启动本地后端，也不读取本机 API Key。

终端版可直接运行：

```bash
python3 start.py --setup
python3 start.py --config
python3 start.py
```

### 前端开发

```bash
# 终端 1
source venv/bin/activate
python3 server.py

# 终端 2
cd frontend && npm run dev

# 终端 3（可选）
cd frontend && npm run electron:dev
```

## 云端与多人游戏

官方入口为 [trpggame.xyz](https://trpggame.xyz)。登录后可以：

1. 在「我的冒险」创建或继续私密云端单人世界；
2. 创建多人房间并生成邀请码，或使用邀请码加入已有房间；
3. 选择未被占用的调查员，准备后由房主开局；
4. 按服务端给出的行动权提交操作，断线后重新进入同一房间即可恢复；
5. 使用快速存档、手动存档和时间线分支管理调查进度。

云端模型凭据由服务器维护者保管，玩家浏览器和 Electron 客户端不会收到 API Key。桌面单机默认关闭账号门禁，不应直接暴露到公网。

## 模组开发

从示例工程开始：

```bash
cp -r examples/module-template /tmp/my-trpg-module
venv/bin/python tools/module_packager.py compile /tmp/my-trpg-module
venv/bin/python tools/module_packager.py pack /tmp/my-trpg-module /tmp/my-module.trpgmod
venv/bin/python tools/module_packager.py validate /tmp/my-module.trpgmod
```

模组编译器会验证稳定 ID、场景与 NPC 引用、发现规则、失败保底、危机和结局契约、素材路径及 ZIP 安全。完整字段见[模组格式](docs/MODULE_FORMAT.md)。

## 项目结构

```text
trpg-master/
├── server.py        # FastAPI HTTP / WebSocket 入口
├── src/             # 游戏引擎、回合工作流、规则、持久化与权限
├── tools/           # 模组、账号、备份与验收工具
├── skills/          # 受控的守秘人规则与能力目录
├── rules/           # 结构化规则数据
├── mod/             # 内置模组
├── schemas/         # .trpgmod JSON Schema
├── examples/        # 模组工程模板
├── frontend/        # React、Vite 与 Electron
├── editor/dist/     # 随服务发布的模组工坊静态制品
└── docs/            # 长期维护的技术契约
```

## 文档

- [架构](docs/ARCHITECTURE.md)：进程、回合工作流、上下文、数据所有权、安全与扩展边界。
- [接口](docs/API.md)：HTTP、WebSocket 消息、事件顺序和错误协议。
- [模组格式](docs/MODULE_FORMAT.md)：`.trpgmod` 作者契约与编译诊断。
- [部署与恢复](docs/DEPLOYMENT.md)：PostgreSQL、TLS、备份、监控和发布流程。
- [路线图](docs/ROADMAP.md)：CoC 规则完整性、复杂战斗、追逐、地图和多人世界演进。

项目只保留上述长期契约；个人 Agent 指令、验收 Skill、临时设计稿和过程记录均不进入版本控制。

## 开发校验

```bash
venv/bin/python -m pytest -q
venv/bin/python -m ruff check src server.py tools tests
venv/bin/python -m compileall -q src tools server.py tests

cd frontend
npm test
npm run format:check
npm run build
```

涉及接口、状态结构或模组格式的变更，必须同步更新对应契约文档。真实模型全流程验收会产生 API 费用，仅在发布候选版本按需执行，不属于普通单元测试。

## 当前边界

- 多人目标为单房间 2–4 人，当前使用单个 Uvicorn worker；尚未实现跨进程房间协调。
- 结构化记忆目前是 shadow-only 内部边界，不参与正常回合、模型工具、提示词、HTTP/WS 或玩家 UI。
- 地图、复杂追逐、完整 CoC 长期角色成长和自由分头行动仍在路线图中。
- Linux 当前从源码运行 Electron；Windows 安装包与便携版通过 `packaging/build_windows.ps1` 构建。

## 许可证

代码使用 [MIT License](LICENSE)。内置模组的文本与素材仅供游玩和研究；再分发前请检查各模组 `manifest.json` 中的许可字段。
