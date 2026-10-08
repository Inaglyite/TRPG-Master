# 协议入口

现行入口，更新于 2026-10-08；平台/UI扩展已保存为开发检查点 `2fd6bbeb`（后端/协议）和 `78102269`（前端）；在此基础上继续补物品转交执行校验。字段契约按 schema 和实际 handler 核对，不以历史交接报告替代；不作为已发布声明。

### 物品转交的执行边界

`transfer_item.from/to` 仍为 `{kind: investigator|npc|scene, id}`，没有新增命令或猜测玩家意图。
两端必须是当前世界内真实存在的持有者；主持可安排异地投递，不强制两端同场景。
来源必须确实持有该稳定物品，数量不足或目标不存在不落账。部分转交保留
`source_clue_id` 及扩展元数据，不把原件拆成无来源的普通物品。
调查员背包变化仅向本人和主持投递，其他玩家/旁观者不因转交收到完整库存。
前端NPC/场景候选仍待接线；服务端支持不能当作界面已完整暴露。

## 1. 三类协议不要混用

| 范围 | 正本/详细参考 | 入口 |
|---|---|---|
| 账号、模组、设置、世界等 HTTP；legacy WS 与房间控制 | [HTTP 与 legacy API](reference/API.md)；实际 HTTP OpenAPI 与路由 | `server.py`、`src/auth`、`src/web`、`src/multiplayer` |
| structured_v1 行动、命令、事件 | [JSON Schema](../schemas/structured-play/v1/) + [语义详表](reference/STRUCTURED_PROTOCOL_V1.md) | `src/structured/{gateway,validation,service}.py` |
| 模组作者格式 | [模组格式](MODULE_FORMAT.md) + [模组 schema](../schemas/trpgmod/) | `src/modules/` |

`/ws` 和 `/ws/room` 是传输入口，不直接表示运行模式。以服务端世界元数据与 `server_capabilities` 协商；结构化协议缺失/不支持时明确报错，不静默回退旧文字动作。

## 2. 请求与命令

人类 `keeper_roll` 命令：payload为 `spec`（受限NdM±K，1–10颗、2–100面、
修正值最多±999）和可选 `visibility=keeper|public`（默认keeper）。不需要
investigator_id，不能携带玩家cause_id或技能/伤害字段。查重前重新核验
can_keeper及当前人类控制权，只允许同一主持重放；角色认领变化不影响查重。
辅助草稿/Agent不能调用。结案后仍可普通掷骰，不改游戏状态/版本/资源，不
触发Agent，不结算玩家待检定。玩家原有free_roll_request权限不放宽。
`keeper_roll_resolved` 载荷为command_id/visibility/expression/dice/total/modifier，
按公开/主持授权投递。`session_snapshot.keeper_rolls[]` 可选，恢复当前世界
最近20条本人有权看到的已提交记录；读档删除未来记录，新分支不继承普通骰
历史。已回滚或分支前的旧命令ID重放返回stale_target，不恢复旧骰点或重掷。

普通骰限频：`keeper_roll` 与 `free_roll_request` 按同数据库、同操作者共享
60秒滑动窗口，默认30次；`TRPG_ORDINARY_ROLLS_PER_MINUTE` 可设1–1000，
非法配置回退30。先校验权限/幂等/载荷再占额度，已提交的同ID重放不占额度；
不同世界、角色、连接和服务实例不能另获额度。拒绝返回可重试的
`rate_limited`，不调用RNG/写骰点/推进版本；界面保留原请求，手动同ID重试，
不自动重掷。正式检定与战斗骰不受此限频影响。使用真实单调时钟，不因
游戏计时、读档或分支清空；已开始掷骰但事务失败的尝试仍占额度。
当前限频为单进程内存实现，重启会清空；多worker上线前需迁到共享存储。

结构化 `session_snapshot.payload.clock` 可选；存在时为
`{elapsed_minutes: 非负安全整数}` 或 `null`，上限9007199254740991。
`state_changed.payload.clock` 使用同一类型。无字段/null/非法值显示未知，
客户端不得猜0或修写服务端数据；无时间变化的其他事件不清空旧投影。
服务端未推进的新世界可合法投影0。快照始终覆盖旧时间，世界绑定切换清空；
其他世界事件由传输边界拒绝。只公开累计分钟，不公开案件/NPC时钟。

`advance_time.payload.activity` 可选，枚举 `wait|travel|check|interact|combat|other`；
省略按 `wait`，显式空值/非法类型拒绝。`reason` 仍必填、只作说明，不解析为
活动类型。旧调用者省略字段合法；新客户端显式字段需配套服务端支持，拒绝
后不静默去字段或回退旧文字路径。命令回执含实际生效 `activity`；公共
`state_changed` 仅带累计分钟，不发案件时钟细节。`travel` 只记录耗时不移动，
`check/combat` 不代掷/结算伤害；其他命令已结算的时间不能再次推进。

玩家帧：`action_request`（move/present_clue/use_item/freeform/combat）、`free_roll_request`、`check_response`、`cancel_request`。主持帧：`command_request`、`memory_query`。每帧字段、必填项、枚举和权限见 schema/permission-matrix；不在此复制易漂移的全部字段。

人类主持快照新增可选 `keeper_progress`（独立 schema `keeper_progress.json`）：
作者完整线索目录、发现规则/条件、实物取得记录及案件时钟。变更通过私有
`keeper_progress_updated` 投递，仅主持可见；玩家快照不带该字段，玩家实时帧/
重放也不带此事件。它不是玩家已知线索，不据此扩大玩家出示、图片或移动权限。

`grant_clue` 默认仍只发知识授权。显式发现增加可选的 `discovery_rule_index`
（从0开始）/`discovery_investigator_id`，前置条件、当前场景与成功检定必须
核验；需要检定时 `check_request_id` 须对应同角色/技能/线索目标及当前场景，
回执仍存在于当前世界outbox（读档回滚的未来检定不能用）。
`acquire_item=true` 才取得作者 `granted_item`，唯一持有人在发现者字段指定，
且发现者必须是接收者之一；分享给多名玩家不会复制原件。转移沿用稳定物品ID，
重复命令幂等，重复取得不同持有人拒绝。阅读物品型线索不落取得标记。
首次入册作者线索推进既有 `clue_clarity`，转发/重放不重复推进。

`known_clue` 和 `clue_granted` 的可选 `allowed_physical_item_ids` 只列该
接收者实际持有、带该线索取得来源的稳定物品；有原件才提供 `original`。
转移/消耗后私发 `clue_updated` 给已经知道线索的相关调查员，实时与刷新
同步候选，不创建新知识，也不派生“刚得知”的新记忆。原件申报在服务端
复核归属及作者来源，不能拿同名/无关物品冒充；出示不等于转交或消耗。

`use_item` 可选 `effect_clue_id`/`effect_rule_index`/`basis` 关联作者使用规则，
真实库存持有人/数量、场景、前置事实与所需实物均复核。历史模组没有明确
物品—规则绑定时只允许人类主持判断适用性并填写依据，不靠自由文本关键词
推断，也不授予Agent任意剧情flag写权限。普通使用不自动附带作者效果；
取得/使用都不代替SAN检定、战斗或结局结算。

`move_party` 成功提交后记录来源和目的场景的已访问键，不伪造NPC遭遇、骰点
或可见事实。拒绝/事务回滚不记录；读档/分支沿各自已提交历史投影。

玩家战斗按钮提交 `action.kind=combat`：本场 `encounter_id`、枚举 `action_type`、
`target_id`（攻击/威胁必填，战术移动/其他可为空）及可选 `approach`。
行动者只取顶层 `investigator_id`，不允许夹带伤害、技能数值、奖励骰或代选防御。
能力 `combat_action_request` 必须显式为真；缺失时不退回文字。服务端在受理及
重放前复核当前控制权，受理时检查进行中的遭遇、当前行动者、存活与目标；
只登记待办，不消耗 RNG/弹药/HP，不推进 revision。刷新时原请求仅本人/主持
可见。主持“准备战斗动作”只填表，明确提交后用 `cause_id` 关联原请求；
关联的类型化请求不能静默换动作、目标或遭遇。技能/伤害仍需主持核验。
可选 `weapon_item_id` 绑定实际持有物品；能力 `combat_weapon_item_id` 必须显式
为真，缺失不退回名称或文字。射击按钮要求选定物品，主持承接时不能换编号。
执行再次核验持有人/数量/原标签，只扣选定件的余弹，不借同名另一把枪；多件
带弹药记录的枪未给编号时拒绝猜测。旧未记录弹药的装备仍沿用原警告，不
凭空补库存。NPC 描述性武器沿用旧规则，尚无 NPC 稳定编号选枪界面。
堆叠编号代表一堆物品：只使用其中一件时原堆叠保留其余件，凭证的
`weapon_item_id` 是所选源堆叠，`used_item_id` 是实际扣弹那件（拆堆后为新编号）。
待掷骰投影可带 `weapon_item_id/weapon_label`；展示批准时选定的装备，掷骰前
仍重新核验，不把旧标签当新授权。战术移动不等于切换场景。
动作准备、玩家响应、结算与请求终态分开；主持/Agent 根据真实结果明确
`resolve_intent` 收尾，不根据叙事或一次点击推测成功。

2026-10-07 本地扩展（已接人类界面，真实 Agent 全链未验收、未发布）：`command_request`
新增 `combat_start`、`combat_action`、`combat_decide`、`combat_roll`、
`combat_end`、`end_game`。其中 `combat_decide`/`combat_roll` 是**玩家响应**，
连接层按玩家身份解析，命令服务重新查询角色控制权；主持/Agent 不得代选代掷。
其余四项需要主持授权。`combat_action` 对玩家只批准动作并生成等待，不直接
消耗骰源/弹药。正式载荷见 schema 与同名 fixtures，字段不能靠叙事猜测。

新增事件 `combat_updated`（脱敏公共战况）、`combat_decision_required` /
`combat_roll_required`（本人及主持）、`combat_roll_resolved`（响应者及主持）、
`game_ended`（公共结局）、`case_settled`（本人及主持的私人奖励）。快照包含
对应恢复字段；不下发原始战斗角色卡、私密策略或其他玩家生涯。

`combat_roll_resolved.payload.result` 为可选的脱敏结算凭证，旧帧仍兼容；
含实际攻击/防御骰点、成败层级、伤害与 HP 变化，不含 NPC 技能值/属性值。
快照 `combat_results[]` 恢复最近20条，按本人/主持权限过滤；同 roll_id 事件
去重，不因展开记录、刷新或回执重发而再掷骰。取消凭证没有骰点和伤害。
此凭证是历史结果，不是新行动或玩家授权；源字段以 `events.json` 为准。
同命令重发返回旧回执，不重骰/重扣/重奖；结局后拒绝新调查、旧检定结算与
游戏状态变更。前端入口/能力、恢复失效与 Agent 等待调度已接线，脚本化测试
不代替真实模型验收；玩家间对抗已接线，Agent 作者效果仍待补齐。

结局在同事务取消剩余行动、待检定、开放线程与未批准草稿，复用已有取消
事件并保持原可见范围；不声称此前副作用未发生、不生成检定成败/骰点。
已经完成的请求/结果保持原样，结局重试不重复收尾；失败整体回滚。
结案后不得新建辅助草稿，但仍可发布收尾叙事、记录记忆和读取旧凭证。

调查员间对抗通过同一命令域，不解析玩家口令：`combat_decision.kind` 可为
`pvp_consent`，先攻击方参与、再目标方参与，被攻击方自行选择防御。
`combat_roll.source=pvp_defense` 是第一份准备确认，不产生骰点/伤害；
`pvp_attack` 是另一方最后确认，双方 RNG、弹药、HP 和各自私有结果在同一
事务统一结算。第一份 `combat_roll_resolved` 不带 result，只撤除旧准备按钮，
不得演成“已掷骰”。任一步取消不扣资源，最终重新验证双方控制权/成员资格/
场景及角色条件；主持与另一玩家不得代选代掷。读档/分支清除两份旧授权。
纯调查员遭遇不会因“没有 NPC”自动胜利；多名调查员仍能行动时继续，主持可
明确结束，剩余不足两名能行动则 `confrontation_resolved`。

人类专用 `record_ruling` 只修改已声明状态键，包含准确旧值和依据；新事件
`ruling_recorded`、快照 `keeper_rulings` 私发主持，不给玩家/旁观者。Agent
不能调用此裁定命令来补结局条件。具体权限/字段以 schema 为准。

主持私有 `keeper_rulings.ending_catalog` 列出全部作者结局及原条件的期望值/
当前值/是否已记录/是否满足，沿用实际结局校验；作者 trigger 是叙事情境说明，
不是关键词门禁。`eligible` 只表示条件齐全；`can_prepare` 还要求不在战斗中、
游戏未结算。准备按钮只填结局表单，实际提交仍走 `end_game` 重新校验。
相关状态变化通过新 `ending_catalog_updated` 原子刷新主持投影；嵌套草稿沿用
同一执行链，人类裁定已有 `ruling_recorded` 则不重复刷新。路由只给主持，
不发玩家/旁观者、不插聊天；快照恢复同样受权限隔离。新事件必含目录；旧
快照/裁定帧可缺目录，客户端提示资料缺失而非编造未满足条件。

人物状态与战斗投影（2026-10-07 本地增量）：`adjust_stat` 对活动调查员读取
实际 `pc`，同步名册；HP/最大HP/条件同步到遭遇。变化在同事务撤销旧决定、
双方授权和待掷骰，发布 `combat_updated`；原行动请求不自动冒充成功或完成。
当前行动者倒地按原规则跳过，无能行动调查员则结束遭遇。增加 HP 不移除
濒死/昏迷/死亡，也不自动重开已结束战斗；调整最大HP不凭空治疗或伤害。
完整 `state_changed` 人物投影仅发本人及主持；公共战況不带 SAN/私有角色卡。
确认/执行还检查实际角色和旧参战投影是否均可行动，不能用旧授权复活倒地者；
状态变化后仍可取消非敌对确认，不扣资源。验证中不回写旧投影来绕过资格门。
前端 structured 人物/线索/位置只采用结构化投影，忽略迟到的 legacy
`character_state/state_data`；否则重连后的空旧帧曾将正确人物卡清成 `-- / --`。
旧世界保留原协议路径。待办中文动作与姓名来自枚举及已授权目标，未解析叙事。

人类状态记录 `record_condition{investigator_id,condition,operation,expected_present,basis}`：
只允许当前获授权的人类主持（Agent及辅助草稿生产者不能调用）。五字段全部必填，
`condition` 为 `major_wound/prone/unconscious/dying/dead`，`operation` 为 `add/remove`；
`expected_present` 是严格布尔值，false不可省略或从未知值强制转换。世界revision与
前状态共同校验；依据1–1000字非空，命令账本保存操作者与准确前后状态。
不改变HP、不消耗骰源/物品，不清其他伤势/作者自定义标记；死亡不能移除，
HP归零后才可新增死亡，HP已大于0才可解除现有昏迷/濒死。副本死亡/状态矛盾
拒绝覆盖。实际变化共用战况同步与旧准备撤销，完整人物事件仍仅本人与主持可见；
相同状态仅回执，不推revision或发布人物/战况变化。前端只读显示核对时状态，
状态漂移需明确重新核对，不根据叙事推断治疗。真实模型验收仍未执行。

结案私人凭证可带白名单 `character_snapshot`，不含控制权/其他角色/NPC。
HTTP `POST /api/character-library/from-case/preview`、`/from-case`、
`/from-case/export` 只按当前控制者的已结算凭证生成卡面；客户端提交世界、
调查员、案件标识、revision、可选名字/receipt_digest，不提交原始奖励。
保存明确创建新副本，不覆盖原卡或写世界；导出只是版本化角色卡下载。
私有快照固定结案卡面，旧案件缺快照不能混入后续分支状态。生命/理智/伤势
保存在附加结案记录，新冒险初始化仍沿用既有建卡规则，不等于结案治疗。

- 按钮提交稳定对象 ID、目标和数量，不拼接文本再解释。
- freeform 原样交主持理解，不直接修改状态。
- 出示区分说明、图片、原件；原件要求持有实物，出示不转移所有权。
- 普通骰不推进剧情，不代替已经请求的检定。检定回应只引用待办 ID 和 roll/decline，参数与随机数由服务端控制。
- 主持命令包含发言、移动、检定、线索/素材、物品、属性/时间、NPC 在场、事实、请求/草稿收尾、角色记忆；以 command schema 列表为准。
- assisted 批准草稿会在同一事务内执行最多 12 条建议命令并发布叙事；复核模型输入版本与当前权限，任一失败整体回滚。拒绝/编辑只收尾、不执行；主持私有草稿可由快照恢复。
- 直接命令和嵌套草稿共用 `execution.py` 的库存/角色状态桥与终态门禁；
  挂起战斗决定/掷骰后，批次只允许叙事与 `resolve_intent(awaiting_player)`，
  其他命令整批拒绝，不留下半份准备或伤害。已经提交的同 ID 仍按回执返回，
  但草稿重放先复核当前人类主持权限；Agent/玩家不得批准或读取他人的受限回执。
- 辅助模型建议先做同一 schema 校验，最多12条；不可委托的控制权、玩家战斗
  响应及人类裁定命令不会发布为草稿。非法建议对原请求可见暂停，不反射原文。
- `control_keeper` 是获授权人类的显式接管/交还/重试入口，不因房主身份授予主持权限；`retry` 只恢复选定暂停请求，重复命令不重复调度模型。

principal 和控制权由服务端 Session、成员、调查员 claim、keeper 授权派生。客户端自填身份不能获得权限。公开投影和主持投影不同，能力开关不是绕过权限的凭证。

## 3. 状态与标识

| 概念 | 含义 |
|---|---|
| request_id | 一次玩家意图/请求；可以产生多个命令 |
| command_id | 一次副作用提交；重试不换 ID 来重复执行 |
| check_request_id | 一次持久检定，不能重掷刷结果 |
| thread_id | 跨请求交互，记录目标与未执行事项，不是授权 |
| revision | 世界版本，不能替代消息 ID |
| sequence / event_id | 新事件排序/身份，不能替代 legacy turn_id+seq 或 room_event_id |

请求有 queued、processing、awaiting_player、paused、failed 及 completed/declined/cancelled 等状态；领域结果 success/failure/not_executed 与请求生命周期分开。completed 不是“故事必定成功”。同 ID 同载荷重试返回已保存结果，同 ID 不同内容拒绝；failed 的重发细节见详表。

## 4. 叙事过渡与交互线程

正常叙事等待通过正式状态表达，不能扫描“你是否……”判断暂停。记录尚未执行行动、已告知条件与目标；玩家可以追问、坚持、改主意或提出新行动，不要求固定口令。

- 原行动成功完成或取消：收尾对应线程与请求，不误关其他人的事项。
- 回答追问：不代表原行动落实。
- 移动抵达：仅对正确关联且被本次移动满足的待办收尾；复合请求仍保留剩余调查事项。
- 实时 `interaction_updated` 与快照 `interactions[]` 必须一致；前端不能只隐藏错误卡片。

## 5. 事件、错误与秘密隔离

新事件信封含版本、世界、事件 ID、sequence、revision、因果请求和 payload；接收范围由服务端过滤。协议范围与载荷见 `events.json`。

权威事件在命令提交后发布。重发/断线通过已提交事件或快照恢复，不重复移动、扣物或掷骰。多条叙事可能共享 revision；不按 revision 丢掉消息。

暂停须有可见原因并解除 loading。正常错误优先持久化；不存在世界或存储故障无法写 outbox 时，使用连接级合成错误信封（event_id/sequence 为 0），不能被普通去重吞掉。缺世界不意味着允许进入 legacy。

主持记忆查询结果只用于获授权的主持通道；不进入玩家聊天或公共重放。HTTP/WS、实时帧、快照和重放都要验证保密，不以“前端没有按钮”代替。

## 6. 恢复与云端分支

云端结构化单人读档是房间生命周期控制，不是 Agent 命令或旧引擎回合：

- 服务端能力 `structured_solo_restore` 必须显式为 true，前端才允许读取；
  不支持时禁用读取但保留已授权的保存、重命名、删除，不退旧 `save_load`。
- 请求 `solo_save_load` 仅含 `world_id`、`slot_id`、稳定 `action_id` 和严格
  非负整数 `expected_revision`。当前世界取连接绑定；房主身份、唯一成员、
  当前认领及存档兼容性在落账前再次核查。多人结构化房间仍禁止读档/分支。
- 复用结构化 reconcile，状态、旧授权作废、主持 epoch 与内部读档凭证同
  事务提交。重复行动不能再次回滚之后的新进度。控制消息有房间持久租约；
  不向公开命令目录暴露内部 `restore_save`，不接受客户端 state/奖励。
- 提交后通知 `solo_save_restored{world_id,slot_id,revision}`，同世界所有
  标签页以关闭码 **4413** 重建连接。清除旧发送队列、游标和私有投影，重新
  获取权威快照；个人未提交笔记保留。普通重连不走此回滚/清队列语义。
- 结构化分支复制各世界的认领，时间线切换只切指针，不搬走父/分支任一侧
  认领或人物状态；legacy 仍随行迁移认领。混合执行配置的切换拒绝。
- 单人开局先把真实选角状态及稳定物品ID写入自动存档，再下发已开局状态。
  失败返回 `room_action_rejected/start_checkpoint_failed`，仍在大厅可重试，
  不把内部异常原文发给用户。重复开局/普通重连不覆写自动槽位。
- 切换与建分支提交成功即退役旧运行时，先于控制锁释放与广播。失败不退役；
  在网关世界锁后才获得执行机会的旧帧收 `stale_target`，不会落账。

- 重连：恢复当前状态与合法待办，不当作主动读档。
- structured 读档：走专用 reconcile；不能直接调用旧 engine.load 后继续使用旧请求。
- `solo_branch_create`：legacy 需要有效 turn_id；structured 从当前 committed 状态分支，不要求伪造 turn_id，可携带 expected_revision；模式不匹配或版本冲突要拒绝。
- 不因云端单人分支已支持就宣称多人房间任意读档/分支入口均支持；边界见[状态](STATUS.md)。

## 7. 协议变更检查

同时核对 schema/fixtures、后端校验与领域授权、Agent 可见命令说明、前端 builder、`server-message.ts` 外层白名单、结构化事件解析、实时投影、快照恢复、错误提示。

参考测试：`tests/test_structured_play_protocol.py`、`test_structured_command_catalog.py`、前端 `protocol/m0-fixtures.test.ts` 及真实后端 E2E。详表中的阶段说明不代表最新完成度；完成度只在 [STATUS](STATUS.md) 更新。
