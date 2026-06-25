# 🎲 TRPG Agent 内核

可插拔模组的克苏鲁的呼唤第七版跑团引擎。规则执行、状态管理、d100 检定、约束生成——纯内核，可接入任意前端。

## 架构

```
玩家 → Electron / 浏览器 (TS + Vite)
          ↕  WebSocket (流式文本 + 结构化事件)
       FastAPI 服务器 (server.py)
          ↕  同步回调
       GameEngine 内核 (src/engine.py)
          ↕  Function Calling + CLI
       DeepSeek V4 Flash/Pro + GLM-4 Flash + Python Tools
```

| 层 | 技术栈 | 职责 |
|---|---|---|
| **前端** | Vite + TypeScript + Electron | 聊天 UI、流式文本、检定弹窗、角色面板、模组选择器 |
| **接口层** | FastAPI + WebSocket | 引擎回调 → WS 消息，WS 消息 → 引擎动作 |
| **内核** | `src/engine.py`（回调驱动） | 游戏循环、LLM 调用、工具执行——零界面依赖 |
| **规则** | `rules/*.json` + `skills/` (15文件4000+行) | COC 7e 完整规则——守秘人/调查员/核心三层架构 + 结构化心理学知识库 |
| **工具** | `tools/*.py` | 确定性 Python 脚本——d100 检定、SAN 三阶段疯狂、角色创建、模组导入、心理特质管理 |

## 项目结构

```
trpg-master/
├── src/                          # 内核（零界面依赖）
│   ├── engine.py                 # GameEngine — 回调驱动游戏循环
│   ├── config.py                 # 路径/API配置/动态模块切换
│   ├── tools.py                  # Function Calling Schema + 执行器
│   ├── llm.py                    # 流式 LLM + GLM 快速摘要
│   ├── persistence.py            # Skill加载 + 多槽位存档/读档
│   └── game_loop.py              # 终端壳
├── server.py                     # WebSocket 服务器
├── game_loop.py                  # 终端版入口
├── start_desktop.sh              # 桌面版一键启动
├── start.py / start.sh           # 跨平台启动器
│
├── frontend/                     # Electron 桌面应用
│   ├── electron/main.cjs         # Electron 主进程
│   ├── src/
│   │   ├── main.ts               # 入口（主题加载 + 启动）
│   │   ├── ws.ts                 # WebSocket 通信
│   │   ├── renderer.ts           # 消息渲染/流式文本
│   │   ├── options.ts            # 选项解析/检定弹窗
│   │   ├── panels.ts             # 角色面板/存档面板/结局
│   │   ├── start.ts              # 开局流程/模组选择
│   │   ├── dom.ts                # DOM 引用集中管理
│   │   └── style.css             # 克苏鲁暗色主题
│   ├── index.html
│   ├── vite.config.ts
│   └── package.json
│
├── tools/                        # 确定性 Python 工具
│   ├── skill_check.py            # d100 roll-under + 三难度 + 奖惩骰 + 属性裸检定
│   ├── dice.py                   # 通用骰子
│   ├── damage.py                 # 伤害/治疗
│   ├── sanity.py                 # X/Y SAN损失 + 三阶段疯狂 + 28恐惧症/24躁狂症 + 精神分析/现实认知
│   ├── character.py              # COC 7e 角色创建 (9职业) + 心理特质初始化
│   ├── module_loader.py          # Markdown→world_state.json 导入
│   └── state_manager.py          # 世界状态读写 + NPC揭示追踪 + 心理特质管理
│
├── skills/                       # 约束型提示模板 (COC 7e 完整规则)
│   ├── core/                     # d100检定/防剧透(TIER+私有工作记忆)/入口
│   ├── keeper/                   # 守秘人方法论/氛围/NPC/线索/战斗/理智/魔法/心理学知识库
│   ├── investigator/             # 技能详解(含心理学→NPC揭示联动)/角色创建/调查方法
│   └── (旧顶层文件保留桥接)
│
├── rules/                        # 规则数据 (结构化 JSON)
│   ├── rule_schema.json          # COC 8属性 + 41技能 + 衍生值 + 检定/战斗/理智
│   └── rule_config.json          # 规则开关
│
├── saves/                        # 存档 (按模组隔离)
│   └── <module_name>/
│       ├── slot_000/             # 自动存档
│       │   ├── messages.json     # LLM 对话历史
│       │   ├── snapshot.json     # 世界状态快照 (读档恢复)
│       │   └── meta.json         # 元数据
│       └── slot_NNN/             # 手动存档
│
├── mod/                          # 模组目录 (可自由加载)
│   ├── mansion_of_madness/       # 疯狂宅邸 (示范)
│   │   ├── module.md             # Markdown 模组定义
│   │   ├── world_state.json      # 当前游戏状态
│   │   ├── world_state_initial.json  # 初始状态 (新游戏恢复)
│   │   ├── theme.json            # 配色/字体/标题
│   │   ├── characters/           # 角色卡
│   │   ├── skills/               # 模组专属守秘人指南
│   │   └── assets/               # 素材
│   └── 猩红文档/                   # 猩红文档模组
│       ├── module.md             # 413行完整定义
│       ├── theme.json            # 血色暗黑主题
│       ├── scenes/               # 详细场景构造文档
│       ├── skills/               # 守秘人指南 (403行)
│       └── assets/               # 15张 NPC/场景素材
│
└── rules/ skillsucai/            # COC 7e 规则书原文 (3本)
    ├── 克苏鲁的呼唤守秘人规则书.md
    ├── 克苏鲁的呼唤第七版调查员手册.md
    └── 快速入门手册-基础规则.md
```

## 设计原则

1. **规则是数据，不是提示词** — COC 7e 规则用 JSON + Skill 存放，模型读取执行
2. **Tool 是确定性 Python 脚本** — 数值计算全部由脚本完成，属性绑定在代码层
3. **Skill 是约束型提示模板** — 只告诉模型「何时调哪个 Tool、如何描述、不能透露什么」
4. **状态是唯一真理之源** — 所有变更通过 `state_manager.py`，模型不能直接改文件
5. **强制防剧透 + 私有工作记忆** — NPC 秘密不在 system prompt 常驻，按需通过工具访问；TIER 四级 + NPC revealed 分层追踪
6. **上下文自适应压缩** — ENGRAM 风格会话摘要 + TIER 滑动窗口注入，防止长期游戏中规则稀释和信息遗忘

## 快速开始

### 前置条件

- Python 3.10+, Node.js 18+
- DeepSeek API Key
- (可选) 智谱 GLM API Key (免费)

### 配置与启动

```bash
python3 start.py --setup    # 创建 venv + 安装依赖
python3 start.py --config   # 配置 API Key

bash start_desktop.sh       # 桌面版 (Electron优先/浏览器兜底)
python3 game_loop.py        # 终端版

# 切换模组
TRPG_MODULE="猩红文档" python3 server.py
```

## 游戏机制

### COC 第七版 d100 检定

```
skill_check(skill="spot_hidden")  → 自动读 PC 技能值 → 掷 d100
d100 ≤ 技能值 = 常规成功  |  ≤ 半值 = 困难成功  |  ≤ 1/5 = 极难成功
01 = 大成功  |  100 = 大失败  |  支持奖励骰/惩罚骰/孤注一掷
```

### 模型路由

| 场景 | 模型 |
|------|------|
| 叙事、对话、场景 | DeepSeek V4 Flash (1M 上下文) |
| 技能检定、战斗、理智 | DeepSeek V4 Pro (自动切换) |
| 检定后即时摘要 / 会话压缩 | GLM-4 Flash (免费, <1s) |

### 存档系统

- 文件夹式多槽位: `saves/<模组>/slot_NNN/`
- 每槽含对话历史 + 世界快照 + 元数据
- 读档恢复快照——杜绝线索跨存档污染
- 新游戏不覆盖已有存档
- 存档面板支持新建/加载/删除/重命名

### 线索系统（四分类）

| 🔍 探案 | 📜 事件 | 🎯 任务 | 👤 人物 |
|---|---|---|---|
| 现场证据 | 剧情推进 | 目标方向 | NPC发现 |

### 防剧透（TIER 四级 + 私有工作记忆）

| 层级 | 示例 | 可用时机 |
|------|------|----------|
| TIER_0 | "面色苍白的管家站在壁炉旁" | 进入场景 |
| TIER_1 | "地毯污渍是陈旧血迹" | 检定成功 |
| TIER_2 | "喷溅方向指向楼梯口" | 多线索关联 |
| TIER_3 | NPC secret 字段 | **绝不透露** |

**私有工作记忆系统**：NPC secret 不再常驻 system prompt，改为 `get_npc_secret(id)` 按需访问 + `npc_reveal(id,tier)` 追踪揭示程度（0-3级）。每次叙事前必须调用 `get_private_memory()` 确认信息边界。

### 三层记忆防线

| 防线 | 机制 | 解决 |
|------|------|------|
| ENGRAM 会话摘要 | GLM-4 Flash 自动压缩旧消息为 Episodic/Semantic 结构化 JSON（超30条或8000 token触发） | 近期事件遗忘 |
| NPC 知识追踪 | 每个 NPC 的 `revealed_level` + `revealed_entries`，检定成功联动 `npc_reveal()` | 秘密泄露 |
| TIER 滑动窗口 | 高危回合（检定/战斗/理智）后≥5轮自动注入规则提醒到 user 消息前缀 | 规则稀释 |

### 心理特质系统

PC 拥有 `psychological_profile`（traits/key_relationships/phobias/manias）。恐惧症/躁狂症三阶段效果：

| SAN 正常 | 潜在疯狂期 | 疯狂发作 |
|----------|-----------|----------|
| 角色扮演点缀 | 触发源存在时惩罚骰×1 | 守秘人接管角色 |

来源：角色创建预设 + 疯狂发作 #9/#10 自动写入 + 守秘人 `set_psychological_trait()` 自定义（必须锚定游戏事件）。

### 新增工具（v2）

| 工具 | 用途 |
|------|------|
| `attribute_check(attr)` | STR撞门/DEX接物/CON抗毒/INT灵感/POW意志/EDU常识等裸属性检定 |
| `luck_check()` | 幸运检定（d100≤POW），外部环境因素 |
| `sanity_trigger(desc)` | 16种日常 SAN 触发场景速查——不仅限于"看到怪物" |
| `psychoanalysis(target)` | 精神分析治疗，恢复1D3 SAN，压制恐惧症1h |
| `reality_check()` | 潜在疯狂期鉴别幻觉 |
| `npc_reveal(id,tier,text)` | NPC 秘密揭示追踪，心理学检定成功后联动 |
| `get_npc_secret(id)` | NPC 完整秘密（守秘人按需访问） |
| `get_private_memory()` | 私有工作记忆——信息边界总览 |
| `set_psychological_trait(cat,name,ctx)` | 心理特质自定义（恐惧症必须锚定事件） |

## 模组开发

### Markdown 模组格式

模组通过 `module.md` 定义，支持以下段：

| 段 | 说明 |
|----|------|
| `# PC` | 调查员建议、属性范围 |
| `# NPC` | 所有 NPC (id/name/visible_tags/secret/skills/hp/disposition) |
| `# 场景` | 场景描述/出口/NPC位置 (支持 detail_file 引用详细构造文档) |
| `# 线索` | 初始线索列表 (investigation/event/task/npc) |
| `# 标志` | 游戏标志 (flags) |
| `# 规则` | 模组专属规则——怪物面板/SAN触发/物品/法术 |
| `# 开场` | 开幕叙事 Hook |
| `# 结局` | 多种结局条件与描述 |

导入: `python3 tools/module_loader.py mod/<name>/module.md`

### 主题配置

`theme.json` 定义配色/字体/标题，前端启动自动加载。切换模组时主题跟随。

## 技术栈

| 组件 | 技术 |
|------|------|
| 后端 | Python 3.12, FastAPI, Uvicorn |
| 内核 LLM | DeepSeek V4 Flash/Pro (OpenAI 兼容) |
| 快速摘要 | 智谱 GLM-4 Flash (免费) |
| 前端 | Vite + TypeScript vanilla + Electron |
| 通信 | WebSocket (流式文本 + 结构化事件) |
| 规则体系 | COC 第七版 d100 (14 skill 文件 3500 行) |

## 可用模组

| 模组 | 说明 | NPC | 场景 |
|------|------|-----|------|
| 🏛 疯狂宅邸 | 维多利亚式宅邸中的克苏鲁恐怖 | 3 | 2 |
| 📜 猩红文档 | 密斯卡托尼克大学学者离奇死亡，女巫审判文档下落不明 | 11 | 8 |
