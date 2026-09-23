# TRPG Master

中文跑团平台：玩家提交行动，守秘人决定如何主持，服务端负责权限、检定和状态落账。守秘人可以是人类，也可以是通过工具工作的 Agent。

当前仓库同时保留 legacy AI 回合模式和实验性的 structured_v1 平台模式。**实验分支能力不代表正式环境已上线，Agent 真实模型验收也不能由人类主持测试替代。** 当前确认状态见[项目状态](docs/STATUS.md)。

<p align="center">
  <img src="docs/screenshots/menu.png" alt="模组选择" width="48%"/>
  <img src="docs/screenshots/character-select.png" alt="调查员选择" width="48%"/>
  <img src="docs/screenshots/gameplay.png" alt="守秘人叙事回合" width="48%"/>
  <img src="docs/screenshots/character-panel.png" alt="调查员面板" width="48%"/>
</p>

截图用于展示界面，不作为当前版本验收证据。

## 能做什么

- 本地 Electron、浏览器云端单人和多人房间；内置「疯狂宅邸」「猩红文档」模组。
- 结构化平台：出示线索、使用道具、请求移动与检定；人类/辅助/Agent 主持共用命令和权限边界。
- 正常叙事中的过渡与等待：记录交互线程，允许玩家追问或改意；待办本身不授权移动。
- 角色记忆与主持查询：按身份和当前上下文提供材料；它与 legacy 的影子记忆机制不同。
- 世界、素材、存档与分支；legacy 从历史回合分叉，结构化单人从当前已提交版本分叉。
- 模组包、Schema 校验、编译诊断和素材工具链。

两种模式的规则和上下文能力并非完全等价。例如 legacy 的战斗、发现规则、Lorebook/Skill 注入不能因平台模式存在就视为已自动迁入。详细边界见[架构](docs/ARCHITECTURE.md)。

## 快速开始

开发基线：Python 3.12、Node.js 22；本地 SQLite，云端部署配置见运维文档。

### Linux 桌面

```bash
git clone https://github.com/Inaglyite/TRPG-Master.git
cd TRPG-Master
bash start_desktop.sh
```

### Windows 构建

```powershell
powershell -ExecutionPolicy Bypass -File packaging/build_windows.ps1 -UseChinaMirrors
```

构建产物位于 `frontend/release/`。本地配置和运行数据不要纳入安装包或版本库。

### 模型与账号

人类主持的结构化游戏无需模型 Key；Agent 主持需要有效模型配置。云端使用 BYOK 授权与绑定，不能假定服务器提供免费额度，玩家加入房间也不等于获得主持密钥访问权。

通过界面配置模型；本地命令行配置入口为：

```bash
python3 start.py --config
```

`.env.json`、API Key、数据库与真实存档不提交。手动安装、启动和测试步骤见[开发说明](docs/DEVELOPMENT.md)。

## 文档入口

日常只需从下面几份文档开始，避免把历史交付报告当作现行规格。

| 文档 | 用途 |
| --- | --- |
| [架构](docs/ARCHITECTURE.md) | 模块边界、两条运行路径、权威状态、上下文与记忆 |
| [协议](docs/PROTOCOL.md) | 请求/命令/事件、权限、恢复语义与 Schema 正本 |
| [开发](docs/DEVELOPMENT.md) | 环境、门禁、协作分工、文档维护规则 |
| [运维](docs/OPERATIONS.md) | 环境隔离、BYOK、备份、发布与回滚 |
| [模组](docs/MODULE_FORMAT.md) | 模组作者入口、格式与迁移边界 |
| [状态](docs/STATUS.md) | 已验收范围、发布门槛、未完成项与限制 |

完整字段表、旧引擎详解和模块索引在[详细参考](docs/reference/README.md)。
过程计划、事故调查与交付报告在[历史归档](docs/archive/README.md)，证据及截图保留原路径。

## 协作与发布

- 日常测试只在本地；预发布优先使用 Pi staging。
- 正式环境不是测试环境。生产发布必须明确授权，按运维流程执行。
- 修改协议先同步 Schema/fixtures，再由前后端共同验收；测试通过要注明版本、环境和跳过项。
- `AGENTS.md` 是仓库协作约束，文档整理不改变其权限规则。
