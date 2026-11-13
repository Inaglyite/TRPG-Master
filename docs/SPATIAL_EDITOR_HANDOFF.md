# Spatial v1：编辑器协作交接

2026-11-13。本体工作区 `../trpg-master-spatial`，分支 `feat/spatial-investigation`；本体代码与编辑器各自提交。Kimi 负责编辑器，本体不修改 `../TRPG_Mod_Editor` 的分支或未提交工作。

## 对接正本

- [开发契约](SPATIAL_INVESTIGATION.md)：运行语义、边界和历史 bug 依据。
- [模型定义](../src/modules/spatial_format.py)：类型、默认值、枚举和额外字段拒绝规则。
- [module-v2 schema](../schemas/trpgmod/module-v2.schema.json)：沿用格式 2.0，新增可选 spatial；没有单独的格式 3。
- [可打包示例](SPATIAL_EXAMPLE.md)：无需自定义 Skill 的房间、钥匙和检定链。
- [确定性专项](../tests/test_spatial_investigation.py)：本体结算的最小行为依据。

同步时记录本体分支与提交 SHA，并把实际 schema 文件 SHA256 写入编辑器既有 schema-lock。不要用现有发布版的 schema 覆盖这个新分支；本体的模块兼容版本为 1.1.0，这不代表已发布相同版本安装包。

## 编辑器应实现的面板

| 编辑面 | 保存字段 | 编辑与校验要求 |
| --- | --- | --- |
| 场景树 | spatial.nodes | 场景名仍来自 scenes；container 选入口叶场景，location 选父场景；禁止循环，最多 8 层；父场景不代表所有子房间已到访 |
| 通路 | spatial.connections | 起终点、双向、可见条件、通行条件、记住开锁、状态消耗；容器端点解析到入口，解析后不得重复或自连 |
| 调查点 | spatial.points | 只挂在叶场景；公开名称/描述、可见条件；动作明确区分看、读、搜索、拿取、使用等 |
| 调查动作 | points.*.actions | 前置条件、可选检定、是否可重复、成功/失败效果；关键线索需失败保底或无检定替代动作 |
| 物品定义 | spatial.items | 稳定 ID、唯一 label、数值状态初值/上下界；有状态的实例逐件登记，不能将多把钥匙共用一份 charge |

条件用 all/any/not 组合树，叶节点用下拉选择 ID 和值；效果用受限操作列表。不得生成任意脚本、自由 JSONPath 或客户端计算结果。`minimum_states` 判断当前持有实例，不是“曾经获得”的旗标。连接 costs 只允许负数的 adjust_item_state；同次检查和扣除绑定同一实例。

## 保存、导出与兼容

1. 新功能只在 v2 显式启用；manifest 增加 `spatial_investigation`，`min_engine_version` 至少 `1.1.0`。不添加 custom_skills 作为空间功能的前提。
2. 平面旧工程无 spatial 时原样保存；导入含 spatial 的工程必须完整保留嵌套字段，不能静默丢失。其他未支持的玩法字段沿既有扩展保留策略处理。
3. 重命名或删除实体 ID 时更新/提示 scenes、父链、通路、points、items、conditions、effects、completed、clues、flags 和 clocks 的引用。禁止使用数组顺序代替 ID。
4. 显式通路优先覆盖同一目标的旧 exits；编辑器应提示双重声明，但不能导出一个可绕开门锁的另一条边。
5. 主持秘密与公开描述分别编辑。运行时当前叶场景的可见调查点进入现有 scene_object 目标投影；success/failure、未发现线索和隐藏地点不进入公开目标。
6. 信息 grant_clue、图片授权和 give_item 分开；不要自动给 clue.granted_item，也不要把生成的主持说明写回成为规则正本。

## 运行接口与首轮联调

经典本体：`spatial_inspect` → 必要时 `skill_check(target_id="point:action")` → `spatial_interact(point_id, action_id, check_id?)`。

结构化本体：`request_check(target={kind:"scene_object", id:"point:action"})` → 玩家实际响应 → `interact_point(investigator_id, point_id, action_id, check_request_id?)`；移动仍用 `move_party`，可选 investigator_id 指定开锁者。模型/编辑器不能提交自报 success、roll 或任意 effects。

联调最小用例：导入示例、改一处房间名、往返导出、由本体编译打包；随后走取钥匙→充能→进卧室→搜索壁炉→入隐藏房间。未取钥匙、未充能、人在别室、伪造检定或重复结算都应被拒绝。只做这些专项，不为每个表单跑全量游戏验收。

## 当前不包含

图形化完整剧情求解器、NPC 日程/派系/法术的数据化、所有旧 Skill 自动迁移、旧存档自动补房间，以及专用的人类主持调查点面板均不在本批。本体提供领域命令与现有目标投影；编辑器完成后再验证真实作者工作流。基础规则由引擎执行，不代表模型不会在自由文本中说错话；真正的模型叙述体验尚未做付费实玩验收。
