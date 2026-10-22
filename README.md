# TRPG Master

[简体中文](README.md) · [English](README.en.md)

**以模组驱动的中文线上跑团平台，让 AI 或人类担任守秘人。**

选择模组与调查员，用自然语言决定下一步。场景、人物对白、线索、道具、检定与存档在同一处呈现；AI 通过受限工具主持，服务端负责掷骰、权限和状态结算。当前主要围绕《克苏鲁的呼唤》（CoC）的调查玩法开发。

[下载 Windows 客户端](https://github.com/Inaglyite/TRPG-Master/releases/latest) · [浏览器入口](https://trpggame.xyz) · [上手与开发](docs/DEVELOPMENT.md) · [制作模组](docs/MODULE_FORMAT.md)

![守秘人与人物分别发言，玩家可以选择建议或自由输入行动](docs/screenshots/gameplay.png)

## 从一场调查开始

1. **选择模组与调查员。** 仓库附带「疯狂宅邸」「猩红文档」，也支持导入 `.trpgmod` 模组包；可以使用预设调查员或管理自己的角色。
2. **决定谁来主持。** 经典 AI 叙事适合直接开始个人调查；结构化模式提供人类主持、AI 辅助主持和 AI 主持入口。人类主持无需模型 Key。
3. **用自己的方式行动。** 选项只是建议，你也可以自由提问、搜查、交涉或提出其他做法。过程中查阅人物、线索和调查笔记，保存进度，或创建分支尝试不同选择。

### 主要功能

| 能力 | 使用方式 |
| --- | --- |
| 模组与创作 | 场景、NPC、线索、发现条件与结局随模组组织；模组工坊编辑、导出，游戏内导入试玩 |
| 人物对话 | 旁白与人物台词分开显示，按身份匹配姓名、头像与气泡；临时人物也可使用公开称呼发言 |
| CoC 调查 | 技能检定、奖励/惩罚骰、SAN、战斗、物品与案件时间由对应规则和执行回执结算 |
| 调查员与资料 | 角色档案、属性技能、物品、已获线索、调查笔记与资料图片 |
| 存档与时间线 | 自动/手动存档、读取反馈、历史回顾与分支；具体分叉范围随运行模式不同 |
| 多种客户端 | Electron 本地桌面、浏览器云端单人，以及多人房间 |
| 主持工具 | 人类主持工作台，以及供 Agent 查询的模组资料、私有笔记、历史和执行回执 |

经典 AI 回合与结构化主持使用不同执行路径，功能和验收覆盖仍有差异。当前开发、正式发布和待完善项目分别记录在[项目状态](docs/STATUS.md)，不将一种模式的通过结果等同于全部模式完成。

## 界面预览

<table>
  <tr>
    <td width="50%"><img src="docs/screenshots/menu.png" alt="模组选择与模组工坊入口"/><br/><sub>选择模组、继续冒险或进入模组工坊</sub></td>
    <td width="50%"><img src="docs/screenshots/character-select.png" alt="调查员选择与完整角色档案"/><br/><sub>选择调查员，查看档案并设置游玩方式</sub></td>
  </tr>
  <tr>
    <td width="50%"><img src="docs/screenshots/character-panel.png" alt="游戏中的调查员属性、线索与物品面板"/><br/><sub>在游戏中查阅属性、线索和物品</sub></td>
    <td width="50%"><img src="docs/screenshots/module-workshop.png" alt="模组工坊编辑界面"/><br/><sub>在模组工坊中组织自己的故事</sub></td>
  </tr>
</table>

截图更新于 **2026-10-22**，来自本地开发版和独立演示数据，不包含玩家存档；界面为中文，已发布安装包可能与当前源码有所不同。

## 安装与启动

### Windows：直接使用安装包

到 [GitHub Releases](https://github.com/Inaglyite/TRPG-Master/releases/latest) 下载 `trpg-game-setup-…-x64.exe` 安装版，或 `trpg-game-portable-…-x64.exe` 便携版。首次启动后选择本地或云端游戏，并按所选主持方式配置模型。

### Linux：从源码启动桌面版

准备 **Python 3.12+、Node.js 22 和 npm**，然后执行：

```bash
git clone https://github.com/Inaglyite/TRPG-Master.git
cd TRPG-Master
bash start_desktop.sh
```

启动器检查依赖并构建前端；选择本地游戏后启动本地后端。源码开发、浏览器本地运行、数据库迁移及 Windows 打包见[开发说明](docs/DEVELOPMENT.md)。

### 模型与账号

通过界面的模型设置配置服务地址、模型和 Key；也可使用 `python3 start.py --config` 配置本地模型。人类主持不调用模型；AI 主持和辅助功能需要可用的模型配置及服务额度。

云端需要账号，并使用对应账号/世界的 BYOK 绑定。加入房间不等于获得主持人的密钥；费用归属取决于实际使用的模型绑定。`.env.json`、API Key、数据库和真实存档不要提交到版本库。

## 创作自己的模组

在开局页点击 **「模组工坊」**，编辑场景、人物、线索与主持资料，导出 `.trpgmod` 后导入游戏，在独立世界试玩。也可以从 [module-template](examples/module-template/) 开始，用命令行编译、打包和校验。

支持独立更新机制的桌面版本，可在工坊提示后选择更新编辑器；「文件 → 检查编辑器更新」也可主动检查。兼容的编辑器网页更新无需每次重发整个游戏安装包。格式、素材和运行边界见[模组作者入口](docs/MODULE_FORMAT.md)。

## 文档与参与开发

| 文档 | 内容 |
| --- | --- |
| [开发与上手](docs/DEVELOPMENT.md) | 环境准备、浏览器/桌面启动、测试与协作 |
| [模组作者入口](docs/MODULE_FORMAT.md) | 工坊、模板、格式、导入与验收 |
| [架构](docs/ARCHITECTURE.md) | 两种执行路径、状态、上下文与记忆 |
| [协议](docs/PROTOCOL.md) | 请求、命令、事件、权限与恢复 |
| [运维](docs/OPERATIONS.md) | 配置、备份、环境隔离与发布 |
| [项目状态](docs/STATUS.md) | 已发布范围、开发进展和已知限制 |
| [English documentation](docs/en/README.md) | English setup, play modes and module authoring guide |

完整入口见[文档索引](docs/INDEX.md)。欢迎通过 [Issues](https://github.com/Inaglyite/TRPG-Master/issues) 提交问题：附上版本、主持方式、复现步骤和脱敏日志，会更容易定位。

开发测试在本地进行，正式服务不用于测试。玩法或协议修改应同步对应文档与测试；完整开发和发布规则见上述文档。

项目源码采用 [MIT License](LICENSE)。
