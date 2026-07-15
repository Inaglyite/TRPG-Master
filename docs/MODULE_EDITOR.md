# 模组编辑器需求与技术规划

本文定义 TRPG Master 模组编辑器的产品目标、MVP 范围、技术架构和验收标准。编辑器以
[模组格式 v1](MODULE_FORMAT.md) 为唯一交换契约，不维护第二套私有数据格式。

## 1. 产品目标

编辑器帮助没有编程经验的模组作者完成：

1. 创建模组工程。
2. 编辑 NPC、场景、线索、结局、初始状态与素材关联。
3. 在保存时获得可定位的结构和引用错误。
4. 预览开局世界、守秘人上下文和玩家可见信息。
5. 导出、导入并本地试玩 `.trpgmod`。

编辑器不是文字处理器、图片生成器、在线模组市场或多人协作文档平台。

## 2. 目标用户

| 用户 | 主要需求 |
|---|---|
| 初次创作者 | 表单、模板、默认值和清晰错误，不需要理解 JSON |
| 熟练模组作者 | 快速批量编辑、Markdown、复制实体、稳定 ID |
| 技术作者 | JSON 源码查看、扩展字段、Schema 与 CLI |
| 测试玩家 | 一键导出安装、开局检查和问题报告 |

## 3. 核心工作流

### 3.1 新建

```text
选择模板
  → 填写名称/ID/版本
  → 创建入口场景
  → 进入内容工作台
```

ID 创建后默认锁定。修改 ID 必须显示受影响引用，并作为显式重构操作执行。

### 3.2 内容编辑

```text
左侧实体树        中央编辑区             右侧检查器
模组              表单 / Markdown        引用、错误、素材
NPC
场景
线索
结局
素材
```

编辑器是工作型界面，应使用紧凑工具栏、树、表格、属性面板和标签页，不沿用游戏开始页的大幅
羊皮纸或装饰按钮。

### 3.3 校验与导出

```text
实时字段校验
  → 工程级交叉引用校验
  → 玩家/守秘人信息边界预览
  → 生成 .trpgmod
  → 安装并试玩
```

## 4. MVP 功能

### 4.1 工程

- 新建、打开、另存为模组工程目录。
- 自动保存草稿，崩溃后恢复未提交修改。
- 最近工程列表。
- 显示 `format_version`、模组版本和脏状态。
- 导入现有 `.trpgmod` 为可编辑工程。
- 旧 `module.md` 迁移向导，明确展示无法无损迁移的字段。

### 4.2 实体编辑器

- 模组元数据与 capability。
- NPC 列表、属性、技能、秘密、位置与肖像。
- 场景列表、出口、在场 NPC、描述和场景文档。
- 线索分类、层级、声明式发现规则、发现说明、人物/场景/素材关联与旗标效果。
- 结局触发条件和类型。
- Flags、案件时钟、初始已知线索。
- `keeper.md` Markdown 编辑与预览。
- `theme.json` 基础色彩、字体和开始页文案。
- `lorebook.json` 条目、关键词、场景/NPC/线索门槛、优先级、分组与冷却编辑。

### 4.3 资产

- 拖入图片并复制到 `assets/`。
- 缩略图、文件大小、尺寸、格式和未使用状态。
- 重命名素材 ID 时更新引用。
- 为共享素材编辑带稳定 `entity_id` 的 `reveal_on` 别名；不要生成按正文关键词发图的规则。
- 检测缺失、重复、未引用以及没有任何分发路径的素材。
- v1 不做图片裁剪和 AI 生成；只提供外部编辑后重新载入。

### 4.4 图关系

- 场景出口图：节点为场景，边为可移动关系。
- 线索关系图：节点为线索，边为 `clue_links`。
- 图视图用于导航与检查，不成为第三份数据源。
- 拖动节点只修改编辑器布局；新增/删除边才修改模组定义。

### 4.5 预览与试玩

- 玩家可见开场：只显示初始场景和已知线索。
- 守秘人预览：显示完整 NPC 秘密、线索目录和 `keeper.md`。
- 发现规则模拟器：输入玩家行动与当前场景，显示命中的线索、技能门槛、SAN、NPC、素材和旗标效果，但不修改工程或试玩状态。
- 编译预览：查看生成的 `world_state_initial.json` 与 `module.md`。
- Lorebook 命中模拟器：输入场景、已知线索、flags 和最近消息，显示本轮条目与 token 预算，不调用模型。
- 一键“导出、安装并开始新世界”。
- 试玩世界与编辑工程隔离，重置试玩不能修改工程。

## 5. 校验体验

错误必须包含：

```text
严重度 + 文件/实体 + 字段路径 + 说明 + 可执行修复
```

示例：

```text
错误  scene.archive_study.exits[0]
      场景出口 missing_room 不存在
      [创建场景] [移除引用]
```

校验分级：

| 级别 | 是否阻止导出 | 示例 |
|---|---:|---|
| Error | 是 | 悬空引用、重复 ID、缺失入口场景 |
| Warning | 否 | 无许可证、自定义 Skill、未使用素材 |
| Advice | 否 | NPC 没有动机、隐藏线索缺少 `discovery_rules` |

结构校验使用同一份 JSON Schema；场景出口、素材和信息边界使用 Python 语义校验器。前端校验用于
即时反馈，后端导出校验仍是最终权威。

线索属性面板中的发现规则使用结构化控件：意图下拉框、目标别名列表、前置叙事 `approach_text` 文本框、技能下拉框、成功门槛开关、
SAN 严重度下拉框和 NPC 揭示列表。`requires_success` 没有技能、NPC 引用不存在、旗标效果引用
未声明 flag 时阻止导出。编辑器应提示 `approach_text` 不得写入成功后才能得知的结论；`discovery_notes` 是给作者看的补充说明，不能替代可执行规则。

## 6. 技术选型

编辑器建议作为独立前端入口 `/editor/`，复用 Electron 壳和 FastAPI 后端，不塞进游戏开始页。

| 层 | 选择 | 原因 |
|---|---|---|
| UI | React + TypeScript + Vite | 复杂表单、树和多面板状态比现有轻量游戏 UI 更适合组件化 |
| 状态 | Zustand + command history | 保存工程态，并实现可控撤销/重做 |
| 表单 | React Hook Form | 字段级校验和大型表单性能 |
| Schema | AJV 2020-12 | 直接消费 `schemas/trpgmod/*.schema.json` |
| Markdown | CodeMirror 6 | 文本编辑、搜索、诊断标记和快捷键 |
| 关系图 | `@xyflow/react` | 场景与线索节点图，不自研画布交互 |
| 后端模型 | Pydantic 2 | 与当前导入器共享领域定义和 Schema 生成 |
| 打包 | 现有 Python packager | 统一安全限制、checksum 和编译结果 |

编辑器 UI 不直接访问文件系统。源码模式通过 FastAPI 工程 API；Electron 如需系统文件夹选择，使用
窄权限 preload/IPC，只暴露 `openProjectDirectory`、`chooseExportPath` 等明确方法，不开启
`nodeIntegration`。

## 7. 前端状态模型

```text
EditorSession
├── projectPath
├── manifest
├── module
├── keeperDocument
├── theme
├── assetIndex
├── diagnostics
├── selection
├── dirtyRevision
└── commandHistory
```

所有写操作封装为命令：

```text
addEntity
updateField
renameEntityId
deleteEntity
attachAsset
connectScenes
```

撤销/重做保存命令前后的最小补丁。自动保存不清空撤销栈，成功导出才建立发布检查点。

## 8. 后端模块划分

建议新增：

| 模块 | 职责 |
|---|---|
| `src/module_workspace.py` | 工程打开、保存、自动恢复和路径权限 |
| `src/module_preview.py` | 玩家视角与守秘人视角的脱敏预览 |
| `frontend/editor/` | 独立编辑器应用 |

已经具备并应直接复用：

- `src/module_format.py`：领域模型、交叉引用校验和 JSON Schema。
- `src/lorebook.py`：Lorebook v3 兼容模型、引用校验和确定性检索。
- `src/module_compiler.py`：权威无副作用编译、运行时产物和字段来源追踪。
- `src/module_diagnostics.py`：带级别、阶段、错误码与字段路径的结构化诊断。
- `src/module_registry.py`：包检查、安装和模组发现。
- `tools/module_packager.py`：compile、Schema、校验与打包 CLI。
- `/api/modules/compile`、`/inspect`、`/import` 和 `/schema/*`。

编辑器首版不应复制一份 TypeScript 编译器。AJV 用于输入时的快速结构反馈，保存/预览时把内存中的
`manifest`、`module`、`keeperDocument` 与可选 `lorebook` 发送到 `/api/modules/compile`，以 Python 返回的
`CompilationResult` 为权威。诊断 `path` 可直接关联表单字段，`trace` 可用于编译结果检查器。

## 9. 工程 API 草案

| 方法 | 路径 | 作用 |
|---|---|---|
| `POST` | `/api/editor/projects` | 新建工程 |
| `POST` | `/api/editor/projects/open` | 打开允许目录内的工程 |
| `GET` | `/api/editor/projects/{session}` | 读取完整编辑会话 |
| `PATCH` | `/api/editor/projects/{session}` | 带 revision 保存变更 |
| `POST` | `/api/editor/projects/{session}/validate` | 完整语义校验 |
| `POST` | `/api/editor/projects/{session}/preview` | 编译预览 |
| `POST` | `/api/editor/projects/{session}/export` | 导出 `.trpgmod` |
| `POST` | `/api/editor/projects/{session}/playtest` | 安装并创建隔离试玩世界 |

工程保存请求携带 `expected_revision`，避免两个编辑器窗口静默覆盖。
其中项目级 preview 应委托现有 `/api/modules/compile`，只额外负责从 session 读取工程数据和生成
玩家/守秘人视角，不再实现另一套运行时转换。

## 10. 安全与信息边界

- 编辑器只能读写用户明确打开的工程目录。
- 资产导入先复制到工程，不能保存任意绝对路径引用。
- Markdown 预览禁用原始 HTML、脚本、远程 iframe 和危险 URL。
- 第三方 `.trpgmod` 先走现有包安全检查，再解包为工程。
- Skill 编辑页持续显示“会进入模型上下文”的风险标记。
- 玩家预览不得包含 NPC `secret`、未发现线索、`discovery_rules` 和 `discovery_notes`。
- 发布前显示作者、来源、许可证和素材授权检查项。

## 11. 非功能要求

- 100 个 NPC、200 个场景、500 条线索时，普通字段编辑反馈低于 100ms。
- 自动保存不得阻塞输入，失败时保留内存草稿并明确提示。
- Windows、Linux、Electron 和浏览器编辑器共享相同 Schema 与导出结果。
- 同一工程连续导出且内容不变时，生成包字节一致。
- 所有破坏性操作可撤销，关闭脏工程必须确认。
- 键盘导航、焦点可见、表单标签和错误关联满足基本无障碍要求。

## 12. 开发阶段

### E0：编辑器内核

- 工程模型、API、revision 和自动保存。
- Schema/语义诊断聚合。
- 打开示例工程并无损保存。

### E1：表单工作台

- 元数据、NPC、场景、线索、结局和初始状态表单。
- 实体树、搜索、复制、删除、撤销与重做。
- Markdown 编辑器。

### E2：素材与关系图

- 资产库、引用检查和缩略图。
- 场景图和线索图。
- 主题预览。

### E3：导出与试玩

- 玩家/守秘人/编译预览。
- 导出、安装和隔离试玩。
- 旧 Markdown 迁移报告。

### E4：体验完善

- 大型模组性能测试。
- 快速修复、发布检查单和错误报告导出。
- Windows 安装包与崩溃恢复验收。

## 13. MVP 验收

编辑器 MVP 完成必须同时满足：

1. 非技术用户可从空模板创建两场景、一 NPC、一线索模组。
2. 删除被引用场景时会被阻止或显式级联，不产生悬空引用。
3. 玩家预览看不到未发现线索和 NPC 秘密。
4. 关闭并重开工程后内容、ID、素材引用和撤销检查点一致。
5. 导出的包通过 CLI、后端和游戏内预检。
6. 一键安装后能选择调查员并完成首轮叙述。
7. 相同版本不能覆盖已有世界依赖的模组内容。
8. 恶意路径、脚本、超限文件和损坏 JSON 不会写入用户模块目录。
