# 角色库 + 角色卡导入：交付记录（2026-09-24）

## 范围

玩家开局前可管理自己的角色：浏览/创建/编辑/复制/删除/导入/导出角色卡，
并在开局选角页直接选用。本地模式与云端单人完整支持；多人房间按隐私边界
不下发任何私有角色（与既有 `include_personal=False` 同一类约束）。

## 数据模型

- 新表 `character_library_entries`（`src/storage/database.py`；迁移
  `migrations/versions/20260923_0017_character_library.py`，沿用
  adopt-or-create 契约）：`id`（服务端生成 `chlib_*`）、`owner_user_id`
  （空串 = 本地模式）、`name`、`card_json`、`created_at/updated_at`。
- 库条目与世界的语义：**开局物化为世界内快照**（复用既有
  `characters.character_to_pc` 路径），此后游戏状态变化不回写库；
  编辑/删除库条目不影响任何已开局世界与历史存档。此语义有后端测试锁定。

## 导入格式与校验

- 信封格式 `trpg-character-card` v1（导出即此格式，示例见
  `frontend/public/examples/character-card.example.json`）；裸卡按 v1 解析并告警。
- 校验在服务端（`src/gameplay/character_library.py`）：必填缺失/非法类型/
  非法数值 → 字段级 errors 阻止导入；超出建卡范围但合法的取值只告警；
  推导字段以属性为权重算（不一致逐条告警）；文件中的身份/权限字段
  （id/owner_user_id/world_id/...）剥离并告警；同名允许共存、新条目新 id。
- 大小上限 256 KB。不做 PDF/OCR，不声明兼容第三方格式。导入不依赖模型调用。

## 前后端入口

- 本地开始页主菜单与选角页头部、云端单人「我的冒险」头部与云端选角页头部
  均有「角色库」入口；面板挂 `GameShell` 全局。
- 选角页数据仍走 WS `character_list`：后端 `list_character_options` 新增
  `library_scope` 参数（本地 = `"local"`；云端单人房间 = 用户 id；多人 = None）。
  库变更后面板主动请求重推，并把新建/导入的角色设为待选中——选角页在列表
  刷新后直接选中（本地）或自动认领（云端单人），无需退出重开。
- `docs/reference/API.md` 2.11.1 记录 HTTP 面与卡面格式契约。

## 验证结果

- 后端 `tests/test_character_library.py` 26 项：校验矩阵、CRUD、跨用户不可见、
  同名不覆盖、inspect 不落库、版本/格式拒绝、导出再导入、本地/云端解析归属、
  真实引擎开局物化快照且库条目编辑/删除不回写。
- 回归：`test_characters / test_investigators / test_solo_play_mode /
  test_multiplayer_membership / test_multiplayer_roster_reconcile` 75 项全过；
  打包升级证据（`test_packaged_upgrade_evidence` + `test_electron_packaging`）
  25 项全过（`LATER_TABLES` 已纳入 `character_library_entries`，否则旧桌面库
  会被拒绝接管——该测试真实抓到了漏登记）。
- 全量后端套件：1432 过 / 1 失败。唯一失败
  `test_context_capacity.py::test_plan_defaults_65536_window_and_78_percent_target`
  是**本机环境预存污染**（非本次改动）：`server.py` 导入期把仓库根
  `.env.json` 的 `context_window_tokens=262144` 注入进程环境，任何 import
  server 的既有测试（如 `test_solo_play_mode.py`，未改动）排在前都会同样触发；
  CI 无该文件所以不受影响。后续值得在 conftest 加「每用例恢复 os.environ」
  的 autouse 夹具根治这一类泄漏。
- 前端组件测试：`CharacterLibraryPanel.test.tsx` 8 项（列表/空态/删除确认/
  新建/保存失败保留输入/导入预览/导入失败不落半成品/超大文件拒绝）；
  `StartScreen` 与 `SoloCharacterSelectScreen` 入口与自动选中/认领测试；
  前端全量 782 项全过。
- E2E：`e2e/character-library.spec.ts`（真实后端 + 模型桩，17s 通过）：
  主菜单进角色库 → 导入 → 预览确认 → 列表可见 → 页面重载仍在 → 选角页
  「角色库」分组自动选中 → 以该调查员开局，角色面板显示该角色。
- 按钮/布局验收（ui-button-check）：1280/939/640 三档截图核对主菜单入口、
  选角页头部入口、面板列表/编辑器/导入视图，无挤压、无竖排、无溢出。

## 已知限制 / 未完成项

- 多人房间本轮不含角色库（私有角色不进共享房间列表；手工构造的认领键
  也会被 options 校验拒绝）。后续若允许多人使用库角色，需要按请求用户
  个性化下发并补认领授权测试。
- 编辑器技能表为自由 id + 已知技能 datalist；模组自定义技能以导入告警呈现。
- 跨设备同步、账号合并不在本轮范围。
