# 🎲 TRPG Agent 内核 — 疯狂宅邸

可插拔模组的 TRPG 跑团引擎。规则执行、状态管理、骰子判定、约束生成——纯内核，可接入任意前端。

## 架构概览

```
玩家 → Electron / 浏览器 (TS + Vite)
          ↕  WebSocket
       FastAPI 服务器 (server.py)
          ↕  同步回调
       GameEngine 内核 (src/engine.py)
          ↕  Function Calling + CLI
       DeepSeek V4 Flash/Pro + GLM-4 Flash + Python Tools
```

### 分层职责

| 层 | 技术栈 | 职责 |
|---|---|---|
| **前端** | Vite + TypeScript + Electron | 聊天 UI、流式文本渲染、检定确认弹窗、角色面板、线索分类展示 |
| **接口层** | FastAPI + WebSocket | 桥接：引擎回调事件 → WS 消息，WS 消息 → 引擎动作 |
| **内核** | `src/engine.py`（回调驱动） | 游戏循环、LLM 调用、工具执行——零界面依赖 |
| **规则 & 工具** | `rules/*.json` + `tools/*.py` | 规则是数据（不是提示词），工具是确定性 Python 脚本 |

## 项目结构

```
trpg-master/
├── src/                          # 内核（纯 Python，零界面依赖）
│   ├── engine.py                 # GameEngine — 回调驱动的游戏循环
│   ├── config.py                 # 路径、API 配置、常量
│   ├── tools.py                  # Function Calling Schema + 工具执行器
│   ├── llm.py                    # 流式 LLM 调用 + GLM 快速摘要
│   ├── persistence.py            # Skill 加载 + 存档/读档（多槽位）
│   └── game_loop.py              # 终端壳（print/input 回调）
├── server.py                     # WebSocket 服务器（接口层）
├── game_loop.py                  # 终端版入口
├── start_desktop.sh              # 桌面版一键启动
├── start.py / start.sh           # 跨平台启动器
│
├── frontend/                     # Electron 桌面应用
│   ├── electron/main.cjs         # Electron 主进程
│   ├── src/
│   │   ├── main.ts               # 渲染进程（WS 客户端 + UI）
│   │   └── style.css             # 克苏鲁暗色主题（旧羊皮纸 + 烛火金）
│   ├── index.html
│   ├── vite.config.ts
│   └── package.json
│
├── tools/                        # 确定性 Python 工具
│   ├── state_manager.py          # 世界状态读写（唯一真理之源）
│   ├── skill_check.py            # 技能检定（属性绑定，查表计算）
│   ├── dice.py                   # 纯骰子（d20/d100/优势劣势）
│   ├── damage.py                 # 伤害/治疗
│   └── sanity.py                 # COC 理智值
│
├── skills/                       # 约束型提示模板
│   ├── trpg_master.skill         # 守秘人入口（工具列表 + 约束）
│   ├── narrative.skill           # 场景叙事 + 选项生成 + 缓存
│   ├── skill_check.skill         # 技能检定流程（强制调用 skill_check）
│   ├── combat.skill              # 战斗流程
│   └── no_spoiler.skill          # 防剧透硬约束（四级边界 + 线索分类指南）
│
├── rules/                        # 规则数据（结构化 JSON）
│   ├── rule_schema.json          # 属性、技能、修正表、检定公式
│   └── rule_config.json          # 规则开关（暴击/大失败/理智）
│
├── saves/                        # 存档目录（多槽位 + 世界快照）
│   └── slot_000/                 # 自动存档槽
│       ├── messages.json         # LLM 对话历史
│       ├── snapshot.json         # 世界状态快照（读档时恢复）
│       └── meta.json             # { 时间, 场景, HP, SAN, 线索数 }
│
└── mod/                          # 模组目录
    └── mansion_of_madness/       # 示范模组：疯狂宅邸
        └── world_state.json      # PC、NPC、场景、线索、标志
```

## 设计原则

1. **规则是数据，不是提示词** — 所有规则系统用结构化 JSON 存放，模型读取执行
2. **Tool 是确定性 Python 脚本** — 数值计算全部由脚本完成，属性绑定在代码层
3. **Skill 是约束型提示模板** — 只告诉模型「何时调哪个 Tool、如何描述、不能透露什么」
4. **状态是唯一真理之源** — 所有状态变更通过 `state_manager.py`，模型不能直接改文件
5. **强制防剧透** — NPC 秘密、未揭示线索绝对不能泄露（四级信息边界系统）

## 快速开始

### 前置条件

- Python 3.10+
- Node.js 18+
- DeepSeek API Key
- （可选）智谱 GLM API Key（免费，用于检定即时摘要）

### 1. 配置

```bash
python3 start.py --setup    # 创建 venv 并安装依赖
python3 start.py --config   # 交互式配置 API Key
```

或手动创建 `.env.json`：

```json
{
  "api_key": "sk-your-deepseek-key",
  "base_url": "https://api.deepseek.com",
  "flash_model": "deepseek-v4-flash",
  "pro_model": "deepseek-v4-pro",
  "glm_api_key": "your-glm-key",
  "glm_model": "glm-4-flash-250414"
}
```

### 2. 启动

**桌面版**（推荐）：
```bash
bash start_desktop.sh
```
优先 Electron 窗口，不可用时自动回退浏览器。

**终端版**：
```bash
python3 game_loop.py
```

## 游戏机制

### 检定流程（属性绑定）

```
玩家行动 → skill_check(skill="investigation", dc=15)
         → 自动读取 PC 属性 → 查修正表 → 计算技能加值 → 掷 d20
         → 🎲 【调查】(d20=14+1+7=22) vs DC 15 → ✓
         → 成功则 state_add_clue() 记录线索
         → GLM-4 Flash 快速摘要 + DeepSeek 流式叙事
```

- **自由行动**：先 `suggest_check` 确认难度并给反悔机会
- **预设选项**：直接 `skill_check`，不确认
- **属性绑定**：CHA=40 → -2 修正，INT=70 → +1 修正——完全由代码查表计算

### 线索系统（四分类）

| 分类 | 说明 | 示例 |
|------|------|------|
| 🔍 探案线索 | 现场证据、物理发现 | "地毯血迹呈喷射状指向楼梯口" |
| 📜 事件线索 | 剧情推进、NPC陈述 | "管家称三十年前圣诞夜发生火灾" |
| 🎯 任务线索 | 目标、下一步 | "需要找到二楼右侧第二扇门" |
| 👤 人物线索 | NPC相关发现 | "管家对楼梯口存在记忆空白" |

线索在角色面板中按分类展示。`skill_check` 成功后强制记录。

### 存档系统（文件夹式多槽位）

- `saves/slot_000/` — 自动存档（退出时）
- `saves/slot_NNN/` — 手动存档
- 每槽含 `messages.json`（对话）+ `snapshot.json`（世界快照）+ `meta.json`
- **读档时从 snapshot 恢复世界状态**，彻底杜绝线索跨存档污染
- 新游戏不覆盖已有存档

### 防剧透系统

| 层级 | 示例 | 何时可用 |
|------|------|----------|
| TIER_0 公开 | "一位面色苍白的老管家站在壁炉旁" | 进入场景立即可见 |
| TIER_1 观察 | "地毯上的污渍是血迹" | 检定成功 |
| TIER_2 推理 | "血迹喷射方向指向楼梯口" | 多线索关联 |
| TIER_3 秘密 | NPC 的 `secret` 字段 | **绝不主动透露** |

### 模型路由

| 场景 | 模型 |
|------|------|
| 叙事、对话、场景 | DeepSeek V4 Flash（1M 上下文） |
| 技能检定、战斗、理智 | DeepSeek V4 Pro（自动切换） |
| 检定后即时摘要 | GLM-4 Flash（免费，<1s） |

## 自定义模组

编辑 `mod/mansion_of_madness/world_state.json`：

- `pc` — 调查员属性、技能、物品
- `npcs` — NPC（含 `visible_tags` 和 `secret`）
- `current_scene` — 初始场景
- `flags` — 游戏标志
- `scene_cache` — 场景描述缓存（首次生成后自动写入）

规则定制：`rules/rule_schema.json` + `rules/rule_config.json`

## 技术栈

| 组件 | 技术 |
|------|------|
| 后端 | Python 3.12, FastAPI, Uvicorn |
| 内核 LLM | DeepSeek V4 Flash + DeepSeek V4 Pro（OpenAI 兼容 API） |
| 快速模型 | 智谱 GLM-4 Flash（免费） |
| 前端 | Vite + TypeScript（vanilla） + Electron |
| 通信 | WebSocket（流式文本 + 结构化事件） |
| 主题 | 克苏鲁暗色 — 旧羊皮纸 + 烛火金 + 暗红血迹 |
