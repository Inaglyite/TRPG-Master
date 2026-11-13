# 子场景与声明式调查契约（Spatial v1）

2026-11-13，开发分支 `feat/spatial-investigation`。本体实现；编辑器由 Kimi 负责。
本次按用户要求只运行最小确定性专项，不逐个功能跑全量、不调用付费模型或发布生产。

## 目标与边界

常规模组可以不写自定义 Skill，通过场景、连接、调查点、物品状态和有限条件/效果描述玩法。引擎负责条件和结算，模型负责表达。现有 v1/v2 平面模组不自动改变行为；新能力为 v2 中可选 `spatial`，并显式声明 `spatial_investigation` capability、`min_engine_version: 1.1.0`。

原始《无声编钟》安装版本只读研究，不覆盖其包、玩家世界或存档。小型示例抽取寓所/书房/壁炉暗室、血谱和失败代价；充能钥匙是独立机制示范，不冒充原作已有剧情。完整咒术、派系调度和所有旧 Skill 自动转换不在第一批范围。

## 职责与历史依据

原模组有 13 场景、16 NPC、25 线索、5 结局，两份 Skill 共 985 行。适合转成数据的例子：

- blood_score：位于寓所书房钢琴，查看信息与拿走残页应分开；图片随实际授权发放。
- dalu_bell：壁炉/暗室侦查，失败仍可发现但须实际支付 human_pressure +1。
- inscription_translation：原先“先有编钟或血谱，keeper 把关”仅写在 discovery_notes；应表达为物品/知识条件。
- 守拙道人曲谱依赖 inscription_translated；终局多个条件不能只交给提示词判断。

历史修复须继承：05af455e/65ecc350 菜单契约；2f4e93ee/803ebaf2 人物身份与归属；67e006cf 抵达只旅行、调查后置；86adfd13 前置条件与否定输入；fa1b4588 图片不进入模型上下文；f43b71ed 时间型时钟不得重复推进。它们说明声明、尝试、结算与叙述必须分层。

## 作者数据

唯一正本为 `src/modules/spatial_format.py` 及生成的 module-v2 schema，不手写另一份编辑器格式。

```text
module.spatial
  version: 1
  nodes: {scene_id: {kind, parent_scene_id?, entry_scene_id?, hidden}}
  connections: {connection_id: {source_scene_id, target_scene_id,
      bidirectional, visible_when, requires, remember_unlock, costs}}
  points: {point_id: {scene_id, name, description, visible_when,
      actions: {action_id: {intent, aliases, requires, check?, repeat, success, failure}}}}
  items: {definition_id: {label, states: {key: {initial, minimum, maximum}}}}
```

实体名称/描述仍来自 module.scenes/npcs/clues。nodes 只补拓扑，不建立第二份名称。container 不作为最终位置，必须声明可到达的入口叶场景；父链无环且深度有限。parent 关系不自动开放所有子房间，不继承在场 NPC。未显式覆盖的旧 exits 可编译为开放连接；同一有向边存在显式连接时，其条件优先，禁止旧出口绕过。

隐藏节点/连接不因“属于同一住宅”或“以前到过”自动公开。连接的 visible_when 控制可知性，requires 控制通行。remember_unlock=true 时首次通过后保存开锁状态，后续不重复收取开锁代价；其他连接每次通过重新核验。反向共用同一连接的解锁状态。

### 条件

op 为受限枚举：always；all/any（conditions）；not（condition）；flag（flag_id/value，严格类型相等）；item（item_id/quantity/minimum_states）；clue（clue_id，当前调查员已知）；clock（clock_id/minimum）；completed（point_id/action_id）。无任意 JSON 路径或可执行脚本。空组/超深表达式、悬空 ID、未知状态和冲突定义在编译期拒绝。

item_id 是作者定义 ID，不是库存实例 ID。判断实际 holder 和数量，不能用“曾经取得”的旗标替代；同一次状态门槛与扣除须使用同一件符合要求的物品。数值状态按作者范围校验，不能溢出、变成负数或默默免费钳制。

### 效果

kind 为有限操作：grant_clue（仅信息）；give_item；adjust_item_state；set_flag；reveal_scene；unlock_connection；advance_clock。连接 costs 仅允许 adjust_item_state。线索信息与物品获得分开，点击查看不能自动拿走原件。主线失败保底必须先结算真实代价；事务任一效果失败整批不落账。

调查动作 success/failure 是已结算结果分支，不接受客户端或模型自行声明成功。check 指定技能和难度；结构化执行引用服务器 CheckRequest 与当前时间线的 check_resolved 回执，经典执行引用引擎记录的匹配检定。检定目标固定为 `scene_object` 的 `point_id:action_id`（经典 target_id 同值）；比较调查员、场景、技能、难度并禁止重复消费。repeat=false 时一次已结算尝试即记录完成；失败后的其他路径由作者另设动作。

## 运行时

- 世界根保持现行 schema；可选领域对象 `spatial_definition`（作者态编译结果）、`spatial_state`（version 1、已揭示地点、连接解锁、动作/检定回执）。
- **唯一位置仍为 current_scene.id，且是叶场景**；祖先路径派生，不另存可变大场景位置。顶栏名称可组合为“寓所 / 书房”。
- 库存只以现有 item_registry 为权威；作者 definition_id 与 states 附加在实例上，转移/拆分保留它们。经典背包写入需要同步，不能让丢掉钥匙后注册表仍持有。
- 移动在真正写入前统一校验当前连接；自然语言、地图、move_party 和经典状态路径不得绕过。读档/分支是恢复，不重新收费或解释成步行。
- 受保护线索由调查点动作授权后发放；模型直接 state_add_clue/grant_clue 不能先跳过条件。已知信息分享沿原有授权语义，不额外生成实物。
- 部分条件/代价不满足返回明确错误码和有限公开说明，不向玩家返回隐藏规则、暗室 ID 或秘密文本。主持可查看条件诊断。
- 新命令仍使用原 Principal、revision、command_id 幂等与事务/outbox。失败零写入；断线重试不重复充能、扣除或授予。

## 接口与编译

结构化新增 `interact_point` 命令：payload 为 investigator_id、point_id、action_id，可选 check_request_id。沿既有请求/命令链，不允许玩家绕过主持直接修改世界。

经典新增 `spatial_inspect` / `spatial_interact` 工具：后者只选择作者动作和真实 check_id，不接收任意效果或自报骰点。开场编译自动附带工具说明、当前场景边界和私有规则说明；无需作者再写同一套 Skill。

快照中沿现有 scene_object 目标投影可见调查点，主持视图可取动作/检定配置；公共视图不得包含 success/failure 中的未发现线索。新增命令与协议字段在 schema 中可检验。生成 schema 仍用 module_packager schema；编辑器按固定分支/提交同步，schema-lock 记录来源。

## 最小验收

仅跑一个确定性空间机制专项文件（需要时加直接受影响的旧兼容用例）、Ruff 目标文件和架构门禁；不逐项全量、不实跑付费模型。一个端到端领域夹具覆盖：父子循环拒绝、容器入口、钥匙缺失/未充能拒绝、充能开门仅扣一次、转交后失去权限、隐藏房间不泄漏、异室调查拒绝、真实检定失败代价、旧出口绕过拒绝、重复命令及读档状态恢复、无 spatial 的旧模组保持原样。

## 给 Kimi 的协作约定

本体目录 `/home/inaglyite/MyProjects/trpg-master-spatial`，分支 `feat/spatial-investigation`；原 master 工作区不动。编辑器已有 `feat/editor-authoring-workbench` 及未提交工作，Kimi 自行在新功能分支继续，勿由本体任务替其切换、覆盖或提交。

优先完成节点树、连接条件表单、调查点动作、物品状态四个编辑面；条件用 all/any/not 树与下拉选择，效果用有限列表，避免要求作者写表达式代码。公开描述与主持秘密分开。编辑器保存/导出往返保留所有 spatial 字段，稳定 ID 重命名须更新引用；不把生成文本写回当作规则正本。

本体提供 examples/spatial-investigation、schema 与专项测试作为双方联调依据。UI 不自行推测效果已生效：条件评估、检定和提交结果均由本体返回。后续派系/日程/完整魔法另开版本契约，不塞进当前已知字段。

## 实施细节与本批局限

- 通路两端若为 container，统一解析为其入口叶场景；解析后重复/自连在编译期拒绝。到访地图沿原有组件展示叶场景路径，不公开未到访房间。
- 调查动作记录真实检定的消费清单；repeat=true 也不能重用更早已消费的骰点。classic 的原始 state_set 不得直接改写空间进度、登记物品状态、作者动作拥有的旗标/代价时钟或受保护线索记录。
- 有状态物品每个实例 quantity=1，避免拆分时复制电量；旧背包有同名多件状态物品须由作者/迁移明确拆成实例，不能猜测共用状态。
- advance_clock 的时钟必须有 max；自动 clue_clarity 和带 time_advance 的时钟不可同时由空间效果推进。失败代价不能支付时整批拒绝，正式模组需设计替代路径。
- 主线 essential_clue_ids 支持调查点；至少有一条无检定成功路径，或同一检定动作的成功/失败都授予该关键线索。静态可达性仅检查拓扑，不证明任意条件组合可通关。
- 本批提供后端结算、既有目标投影和主持通用命令表单，不增加专用的调查点面板；编辑器另由 Kimi 实现。未进行真实模型付费实玩，模型自由文本的语义正确性不能由有限条件校验完全保证。

编辑器同步入口见 [SPATIAL_EDITOR_HANDOFF.md](SPATIAL_EDITOR_HANDOFF.md)。
