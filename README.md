# TRPG Master

一个面向本地单人跑团的 AI 守秘人桌面应用。项目把叙事模型、CoC 7e 风格规则、确定性工具、模组状态和 Electron 界面拆成独立层，通过 FastAPI WebSocket 传递流式叙事与结构化游戏事件。

当前仓库内置两个可游玩模组：`mansion_of_madness`（疯狂宅邸）与 `猩红文档`。目前的产品体验仍是单机、单玩家，但底层世界已经按 `world_id` 隔离，可并行运行多个实例；共享 GM 历史、房间身份和事件广播将在下一阶段实现。

## 主要能力

- Electron 桌面端与浏览器前端，共用 Vite + TypeScript 渲染层。
- OpenAI 兼容接口，默认配置面向 DeepSeek，可自定义请求地址和模型名。
- LangGraph 双角色编排：探索由叙事 Agent 处理，战斗由无私有记忆的战斗 Agent 接管。
- 服务端权威战斗状态机，负责先攻、回合、d100 对抗、伤害、枪械弹药与玩家防御确认。
- 非敌对 NPC 的首次不可逆攻击和武力威胁会在 GM 叙事前确认；取消时场景完全不变，确认后仍承担正常后果。
- 统一道具使用层：验证耐用品、消耗一次性物品，并让战斗内外的真实开枪共用余弹结算。
- d100 技能检定、属性检定、伤害、SAN 与心理状态等确定性工具。
- 模组切换、调查员选择、长期角色履历和按世界实例隔离的多槽位存档。
- `.trpgmod` 模组包预检、一键导入、版本并存、JSON Schema 与安全安装。
- `RuntimeContext + WorldStore`：revision 检查、线程/进程房间锁、原子替换、备份恢复和旧存档迁移。
- 图片线索、人物档案、场景展示材料与线索加入提示。
- 每 50 个玩家回合静默压缩旧上下文，保留最近 24 条消息。
- TIER 信息边界、NPC 揭示记录和私有工作记忆，降低模型提前剧透的概率。
- Windows 安装包/便携版构建，以及 Linux 源码桌面启动脚本。

## 文档

- [开发路线图](docs/ROADMAP.md)：单机收口、多人房间、双人可玩版本、数据库与多 Agent 规划。
- [架构文档](docs/ARCHITECTURE.md)：进程、模块、回合时序、数据所有权、扩展点与多人化边界。
- [接口文档](docs/API.md)：HTTP 路由、WebSocket 双向消息、事件顺序与数据结构。
- [模组格式](docs/MODULE_FORMAT.md)：`.trpgmod` 目录、字段、校验、安全和版本规范。
- [模组编辑器规划](docs/MODULE_EDITOR.md)：编辑器需求、技术架构、阶段与验收标准。
- [模组工程模板](examples/module-template/manifest.json)：可直接打包的 v1 示例。

## 快速开始

### 环境要求

- Python 3.10+
- Node.js 20 LTS 或更新版本
- 一个 OpenAI 兼容 API Key
- 可选：智谱 GLM API Key，用于快速摘要与上下文压缩

### 安装依赖

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cd frontend
npm install
npm run build
cd ..
```

### 配置模型

交互式写入项目根目录的 `.env.json`：

```bash
python3 start.py --config
```

也可以手动创建：

```json
{
  "api_key": "your-api-key",
  "base_url": "https://api.deepseek.com",
  "flash_model": "deepseek-v4-flash",
  "pro_model": "deepseek-v4-pro",
  "glm_api_key": "optional-glm-key",
  "glm_base_url": "https://open.bigmodel.cn/api/paas/v4/",
  "glm_model": "glm-4-flash-250414"
}
```

`.env.json` 已被 Git 忽略，不会进入版本库。系统环境变量优先于文件配置：

| 环境变量 | 用途 | 默认值 |
|---|---|---|
| `OPENAI_API_KEY` | 主模型 API Key | 空 |
| `OPENAI_BASE_URL` | OpenAI 兼容请求地址 | `https://api.deepseek.com` |
| `TRPG_FLASH_MODEL` | 常规叙事模型 | `deepseek-v4-flash` |
| `TRPG_PRO_MODEL` | 强制 Pro 与上下文摘要兜底模型 | `deepseek-v4-pro` |
| `TRPG_FORCE_PRO` | 全程强制使用 Pro，支持 `1/true/yes` | 关闭 |
| `GLM_API_KEY` | 可选摘要模型 API Key | 空 |
| `GLM_BASE_URL` | GLM 请求地址 | `https://open.bigmodel.cn/api/paas/v4/` |
| `GLM_MODEL` | GLM 模型名 | `glm-4-flash-250414` |
| `TRPG_MODULE` | 启动时使用的模组目录名 | `mansion_of_madness` |
| `TRPG_PROJECT_ROOT` | 模组、规则与 Skill 的只读定义根目录 | 自动识别 |
| `TRPG_RUNTIME_ROOT` | `worlds/`、自定义角色和长期档案的可写根目录 | 源码模式同项目根目录；打包模式为后端目录 |
| `TRPG_WORLD_ID` | 工具子进程打开的世界实例；通常由引擎自动注入 | 当前模组的默认本地世界 |

### 启动桌面版

从终端启动并查看实时日志：

```bash
./start_desktop.sh
```

无终端桌面入口应使用 `Terminal=false` 的 `.desktop` 文件调用：

```text
Exec=/absolute/path/to/trpg-master/start_desktop.sh --desktop
Terminal=false
```

桌面模式日志写入 `/tmp/trpg-desktop.log`，后端日志写入 `/tmp/trpg-server.log`。Electron 最后一个窗口关闭后，启动脚本会自动停止后端并释放 `8765` 端口。

### 启动终端版

```bash
python3 start.py
```

### 前端开发模式

分别启动后端、Vite 和 Electron：

```bash
# 终端 1
source venv/bin/activate
python3 server.py

# 终端 2
cd frontend
npm run dev

# 终端 3
cd frontend
npm run electron:dev
```

后端默认地址为 `http://127.0.0.1:8765`，WebSocket 为 `ws://127.0.0.1:8765/ws`。Vite 开发服务器默认使用 `http://127.0.0.1:5173`。

## 游戏内操作

- `快速存档`：直接覆盖当前世界的自动槽 `slot_000`。
- `存档管理`：读取、新建手动存档、重命名和删除手动槽。
- `角色/线索`：查看当前调查员状态、物品、线索和已发放图片。
- `新游戏`：返回开始流程，重新选择模组与调查员。

每个 GM 回合完成时也会更新自动槽。退出确认窗口仍建议玩家先快速存档，以免在正在生成的回合中途关闭。

## 项目结构

```text
trpg-master/
├── server.py                 # FastAPI HTTP + WebSocket 适配层
├── game_loop.py              # 终端版入口
├── start.py                  # 配置与终端启动器
├── start_desktop.sh          # Linux 桌面进程托管脚本
├── requirements.txt
├── docs/
│   ├── ARCHITECTURE.md
│   ├── API.md
│   ├── MODULE_FORMAT.md
│   └── MODULE_EDITOR.md
├── src/
│   ├── engine.py             # GameEngine、模型调用、记忆与回调
│   ├── agent_graph.py        # LangGraph 回合状态机
│   ├── combat_agent.py       # 战斗 Agent 的临时职责提示词
│   ├── combat.py             # 权威战斗状态与确定性结算
│   ├── inventory.py          # 道具验证、消耗与资源数量结算
│   ├── tools.py              # Function Calling schema 与工具分发
│   ├── persistence.py        # Skill 组装、存档与快照恢复
│   ├── characters.py         # 调查员选择与长期履历
│   ├── runtime.py            # RuntimeContext、world_id 路径与旧数据迁移
│   ├── module_format.py      # v1 模组领域模型、引用校验与 Schema
│   ├── module_compiler.py    # 无副作用编译、编译产物与字段来源追踪
│   ├── module_diagnostics.py # 结构化错误、警告与作者建议
│   ├── module_registry.py    # 内置/用户模组发现及安全包安装
│   ├── world_store.py        # revision、房间锁、原子写与备份恢复
│   ├── world_migrations.py   # 世界 schema 迁移注册表
│   ├── config.py             # 默认路径、模型和 Skill 配置
│   └── llm.py                # GLM 辅助摘要
├── tools/                    # 骰子、状态、战斗、伤害、SAN 等确定性 CLI 工具
├── skills/                   # 常驻与按需加载的守秘人约束
├── rules/                    # 结构化规则数据
├── mod/<module>/             # 模组定义、初始模板、主题、素材和专属 skill
├── modules/<id>/<version>/   # 用户导入模组（运行时生成）
├── schemas/trpgmod/          # 编辑器与第三方工具共享的 JSON Schema
├── examples/module-template/ # v1 模组工程模板
├── characters/               # 默认与自定义调查员
├── profiles/                 # 长期角色履历（运行时生成）
├── worlds/<world_id>/        # 当前世界、备份、元数据和存档（运行时生成）
├── frontend/
│   ├── electron/main.cjs     # Electron 主进程与打包后端托管
│   ├── src/                  # TypeScript UI
│   └── package.json
└── packaging/                # PyInstaller 与 Windows 构建脚本
```

## 运行链路

```text
玩家
  -> Electron / Browser
  -> WebSocket server.py
  -> GameEngine
  -> LangGraph 回合图
  -> OpenAI 兼容模型 / Python 工具
  -> RuntimeContext
  -> worlds/<world_id>/world_state.json + saves/
```

模型负责叙事和决定行动意图：非战斗回合走叙事 Agent，战斗激活后按同一世界状态切换为战斗 Agent。两者不维护互相独立的长期记忆；骰子、先攻、回合推进、技能值、伤害、SAN、世界状态、存档和素材发放均由 Python 代码执行。详细线程模型与数据流见 [架构文档](docs/ARCHITECTURE.md)。

## 存档与角色数据

存档按世界实例隔离：

```text
worlds/<world_id>/saves/slot_000/      # 自动槽
worlds/<world_id>/saves/slot_001/      # 手动槽
├── messages.json             # 模型对话与工具历史
├── snapshot.json             # world_state 快照
└── meta.json                 # 场景、调查员、HP/SAN、线索数等摘要
```

调查员数据分为三层：

| 层 | 路径 | 作用 |
|---|---|---|
| 角色模板 | `characters/default`、`characters/custom`、`mod/*/characters` | 新游戏的候选调查员 |
| 当前案件 | `worlds/<world_id>/world_state.json.pc` | 当前 HP、SAN、物品、心理状态与案件内成长 |
| 长期履历 | `profiles/player_profile.json` | 已完成模组、结局、声望、人脉与最后状态 |

运行时数据和 API Key 均已加入 `.gitignore`。旧版 `mod/<module>/world_state.json` 只作为首次迁移来源保留；新游戏与工具调用不会再写入模组目录。

## 模组开发与导入

新模组使用 `.trpgmod` v1。它是经过安全限制的 ZIP 包，至少包含：

```text
module-project/
├── manifest.json
├── module.json
├── keeper.md                 # 可选
├── theme.json                # 可选
├── skills/                   # 可选，需声明 custom_skills
├── characters/               # 可选，需声明 bundled_characters
├── scenes/                   # 可选，需声明 scene_documents
└── assets/                   # 可选，图片与音频
```

编译预览、生成、检查和导入：

```bash
venv/bin/python tools/module_packager.py compile examples/module-template
venv/bin/python tools/module_packager.py compile examples/module-template \
  --output /tmp/example-compiled
venv/bin/python tools/module_packager.py pack \
  examples/module-template /tmp/example.trpgmod
venv/bin/python tools/module_packager.py validate /tmp/example.trpgmod
```

`compile` 直接调用游戏安装流程使用的编译内核，返回字段级诊断和输入到输出的来源追踪；不传
`--output` 时不会写文件。编辑器也可调用 `POST /api/modules/compile` 获得同样的结果。

游戏开始页的“导入”按钮会先做格式、安全和引用预检，确认后安装到
`<runtime>/modules/<id>/<version>/` 并自动切换。安装过程生成兼容当前引擎的 `module.md` 和
`world_state_initial.json`；作者工程与玩家运行状态始终分离。

完整字段、大小限制、版本兼容和错误规则见 [模组格式](docs/MODULE_FORMAT.md)。旧式
`mod/<name>/module.md` 仍可运行，`tools/module_loader.py` 只作为有损兼容入口保留。

## Windows 打包

在 Windows PowerShell 中执行：

```powershell
powershell -ExecutionPolicy Bypass -File packaging/build_windows.ps1
```

脚本会：

1. 安装/检查 Python 与 Node.js 依赖。
2. 用 PyInstaller 构建 `trpg-server.exe`。
3. 用 electron-builder 构建 NSIS 安装版和便携版。

输出位于 `frontend/release/`。`.env.json` 不会被打进安装包；首次运行时由 Electron 配置窗口收集 API 地址和 Key。

## 开发校验

后端包含世界隔离、并发、战斗、道具、旧存档和恢复测试。提交前运行：

```bash
venv/bin/python -m unittest discover -s tests -v
venv/bin/python -m ruff check src server.py tools tests
venv/bin/python -m compileall -q src tools server.py tests
cd frontend && npm run build
bash -n ../start_desktop.sh
```

接口变化还应同步更新 [接口文档](docs/API.md)。修改存档、角色或模组状态结构时，应同时更新 [架构文档](docs/ARCHITECTURE.md) 的数据所有权章节。

## 当前边界

- API 没有鉴权，后端用于本机桌面应用，不应直接暴露到公网。
- 不同 `world_id` 已隔离；同一世界的写入由 `WorldStore` 跨线程/进程串行并带 revision。
- 每条 WebSocket 仍拥有独立 `GameEngine.messages`；多个连接尚不能作为一个共享 GM 房间。
- 多人模式下一步需要 `RoomManager`、共享引擎、行动队列、玩家身份和事件广播，不能只增加玩家列表。
