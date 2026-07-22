# TRPG Master 模组格式（.trpgmod）

本文定义 `.trpgmod` 模组包格式，面向两类读者：

- **模组作者**：想为 TRPG Master 创作可导入、可游玩的模组；
- **工具开发者**：构建模组编辑器、校验器或第三方工具（编译器内部契约另见
  [架构文档](ARCHITECTURE.md)的「扩展方式」一章）。

基本约定：

- 格式版本：`1.0` 与 `2.0` 并存，均可导入（差异见 [5.7 主线安全](#57-主线安全progressionv2)）
- 交换容器：ZIP，扩展名 `.trpgmod`
- 结构化数据：UTF-8 JSON
- 长篇正文：UTF-8 Markdown
- JSON Schema：Draft 2020-12（生成产物见 `schemas/trpgmod/`）

## 1. 快速开始

仓库自带一个完整的最小工程 [examples/module-template](../examples/module-template/manifest.json)
（两场景短篇调查「低语档案馆」）。以它为骨架，十分钟可以跑通全流程：

```bash
# 1. 复制模板作为自己的工程目录
cp -r examples/module-template my-module

# 2. 编辑 my-module/manifest.json（id、title）和 my-module/module.json（场景、NPC、线索）

# 3. 编译预览：只向 stdout 输出诊断与编译产物，不写任何文件
venv/bin/python tools/module_packager.py compile my-module

# 4. 打包与校验
venv/bin/python tools/module_packager.py pack my-module dist/my-module.trpgmod
venv/bin/python tools/module_packager.py validate dist/my-module.trpgmod
```

然后在游戏开始页点「导入」，选择生成的 `.trpgmod` 文件。导入前会做格式、安全和引用
预检，确认后安装并自动切换到该模组。

`compile` 与游戏安装流程使用同一个编译内核，返回字段级诊断；编辑器也可调用
`POST /api/modules/compile` 获得同样的结果。完整的命令行说明见 [第 11 节](#11-命令行工具)。

## 2. 概念模型

### 2.1 作者态与运行态

模组包含两类数据，不能混为一份运行状态：

| 数据 | 所有者 | 是否随游戏改变 |
|---|---|---|
| NPC、场景、全部线索、结局和规则定义 | 模组作者 | 否 |
| 当前场景、已发现线索、HP、SAN、战斗和标志 | 运行世界 | 是 |

`.trpgmod` 保存作者态定义。安装时，编译器生成运行时使用的两份产物：

- `module.md`：结构化定义与 `keeper.md` 合成的守秘人提示。
- `world_state_initial.json`：新游戏的世界初始模板。

玩家真正的世界状态保存在运行时的世界存储中，不会写回模组包；作者工程与玩家运行
状态始终分离。

### 2.2 稳定 ID

NPC、场景、线索和结局均使用稳定 ID 作为对象键：

```text
^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$
```

显示名称可以随时修改，ID 一旦发布不应改变——场景出口、NPC 位置、素材与线索关联都
通过 ID 引用。删除或改名已发布实体 ID 属于存档不兼容变更（见
[第 12 节](#12-版本与兼容策略)）。

### 2.3 三个状态要素

模组能感知和影响的世界状态只有三类，全部在 `initial_state` 中声明初始值：

- **flags**（`initial_state.flags`）：布尔/数值/字符串标志，记录"某件事是否发生过"。
  线索的 `flag_effects`、遭遇的 `required_flags`、结局的 `required_flags` 都读写它。
- **案件时钟**（`initial_state.case_clocks`）：命名计数器，记录压力或时间推移，主要
  供失败保底（[5.6](#56-失败保底fallback)）扣除代价使用。
- **线索**（`clues` + `initial_state.known_clue_ids`）：玩家已获得的事实清单，是主线
  进度的衡量单位。

## 3. 包目录

包根目录直接包含 `manifest.json`，不能再套一层文件夹。

```text
example.trpgmod
├── manifest.json       必需：包信息、版本与能力声明
├── module.json         必需：结构化模组定义
├── keeper.md           可选：守秘人长篇正文
├── theme.json          可选：游戏主题
├── lorebook.json       可选：按回合检索的 Lorebook v3
├── assets/             可选：图片与音频素材
├── skills/             可选：模组专属 Skill
├── characters/         可选：模组调查员 JSON
└── scenes/             可选：场景补充 Markdown
```

包内不得包含 `world_state.json`、玩家存档、API Key、Python/JavaScript、动态库或可执行文件。

## 4. manifest.json

最小示例：

```json
{
  "$schema": "https://trpg-master.local/schemas/module-manifest-v1.json",
  "format_version": "1.0",
  "id": "example.whispering-archive",
  "version": "1.0.0",
  "title": "低语档案馆",
  "entry": "module.json"
}
```

主要字段：

| 字段 | 必需 | 说明 |
|---|---:|---|
| `$schema` | 否 | v1 指向 `module-manifest-v1.json`，v2 指向 `module-manifest-v2.json` |
| `format_version` | 是 | `1.0` 或 `2.0`（v2 差异见 [5.7](#57-主线安全progressionv2)） |
| `id` | 是 | 稳定包 ID，ASCII 小写，支持 `.` `_` `-` |
| `version` | 是 | 模组 SemVer，例如 `1.2.0` |
| `title` | 是 | 面向玩家的显示名称，可使用中文 |
| `author` | 否 | 作者或团队 |
| `description` | 否 | 模组列表和导入预览使用的简介 |
| `system` | 否 | 规则系统，默认 `COC 第七版` |
| `era` | 否 | 时代或背景 |
| `language` | 否 | BCP 47 风格语言标签，默认 `zh-CN` |
| `license` | 否 | 内容许可证或授权说明 |
| `homepage` | 否 | 项目主页或来源页 |
| `min_engine_version` | 否 | 最低兼容程序版本，默认 `0.1.0`；导入时强制检查 |
| `entry` | 是 | 固定为 `module.json` |
| `keeper_document` | 否 | 固定为 `keeper.md`，无正文时设为 `null` |
| `theme` | 否 | 固定为 `theme.json`，无主题时设为 `null` |
| `lorebook` | 否 | 固定为 `lorebook.json`；未使用时省略或设为 `null` |
| `capabilities` | 否 | 包内高层能力声明 |
| `tags` | 否 | 模组筛选标签 |
| `created_with` | 否 | 生成这个包的编辑器版本 |
| `checksums` | 否 | 包内文件 SHA-256；打包工具自动生成 |

支持的 capability：

| 值 | 含义 |
|---|---|
| `custom_skills` | 包含会进入守秘人上下文的 `skills/*.skill` |
| `bundled_characters` | 包含 `characters/*.json` |
| `scene_documents` | 包含 `scenes/*.md` |

包内存在相应目录时必须声明 capability。导入界面会据此显示信任提示。

`min_engine_version` 高于当前引擎版本（`src/module_format.py` 中的 `ENGINE_VERSION`）
的包会被拒绝，错误码 `engine_too_old`，不会进入安装目录。

## 5. module.json

顶层结构（v2 示例；v1 没有 `progression` 字段，`$schema` 与 `format_version` 相应不同）：

```json
{
  "$schema": "https://trpg-master.local/schemas/module-v2.json",
  "format_version": "2.0",
  "entry_scene_id": "archive_study",
  "opening_prompt": "调查员来到档案馆。",
  "npcs": {},
  "scenes": {},
  "clues": {},
  "endings": {},
  "rules": {},
  "assets": { "npcs": {}, "scenes": {}, "clues": {} },
  "initial_state": {},
  "progression": { "essential_clue_ids": [] },
  "clue_links": []
}
```

### 5.1 NPC

```json
{
  "archivist_lin": {
    "name": "林馆长",
    "visible_tags": ["年迈", "谨慎"],
    "secret": "他听见过手稿低语。",
    "hp": 9,
    "disposition": "cooperative",
    "current_location": "archive_study",
    "attributes": {},
    "skills": { "图书馆使用": 75 },
    "conditions": [],
    "spells": [],
    "asset_id": "archivist_portrait",
    "initial_reveal": 0
  }
}
```

`current_location` 必须引用存在的场景。`asset_id` 必须引用 `assets.npcs`。
`secret` 只进入守秘人上下文，玩家不可见；`visible_tags` 是玩家可见的外在印象。

### 5.2 场景

```json
{
  "archive_study": {
    "name": "档案馆书房",
    "description": "雨水拍打窄窗。",
    "exits": ["archive_courtyard"],
    "npcs_present": ["archivist_lin"],
    "aliases": ["书房", "档案馆二层"],
    "document": "scenes/archive-study.md",
    "asset_id": "archive_study_image"
  }
}
```

`entry_scene_id`、出口和在场 NPC 都必须存在。`document` 只能指向 `scenes/` 下的
Markdown（需声明 `scene_documents` capability）。

`npcs_present` 表示新游戏时的默认驻点，同时供"去找某人"解析目的地；它不表示人物
永远待在该场景。运行时以每个 NPC 的 `current_location` 为准，在抵达时重新计算实际
在场者。因此前往某人的办公室可能只发现空房间，人物头像也只会为实际在场者触发。

`aliases` 是玩家移动时使用的简称，例如完整名称"哈兰德·洛奇的历史系办公室"可配置
`["洛奇办公室", "东翼二层"]`。别名属于模组数据，引擎不需要预先认识模组的地名。

需要条件或随机出现时，在场景上声明 `encounters`：

```json
{
  "name": "教授办公室",
  "description": "门上贴着本学期的课程表。",
  "exits": ["campus"],
  "npcs_present": [],
  "encounters": [
    {
      "id": "professor_after_hours",
      "npc_id": "professor_hale",
      "availability": "luck",
      "required_flags": {"campus_open": true},
      "luck_difficulty": "hard",
      "repeat": "once",
      "on_present_text": "办公室门缝里还透着灯光，教授尚未离开。",
      "on_absent_text": "办公室已经锁门，走廊里也没有其他人。"
    }
  ]
}
```

`availability` 的语义：

- `guaranteed`：条件满足时必然在场。
- `conditional`：只按 `required_flags` / `forbidden_flags` 判断；至少声明一个条件。
- `luck`：条件满足后执行幸运检定，难度可为 `regular`、`hard` 或 `extreme`。
- `unavailable`：本阶段明确不在场，可用于保留一致的失败叙述。

`repeat: once` 会把首次结果记入世界状态，反复进出不会无限重掷；`always` 表示每次
抵达重新解析。失败只意味着人物本次不在场，不会自动透露其真实位置，也不会产生人物
头像。玩家之后可以等待、询问附近人员或从其他线索获知去向。

未声明 `encounters` 的模组继续按 NPC 的 `current_location` 解析在场情况，保持向后
兼容。导入时会校验 NPC、flag、遭遇 ID 和枚举值。

### 5.3 初始状态

`initial_state` 只保存新游戏开始时确实已经成立的状态：

```json
{
  "initial_state": {
    "pc": {
      "name": "",
      "occupation": "",
      "hp": 11,
      "max_hp": 11,
      "san": 65,
      "max_san": 65,
      "attributes": {},
      "skills": {},
      "inventory": [],
      "conditions": []
    },
    "known_clue_ids": [],
    "granted_items": ["档案室黄铜钥匙"],
    "flags": { "well_opened": false },
    "case_clocks": { "whispers": 0 },
    "private_memory": {
      "goals_and_plans": "",
      "hidden_facts": {},
      "inference_notes": "游戏刚开始。"
    }
  }
}
```

实际玩家调查员在开局时由角色选择覆盖，模组作者不能通过 PC 模板指定玩家身份。
`granted_items` 在角色覆盖后合并进调查员背包，适合模组必需的开场钥匙或委托物；
重复名称只加入一次。

- `flags`：所有会被 `flag_effects`、遭遇或结局引用的标志都必须在这里预先声明。
- `case_clocks`：所有会被失败保底的 `cost_clock` 引用的时钟都必须在这里预先声明。
- `known_clue_ids`：开局即已发现的线索（等价于在线索上设 `initially_known: true`）。
- `private_memory`：守秘人的私有工作记忆初值，玩家不可见。

调查员角色 JSON 可使用可选 `portrait` 字段（字符串，模组 `assets/` 内的相对文件名，
如 `"assets/detective.png"` 或 `"detective.png"`）为消息中的调查员头像提供素材；
缺失时前端显示姓名首字徽章。NPC 头像无需额外字段，直接取 `assets.npcs` 中与
NPC id 同键的条目。

### 5.4 线索

```json
{
  "well_fragment": {
    "text": "井边纸片与失踪手稿使用同一种纤维。",
    "category": "investigation",
    "type": "hidden",
    "tier": 1,
    "source": "archive_courtyard",
    "related_npcs": [],
    "related_scenes": ["archive_courtyard"],
    "asset_id": "fragment_photo",
    "granted_item": "井边纸片",
    "flag_effects": {},
    "discovery_rules": [
      {
        "intent": "search",
        "targets": ["排水沟", "旧井"],
        "approach_text": "你蹲下身，借着风灯检查旧井边潮湿的排水沟。",
        "skill": "spot_hidden",
        "requires_success": true,
        "sanity_severity": "minor",
        "npc_reveals": []
      }
    ],
    "initially_known": false,
    "discovery_notes": "调查排水沟成功后揭示。"
  }
}
```

分类 `category` 固定为：

- `investigation`：现场、文档和调查证据。
- `event`：已经发生的重要事件。
- `task`：委托、目标和待办事项。
- `npc`：人物相关信息。

只有 `initially_known: true` 或列入 `initial_state.known_clue_ids` 的线索在开局即已
发现，其余线索等待玩家在游戏中触发。

`granted_item` 可选，用于"发现线索时同时获得同名实物"；会幂等加入背包，旧存档已有
线索但缺少该物品时也会补齐。

`flag_effects` 的键必须预先存在于 `initial_state.flags`；发现或重新对账该线索时会
幂等应用。

`discovery_notes` 仅供作者说明和编辑器提示，不承担运行时语义。

### 5.5 发现规则（discovery_rules）

`discovery_rules` 是"玩家做出某个动作时，必然按规则结算线索"的声明式契约——命中后
的发放由引擎保证，不依赖模型是否记得调用工具。每条规则的字段：

| 字段 | 必需 | 说明 |
|---|---:|---|
| `intent` | 是 | `examine`、`search`、`read`、`take`、`talk`、`enter` 或 `use` |
| `targets` | 是 | 玩家话语中可识别的目标别名，至少一个；不接受正则或可执行代码 |
| `approach_text` | 否 | 最多 500 字。命中后立即展示的玩家可见建立句，先于骰子、SAN 和素材；只描写"开始做什么/看见什么表象"，不得剧透检定成功后的线索结论 |
| `skill` | 条件 | 技能 ID；`requires_success: true` 且非幸运检定时必填 |
| `check_type` | 否 | `skill` 或 `luck`；省略时沿用 `skill`。选择 `luck` 时不填写 `skill`，且 `requires_success` 必须为 `true` |
| `difficulty` | 否 | 检定难度 `regular`、`hard` 或 `extreme`，默认 `regular` |
| `requires_success` | 否 | 为真时，只有本轮指定检定成功才发放线索；为假时命中即发放，引擎不再追加语言推断出的侦查 |
| `sanity_severity` | 否 | `minor`、`moderate` 或 `major`；命中后在 `approach_text` 可见、模型续写之前结算 SAN |
| `npc_reveals` | 否 | 同一发现中一并提交的人物揭示，每项含 `npc_id`、`tier`（1-3）和 `entry_text`（最多 500 字） |
| `fallback` | 条件 | 检定失败时的保底结果，见 [5.6](#56-失败保底fallback)；v2 主线线索的 `requires_success` 规则必填 |

规则匹配与结算的作者可见保证：

- 规则只在线索 `source` / `related_scenes` 对应的当前场景匹配；否定句、询问能否
  行动、已发现的线索不会触发。
- 命中后，玩家先看到 `approach_text`（若有），随后线索、素材、SAN、人物揭示和
  `flag_effects` 在同一回合内结算完成。
- 幸运检定适合偶然发现的奖励证物，但不建议作为案件唯一推进路径；核心线索应另有
  无检定路径、失败仍推进的保底或其他来源。

玩家的每个行动在运行时先归入三个阶段：

- **抵达**：从一个场景前往另一个场景；本回合只完成抵达、环境建立和人物接洽。
- **互动**：留在当前场景进行普通对话、整理或未命中任何发现规则的行动。
- **接触**：在当前场景明确对目标执行 `discovery_rules` 所声明的动作。

跨场景输入即使同时写了后续目的（例如"去停尸房请医生带我查看遗体"），本回合也只
完成抵达；打开冷柜、阅读文档、触摸物件必须由抵达后的独立玩家行动触发。这是引擎
固定的承诺边界：出行目的不等于已经发生的接触，模组不需要为这种情况增加否定关键
词或特殊规则。

### 5.6 失败保底（fallback）

`requires_success: true` 的规则在检定失败时，由 `fallback` 决定发生什么。它的意义是：
**随机检定可以改变代价、叙事和调查路径，但不能让调查永久卡死。**

```json
{
  "intent": "search",
  "targets": ["排水沟", "旧井"],
  "approach_text": "你蹲下身，借着风灯检查旧井边潮湿的排水沟。",
  "skill": "spot_hidden",
  "requires_success": true,
  "fallback": {
    "mode": "grant_clue",
    "narrative": "尽管没找到夹层，纸片的纤维质地仍然引起了你的注意。",
    "cost_clock": "whispers",
    "cost_amount": 1
  }
}
```

字段：

| 字段 | 必需 | 说明 |
|---|---:|---|
| `mode` | 是 | `grant_clue` 或 `alternate_clue` |
| `clue_id` | 条件 | `alternate_clue` 时必填，指向另一条已定义的线索；`grant_clue` 时不可填写（当前线索本身就是发放对象） |
| `narrative` | 否 | 最多 500 字，提供给守秘人模型的失败叙事素材，用于把保底结果织入正文 |
| `cost_clock` | 条件 | 代价时钟 ID，必须预先在 `initial_state.case_clocks` 声明；`cost_amount` 非零时必填 |
| `cost_amount` | 否 | 失败时给 `cost_clock` 增加的数值，0-100，默认 0 |

两种模式的运行时语义（均在检定失败时触发）：

- `grant_clue`：**失败仍发放当前线索**，先给 `cost_clock` 加上 `cost_amount`，再走正常
  发放流程（`sanity_severity`、`npc_reveals`、`flag_effects` 照常结算）。适合"必定找到，
  但代价不同"的主线设计。
- `alternate_clue`：**改发 `clue_id` 指定的替代线索**，当前线索不发放；代价时钟同样
  先结算。适合"错过正路，但有旁证"的网状设计。替代线索自身必须可发现
  （`initially_known` 或拥有自己的 `discovery_rules`）。

不声明 `fallback` 时，检定失败即什么都得不到（v1 模组允许；v2 主线线索不允许，见
下节）。

### 5.7 主线安全（progression，v2）

`format_version: "2.0"` 在 v1 的场景、遭遇和发现规则之上增加
`progression.essential_clue_ids`——作者标记的"缺少它主线就无法完结"的线索清单：

```json
{
  "progression": {
    "essential_clue_ids": ["well_fragment", "manuscript_location"]
  }
}
```

被列为 **主线线索** 的线索必须满足以下约束，导入时逐条校验，违反即拒绝安装：

1. 未标记 `initially_known: true` 的主线线索必须具有 `discovery_rules`；
2. 其中 `requires_success: true` 的规则必须声明 `fallback`；
3. fallback 的 `cost_clock` 必须在 `initial_state.case_clocks` 声明；
4. `alternate_clue` 必须引用存在、且自身可发现的线索；
5. 从入口场景必须能沿 `exits` 到达全部场景（保证所有线索物理上可达）。

v1 模组不受这些约束，仍可正常加载。可以用迁移器把 v1 工程升级为 v2：

```bash
venv/bin/python tools/module_packager.py migrate-v2 <v1工程目录> <新的v2目录>
```

迁移器不会原地覆盖源工程，会写出 v2 manifest、module 和 `migration-report.json`。
未手动指定时，它默认把 `category: "task"` 的非初始线索选为主线线索，并为缺少保底的
`requires_success` 规则插入安全默认值（`grant_clue`，无代价）；作者仍应根据模组风格
补写具体的失败叙事、替代线索和时钟代价。

### 5.8 结局

```json
{
  "manuscript_recovered": {
    "title": "手稿归档",
    "trigger": "找回手稿并封住旧井",
    "description": "低语终于停止。",
    "ending_type": "good",
    "required_flags": {
      "manuscript_recovered": true,
      "well_sealed": true
    }
  }
}
```

`ending_type` 支持 `good`、`neutral`、`bad`、`secret`。`required_flags` 是结局结算的
硬门槛：缺少任一条件时，结局不会被结算。

### 5.9 素材

```json
{
  "assets": {
    "npcs": {
      "archivist_portrait": {
        "file": "assets/archivist.png",
        "label": "林馆长",
        "alt": "戴金丝眼镜的年迈馆长"
      }
    },
    "scenes": {},
    "clues": {
      "fragment_photo": {
        "file": "assets/fragment.png",
        "label": "井边纸片",
        "reveal_on": [
          {
            "event": "clue_discovered",
            "entity_id": "well_fragment"
          }
        ]
      }
    }
  }
}
```

NPC、场景或线索填写 `asset_id` 后，素材会在对应事件发生时自动展示：人物首次揭示、
进入场景、按稳定 `clue_id` 发现线索。这是格式契约的保证，不依赖模型在叙事中是否
提起素材。

`reveal_on` 是可选的精确事件绑定，适合一个素材对应多个实体。只接受稳定
`entity_id` 作为展示授权：

| `event` | 触发源 | 推荐条件 |
|---|---|---|
| `npc_revealed` | NPC 首次揭示 | `entity_id` |
| `scene_entered` | 当前场景切换 | `entity_id` |
| `clue_discovered` | 新线索写入状态 | `entity_id` |

旧包中的 `match_all`、`match_any` 和 `sanity_triggered` 字段仍可被解析，但只作为兼容
元数据，不能授权自动展示；编译器会给出 `text_handout_trigger_ignored` 警告。关键词
适合 Lorebook 检索，不适合决定玩家是否亲眼获得证据。请把恐怖证据建成带
`discovery_rules`、`sanity_severity` 和 `asset_id` 的目录线索。

数据流向是单向的：先由发现流程提交线索和 `flag_effects`，再展示其素材；展示素材
本身永远不会反向解锁线索。素材的首次展示状态会持久化，不会重复刷图；图片线索保留
在玩家线索清单中，读档后仍可查看。

允许的素材扩展名：

```text
.png .jpg .jpeg .webp .gif .avif .mp3 .ogg .wav
```

游戏 UI 目前只展示图片素材；音频类型保留给后续版本。

### 5.10 线索关联（clue_links）

`clue_links` 声明线索之间的推理关系，会编译进世界状态，供守秘人理解证据链和编辑器
绘制关系图：

```json
{
  "clue_links": [
    { "from": "well_fragment", "to": "manuscript_location", "reasoning": "纸片纤维指向手稿被藏在旧井附近" }
  ]
}
```

`from` 和 `to` 都必须是已定义的线索 ID。

### 5.11 extensions

NPC、场景、线索、PC、初始状态和模组顶层都可使用 `extensions` 保存编辑器插件数据。
编译器会把这些字段合并进对应运行时对象，但扩展字段不能覆盖标准字段。

## 6. keeper.md

`keeper.md` 保存不适合结构化表单的内容，例如：

- 开场节奏和信息边界。
- NPC 扮演建议。
- 失败推进与压力升级。
- 模组作者给守秘人的长篇说明。

它不是结构化事实的第二份副本。NPC HP、位置、线索关系和素材关联应只写在
`module.json`。

## 7. lorebook.json

叙事知识库复用 [Character Card V3 Lorebook 规范](https://github.com/kwaroran/character-card-spec-v3/blob/main/SPEC_V3.md)，不另造不兼容的条目格式。独立文件使用标准信封：

```json
{
  "$schema": "https://trpg-master.local/schemas/lorebook-v3.json",
  "spec": "lorebook_v3",
  "data": {
    "scan_depth": 2,
    "token_budget": 600,
    "recursive_scanning": false,
    "extensions": {},
    "entries": [
      {
        "id": "study-sound",
        "keys": [],
        "content": "从窗框轻震、远处翻书声或壁炉余烬中选一个细节。",
        "extensions": {
          "trpg_master": {
            "kind": "sensory_palette",
            "scene_ids": ["archive_study"],
            "group": "study-palette",
            "cooldown_turns": 1
          }
        },
        "enabled": true,
        "insertion_order": 10,
        "use_regex": false,
        "constant": true,
        "priority": 100
      }
    ]
  }
}
```

标准字段 `keys`、`constant`、`selective`、`secondary_keys`、`case_sensitive`、`priority`、`insertion_order`、`scan_depth` 和 `token_budget` 参与本地检索。`use_regex: true` 与
`recursive_scanning: true` 会被无损保留但不执行，导入预览给出 warning——这样可以避免
不受信任的正则造成回合阻塞，也避免条目内容互相激活。

`extensions.trpg_master` 可包含：

| 字段 | 说明 |
|---|---|
| `kind` | `fact`、`sensory_palette`、`npc_voice`、`scene_pressure` 或 `style` |
| `scene_ids` / `npc_ids` | 仅在对应场景或在场人物满足时可用 |
| `required_flags` / `forbidden_flags` | 世界 flag 门槛；支持点分路径 |
| `required_clue_ids` | 只有线索已经进入玩家线索清单后才可用 |
| `visibility` | `public` 或 `gated`；`gated` 必须有 flag/线索门槛 |
| `group` / `weight` | 同组每回合只确定性选择一个变体 |
| `cooldown_turns` | 使用后多少叙事回合内不重复 |
| `sensory_focus` | 供守秘人控制本轮感官重点的短标签 |

场景、NPC、线索和 flag 引用会在编译/导入阶段校验。玩家输入中的关键词只负责激活
已经满足信息门槛的条目，不能解锁 `gated` 内容。检索完全在本地完成，不产生额外模型
请求，也不把检索素材加入常驻守秘人提示；它在每回合状态之后追加一个有 token 上限的
素材块。

## 8. 校验与诊断

导入器按以下顺序拒绝问题包：

1. ZIP 路径与体积安全检查。
2. `manifest.json` 结构与最低引擎版本校验。
3. `module.json` 结构校验（含 v2 主线安全约束，见 [5.7](#57-主线安全progressionv2)）。
4. NPC、场景、出口、线索、Lorebook 和素材交叉引用校验。
5. capability 与目录一致性校验。
6. UTF-8、JSON 与 SHA-256 校验。
7. 编译运行时模板并原子安装。

错误响应包含稳定的 `error_code`、面向用户的 `error` 和可定位字段的 `details`。
诊断分 `error`、`warning`、`advice` 三级，只有 `error` 阻止安装。例如：未被实体引用
且没有 `reveal_on` 的素材不会阻止导入，但会产生 `asset_without_reveal_path` warning，
编辑器应在发布前明确展示。

编译器本身不读写文件、不安装模组、不创建世界；它会为每个运行时值记录来源字段，
供编辑器解释"这个值从哪里来"。面向工具开发者的编译器接口契约见
[架构文档](ARCHITECTURE.md)的「扩展方式」一章。

## 9. 安全限制

| 限制 | 上限 |
|---|---:|
| 压缩包大小 | 64 MiB |
| 解压后总大小 | 256 MiB |
| 单文件大小 | 32 MiB |
| 文件数量 | 1024 |
| 异常压缩比 | 大文件最高 200:1 |

导入器拒绝：

- 绝对路径、`..`、反斜杠路径、大小写重复路径、Windows 保留名称和非法字符。
- 符号链接、加密 ZIP 和可执行脚本。
- 未声明的 Skill、角色或场景文档目录。
- 缺失素材、悬空 ID、错误 checksum 和未来格式版本。

自定义 Skill 会影响模型行为。导入界面必须展示 capability 警告，不能把第三方 Skill
当作普通图片素材。

## 10. 安装与版本

```text
内置模组：<project>/mod/<legacy-name>/
用户模组：<runtime>/modules/<package-id>/<version>/
```

用户模组的运行时 key 是 `<id>@<version>`。同一 ID 的多个版本可以并存；相同版本、
不同内容不会被覆盖，作者必须提升版本号。相同 SHA-256 的包重复导入是幂等操作。

安装目录保留包内 `manifest.json`、`module.json`、`lorebook.json` 与作者文件的原始字节，
只额外生成 `module.md`、`world_state_initial.json`、`install.json`，并在缺少主题时生成
默认 `theme.json`。因此包内 checksum 的含义不会在安装后改变。

世界元数据保存 `module_name`、`module_id` 和 `module_version`，因此旧存档不会自动切换
到新版本。

## 11. 命令行工具

```bash
# 无副作用编译预览：向 stdout 输出诊断、trace 和编译产物
venv/bin/python tools/module_packager.py compile examples/module-template

# 显式写出运行时文件与 compilation-report.json
venv/bin/python tools/module_packager.py compile examples/module-template \
  --output /tmp/whispering-archive-compiled

# 生成包
venv/bin/python tools/module_packager.py pack \
  examples/module-template dist/whispering-archive.trpgmod

# 检查已有包
venv/bin/python tools/module_packager.py validate \
  dist/whispering-archive.trpgmod

# v1 工程迁移为 v2（见 5.7）
venv/bin/python tools/module_packager.py migrate-v2 <v1工程目录> <新的v2目录>

# 重新生成编辑器共享 Schema
venv/bin/python tools/module_packager.py schema schemas/trpgmod
```

`pack` 会重新计算包内所有文件的 checksum；编辑工程中旧的 `checksums` 不参与构建
校验。源码工程中的符号链接、超量文件和不可移植路径同样会在输出包落盘前被拒绝。

旧 `module.md` 仍可由 `tools/module_loader.py` 读取，但它属于有损兼容入口。新模组和
编辑器不能把 Markdown 正则解析结果作为结构化数据来源。

## 12. 版本与兼容策略

- `format_version` 控制包结构，不等同于世界状态的 schema 版本。`1.0` 与 `2.0` 包均可
  导入；v2 新增的 `progression` 与 `fallback` 在 v1 包中不需要也不生效。
- 模组 `version` 控制作者内容版本。
- 不兼容的包结构升级使用新的格式主版本和迁移器。
- 标准字段新增应保持向后兼容，并提供默认值。
- 删除或改名已发布实体 ID 属于存档不兼容变更，应提升模组主版本。

## 13. 写作建议：回合选项与时间线

模组负责声明场景、出口、发现规则和可供守秘人使用的剧情事实；存档与时间线由引擎
实现，模组不参与其中。引擎会持久化每个回合末尾的结构化选项；玩家在行动结果上创建
时间线分支时，引擎恢复该回合之前的世界快照、消息和选项，玩家从那里提交新行动。

因此对模组写作的实际建议：

- 把真正可选的后续行动放入回合末尾的结构化 choices，不把必要分支只藏在散文中；
- 保证关键推进不是单一路线的随机成功专属结果——为失败或错过人物提供替代入口
  （v2 的 `fallback` 就是为此设计的）；
- 使用稳定实体 ID、flags 和发现规则表达状态，不在 keeper 文本中维护不可恢复的
  隐藏状态；
- 不依赖前端界面细节（消息序号、按钮位置）或世界实例标识，这些均属于引擎能力。

时间线分支不会执行模组代码，也不会重新掷骰；只有玩家从恢复的决策点提交新行动后，
才产生新的确定性结算和叙事回合。
