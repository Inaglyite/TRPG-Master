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
| **前端** | Vite + TypeScript + Electron | 聊天 UI、流式文本渲染、检定确认弹窗、角色面板 |
| **接口层** | FastAPI + WebSocket | 桥接：把引擎回调事件转为 WS 消息，把 WS 消息转为引擎动作 |
| **内核** | `src/engine.py`（回调驱动） | 游戏循环、LLM 调用、工具执行——不依赖任何界面 |
| **规则 & 工具** | `rules/*.json` + `tools/*.py` | 规则是数据（不是提示词），工具是确定性 Python 脚本 |

## 项目结构

```
trpg-master/
├── src/                          # 内核（纯 Python，零界面依赖）
│   ├── engine.py                 # GameEngine — 回调驱动的游戏循环
│   ├── config.py                 # 路径、API 配置、常量
│   ├── tools.py                  # Function Calling Schema + 工具执行器
│   ├── llm.py                    # 流式 LLM 调用 + GLM 快速摘要
│   ├── persistence.py            # Skill 加载 + 存档/读档
│   └── game_loop.py              # 终端壳（print/input 回调 → 终端版）
├── server.py                     # WebSocket 服务器（接口层）
├── game_loop.py                  # 终端版入口
├── start_desktop.sh              # 桌面版一键启动
├── start.py / start.sh           # 跨平台启动器
│
├── frontend/                     # 前端桌面应用
│   ├── electron/main.cjs         # Electron 主进程
│   ├── src/
│   │   ├── main.ts               # 渲染进程（WS 客户端 + UI）
│   │   └── style.css             # 克苏鲁暗色主题
│   ├── index.html
│   ├── vite.config.ts
│   └── package.json
│
├── tools/                        # 确定性 Python 工具
│   ├── state_manager.py          # 世界状态读写（唯一真理之源）
│   ├── dice.py                   # 骰子（d20/d100/优势劣势/多骰修正）
│   ├── damage.py                 # 伤害/治疗（通过 state_manager 操作）
│   └── sanity.py                 # COC 理智值
│
├── skills/                       # 约束型提示模板（不包含计算逻辑）
│   ├── trpg_master.skill         # 守秘人入口
│   ├── narrative.skill           # 场景叙事 + 选项生成
│   ├── skill_check.skill         # 技能/属性检定流程
│   ├── combat.skill              # 战斗流程
│   └── no_spoiler.skill          # 防剧透硬约束（四级信息边界）
│
├── rules/                        # 规则数据（结构化 JSON）
│   ├── rule_schema.json          # 属性、技能、检定公式
│   └── rule_config.json          # 规则开关（暴击/大失败/理智系统）
│
└── mod/                          # 模组目录
    └── mansion_of_madness/       # 示范模组：疯狂宅邸
        └── world_state.json      # 世界状态（PC、NPC、场景、线索、缓存）
```

## 设计原则

1. **规则是数据，不是提示词** — 所有规则系统用结构化 JSON 存放，模型读取执行
2. **Tool 是确定性 Python 脚本** — 数值计算全部由脚本完成，输出 JSON 结果
3. **Skill 是约束型提示模板** — 只告诉模型"何时调哪个 Tool、如何描述、不能透露什么"
4. **状态是唯一真理之源** — 所有状态变更通过 `state_manager.py`，模型不能直接改文件
5. **强制防剧透** — NPC 秘密、未揭示线索绝对不能泄露（四级信息边界系统）

## 快速开始

### 前置条件

- Python 3.10+
- Node.js 18+
- DeepSeek API Key（[获取地址](https://platform.deepseek.com/)）
- （可选）智谱 GLM API Key（[获取地址](https://open.bigmodel.cn/)，免费）

### 1. 配置

```bash
# 创建 venv 并安装依赖
python3 start.py --setup

# 交互式配置 API Key
python3 start.py --config
```

或手动创建 `trpg-master/.env.json`：

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

### 2. 启动桌面版

```bash
bash start_desktop.sh
```

- 优先启动 Electron 桌面窗口
- Electron 不可用时自动回退到浏览器（`http://localhost:8765`）
- 后端日志写入 `/tmp/trpg-server.log`

### 3. 终端版（无前端）

```bash
python3 game_loop.py
```

命令：`/quit` 退出 | `/save` 存档 | `/load` 读档 | `/state` 查看状态

## 游戏机制

### 检定流程

```
玩家行动 → suggest_check 确认（自由行动时）
         → dice_roll 掷骰（🎲 即时显示结果）
         → GLM-4 Flash 快速摘要（<1s）
         → DeepSeek 流式生成完整叙事
         → 选项生成
```

- **预设选项**：直接掷骰，不确认
- **自由行动**：先弹出难度提示，玩家可反悔

### 模型路由

| 场景 | 模型 | 说明 |
|------|------|------|
| 叙事、对话、场景描写 | DeepSeek V4 Flash | 默认，1M 上下文 |
| 技能检定、战斗、理智判定 | DeepSeek V4 Pro | 自动切换 |
| 检定后即时反馈 | GLM-4 Flash（免费） | <1s 摘要，消除愣神 |

### 防剧透系统

| 层级 | 示例 | 何时可用 |
|------|------|----------|
| TIER_0 公开 | "一位面色苍白的老管家站在壁炉旁" | 玩家进入场景立即可见 |
| TIER_1 观察 | "地毯上的污渍是血迹" | 检定成功 |
| TIER_2 推理 | "血迹喷射方向指向楼梯口" | 多线索关联 |
| TIER_3 秘密 | NPC 的 `secret` 字段 | **绝不主动透露** |

## 自定义模组

编辑 `mod/mansion_of_madness/world_state.json`：

- `pc` — 调查员属性、技能、物品
- `npcs` — NPC（含 `visible_tags` 和 `secret`）
- `current_scene` — 初始场景
- `flags` — 游戏标志

规则定制：编辑 `rules/rule_schema.json` 和 `rules/rule_config.json`。

## 技术栈

| 组件 | 技术 |
|------|------|
| 后端 | Python 3.12, FastAPI, Uvicorn |
| 内核 LLM | DeepSeek V4 Flash (1M ctx) + DeepSeek V4 Pro，OpenAI 兼容 API |
| 快速模型 | 智谱 GLM-4 Flash (128K ctx)，免费 |
| 前端 | Vite + TypeScript（vanilla），Electron，WebSocket |
| 主题 | 克苏鲁暗色 — 旧羊皮纸 + 烛火金 + 暗红血迹 |
