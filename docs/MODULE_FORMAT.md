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

### 4.5 结局

```json
{
  "manuscript_recovered": {
    "title": "手稿归档",
    "trigger": "找回手稿并封住旧井",
    "description": "低语终于停止。",
    "ending_type": "good"
  }
}
```

`ending_type` 支持 `good`、`neutral`、`bad`、`secret`。

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
    "clues": {}
  }
}
```

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

### 4.8 extensions

NPC、场景、线索、PC、初始状态和模组顶层都可使用 `extensions` 保存编辑器插件数据。
编译器会把这些字段合并进对应运行时对象，但扩展字段不能覆盖标准字段。

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
4. NPC、场景、出口、线索和素材交叉引用校验。
5. capability 与目录一致性校验。
6. UTF-8、JSON 与 SHA-256 校验。
7. 编译运行时模板并原子安装。

错误响应包含稳定的 `error_code`、面向用户的 `error` 和可定位字段的 `details`。

### 6.1 编译器契约

`src/module_compiler.py` 是游戏安装器、HTTP 预览和 CLI 共同使用的权威编译入口：

```text
manifest.json + module.json + keeper.md
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

安装目录保留包内 `manifest.json`、`module.json` 与作者文件的原始字节，只额外生成
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
