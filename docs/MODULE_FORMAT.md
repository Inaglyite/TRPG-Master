# TRPG Master 模组格式 v1

本文定义可导入、可验证、可被未来模组编辑器无损读写的 `.trpgmod` 格式。

- 格式版本：`1.0`
- 交换容器：ZIP，扩展名 `.trpgmod`
- 结构化数据：UTF-8 JSON
- 长篇正文：UTF-8 Markdown
- JSON Schema：Draft 2020-12

## 1. 设计边界

模组包含两类数据，不能混为一份运行状态：

| 数据 | 所有者 | 是否随游戏改变 |
|---|---|---|
| NPC、场景、全部线索、结局和规则定义 | 模组作者 | 否 |
| 当前场景、已发现线索、HP、SAN、战斗和标志 | 运行世界 | 是 |

`.trpgmod` 保存作者态定义。安装时，编译器生成当前引擎使用的：

- `module.md`：结构化定义与 `keeper.md` 合成的守秘人提示。
- `world_state_initial.json`：新游戏只读模板。

玩家真正的世界状态仍保存在 `worlds/<world_id>/`，不会写回模组包。

## 2. 包目录

包根目录直接包含 `manifest.json`，不能再套一层文件夹。

```text
example.trpgmod
├── manifest.json       必需：包信息、版本与能力声明
├── module.json         必需：权威结构化模组定义
├── keeper.md           可选：守秘人长篇正文
├── theme.json          可选：游戏主题
├── lorebook.json       可选：按回合检索的 Lorebook v3
├── assets/             可选：图片与音频素材
├── skills/             可选：模组专属 Skill
├── characters/         可选：模组调查员 JSON
└── scenes/             可选：场景补充 Markdown
```

包内不得包含 `world_state.json`、玩家存档、API Key、Python/JavaScript、动态库或可执行文件。

## 3. manifest.json

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
| `$schema` | 否 | 固定指向 v1 manifest Schema |
| `format_version` | 是 | 当前只支持 `1.0` |
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
| `min_engine_version` | 否 | 最低兼容程序版本；导入时强制检查 |
| `entry` | 是 | v1 固定为 `module.json` |
| `keeper_document` | 否 | v1 固定为 `keeper.md`，无正文时设为 `null` |
| `theme` | 否 | v1 固定为 `theme.json`，无主题时设为 `null` |
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

当前引擎兼容版本为 `1.0.0`。`min_engine_version` 高于它的包会返回
`engine_too_old`，不会进入安装目录。

## 4. module.json

顶层结构：

```json
{
  "$schema": "https://trpg-master.local/schemas/module-v1.json",
  "format_version": "1.0",
  "entry_scene_id": "archive_study",
  "opening_prompt": "调查员来到档案馆。",
  "npcs": {},
  "scenes": {},
  "clues": {},
  "endings": {},
  "rules": {},
  "assets": { "npcs": {}, "scenes": {}, "clues": {} },
  "initial_state": {},
  "clue_links": []
}
```

### 4.1 ID

NPC、场景、线索和结局均使用稳定 ID 作为对象键：

```text
^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$
```

显示名称可以修改，ID 一旦发布不应改变。场景出口、NPC 位置、素材与线索关联都通过 ID 引用。

### 4.2 NPC

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

### 4.3 场景

```json
{
  "archive_study": {
    "name": "档案馆书房",
    "description": "雨水拍打窄窗。",
    "exits": ["archive_courtyard"],
    "npcs_present": ["archivist_lin"],
    "document": "scenes/archive-study.md",
    "asset_id": "archive_study_image"
  }
}
```

`entry_scene_id`、出口和在场 NPC 都必须存在。`document` 只能指向 `scenes/` 下的 Markdown。

### 4.4 线索

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

分类固定为：

- `investigation`：现场、文档和调查证据。
- `event`：已经发生的重要事件。
- `task`：委托、目标和待办事项。
- `npc`：人物相关信息。

所有线索保存在 `clue_catalog`，只有 `initially_known: true` 或列入
`initial_state.known_clue_ids` 的线索才进入开局 `clues_found`。
`granted_item` 可选，用于“发现线索时同时获得同名实物”；运行时会幂等加入背包，旧存档已有
线索但缺少该物品时也会补齐。

`discovery_rules` 是运行时可靠触发契约。每条规则包含：

- `intent`：`examine`、`search`、`read`、`take`、`talk`、`enter` 或 `use`。
- `targets`：玩家话语中可识别的目标别名，至少一个；不接受正则或可执行代码。
- `skill`：可选的技能 ID；`requires_success: true` 时必填。
- `requires_success`：为真时，只有本轮指定技能成功才发放线索。
- `sanity_severity`：可选的 `minor`、`moderate` 或 `major`，命中后在叙事前结算 SAN。
- `npc_reveals`：同一发现中原子提交的人物揭示，包含 `npc_id`、`tier` 和 `entry_text`。

规则仅匹配线索 `source` / `related_scenes` 对应的当前场景，否定句、询问能否行动、已发现线索
不会触发。命中后引擎在模型生成正文前提交线索、素材、SAN、人物揭示和 `flag_effects`，避免
长工具链与延迟分发。`discovery_notes` 仍用于作者说明和编辑器提示，不承担运行时语义。

`flag_effects` 的键必须预先存在于 `initial_state.flags`；发现或重新对账该线索时会幂等应用。

### 4.5 结局

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

`ending_type` 支持 `good`、`neutral`、`bad`、`secret`。`required_flags` 是运行时结局硬门槛；
`end_game` 会按作者态定义校验当前 flags，缺少任一条件时拒绝结算。

### 4.6 素材

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
            "match_all": ["纸片", "纤维"]
          }
        ]
      }
    }
  }
}
```

NPC、场景或线索填写 `asset_id` 后，编译器会自动生成精确触发：人物首次揭示、进入场景或按
稳定 `clue_id` 发现线索时分发对应素材。`state_add_clue` 命中 `clue_catalog` 时应传 `clue_id`；
模型是否记得调用 `show_handout` 不再是素材能否出现的前提。

`reveal_on` 是可选的声明式兜底，适合兼容自由文本线索或确实需要从理智事件触发的素材：

| `event` | 触发源 | 推荐条件 |
|---|---|---|
| `npc_revealed` | NPC 首次揭示 | `entity_id` |
| `scene_entered` | 当前场景切换 | `entity_id` |
| `clue_discovered` | 新线索写入状态 | `entity_id` 或文本条件 |
| `sanity_triggered` | 守秘人确认玩家目击冲击场面 | `match_all` / `match_any` |

`match_all` 中的词必须全部出现，`match_any` 至少出现一个；同时填写时两组条件都要满足。规则
只接受上述白名单事件、实体 ID 和文本条件，不执行脚本。运行时按素材 ID 持久化首次展示状态，
因此自动触发不会重复刷图；图片线索也会写回 `clues_found[].asset`，读档后仍可查看。展示一个
能映射到 `clue_catalog` 的线索素材时，引擎会确保该线索同时进入清单。

允许的素材扩展名：

```text
.png .jpg .jpeg .webp .gif .avif .mp3 .ogg .wav
```

当前游戏 UI 主要消费图片；音频类型为后续编辑器和播放功能保留。

### 4.7 初始状态

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

实际玩家调查员在开局时由角色选择覆盖。模组作者不能通过 PC 模板指定玩家身份。
`granted_items` 在角色覆盖后合并进调查员背包，适合模组必需的开场钥匙或委托物；重复名称只加入一次。

### 4.8 extensions

NPC、场景、线索、PC、初始状态和模组顶层都可使用 `extensions` 保存编辑器插件数据。
编译器会把这些字段合并进对应运行时对象，但扩展字段不能覆盖标准字段。

### 4.9 lorebook.json

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

标准字段 `keys`、`constant`、`selective`、`secondary_keys`、`case_sensitive`、`priority`、`insertion_order`、`scan_depth` 和 `token_budget` 参与本地检索。当前版本会无损保留但不执行 `use_regex:true` 与 `recursive_scanning:true`，导入预览会给出 warning；这样可以避免不受信任的正则造成回合阻塞，也避免条目内容互相激活。

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

场景、NPC、线索和 flag 引用会在编译/导入阶段校验。玩家输入中的关键词只负责激活已经满足信息门槛的条目，不能解锁 `gated` 内容。运行时不发起额外模型请求、不生成 embedding，也不把检索素材加入常驻 system prompt；它在当前回合权威状态之后追加一个有 token 上限的私有素材块。

## 5. keeper.md

`keeper.md` 保存不适合结构化表单的内容，例如：

- 开场节奏和信息边界。
- NPC 扮演建议。
- 失败推进与压力升级。
- 模组作者给守秘人的长篇说明。

它不是结构化事实的第二份副本。NPC HP、位置、线索关系和素材关联应只写在 `module.json`。

## 6. 校验阶段

导入器按以下顺序拒绝问题包：

1. ZIP 路径与体积安全检查。
2. `manifest.json` Pydantic/JSON Schema 与最低引擎版本校验。
3. `module.json` 结构校验。
4. NPC、场景、出口、线索、Lorebook 和素材交叉引用校验。
5. capability 与目录一致性校验。
6. UTF-8、JSON 与 SHA-256 校验。
7. 编译运行时模板并原子安装。

错误响应包含稳定的 `error_code`、面向用户的 `error` 和可定位字段的 `details`。
未被实体引用且没有 `reveal_on` 的素材不会阻止导入，但编译器会产生
`asset_without_reveal_path` warning，编辑器应在发布前明确展示。

### 6.1 编译器契约

`src/module_compiler.py` 是游戏安装器、HTTP 预览和 CLI 共同使用的权威编译入口：

```text
manifest.json + module.json + keeper.md + lorebook.json（可选校验输入）
  -> CompilationResult
     ├── world_state
     ├── keeper_prompt
     ├── diagnostics[]
     └── trace[]
```

`diagnostics` 的 `level` 分为 `error`、`warning`、`advice`。每项包含稳定 `code`、阶段
`phase`、作者态字段 `path` 和说明 `message`；只有 `error` 阻止安装。`trace` 列出
`source_path -> output_path` 与转换动作，供编辑器解释“这个运行时值从哪里来”。

编译器本身不读写文件、不安装模组、不创建世界。包路径、素材是否存在、checksum 和 ZIP 安全
由 `module_registry` 在编译前检查；落盘也只发生在注册表安装或 CLI 明确指定 `--output` 时。

## 7. 安全限制

| 限制 | v1 值 |
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

自定义 Skill 会影响模型行为。导入界面必须展示 capability 警告，不能把第三方 Skill 当作普通图片素材。

## 8. 安装与版本

```text
内置模组：<project>/mod/<legacy-name>/
用户模组：<runtime>/modules/<package-id>/<version>/
```

用户模组的运行时 key 是 `<id>@<version>`。同一 ID 的多个版本可以并存；相同版本、不同内容
不会被覆盖，作者必须提升版本号。相同 SHA-256 的包重复导入是幂等操作。

安装目录保留包内 `manifest.json`、`module.json`、`lorebook.json` 与作者文件的原始字节，只额外生成
`module.md`、`world_state_initial.json`、`install.json`，并在缺少主题时生成默认 `theme.json`。
因此包内 checksum 的含义不会在安装后改变。

世界元数据保存 `module_name`、`module_id` 和 `module_version`，因此旧存档不会自动切换到新版本。

## 9. 命令行

仓库包含完整示例工程：[module-template](../examples/module-template/manifest.json)。

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

# 重新生成编辑器共享 Schema
venv/bin/python tools/module_packager.py schema schemas/trpgmod
```

`pack` 会重新计算包内所有文件的 checksum；编辑工程中旧的 `checksums` 不参与构建校验。
源码工程中的符号链接、超量文件和不可移植路径同样会在输出包落盘前被拒绝。

旧 `module.md` 仍可由 `tools/module_loader.py` 读取，但它属于有损兼容入口。新模组和编辑器不能
把 Markdown 正则解析结果作为权威数据。

## 10. 兼容策略

- `format_version` 控制包结构，不等同于世界状态 `schema_version`。
- 模组 `version` 控制作者内容版本。
- 不兼容的包结构升级使用新的格式主版本和迁移器。
- 标准字段新增应保持向后兼容，并提供默认值。
- 删除或改名已发布实体 ID 属于存档不兼容变更，应提升模组主版本。
