# 结构化战斗与结局：本轮扩展与验收契约

2026-10-05：用户选择“2”，完整战斗与结局纳入当前目标。
不是新的发布授权。仅本地隔离数据开发；不改真实存档、生产或模组剧情。

## 当前完成度（2026-10-08，覆盖下面历史阶段说明）

最新代码已保存为中文开发检查点 `2fd6bbeb`（后端/协议）和 `78102269`（前端）。
准确验收以[当前验收表](CURRENT_ACCEPTANCE.md)顶部为准：后端1961/8跳过/165子项，
前端1324/127文件，无模型猩红主线18/18；完整95项已93通过/2跳过/0失败、26.0分钟、exit 0，
源/dist终态一致。外部staging/未授权真实模型不计通过，真实模型仍待授权。
后续已证实NPC库存候选与非法持有者转交缺口，整体目标未关闭、未正式发布。
下面的“未提交”“正在运行”和旧测试数字均是阶段历史，不替代上述检查点状态。

主持台存档操作区收口：拆出 `KeeperSaveActions`，分别查询读取策略与房主/
连接/同步状态。当前多人模式禁止读取但房主保存/管理可用；缺读取能力同样
只禁读取，不退旧协议。主持非房主时三操作禁用并解释权限差别；同步恢复
不自动补发操作。可读说明直接写读取的回滚影响，未知能力不显示假可用。
无后端/权限/schema变更。11个新组件对偶、相关68项，前端全量1276/121文件，
类型/构建/格式/ruff/架构通过；实际四宽度与390×360按钮44px完整命中、等高/
有内边距/无竖排，已查看939/390/短窗实装及真实多人禁用提示截图。
布局替身+真实三客户端+云端双标签页读档组合4/4（3.3分钟）；不冒充模型。
复用内置imagegen已生成的[读档参考](solo-save-restore-concept-v1.png)和档案夹
材质，以暗色说明与细黄铜左边线落实，不重复生成装饰位图。实际
[939](keeper-save-actions-939.png)、[390短窗](keeper-save-actions-390-short.png)、
[多人390](keeper-save-multiplayer-390.png)。完整93项已在最终冻结源/dist上
终态91 passed /2 skipped /0 failed（22.2分钟），源码/dist一致。跳过外部
staging/未授权模型，不计通过；保护的旧脏截图已恢复。仍有创建页过时说明、
游戏时间可读投影/无角色主持普通骰缺口，不以整套绿灯冒称目标已完成。
首轮命令浏览器路径重复写了一段，启动前失败；该进程exit130终止，保留
日志，不归为产品失败，不算通过。已核验可执行路径后复跑同版本。
证据 `test-results/keeper-platform/{keeper-save-policy-*,save-policy-full-*}`。

恢复边界：新增单人选角完成后的自动槽位（稳定人物/库存ID），存档失败
保留lobby、给可重试拒绝，不发布已开局快照。普通重连/重复开局不覆写；
不迁改旧存档。真实云端双标签页未快速存档直接读取原自动点2/2（1.4分钟），
返回原场景/角色、清未来历史。时间线切换/分支在提交成功且释放控制锁前
退役旧运行时；失败不退役。WS/HTTP控制释放状态使用外部观测断言，不能
把release_action吞掉的回调assert当成真通过；真实网关锁等待与拆房间的
排队帧对偶零写入。52项定向、最终整仓1866/8跳过/159子项（143.23秒，
既有1项warning），ruff/架构通过。16项浏览器组合在冻结源码/dist下16/16
终态（6.4分钟），含完整三客户端与生命周期6项，指纹一致；
产品前端未动，沿用已有imagegen档案夹与上轮1265项，不声称本轮重新全跑。
证据 `test-results/keeper-platform/{restore-boundaries-*,initial-checkpoint-browser*}`。

Kimi原始生命周期5/6+Python2/2已复核，版本漂移如其报告保留，不拼成稳定
全绿；报告/指纹及其余产物备份在
`test-results/keeper-platform/kimi-lifecycle-original-20261008/`。
证据保全限制：首次C复跑时备份命令误从frontend目录执行，纠正路径前C已
覆盖同名服务日志；不声称原C日志仍完整。原失败trace/error-context仍在
`frontend/test-results/combat-lifecycle/`，缺陷文档保留原错误摘录；后续整组
复跑已预先备份，其原报告未改。新版通过日志单列，不回写Kimi5/6结论。
实际阻断是结构化solo分支认领复制与切换搬行冲突，已修为按执行配置分流；
原样C转绿后整组6/6（1.7分钟），父世界HP/弹药/物品不受分支行动影响。
HTTP15/15含双向切换与混合配置拒绝，未删除重复认领或放宽约束。

云端结构化单人读档已接线：真实UI两规格2/2（1.3分钟），包含两个同主
标签页回滚/重连、旧检定失效、同revision未来叙事清除和重放不重复回滚；
四宽度/390×360确认按钮≥44px且完整命中。原两轮脚本错误（input当select、
使用不属于选角技能表的固定技能）保留失败日志，改用实际授权技能键。
全量首跑发现新能力遗漏严格事件schema：12 failed/1850 passed/8 skipped，
补字段而非放宽additionalProperties；实际投影/坏类型/未知能力对偶与
相关回归113/113通过。前端全量1265/120文件，类型/构建/格式/ruff/架构通过。
最终后端1863 passed /8 skipped /159 subtests（138.78秒，既有1项warning）；
完整92项浏览器终态89 passed /2 skipped /1 failed（21.9分钟），源/dist一致。
唯一失败在三客户端PvP：攻击方卡片更新先于防御方另一条连接回执，脚本
立即断言数组长度1，实际0；后续失败截图已显示防御方确认完成。两连接
投递无同步完成契约，现直接等待防御方自己的唯一回执并追加同roll_id断言，
保留不得含骰点result/HP不变；不重发动作、不放宽门禁/超时、不改产品代码。
修正后完整三客户端专项1/1（1.7分钟），保留原失败trace；产品源码/dist
未变，仅等待断言变更。不把原整套89通过加专项拼成一次新版完整全绿。
日志 `test-results/keeper-platform/{solo-restore-*,timeline-claims-*,lifecycle-fixed-*,restore-full-*}`。
未提交/推送/发布，未用真实存档/付费模型/正式环境测试。

最新人类状态记录：后端整仓1832/8跳过/159子项（157.20秒，既有1项warning），
前端1252/119文件，最终草稿/短窗/真实三客户端7/7（2.6分钟），源/dist终态
一致。`record_condition`及冻结前值表单完成，27项后台条件对偶及辅助生产者
拒绝通过；四宽度/390×360真实UI记录与移除倒地、刷新、隐私/HP/余弹不变。
状态记录版完整86项已终态84/2跳过（20.5分钟），源码/dist一致，日志
`condition-full-*`；仅属于读档增量之前，不冒称当前新版完整证明。
真实模型仍独立待授权；未提交/推送/发布。

最新状态同步：整仓后端1802/8跳过/156子项、前端1238/118文件，真实认证三
客户端1/1（2.0分钟，待办标题中文化前）。初轮完整浏览器83/2跳过/1失败
（20.6分钟，源与dist指纹一致）。失败已修成待办自有滚动边界/短窗工具行，
新版草稿/短窗/真实三客户端7/7（2.5分钟）通过；完整新版86收集→84 passed /
2 skipped /0 failed（20.5分钟），源码/dist终态指纹一致。跳过外部staging与未
授权模型，不计通过；Kimi新增规格单独验收，未混入本套。
战斗读档/分支专项可由Kimi在独立8876服务上补证，不交叉写源码。

最新武器接线：整仓后端1786/8跳过/156子项、前端1232/117文件；定稿三规格
3/3（2.2分钟），无模型真实三客户端原模组左轮按钮射击6→5发、对方零库存帧。
完整新套件与真实模型仍待；下文数字保留对应阶段意义。

此前辅助接线：整仓后端1748/8跳过/150子项（206.83秒）、前端1222/116文件；
脚本化建议生产+真实认证/后端/UI三客户端1/1（1.7分钟），审批阅读四宽度/
390×360替身1/1（9.8秒）；不冒称真实模型或完整浏览器套件验收。下文各次
数字按其阶段保存，不替代本条。玩家类型化申报的最新增量见下节；真实模型
及完整新版浏览器套件仍未宣称通过。

| 能力 | 当前实现与证据 | 仍缺的证明/能力 |
| --- | --- | --- |
| 战斗按钮与真实结果 | 六命令、等待/本人响应、幂等、弹药、私密结果；双方参与/防御/确认，草稿审批共用库存/状态桥与等待屏障；类型化申报与稳定物品ID选择/精确扣弹；人物状态同步/倒地待决检查；人类显式状态记录及HP0恢复资格；生命周期6/6覆盖读档/分支及多人PvP失效 | 完整当前新版浏览器终态；真实模型新域 |
| 人类结局裁定 | `record_ruling` 只操作已声明原始类型状态键，依据/前后值/操作者持久审计；CAS、撤权重放、outbox 回滚；完整私有结局条件目录与实时刷新已接 | Agent 作者效果通道；真实模型新域 |
| 合法结局 | 原模组、真实两名玩家明确放弃→主持收尾→裁定→`leave_arkham`→各自奖励→刷新；原子取消剩余工作，完整条件只对主持可见，准备不执行；生命周期D/E/F覆盖重复结算/回滚/旧凭证拒绝 | 不是猩红文档完整主线；真实模型新域 |
| 结案角色库 | 玩家明确另存新副本/导出，云端两账号真实 HTTP 与 UI 验收；原卡不覆写、重复不重奖、刷新已保存、主持无角色无入口；生命周期D/E验证历史凭证不混入新卡 | 不冒称真实模型或生产部署证明 |

### 最新：人物与战况同源、旧准备失效、重连界面一致

`adjust_stat` 活动调查员原先只改名册副本，后续战斗仍读旧 `pc` 和参与者HP；
已有确认则绕过新动作的资格检查。现使用实际角色查询并投影名册，同事务
同步HP/最大HP/条件，撤销旧决定、双方授权、待掷骰，发布脱敏战况。
当前行动者不能行动时跳过；无可行动调查员时沿用原遭遇结束规则，零RNG/
零弹药。原请求仍是主持待办，不从一次状态变更推断成功。增加HP不清伤势/
死亡、不重开战斗；改变最大HP不新增治疗/伤害。完整状态仅本人/主持可见。

已准备动作的确认/执行重新核查实际角色和旧遭遇副本；任一认为倒地则拒绝，
不能在验证时同步活跃缓存从而复活旧倒地记录。取消非敌对确认仍合法，零
资源；NPC攻击/玩家防御也核查双方。人类批准草稿使用同一同步链，批内倒地
后继续行动整份回滚。未改变 legacy 引擎/registry 或模组内容。

16项新增后台对偶，相关155项通过；整仓1802 passed /8 skipped /156 subtests
（190.05秒，原SQLAlchemy警告保留）。早期两个新夹具期望错将双PC当单PC、
最大HP当当前HP，修夹具不放宽产品断言；旧“名册倒地/遭遇倒地”对偶曾揭示
验证时同步缓存会复活的问题，改为验证先fail-closed，不删除旧断言。

真实三客户端第一轮失败：快照HP=9，UI为`-- / --`，轨迹证明迟到legacy
`state_data.data="{}"` 覆盖授权结构化快照，并非加载慢。前端明确隔离两种协议的
角色/线索/位置投影；4项新对偶含旧模式仍正常、结构化不支持时不回退。
复测真实认证/后端/UI三客户端1/1（2.0分钟）通过：准备后主持确认伤势→
旧骰钮消失/无扣弹→刷新正确HP与资源→重新批准产生新roll_id→实际左轮6→5。
另一玩家零私人完整状态/库存事件。截图[人物与战况](combat-vitals-synced.png)
已实际查看。随后将恢复待办的英文枚举/编号改为中文动作与授权目标姓名，
加2项回归；最新前端1238/118文件、构建/格式通过，未添加正文解析器。

证据 `test-results/keeper-platform/combat-vitals-*`：完整后端/定向、真实浏览器
首轮反例及复测、前端最新门禁、冻结源码和dist指纹。中文化后初轮完整浏览器
83/2跳过/1失败，以下布局修复后新版整套84/2跳过已终态；不将专项等同全套。
未调用付费模型、改真实存档、提交或发布。
本轮只修数据/显示接线，复用已有生成图设计与素材，不为数值同步重复生成位图。

完整回归的布局反例：草稿卡没有自有滚动边界，撑出 `overflow:hidden` 的
父聊天面板；输入框启用时原有 autofocus 将父面板拉到底部，标题可见性
随布局时间变化。独立8880替身探针复现939宽标题由y=286变为-182、父面板
scrollTop=469，600ms后仍不可见，不归因为慢机。只加自有滚动时短窗区域
会压成37px，中心命中虽真但按钮被裁，不拿它交付。

现待办独立滚动、80px最低阅读区与有界最大高度，短窗将掷骰/上下文并行；
主持入口从聊天面板独占一行移入同一底部工具行，44px，权限/命令/打开行为
不变，给叙事多留空间。素材与DOM字段沿用既有imagegen设计，不生成装饰位图。
审核原断言保留，追加按钮完整边界、输入框完整可见、父面板scrollTop=0及
布局稳定后标题命中；不加skip/retry/扩大超时。新版前端1238/118、构建/格式/
类型通过，草稿/短窗/真实三客户端7/7（2.5分钟）；已实际查看四宽度/短窗
截图，新源与dist冻结。日志 `test-results/keeper-platform/draft-dock-*`，
整套新版已84通过/2跳过（20.5分钟），源/dist终态指纹一致；不是把初轮83
通过加专项7通过拼成通过。已保护的旧脏截图在跑完后恢复；未提交、推送或发布。

此前整套后发现的缺口：`adjust_stat` 只能改四种数值，没有人类主持显式
添加/清除伤势条件的通路，HP恢复不等于从昏迷/濒死恢复行动。已在上一套
终态后新增下节的人类状态记录，不在上一套受测代码中途修改，不把旧84/2
数字当作本增量验收。Agent不得借此自我授权、死亡不可逆、不擅自加HP，
不通过文本关键词触发。

### 游戏时间只读投影（已接线）

已接快照、时间/移动事件、store与顶栏/主持表单：直接显示已结算分钟，
不解读叙事、不跟随系统时钟、不显示NPC截止/案件秘密、不虚构日期。
缺字段/坏类型显示未提供而非0；读取/分支/重连按快照恢复，换世界清空。
服务端仅未推进的新世界合法0，坏存量不修写。超出JS安全整数范围投影null，
整条事件仍合法，不丢其他状态。创建模式说明也已按现有人类战斗/结案域校正。

本地最终门禁：后端1876/8跳过/159子项、前端1292/123文件、类型/构建/
格式/ruff/架构通过。最终浏览器5/5（3.8分钟，exit0），源码/dist终态
指纹一致，日志 `test-results/keeper-platform/game-clock-browser5.log`。
真实三客户端实际结算200分钟/刷新一致，云端双标签页存档按原保存时间
回滚；界面替身只验证旧服务未知态与四宽度/短窗，不冒充真实时间结算。
首轮顶栏越界已修；单测抓到换世界未清时间并修复。保留错误脚本的两轮
日志：误假定存档0分钟、异步列表未到便跳过管理入口；最终改成真实保存值
与等待按钮，不改产品求绿，不加skip/retry/超时。旧完整93项属于此前版本。
该只读增量当时仍缺无角色主持普通骰，现已接下方独立通道。此增量当时未改时间机制；随后
`reason/activity` 分离的实际证据见下一节，不将两个版本的证据拼接。

#### 时间活动类型：已接线，无模型验收通过

`activity` 可选wait/travel/check/interact/combat/other，省略按wait，原因只作
说明；非法显式类型拒绝。回执含生效类型，公共事件仍只投影累计分钟。
主持表单与草稿中文说明“仅计时、不代执行”，44px下拉/输入，不加动画。
猩红文档原作者2880分钟等待规则可触发显形时钟，非时间时钟不受影响；
赶路不会因原因含“等待”误计，亦不会替队伍移动。模组文件未改。
19项新后端对偶、1895/8跳过/159子项全量；前端1295/123文件及类型/构建/
格式/ruff/架构通过。最终浏览器5/5（3.7分钟，exit0，源/dist一致），包含
真实三客户端/云端单人类型提交、时间显示/读档与原权限、草稿审阅/四宽度/
短窗。首轮label定位错、第二轮34px是真界面缺陷，最终按44px原断言验证。
日志/指纹 `test-results/keeper-platform/time-activity-*`，截图已查看；不是
新版完整浏览器重跑。付费真实模型主线待独立授权，不声称全链已验收。

#### 主持独立普通骰：已接线，无模型真实UI通过

命令keeper_roll仅供当前人类主持主动调用，不需要调查员，不能给玩家cause_id、
技能/伤害参数；Agent与辅助草稿拒绝。默认仅主持、可公开，复用受限Dice/RNG，
不改世界JSON/revision/时间/资源，不触发剧情或代结算待检定。命令与事件同事务。
查重前核验当前授权；同一主持认领变化不误拒，其他操作者/撤权拒绝。当前
世界outbox提供最近20条可见收据，刷新恢复、同版本号读档清未来、分支不
继承普通骰历史；旧回滚ID返回stale_target，不复活/重掷。

23项新后台对偶；后端1918/8跳过/162子项（208.17秒，既有warning），前端
1303/125文件，类型/构建/格式/ruff/架构通过。最终组合5/5（4.3分钟，exit0），
本地真实无Key/云端单人/无角色主持+两玩家、私发帧隔离/公开一致/刷新、
原战斗结案与四宽度/390×360。源/dist终态一致，日志`keeper-dice-*`。
初轮漏接有界高度导致短窗越界（3/1），修复5/5后实际看图发现非当前字段
标签被滚动裁切；逐控件标签专项虽通过仍加整组可见断言，短窗改并排后
全部5项重跑通过。不放宽/skip/retry/强制点击；失败证据保留。
生成[参考及完整提示词](keeper-free-dice-prompt-v1.md)使用内置imagegen，
复用九宫格/小纸质收据，不采用示例27/塔楼插画/大型标题。已查看939及短窗。
当前组合不是完整浏览器；不标记目标完成/发布，付费模型主线仍待授权。

新内置imagegen参考[game-clock-concept-v1.png](game-clock-concept-v1.png)已
复制进项目并查看，原件
`/home/inaglyite/.codex/generated_images/01a06c3d-3b8c-7263-82c5-de1bee5f9328/exec-aaae01a7-02a4-42ca-847e-a46499d186c0.png`。
实装只取次级时间行、小暖纸参考与暗色字段；不采用图中大型标题/额外纸叠，
不把整图当背景，也不把示例3小时20分钟烘焙为数据。已查看390顶栏/短窗表单，
并以几何断言核对固定顶栏边界与44px操作区。完整提示词：

```text
Use case: ui-mockup.
Asset type: implementation reference for a Chinese 1920s mystery tabletop game. Show a compact desktop header and a narrow mobile header, plus a small human-keeper time form excerpt.
Primary request: a calm, readable READ-ONLY game-time indicator integrated with the current-scene line, not an additional large card. Main title "猩红文档", scene label "当前场景：密斯卡托尼克大学医学院", and a quiet line "游戏时间：已过3小时20分钟". The time is an illustrative example, not a real save or current date.
Below show a compact keeper form headed "推进时间", with a small reference line "当前已过3小时20分钟", input labelled "推进分钟数" containing "20", a restrained brass button "推进时间", and explanatory text "只有提交并结算后才会改变游戏时间。". Also show an unobtrusive unknown-state example "游戏时间：未提供"; absence must not look like zero.
Visual direction: match a FLAT hand-illustrated archive folder with paper tabs and thin muted brass edges, matte dark brown #211a14 / #292118, warm text #e5d8b8, small parchment reference strip #d1bf9b with dark ink #312a21, subdued brass #c9b77b. Chinese serif headings used sparingly with a clean readable Chinese body font. Opaque backing, good contrast, restrained texture.
Composition: time label is secondary to scene, aligned in a small edge strip, wrapping naturally on the 390px mobile concept. Preserve room for narrative. Keeper form has DOM-like 44px controls, a fixed small reference above the input, no timeline chart. No phone/device frame, no photo of a desk, no photorealistic leather, no 3D metal, no neon or glow.
Constraints: all labels and values will be real DOM in implementation, this is a layout/design reference only. No dates, real-world clock, live ticking seconds, countdown, secret NPC schedules, deadline indicators, progress percentage, success badge, additional avatars or props, or automatic healing/rewards. Do not number the parallel views as steps. No watermark.
```

### 云端单人结构化读档：已接线

代码证据：`src/multiplayer/messages.py` 将结构化世界的 `save_load` 一律
拒绝，含单人；修复前 `panels.loadSave` 与读取按钮只按房主限制，
会发旧消息。此前完整回归没有云端结构化读档用例，因此即使全绿也不能
证明此功能。多人结构化读档继续禁止，不能顺带开放共享世界回滚。

已实现：显式 `structured_solo_restore` 能力与 `solo_save_load` 控制消息；
稳定 action_id、当前世界与严格 expected_revision，授权/单成员/CAS/回滚/
幂等凭证同事务，复用原结构化 reconcile，撤销旧授权及主持实例；提交后
同世界断开重建、清旧运输队列与游标，重新取快照，普通重连不回滚。
坏/早于调查员就绪的存档不伪造角色状态；旧服务端缺能力禁用，不退回经典
读档。保存/重命名/删除的权限不因读取门禁被整体误关。

设计参考 [solo-save-restore-concept-v1.png](solo-save-restore-concept-v1.png)，
内置 imagegen，未使用CLI/BYOK，原件
`/home/inaglyite/.codex/generated_images/01a06c3d-3b8c-7263-82c5-de1bee5f9328/exec-a6db6d2d-6255-4a8f-adc8-a6835da608bf.png`。
已查看。小暖纸存档摘要、暗色说明、底部确认/取消固定；效果图的示例名不是
真实存档。三项是并列影响而非步骤，实装不复制生成图的1/2/3装饰编号，
不绘制手机外壳；控件和数据继续为DOM。完整提示词：

```text
Use case: ui-mockup.
Asset type: implementation reference for a Chinese 1920s mystery tabletop game, cloud solo save restoration confirmation. Desktop and narrow mobile versions side by side.
Primary request: a calm, legible confirmation inside a flat hand-illustrated archive-folder UI. This is a narrative-first DOM game, not a 3D game. The dialog title is "读取这个存档点？", with a small "云端单人 · 当前时间线" caption. Show one small opaque warm parchment reference slip for the chosen example checkpoint: "医学院调查前" and "密斯卡托尼克大学 · 黄千陆". All this is illustrative mockup data, not a real save.
Below on matte dark-brown backing, present three quiet, clearly readable consequences: "当前进度将回到这个存档点。" "旧行动、检定和战斗授权将作废，需要重新批准。" "服务端确认后重新同步；普通刷新不会回滚。". Footer has "取消" as a restrained dark button and "确认读取" as softly worn brass, not a red danger button. Keep the title and footer fixed and body independently scrollable in the mobile variant. All controls at least 44px.
Visual direction: thin subdued brass edges, a modest paper tab, opaque backing for legibility. Dark brown #211a14, warm body text #e5d8b8, parchment #d1bf9b limited to the small checkpoint reference slip, dark ink #312a21, muted brass #c9b77b. Restrained Chinese serif heading and clean Chinese body text. Use existing game-folder material language, not a generic dashboard.
Constraints: FLAT 2D illustrated UI, no photographic desk or realistic leather, no 3D metallic frame, no glow, no neon, no huge bright paper surface, no extra decoration, no avatars, no skulls, no watermark, no imaginary progress percentages or completion badges. Final labels, facts and controls will be DOM, not baked into a game asset. Do not add claims about physical deletion, automatic healing or automatic rewards. No multiplayer rewind option.
```

### 可交给 Kimi 的独立验收任务提示词

```text
请为 trpg-master 补齐结构化战斗/结案的生命周期浏览器验收。只在本地隔离
世界运行、人类主持、零模型调用；不连接Pi/正式环境、不用真实存档、
不commit/push/deploy。不得把提交授权视为真实模型额度授权。

Codex负责人物/战况同步、前端和完整回归。不要修改已有src/**、frontend/src/**、
schemas/**、现有测试或公共测试配置。你的文件限定为：
frontend/e2e/structured-combat-lifecycle.spec.ts；必要的新helper命名combat-lifecycle-*；
tests/test_structured_combat_lifecycle.py（仅必要补充对偶）；
docs/evidence/keeper-combat-lifecycle/（脱敏证据）。
发现产品缺陷先给最小复现、正确边界与修法，不抢改共享源码。

独立服务用127.0.0.1:8876、mkdtemp runtime/database；Playwright单独输出目录。
模型地址用本地关闭端口、记录零调用。验收当前未提交工作区，不只测HEAD；
记录相关源码/dist指纹，结束再核对；有变化需说明受影响范围。

至少验证：
A 战斗中保存：实际结算的HP/伤势/选定武器余弹/物品ID被保存；普通刷新重连
  不回滚、不再扣弹或掷骰。
B 主动读档：资源和遭遇回到保存点；旧决定、双方参与/待掷骰授权全部失效；
  旧响应不得执行，主持重新准备、本人重新响应才能继续。
C 云端单人结构化分支：真实UI从已提交状态分叉、不伪造legacy turn_id；
  复制资源但不复制旧授权；分支行动不影响父世界。多人不支持分支则验证
  真实禁止边界，不绕过。
D 结案后：合法结局/本人奖励可恢复，另存角色不覆盖原卡；重复结算/请求重放
  不重奖；读档/分支的历史凭证不能混入未来卡面。
E 本人/主持/另一玩家/旁观者的私密角色、骰点、奖励和控制权限不能串线。

以真实后端、真实UI、实际命令/状态证明。不能靠改数据库、注入前置状态或
叙事关键词假装跑通；故障注入对偶单独标注，不能冒充正常UI验收。
不加skip/fixme/retry，不放大超时掩盖失败，不改断言迎合实现。
交付逐项PASS/FAIL/未测、测试/命令、版本指纹、脱敏日志、缺陷最小复现。
先完成你自己的验收与交接，不等待Codex提交或发布。
```

### 人类状态恢复：生成参考与实现边界

已生成设计参考 [condition-record-concept-v1.png](condition-record-concept-v1.png)，
仅人物摘要保留暖纸，主体深棕/暖色文字，已实际查看并做一次有针对性的减亮。
现已注册人类专用`record_condition`及严格五字段前端表单；不把效果图当成验收
或真实存档事实。前端1252项、后端1832项、三规格7/7均已终态；真实三客户端
用实际主持按钮记录/移除倒地，不改HP/余弹，刷新一致，他人零私有状态/依据帧。
既有完整84/2浏览器数字属于增加本命令之前的版本，不沿用为新增命令全套结论。
最终文字/控件为
DOM，图中数字、角色名与计数器不是协议字段或承诺。实现复用现有固定边缘
文件夹，手机单列、标题/底部操作固定、中段滚动；不引入3D材质或闪光。

仅人物摘要为小面积暖纸，主体保留暗色表单。稳定调查员编号、闭合状态枚举、
显式添加/移除、只读已核对布尔前值与依据；角色变动不自动改草稿，人工重新
核对。增加HP与移除昏迷分开；不解除死亡、不推断治疗、不让Agent调用。
无变化只回执，不借无效操作同步无关的陈旧战况。正常命令、重放/撤权、
outbox回滚、坏副本、HP0资格及辅助生产者拒绝均有定向对偶。

最终日志`test-results/keeper-platform/condition-record-{backend-full,front-final,build-final,final-browser}.log`，
终态指纹`condition-record-final-{source,dist}-terminal.log`。首轮浏览器脚本重复
点击已打开的主持台被正确遮罩拦截，修流程并保留断言重跑；第二轮1/1通过。
看图后补下拉44px并加实际高度断言，最终三规格7/7。已查看
[939阅读](condition-record-reading-939.png)、[390阅读](condition-record-reading-390.png)、
[390短窗](condition-record-390-short.png)。旧脏截图按受保护备份还原；生成图
只指导参考条/暗色主体，字段与按钮仍为DOM，不新增装饰位图。

内置 imagegen 模式，未调用CLI/BYOK。原件：
`/home/inaglyite/.codex/generated_images/01a06c3d-3b8c-7263-82c5-de1bee5f9328/exec-a032e12a-0aad-49a8-b814-a8a83e20ad2c.png`。
首轮亮纸版本未采用，未覆盖任何旧素材。首轮完整提示词（结局参考图仅用于材质）：

```text
Use case: ui-mockup.
Asset type: high-fidelity implementation reference for a Chinese 1920s Call of Cthulhu tabletop game, keeper-only condition-record form, desktop and narrow mobile side by side.
Input image 1: STYLE REFERENCE ONLY. Preserve the flat hand-illustrated archive-folder material, muted brass edge, dark brown surround, warm matte paper and dark readable ink. Do not copy its ending catalogue, numbering or story text.
Primary request: an understated medical case-note inside this same UI family, titled "记录人物状态" with small "人类主持" / "仅主持可见" labels. A compact reference slip at top: "黄千陆", "HP 1 / 10", "已记录：昏迷、重伤". Below it show practical labelled controls "调查员" (黄千陆), "状态" (昏迷), "变更" (移除), a clearly READ-ONLY previous-state line "当前记录：存在", and a roomy multiline "裁定依据" field with example "急救已结算，调查员已恢复意识。". At bottom quiet explanatory copy "只调整状态标记，不增加 HP。死亡不能移除。" and "已有战斗准备会失效，需重新批准。" Footer buttons "取消" and "记录变更". No hidden automatic healing, no reward numbers, no success celebration.
Composition: one desktop-sized form and one mobile single-column form. Header and footer stay clear, central body scrolls if long, all touch controls at least 44px; only modest thin paper-slip backing for the reference, not a blinding white screen. Form names and basis must be easily readable. All final text and controls will be DOM, this is a reference image only.
Typography and palette: restrained Chinese serif for the title, clean Chinese body type. Existing brown #211a14, worn brass #c9b77b, opaque parchment #d1bf9b, dark ink #312a21, quiet olive #65734f for the present-state stamp. Reference game is narrative-first DOM, not a 3D playfield.
Constraints: flat 2D illustrated UI, no photoreal desk, no realistic leather, no 3D metal, no glow, no neon, no ornamental skulls or crosses, no avatars or unrelated props, no large medical branding, no charts, no watermark, no baked-in claims about a real save. The medical record is a deliberate note-taking surface, not a SaaS dashboard.
```

最终减亮编辑提示词（保持布局/文案/控件，不复制图片状态进游戏）：

```text
Edit this UI reference, one targeted change: reduce the large bright paper area so it is comfortable in this dark narrative game.
Keep the exact folder silhouette, desktop/mobile composition, header, character reference strip, all controls, labels and footer actions. Keep the small top character summary on warm opaque parchment. Change ONLY the large form-body backing and its form fields to a matte dark-brown reading surface (#292118), with warm ivory labels/text (#e5d8b8), subdued brass outlines, and generous legible spacing. Keep the basis textarea dark as well. The warning notes should be quiet warm text on dark brown, not a big bright paper block. Cancel button dark brown, primary button softly worn brass, no glow. Preserve the flat 2D illustrated material and all control shapes; no photorealism, no 3D, no extra props or decoration. Ensure large body text remains easy to read and no labels vanish into the background. The reference is a mockup only, not real save state.
```

### 此前：按持有物品编号选择武器

玩家按钮提交可选 `weapon_item_id`，射击必须选定自己的实际持有物品；
明确能力 `combat_weapon_item_id` 缺失时不回退名称。主持准备原申报携带同一
编号，切换行动者清旧编号，候选不混其他角色的物品。审核页同名物品仍展示
完整编号。Agent 目录也说明原编号不可偷换，但尚未做真实模型新域验收。

`combat_weapons` 适配层给旧规则仅选定那件的临时库存视图，finally 恢复投影，
不改旧引擎/registry、不新增玩家文本意图解析。结算通过共享库存桥按编号落账，
准备/确认/双方参与阶段零 RNG、零资源消耗。空枪、数量为零、持有者变化、
名称与编号矛盾均拒绝；取消不要求仍持有武器。无编号的多件带余弹枪拒绝猜测。
堆叠只使用一件：源 `weapon_item_id` 与拆分后的 `used_item_id` 明确区分。
旧未记录弹药物品仍保留原警告；NPC 描述性武器不是新的稳定ID装备系统。

13项新增后端对偶（同名多枪、空枪、拆堆、转移、非敌对确认、双方对抗、
重发及 outbox 回滚）；相关69项/119子项，后端全量1786 passed /8 skipped /
156 subtests（194.75秒）。前端全量1232/117文件，类型/构建、ruff/架构/diff通过。
最终浏览器3/3（2.2分钟）：四宽度/390×360下拉与按钮命中、审阅同名编号，
以及真实认证三客户端原模组实际左轮6→5发、另一玩家零库存事件。枪由角色
原卡提供，不改库/存档前置、不调用模型；主持明确按真实成败收尾请求。
日志 `test-results/keeper-platform/weapon-binding-{backend-full,front-final,browser-final}.log`，
23源指纹 `weapon-binding-final-fingerprint.txt`。截图
[939申报](combat-declare-939.png)、[390短窗](combat-declare-390-short.png)，
主代理实际查看，复用生成文件夹/固定边缘，不为代码原生下拉再生成位图。
首个组件测试夹具缺参战者且 select 引用在切角色后已卸载，修夹具与重新定位；
没有放宽产品权限/命中断言。未提交、推送、发布；不称整个新套件/模型全链通过。

### 此前：主持结局条件目录

人类主持可以查看所有作者结局，不再只看到已满足列表。投影
`keeper_rulings.ending_catalog` 含作者标题/类型/说明/收尾情境及每项条件的
期望值、当前值、是否已记录、是否满足；当前值缺失不冒充 false。
资格按既有 `validate_ending` 判定，重复旧 ID 与实际校验一致采用最后定义，
不修改布尔/数值相等语义、不解析剧情文本、不自动补条件或结算。
`eligible` 与 `can_prepare` 分开：战斗中或结案后即使条件齐全仍不能准备。

共享执行器在相关状态或结算可用性变化时原子追加
`ending_catalog_updated`（仅 keeper）；人类裁定已有完整
`ruling_recorded`，不重复发刷新；嵌套草稿按子命令走同一链，不重复外层刷新。
读取无状态写入，失败无半份状态/目录事件，同 ID 重放不重复变更。
新事件必须含目录；旧裁定/快照可缺该字段，界面明确说明完整条件未提供，
不编造条件。正式事件枚举、唯一 WS 入口、schema 与 fixture 均已接线。
另一玩家/旁观者不接收目录或裁定秘密，页面阅读不进入聊天。

8项新增后端对偶（含真实数据库/控制权与 outbox）、相关定向44项/118子项；
后端整仓1773 passed /8 skipped /155 subtests（128.69秒），已有 SQLAlchemy
警告原样保留。前端新增4项，整仓1230/117文件。统一版浏览器3/3（1.9分钟）
包含真实三客户端未齐→裁定→已齐→仅准备→实际结算、玩家帧/快照不含秘密。
日志 `test-results/keeper-platform/ending-catalog-browser-final2.log`；格式仅修正
测试文件后重跑全量，见 `ending-catalog-front-final3.log` 与
`ending-catalog-format-final2.log`，12个产品/浏览器源文件指纹保持一致。
截图 [939阅读](ending-catalogue-reading-939.png)、[390阅读](ending-catalogue-reading-390.png)、
[短窗按钮](ending-catalogue-390-short.png)，主代理已实际查看四宽度与短窗。
首轮新替身漏声明结算能力导致按钮正确禁用，不改产品授权来过测试；补齐
替身角色/能力后通过。实际截图发现摘要与按钮继承金字导致低对比，修局部
选择器并加摘要/按钮深墨颜色断言。早期失败保留，不复用旧图或旧版结果充数。
上述测试不替代真实模型或整个新版浏览器套件；未触碰真实存档/生产，未提交发布。

图像生成使用内置 imagegen，不调用CLI/BYOK，输入参考
`draft-review-concept-v1.png`。生成原件：
`/home/inaglyite/.codex/generated_images/01a06c3d-3b8c-7263-82c5-de1bee5f9328/exec-488597f8-e2a8-45b7-873c-08350be21eb5.png`；
工作区概念 [ending-catalog-concept-v1.png](ending-catalog-concept-v1.png)。
采用其暖纸、深墨字、低饱和状态章；去掉装饰编号，结局是分支不是步骤。
实际参数/标题均为 DOM，背景复用已有素材，不将图中文字烘入游戏数据。

最终提示词（参考图，不代表真实世界状态）：

```text
Use case: ui-mockup.
Asset type: high-fidelity design reference, desktop and mobile keeper-only ending catalogue inside an existing Chinese 1920s Call of Cthulhu browser tabletop platform.
Input image 1: material and style reference ONLY; its command-review content must not be copied.
Primary request: a quiet "结局条件" archive folder, marked "仅主持可见". Show two author-defined ending slips "真相与封印" and "离开阿卡姆", not analytics. First is marked "条件未齐", has an expanded plain checklist: "monster_defeated — 期望 true / 当前 false — 未满足", "documents_recovered — 期望 true / 当前 true — 已满足". Second is marked "条件已齐" and has a "准备结算" button. Footnote "这里只准备表单，不自动结束游戏。战斗中须先结束遭遇。" Small collapsed "最近裁定记录" underneath.
Composition: one side-by-side desktop and narrow mobile reference board, straight-on flat 2D illustrated UI. Desktop can use two slips, mobile one-column natural scroll. Field labels and expected/current values should be easily readable, long IDs wrap. No elaborate new decoration. All real text will remain DOM; illustrated text is a reference only.
Palette and typography: existing dark brown #211a14 folder, restrained worn brass #c9b77b edges; opaque warm parchment #d1bf9b slips and dark ink #312a21; muted olive #65734f for satisfied, muted umber #785d3b for unmet. Restrained Chinese serif headings, clean readable Chinese body type.
Constraints: no photoreal desk, no 3D leather, no shiny metal, no glow, no neon, no charts, no tiny JSON wall, no lock icons implying numeric grind, no auto fulfil buttons, no player-visible spoilers, no watermark. Generous 44px-or-more touch controls, practical DOM layout.
```

### 最新：玩家类型化战斗申报（本地已验收）

按钮提交 `action.kind=combat`、当前 `encounter_id`、动作枚举、参战者
`target_id` 与可选做法，不拼自由文本、不关键词解意；技能、伤害和持有武器
由主持核验。五类动作复用规则域，战术移动不等于整队跨场景。玩家不能注入
伤害骰、技能数值、奖励骰或目标防御；主持/Agent 准备后仍等待对应玩家响应。
该申报初版尚无武器稳定 ID；已由上方最新武器增量补齐，不再是当前缺口。

- `combat_action_request` 能力显式门控，旧服务端不开放假入口、不回退文字。
- 请求入口/重放重新查询成员与角色认领；遭遇、当前行动者、HP/条件、目标
  做事实检查。受理只写请求/outbox，不掷骰、不扣资源、不推进世界 revision。
- 原申报仅本人/主持快照可恢复，另一玩家不可见。主持“准备战斗动作”从
  原请求填表、不执行；明确提交携带 `cause_id`。关联的类型化请求不能静默
  改动作/目标/遭遇。角色候选用实际参战投影，避免选到场外 NPC。
- 结算后根据真实结果显式收尾请求，不从一句叙事推断成败；拒绝后可重申报，
  界面不继续显示假的“等待审核”。自由复合意图原先不自动收尾的契约未放松。

17项新增后端对偶、扩大定向47项/117子项通过；最终后端整仓1765 passed /
8 skipped /154 subtests（139.84秒），前端1226/116文件。最终新增布局及真实
认证三客户端2/2（1.7分钟）：玩家按钮申报→持久化原请求→脚本化辅助建议→
主持刷新审批→双方参与/防御/确认→实际骰点/结果→主持明确收尾→合法结局→
各账号保存。脚本调用者不等于真实模型，不作 Agent 判断稳定性证明。
日志 `test-results/keeper-platform/combat-declaration-{backend-final,front-final2,browser-final}.log`，
当前18文件指纹 `combat-declaration-final-fingerprint.txt`；ruff/架构/diff/构建通过。
同一源指纹下既有五条战斗/PvP/只读/结局/结果展示5/5通过（31.9秒），日志
`combat-declaration-existing-screens-final.log`；本轮7项专项，不称完整浏览器套件。

本轮暴露并修掉自己的问题：① 新能力漏进严格快照 schema，全量11项红，补正
字段而非放宽校验；② 弹窗嵌战斗卡被聊天 stacking context 覆盖，改 body portal；
③ 紧凑弹窗白名单/按钮尺寸漏接，补44px和短窗内部滚动；④ 两个 select 的
准确标签不稳定，补显式可访问名而非宽松定位。旧“结算后所有按钮为0”断言
改为旧响应按钮为0，因为当前轮的申报入口现在合法存在；骰点/结果/资源/
权限断言全部保留。原失败日志保留，不把早期失败拼成完整通过。

视觉复用已有 imagegen 文件夹与固定九切边缘，不为纯表单排版重复生成纹理。
实际界面 [939](combat-declare-939.png)、[390](combat-declare-390.png)、
[短窗口](combat-declare-390-short.png)，1280/939/640/390和390×360均已实际查看。
标题、选择控件、按钮以 DOM 呈现，选择项和按钮均验中心命中/44px，按钮
验 padding/nowrap，不强点、不扩大超时、不用 skip 掩盖。

最终真实三客户端和布局联合：`test-results/keeper-platform/case-closure-real-ui.log`，
2/2、2.1分钟，通过真实认证与本地隔离后端；三客户端本身1.8分钟。
四宽度及390×360布局替身与真实权限验收分开声明，截图已实际查看。
后端角色保存/结算/战斗/收尾事务定向57/57；整仓1714 passed /8 skipped /
148 subtests，174.16秒；前端全量1216/115文件。类型、格式、构建、ruff、
架构、diff与开跑后的源指纹复核通过。整仓日志 `case-closure-backend-full.log` /
`case-closure-frontend-full.log`；保留之前143中断和测试夹具修正前失败日志。
本页不代表 C5 或整个目标完成，不构成提交/推送/发布授权。

### 调查员间对抗（2026-10-07 最新增量）

`pvp_flow.py` 接入六命令原有事务，不改成关键词执行：攻击方明确参与→
目标方明确参与→目标选防御→目标准备掷骰→攻击方准备掷骰→统一计算。
第一份准备没有骰点/结果，不先公布一方骰运来影响另一方决定；第二份提交
才调用共享规则 RNG，资源/私密结果/outbox 一起落账。每个 nonce 只接受
该角色现行控制者，最终复核另一方访问和控制权，条件改变拒绝；取消允许
解除等待、零 RNG/零资源。原自由行动绑定仍保留，不自动关掉复合请求。

真实三客户端 `pvp-real-3p-current.log` 1/1、2.0分钟通过：实际选角/先攻、
双方响应、第一准备 HP 不变/无 result、刷新恢复最后确认、两人各收私人
结果、主持结束遭遇，后续合法结局和各自另存卡链保持。无模型、无改 DB。
替身界面 `pvp-layout.log` 5/5，四宽度+390×360按钮44px、内边距/真实命中/
无溢出；[939](combat-pvp-939.png)、[390](combat-pvp-390.png)及
[短窗口](combat-pvp-390-short.png)主代理已逐张查看。复用现有 imagegen
档案夹1×/2×与九宫格材质，不把概念图文字当作游戏事实。

真缺陷与失败记录保留：旧 ID 兼容层每次强行重写响应者为 actor，已改成
保留明确 stable 响应者、只对缺失旧载荷推导，旧 pc 别名仍升级；旧结束
条件把“无NPC”当胜利，已支持纯调查员遭遇继续/自然结束。首轮浏览器因
测试新变量未定义中断（不是产品断言）；第二轮真实抓到错误提前结束，
修复后不放宽原结束命令断言才转绿。日志 `pvp-real-3p.log` /
`pvp-real-3p-fixed.log` / `pvp-real-3p-current.log` 分开保留。

当前领域+旧combat/事务定向63项、接口/旧规则56项+113子项通过；最终整仓
后端1730 passed /8 skipped /150 subtests（157.59秒），前端1218/115文件；
类型/格式/构建/ruff/架构/diff与源指纹复核通过。日志 `pvp-current-backend-full.log`
及`pvp-current-frontend-full.log`；先前1729/8是结束条件修复前版本，未混作最终。
待补：assisted 草稿的嵌套战斗执行库存桥/等待屏障（代码复核发现外层只对
直接六命令做库存同步，不能以直接路径绿灯证明草稿批准路径）；作者效果、
真实模型、完整浏览器与战斗后恢复专项仍待。未提交、推送或发布。

### 已关闭：辅助草稿的共享执行与审阅界面

上述嵌套桥/屏障待办由本节关闭。`execution.py` 是直接命令与草稿的共同
领域执行边界，复用同一库存/角色名册同步、私有状态事件与游戏终态门禁。
审批是原有短事务/CAS/outbox内最多12条的原子批次，不变成静默执行一半。
产生玩家决定/待骰后若还含移动/时间/终局或 completed 收尾等命令，整批
拒绝并保留草稿；可先执行准备并正常叙事/await，之后由本人响应。
嵌套结局后也不能继续推进世界；已完成奖励、请求与收尾按统一结局契约处理。

新增18项对偶（与旧Agent/战斗联合63项）覆盖：合法草稿→等待→本人掷骰
扣一次弹药；NPC对无控制调查员的既有自动防御结算同步名册/私有事件；
跨等待/终态拒绝、outbox整体回滚、旧回执撤权拒绝、玩家不能批准。
这没有把“缺少库存桥”笼统说成NPC无限弹药或实际漏扣：证明的是准备时
读取稳定持有物、嵌套结果同步和后续本人响应资源一致性。

辅助建议在发布前验证格式/schema/12条上限，并排除不可委托的玩家响应、
控制权与人类裁定。畸形对象、缺载荷、坏枚举/过量不会让循环崩溃或投递一
条被前端丢弃的坏草稿，而对原请求可见暂停；canary证明不反射私密模型原文。

`DraftCommandPreview` 以共用字段表与当前授权投影展示名字/枚举/全部参数；
按人物/场景/物品等命名空间查名字，同 ID 不串名。零值/否值不丢弃，未知
字段保留，原始 JSON 可展开且不改变载荷；HTML文本不执行。草稿审批/撤权
原回归保留，新增4项审阅用例。手机批准/拒绝满宽44px，标题20px，数值是DOM。

最终三客户端 `assisted-draft-real-3p-final.log` 1/1（1.7分钟）：**脚本化 caller**
产生建议，真实 DB 持久化，刷新恢复，主持按真实审批按钮，然后两名玩家
确认/防御/掷骰；不是调用云端模型、也不是以假WS替代批准事务。原自由意图
由人类明确收尾，保留“单次战斗不推断整条复合意图已完成”；结案、另存、
私密隔离、旁观与房间移交原断言不删。两次初跑失败分别是新申请未写主持
收尾造成严格选择器匹配两条开放线程，以及测试忘记在刷新后展开房间栏；
补实际用户操作，不修改产品来自动关线程或强迫浮层保持展开。

前端初跑全量1项只因原测试期待旧“将执行：move_party”文字；改为中文
操作标题并额外核对完整原始载荷，原批准/拒绝/不假装成功断言保持。
定稿后端1748/8/150、前端1222/116，ruff/架构/类型/构建/diff通过；
最终源指纹 `test-results/keeper-platform/assisted-final-fingerprint.txt`。
完整新版E2E/真实模型/恢复专项仍待，未提交或发布。

#### imagegen：辅助审阅概念与可复用提示词

使用内置 imagegen，以 `combat-pvp-939.png` 为材质参考，生成
[审阅概念](draft-review-concept-v1.png)，项目内已保存，不将其文字当游戏事实。
原图 `/home/inaglyite/.codex/generated_images/01a06c3d-3b8c-7263-82c5-de1bee5f9328/exec-3d05d1a3-e570-42f8-b451-21b243f118f2.png`。
实装 [939操作](draft-review-939.png)、[390审阅](draft-review-reading-390.png)、
[短窗口](draft-review-390-short.png)，主代理实际查看。移动端分别滚动核对
标题命中与按钮命中，不把长卡全图的空白截图当用户真实视口。

```text
Use case: ui-mockup.
Asset type: shippable high-fidelity desktop and mobile UI concept for a Chinese 1920s Call of Cthulhu tabletop game.
Primary request: a human Keeper review sheet for AI-proposed commands, before any action executes.
Input image 1 is only a material/style reference: preserve its flat illustrated archival folder tabs, quiet brown surfaces, thin worn brass ink edges, pale warm readable text. Do not preserve its combat content or turn number.
Layout: on one board show a wide desktop review card and a one-column mobile review card. Header '主持建议' with olive '尚未执行' stamp. One brief narrative paragraph. Beneath it two clearly separated command summaries: '准备战斗行动' with readable investigator/target/type values, and '等待玩家回应' with explanation. Each summary offers a collapsed '查看原始参数' detail. Footer '批准并执行' and '拒绝草稿', equal generous touch targets. Footnote '批准准备动作不代表已命中；调查员仍需自己确认与掷骰。'
Composition: calm, flat 2D game interface, implementable as DOM. Use a warm paper slip inset into the dark folder for the reviewable action list, keep all text areas plain and high contrast. Desktop horizontal footer; mobile vertically stacked buttons and natural scrolling.
Palette: dark brown #211a14, pale parchment #d1bf9b, dark ink #312a21, worn brass #c9b77b, muted olive #85906c. Restrained Chinese serif heading and legible body type.
Constraints: no photoreal desk, no 3D leather, no metallic glare, no neon/glow, no tiny dense JSON block, no charts or dashboards, no story images, no automatic execute controls, no player secrets. Generated text is a design reference only; actual data and authorization remain code and DOM.
```

## 要达成的体验

人类主持无需模型/Key，可以发起遭遇、主持NPC行动，玩家用按钮申报攻击、
防御和掷骰；工具负责行动顺序、资源和伤害，主持负责合理性、叙事与收尾。
单人、多人以及Agent/assisted主持共用同一个命令与状态域。
自然语言仍可表达自由意图，但不是确定性状态改变的入口。

战斗是可收起的遭遇卡，不新增常驻满屏仪表盘。保持档案夹与纸面指南针的
现有生成美术，必要时生成新的二维插画参考/卡片皮肤；文字、按钮和状态保持
DOM，素材不烘焙名字/数值。至少1280/939/640/390和390×360实测，
固定边角不拉伸，操作目标至少44px，减少动态、尊重reduced-motion。

## 分层及必须保留的不变量

1. **规则适配**：复用`gameplay/combat.py`、`endings.py`，不调用旧Engine，
   不调用玩家文本关键词解析。专用模块不把战斗逻辑继续塞进domains.py。
2. **协议/事务**：新增命令、玩家帧、事件、fixtures和权限矩阵必须同批对账。
   使用既有短事务、revision CAS、epoch和outbox；重复提交不重骰、不重扣。
   任何失败不能留下半次伤害/消耗；公开事件不传整个原始combat_state。
3. **玩家执行权**：攻击先申报、主持批准后挂待掷骰；由本人按钮触发执行。
   防御/非敌对攻击确认同样核验服务端控制权，玩家不能替他人答复。
   主持代掷须独立、明确的授权动作和可见记录，Agent不得伪造玩家确认。
   NPC行动可由主持执行，但NPC攻击玩家时必须等待玩家防御选择。
4. **主持与Agent**：都见合法参战候选与当前行动者；已挂起的掷骰/决定是
   停止信号，不得自动循环；读工具结果再叙事，未执行不能演成命中。
5. **结局**：配置结局只按authoritative flags核验，缺条件就拒绝。
   判定前置事实须有显式主持裁定/模组效果通道，不能靠结局工具自填条件。
   战斗未结束不能直接触发结局；终态禁止继续改变玩法状态。
   同ID重试返回原奖励记录，异ID二次结算也不得重复加奖励。
6. **角色生涯**：奖励结算覆盖每个参与调查员，不仅active pc。
   云端不得写本地profiles文件；先在事务内持久化世界结算凭证，再按对应
   账号/角色归属导出或保存长期记录。没有权限的角色不能被主持任意覆盖。
7. **恢复/保密**：重连恢复当前遭遇，读档回滚到存档点，分支复制世界状态
   不复制outbox/控制权；待掷骰和决定必须按现有reconcile契约失效或重建，
   禁止复活已经使用的骰点。角色私密背景、NPC技能表、行动草案仅主持可读。

## 增量里程碑（全部属于本轮，不缩小目标）

| 阶段 | 交付 | 完成证据 |
| --- | --- | --- |
| C1领域基础 | 遭遇开始、规则执行、私人决定、遭遇结束、结局校验及脱敏投影 | 纯领域对偶；旧combat/endings不回归 |
| C2协议与存储 | 帧/命令/事件、权限、玩家掷骰、事务/幂等、快照/投影与恢复 | schema正反fixtures、独立DB连接故障/重放测试 |
| C3人类主持界面 | 场景在场参战者选择、当前行动者、玩家行动/防御/掷骰、终局与奖励展示 | 无Key真实后端三客户端闭环、单人、四宽度/短窗截图 |
| C4Agent harness | 同一工具目录、上下文与停止信号，assisted草稿可批准/拒绝 | 脚本化只作为接口测试；真实模型独立授权后验收 |
| C5完整收口 | 战斗→状态效果→合法结局→奖励→读档/分支 | 新结构化全流程 + legacy猩红文档技能主线 + 全量门禁 |

命令候选为combat_start、combat_action（准备/执行必须分清）、combat_decide、
combat_end、end_game及有来源依据的状态裁定。具体帧名与枚举以C2唯一schema
为准，不把本表当成已经发布的协议。原17条命令和旧世界保持兼容。

## 历史实现记录（完成度以顶部为准）

C1在开发：新增`src/structured/combat_endings.py`独立基础，直接复用旧规则。
当前刻意**没有**注册进service/schema/Agent目录，避免开放没有玩家控制权
与掷骰等待的半成品接口。已覆盖先攻、伤害、错行动者/倒地拒绝、非法参战者、
重复遭遇、非敌对确认与取消、私发投影、输入边界、战斗/结局前置与二次结束。
奖励的纯状态计算已落码（见下），正式事务/协议注册、终态服务层门禁、
快照、前端与真实模型仍未实现/验收。

旧战斗`_public_state`并不满足结构化多人保密要求：包含技能、参数和个性化
roleplay_context。因此C1事件和回执采用独立白名单，不直接复用原始公共输出。
旧`settle_case`即便persist_profile=False仍会装配RuntimeContext；C2必须提供
纯内存/数据库结算入口，而不是在事务里触发默认本地环境和角色文件写入。

最新已完成的界面基线为阶段55：前端1181项，完整浏览器74/2，非本扩展验收。
新增领域测试不能冒充完整战斗/结局链路完成。真实模型额度未随“2”自动授权。

C1首次验证：`python -m pytest tests/test_structured_combat_endings.py
tests/test_combat.py tests/test_endings.py -q`：43 passed（其中新文件15项）；
ruff check/format、架构和diff检查通过。本次未改模组、未调用模型、未写真实存档。

### 结局奖励的事务内计算基础

新增`gameplay/case_settlement.py`：不创建RuntimeContext、不读取或写入profile；
复用现有声望映射，按稳定调查员ID逐人计算HP/SAN差值与career，先验证全部
角色再落到工作副本。case_settlements凭证以世界/结局为键；同凭证返回旧值，
凭证丢失但career已有case_id时也不重复增加声望。active pc为权威时同步名册
career；不把全局revealed NPC关系擅自写进每人的私密联系人。

finish_game基础函数在成功计算后才写game_over；game_ended只含公开结局，
case_settled逐人私发且主持可见，不公开生涯。尚未接service、schema或快照，
不是已可用协议；个人角色库的授权导出/回写仍待实现。

新增14项奖励对偶，与战斗/旧结局联合57 passed，exit 0
（`/tmp/trpg-structured-case-foundation.log`）；ruff、格式、架构与diff通过。
包括重试不重复奖励、两人差值与秘密隔离、损坏名册/凭证拒绝、后一个角色
失败时前一个不落半份奖励，以及RuntimeContext/profile读写不得调用的断言。
没有把纯状态测试冒充数据库提交故障、重连或浏览器验收。

### 玩家战斗等待链基础

新增`structured/combat_flow.py`，尚未注册外部协议。主持prepare动作时在一次性
工作副本上调用既有规则，骰源遇首次掷骰即停止；合法动作生成待掷骰状态，
不消费真实RNG/弹药/HP。非敌对确认和NPC攻击玩家的防御门仍由旧规则生成。
玩家选择会掷骰的确认/防御后仍要显式响应roll，Agent/keeper/viewer及其他
控制者均被拒绝；条件指纹变化拒绝旧roll，但本人仍可取消以避免悬空。

实际roll在另一工作副本上完整计算，成功后才替换调用方状态；异常不留下
半份伤害/消耗。完成后旧roll_id失效，结束遭遇清掉旧roll。正式帧重发的幂等
回执、数据库故障、控制权DB复核、读档失效及多人玩家间对抗的双方授权，仍
由C2后续实现，不能以工作副本单测替代。底层execute_combat_action不会作为
模型可直接绕过等待的命令注册，外部combat_action只接prepare。

新文件14项等待/授权对偶，与已有基础及旧战斗/结局联合71 passed、exit 0
（`/tmp/trpg-structured-combat-wait-foundation.log`）；ruff/format、架构、diff通过。
包含NPC攻击→玩家防御选择→待骰→执行及中途RNG故障不修改真实工作副本。
本阶段没有界面、没有真实数据库提交证明、没有真实模型调用；均继续待办。

### C2：命令服务事务接线（未开放协议/界面）

`StructuredPlayService` 注册六项战斗/结局处理器。其中 `combat_action` 只接
prepare，`combat_decide`/`combat_roll` 限玩家响应；不能直接调用内部执行器
绕过等待。玩家响应及旧回执重放都先重新查询成员/调查员控制表，拒绝伪造
控制角色列表与已撤销授权；战斗回执另绑定原操作者。主持命令复核当前控制权。
结局后的新游戏状态变更拒绝，但已提交的同一结局命令仍可返回幂等回执。

新增 `tests/test_structured_combat_transactions.py` 七项真实临时数据库测试，
独立连接证明：准备零 RNG；roll 重发不重骰/重扣；伪造与撤销控制权拒绝；
主持不能代替玩家 roll；outbox 故障时 HP/弹药/状态/命令/事件一起回滚；
结局回执重发不重复奖励；结局 outbox 故障不留下奖励或 game_over。
七项定向通过，ruff 与架构门禁通过。未调用模型、未读写真实存档或生产。

仍待 C2：正式命令/事件 schema、网关玩家身份路由及 Agent 再调度、快照角色
过滤、背包稳定 ID 与弹药标签同步、读档/分支待骰失效、多人对抗双方授权。
因此这些处理器当前只是服务层接线，不代表客户端或 Agent 可用；后续也需
覆盖结局后的玩家请求入口，不能仅凭命令门禁声称全世界已冻结。

### C2：正式 schema / 网关 / 快照已接线

2026-10-06：新增六类严格命令及六类事件，各有官方正例 fixture，权限矩阵
同步。Agent 可见目录与 schema 字段对账通过，但新增域的运行调度尚未
完成，目录存在不等于 Agent 全链可用。服务层程序化新命令也过同一 schema。
网关把 combat_decide/combat_roll 解析成玩家身份，不误要求主持权限。

快照提供脱敏公共战况、本人的待骰/决定及奖励；主持可以读取私人记录，
其他玩家不能读取。新事务用例对实际事件/快照作 schema 校验，并覆盖
八类异常载荷拒绝与真实网关受理。结局后新行动、failed 行动重发以及旧
检定的新结算均拒绝，已提交回执仍允许幂等返回；普通自由骰不改变世界。
另堵住主持在 NPC 攻击里夹带 defender_choice 替玩家选择的绕过路径。

终态本地回归：结构化全部用例 + 旧战斗/结局，368 passed / 1 skipped /
108 subtests passed，exit 0（`/tmp/trpg-structured-combat-protocol-final.log`）；
ruff、架构、diff 通过。未改前端，不把旧前端验收当成新协议适配已通过，
没有真实模型调用或发布。

继续待办：前端事件白名单/解析/store/UI 与正式能力暴露，Agent 响应后调度，
背包稳定 ID 与弹药标签同步，读档/分支待骰失效，多人对抗双方授权及结局
前置事实的可审计裁定通道。C2 不能据此整体关闭，目标继续。

### C2：弹药与恢复接线

2026-10-06：战斗开始/动作/玩家响应前，以物品注册表投影角色背包，不能
重新导入已经消耗/转移的旧字符串。掷骰后的确切弹药凭证在同一事务更新
注册表：单把枪保留 item_id；多把相同枪的堆叠只分出实际开火的一把，
其余原堆叠/ID不变。背包与HP/SAN/条件的变更事件分别定向本人及主持。
新迁移也避免把 active pc 与名册里的同一调查员当成两份背包。

真实读档/分支路径保留已提交遭遇与资源，清除待骰及待决定授权，回到
awaiting_action，由主持重新批准；旧按钮拒绝。分支不改变源世界待骰。
选择防御后若还有待骰，快照不把已选防御再次作为可操作选项展示。

新增临时数据库恢复用例两项，事务新增消耗武器不复活、枪械堆叠只扣一把、
active pc 不重复迁移、NPC伤害同步私人状态等对偶。当前相关定向通过，
完整结构化及旧战斗/结局回归见 `/tmp/trpg-structured-combat-recovery-verified.log`。
伤害结算也同步 active pc 的名册镜像，避免主持引用角色表时读到旧 HP。
终态：374 passed / 1 skipped / 108 subtests passed，exit 0；ruff、格式、
架构及 diff 通过。这里仍只是后端恢复/同步证据，不是浏览器操作或真实模型验收。
继续待办：完整前端操作/恢复提示与能力暴露、Agent调度、多人对抗双方授权、
有依据的结局前置裁定、个人角色库授权写回及全链验收。未发布。

### C3：战斗与案件结算界面基础（开发中）

新增 `CombatAndEndingCards`：共用已生成的档案夹九宫格 1×/2×皮肤，
实时名字、状态、数值和按钮全部为 DOM，不把生成图中的文字烘焙进界面。
战斗卡包含轮次、行动者、可收起顺序、本人防御/掷骰按钮；结束后撤除旧按钮。
结局卡展示公开摘要与当前调查员的奖励，不展示其他角色履历；明确个人角色库
尚未自动写回。主持控制台新增四项领域命令，两项玩家响应不进入主持菜单。
快照/六类事件进入统一协议白名单和 typed store；旧 nonce 的结算不能清除新待骰。

新增十四项前端定向测试通过，TypeScript 与构建通过；主持字段与官方 fixtures
等既有定向六十三项通过。浏览器四宽度截图与权限用例正在执行。
以上不替代真实后端三客户端完整战斗/结局验收，不宣称 C3 或整个目标完成。
新命令已经加入结构化世界的服务端能力清单；不影响 legacy 能力。
Agent 停止/再调度与剩余 C2/C4/C5 仍待办。

本阶段新增浏览器验收三项通过（替身后端，仅界面与帧形态证明）：
1280/939/640/390 的按钮高度≥44px、内边距、无换行、真实命中、无横向溢出；
明确分配另一位调查员时无代掷按钮；结局卡窄屏可读、本人奖励与终态展示。
截图 `combat-card-{1280,939,640,390}.png`、`ending-card-{1280,390}.png` 已实际查看。
首轮只读用例失败是夹具身份没有经 WebSocket 传递，现由专用 scenario 明确
分配调查员，保留原权限断言；不能把此替身证明当作云端服务端控制权验收。

全量前端抓到并修复 `case_settled` 新旧协议同名回归：仅无任何结构化信封字段
且显式含 boolean `ok` 的旧回执走旧处理器；结构化奖励入 store，畸形信封不得
降级成“已写入个人角色库”。旧四项结算提示测试保留，新增两项路由对偶。
前端全量1199 passed /114文件（`/tmp/trpg-combat-ui-unit-verified.log`），
服务端能力暴露后结构化+旧战斗/结局374 passed /1 skipped /108 subtests。
后端不是整仓全量、浏览器不是整套E2E，不扩大上述结论。

#### 本轮 imagegen 设计参考与可复用提示词

生成图：`combat-ending-concept-v1.png`（本目录）。工具为内置 imagegen；
参考本地 `docs/screenshots/structured-desktop.png`，仅用作布局/材质方向。
原始生成输出位于
`/home/inaglyite/.codex/generated_images/01a06c3d-3b8c-7263-82c5-de1bee5f9328/exec-d6158e6e-f8ea-43bd-956c-a6baeffb1eb8.png`。

提示词设计要点（用于复现，不是文字像素的正确性验收）：

> 为1920年代克苏鲁跑团平台绘制二维游戏UI效果图，沿用参考中的纸质档案夹、
> 深棕背景、旧黄铜边线和低饱和墨绿状态章，不使用写实摄影、发光金边或满屏
> 仪表盘。在同一图展示桌面与手机卡片：战斗现场记录含轮次、当前行动者、
> 可收起行动顺序与状态、尚未执行的攻击、防御与掷骰按钮；案件结算含结局摘要、
> 本人声望变化和档案提示。留出宽松文字区、清晰层级与大触摸目标，保持柔和
> 对比。纹理只在边缘，正文底色平静。不要把动态玩家名字和数值变成必需素材。

实施选择：沿用已有不同分辨率九宫格皮肤，不裁剪效果图充当真实按钮。
所有权限与动作来自服务端结构化字段，绝不解析图片或叙事关键词。

### C4：战斗等待屏障、恢复调度与回执上下文（开发中）

Agent 命令执行遇到已提交的 `combat_decision_required` / `combat_roll_required`
后，只继续正常消息或显式 awaiting 收尾，不执行同批后续时间推进、其他状态
命令或 completed+success。若模型未自己挂起，runner 将原行动持久化为等待，
以 `wait_combat_player` 停止本次运行；不再多跑模型循环。

网关在玩家 combat_decide/roll 提交后检查权威快照：选择防御但仍待骰时不
调度，待办清空才继续；同 ID 回执重放不重复调度。人类模式仍由既有调度器
返回不启动 Agent。恢复运行的上下文增加按世界隔离的最近八条已提交战斗
回执，让叙事依据真实检定/伤害/取消结果，不从 HP 差值或玩家文字猜测。
这不是长期记忆，也不是执行授权；没有调用付费模型。

新增脚本化与临时数据库用例证明上述屏障、无资源推进、等待叙事仍发出、
恢复调度及重放不重复。与原 Agent 测试联合22项通过，不能冒充真实模型验收。
真实模型调度与叙事正确性、assisted 草稿全链、原行动在执行后的关联收尾
仍须继续验证。C4未整体关闭。

真实人类三客户端浏览器扩展首轮发现：移动后主持候选仍是旧场景 NPC，
服务端正确拒绝参战。现用已支持的 `state_changed.targets` 在移动事务内
刷新候选，公开目标只含 ID/姓名/类型，有 schema 验证，不增加新的文本解析。
浏览器用例继续要求无需刷新就收到正确候选；首轮失败日志保留于
`/tmp/trpg-human-real-combat-browser.log`，修复后复跑中，不提前声称通过。

追加预算对偶：刚好用完命令预算时若已有玩家待骰，仍以正常战斗等待停止，
不降级成预算故障暂停。新增域最终本地结构化全套+旧战斗/结局为
379 passed /1 skipped /108 subtests，exit 0，日志
`/tmp/trpg-combat-agent-targets-final.log`；ruff、架构与diff通过。
移动的目标刷新与时间更新合并为同一 `state_changed`，保留既有有时间移动
的事件数量/顺序断言，不放松原事务测试。

三客户端第二轮：目标刷新已生效；脚本误把 `cancel_violence` 当作非取消选择，
导致合法取消后没有待骰。修正脚本为精确选择公开协议的 `confirm_violence`，
不改产品逻辑、不放宽等待断言。失败证据
`/tmp/trpg-human-real-combat-browser2.log` 保留；第三轮仍在执行。

第三轮真实前端+真实后端三客户端通过（1.5分钟，exit 0）：使用原模组在场NPC
和真实角色卡，无数据库种子篡改、无模型调用；主持准备、非敌对确认、本人
掷骰、刷新恢复待骰、其他玩家帧级私密待办隔离、无角色主持不能代掷、停止
遭遇撤销旧按钮。原私发/检定/SAN/道具/移动/存档/旁观/移交主持用例仍保留。
日志 `/tmp/trpg-human-real-combat-browser3.log`；两轮失败没有删除或记为通过。
此轮不含合法终局前置/奖励/角色库写回，也未证明PvP双方授权，因此不是C3/C5
全收口。完整后端正在跑；不得把379项定向回归冒充整仓绿灯。

真实验收后新增明确缺口：`combat_roll_resolved` 目前只提供 nonce 与响应状态，
人类主持界面缺少真实骰点/结果可读回执（Agent已能读取提交账本）。下一步需
增加有 schema 的脱敏结果投影并同步刷新恢复和两端卡片；不可直接公开旧结果
中的NPC技能值、属性或隐私。结局前置裁定与关联行动收尾继续待实现。

整仓首跑记录：1662 passed /16 failed /8 skipped /145 subtests，失败全部来自
备份子进程通过PATH调用系统Python而缺SQLAlchemy，非新的产品断言失败；
日志 `/tmp/trpg-combat-platform-backend-full.log`。明确加入venv/bin到PATH后
备份18/18通过（未修改任何备份代码/测试），整仓使用同环境继续复跑至
`/tmp/trpg-combat-platform-backend-full-venv.log`。复跑结束前不报整仓绿灯。

正确环境的整仓复跑已完成：**1678 passed /8 skipped /145 subtests**，97.42秒，
exit 0；保留一项既有SQLAlchemy pin manifest删除计数警告，不当作新异常隐藏。
该数字覆盖当前新增域后端代码，不以379项代替。前端本轮仅扩展真实人类E2E，
上一轮1199单测不被写成新版完整浏览器套件；浏览器仍只声明本轮三客户端
扩展通过。未提交、推送、发布、调用付费模型或修改真实存档，目标继续。

### C3：人类可读战斗结算凭证（本地接线）

新增 `combat_receipts.py`：只从工具实际结果提取攻击/防御骰点、层级和伤害/HP，
严格白名单丢弃技能值、属性、策略、原始角色卡；同一事务保留最近20条。
本人及主持分别收到 `combat_roll_resolved.result`，快照按同权限恢复；其他
玩家与旁观者不收私人结果。原事件无 result 仍兼容，schema与官方fixture
同步。前端按 roll_id 去重，换世界清空，展开只读记录不提交命令。

UI沿用本目录imagegen档案方向：安静深棕记录、旧金墨色标题，默认收起，
实时骰点/成败/伤害全部DOM。`combat-result-{1280,939,640,390}.png` 四宽度
已实际查看。替身浏览器4/4通过（26.3秒）；前端全量1201/114文件通过；
后端结构化+旧战斗/结局381 passed /1 skipped /108 subtests、ruff/架构通过。
本阶段不是新的整仓后端或整套浏览器验收，1678旧全量对应上一阶段。

真实三客户端扩展增加：主持与行动者显示实际骰点、其他玩家无私人记录、
刷新仍保留记录。首跑进程退出143（SIGTERM）、没有断言失败结果，不算通过；
原因未确认，日志 `/tmp/trpg-human-combat-result-browser.log` 保留。
单独复跑 `/tmp/trpg-human-combat-result-browser-alone.log` 当前正在执行。
原freeform复合意图不会仅因一次攻击就自动判为完成；关联收尾须继续明确
主持裁定边界，不加入玩家文本关键词猜测。结局/生涯全链与双方授权仍待办。

### C4：原请求因果关联（不是自动收尾）

新增 `combat_requests.py`：只按同世界、同调查员、非终态 action_request 的
稳定 ID 关联。准备/非敌对决定/待骰链内部保存关联，玩家不能用响应帧 cause_id
另指定请求；实际响应回执账本保存 source_request_id，网关恢复运行时使用
核验后的原请求而非空触发。原请求已终态、角色不符或ID不存在时不绑定。
不将其加入公开结果字段，不靠玩家文字匹配，不自动关闭自由文本复合请求。
人类主持仍可显式 resolve_intent；Agent提示明确完整意图与单次攻击的区别。

两个新增临时数据库对偶：原攻击+后续调查仍保留queued、另一玩家cause不抢
关联；错调查员cause不绑定。与Agent/结果测试联合9/9通过，ruff/架构/diff通过。
本阶段后端整仓使用正确PATH正在复跑，新结果不能沿用1678旧数字。

运行环境重置后，上述/tmp复跑日志和原句柄不再可用，未据此声明通过。
从当前代码重建并单独运行真实三客户端，已1/1通过、1.5分钟、exit 0；记录于
工作区 `test-results/keeper-platform/combat-result-real-3p.log`，包括本人/主持
实际骰点、他人无私人记录、刷新恢复。之后的因果关联增量由新增对偶及整仓
复跑验证，不冒称其真实模型链已验收。付费模型与正式环境均未调用。

本阶段最终可读日志：后端整仓1682 passed /8 skipped /145 subtests、exit 0
（`test-results/keeper-platform/combat-request-backend-full.log`，101.68秒）；
前端1201 passed /114文件、exit 0（`combat-result-frontend-full.log`）；
原请求提示词增量之后重跑Agent/目录21项通过（`combat-source-prompt-regression.log`）。
架构/ruff/diff通过。该轮真实三客户端在关联增量之前通过，关联增量由上述
数据库对偶和整仓证明，不跨版本拼成真实模型全链通过。

#### 下一步 C5 的授权边界

当前 `record_fact(source=ruling)` 只记录文本，不更新模组结局所需 flags；
因此不能用它或直接数据库改值冒充合法终局。下一步提供可审计的**人类主持
裁定**通道：只操作模组已声明的状态键，明确依据、操作者与前后值，复用
CAS/短事务/outbox/幂等，玩家与Agent不得自行调用人类裁定命令。
Agent应走作者声明效果的工具，不得到一个无限制state_set来补结局条件。

角色生涯保存也必须是所属玩家的显式动作：世界内结算凭证是来源，不能
让主持覆盖他人的角色库；原角色库条目变化时应版本冲突或另存，不静默覆写。
保存/导出入口要同时覆盖本地与云端，并验收终局后查看、存档恢复、分支、
重复点击不加奖励。这两项未实现，不声称完整无Agent模式已齐全。

### 已实现：人类裁定与结案副本

上述“下一步”由本节部分实现取代。`structured/rulings.py` 提供严格的
`record_ruling{flag_id,value,expected_before,basis}`，当前人类主持才可执行，
值类型与旧值精确匹配，只有模组/权威状态已声明的键；没有通用 state_set。
`ruling_recorded` 与 `keeper_rulings` 快照只给主持。控制台按类型录入，读取
旧值不会提交，“准备结算”只填已有结局 ID，不自动执行。

`structured/case_characters.py` 的三个 `/api/character-library/from-case`
入口提供 preview、显式保存、export，身份由现行 Session/本地信任路径取得，
不采纳客户端卡面/奖励/owner。每次重试重新核控制权；预览返回 revision 与
receipt_digest，保存新副本和内部审计账本在同一事务；同凭证重试不覆写，
主动删除后不悄悄复活。预览/导出不写世界或角色库，导出迟到也不跨账号、
服务器、世界、角色或案件触发下载。

新结算凭证包含私有白名单 `character_snapshot`，固定结案时卡面，防止旧
案件奖励与后来分支角色混用。旧凭证缺快照时仅允许最新结案回退，否则明确
拒绝。缺少属性/职业不补造；超大小不截断履历。角色库新冒险仍采用既有建卡
推导值，结案 HP/SAN/伤势另附 `trpg_case_record.final_state`，保存不等于治疗。

新增保存边界包含两连接并发、撤权、删除、凭证变化、存储故障整体回滚、
缺属性不补造及旧凭证对偶。真实三客户端两名玩家各自保存/刷新/核对账号
角色库，主持不能代存；这不替代本地完整新建或战斗后恢复浏览器专项。

`case_closure.py` 复用已有 `check_cancelled` / `action_status` /
`interaction_updated` / `keeper_draft_resolved` 契约，在结局事务内收尾。
只取消剩余事项，不把复合请求标为“全未执行”，不改已完成回执、不生成骰点，
保持检定原可见范围并解除孤注一掷等待链接。outbox 故障使结局、奖励及待办
一起回滚，结局重放不再取消一次。结案后拒绝新辅助草稿；前端拦新玩法请求
和主持状态变更，但收尾发言/记忆及旧回执重发仍允许。
真实三客户端额外挂起正常叙事线程及待检定，再合法结案：实时取消卡与
刷新均不残留待办，`check_resolved` 没增加；没有仅通过隐藏卡片绕过后端。

#### imagegen：结案保存界面参考与提示词

[生成概念图](career-save-concept-v1.png)（内置 imagegen）仅用于二维档案夹与
纸面布局；实际状态、姓名、按钮是 DOM，不采用图里的虚构日期/剧情。
原图：`/home/inaglyite/.codex/generated_images/01a06c3d-3b8c-7263-82c5-de1bee5f9328/exec-13cb4e0b-a338-445c-b0e6-8eed83ff79d4.png`。
实装：[939宽度](career-save-939.png)、[390宽度](career-save-390.png)、
[保存完成](career-save-complete-390.png)、[短窗口滚动](career-save-390-short.png)。

```text
Use case: ui-mockup. Asset type: high-fidelity UI concept for an existing 1920s Chinese Call of Cthulhu tabletop roleplaying platform, desktop and mobile variants on the same board. Primary request: a readable post-game '案件档案' panel and explicit '保存为新角色' flow. 2D illustrated folder tabs, quiet dark brown paper surfaces (#211a14), aged brass ink (#c9b77b), pale warm text (#e5d8b8), subdued olive success stamp (#8b9b72). Flat game interface, not a photographed desk or realistic 3D leather. Keep center text areas plain and readable; distressed material only near edges. Layout: folder card with case title '逃离阿卡姆', outcome summary, current investigator name, reputation and completed cases; beneath it a small archive sheet '保存角色生涯' explaining '只保存你自己的角色，原卡不会被覆盖。' Show a name input and two clear controls '保存为新角色' and '导出角色卡', plus a quiet completed save state. Show a collapsed case history section rather than always-open dense lists. On mobile use one column with roomy full-width touch targets and natural scrolling. High-fidelity implementable DOM layout, subtle borders, restrained typography, no neon, no glowing gold, no lens blur, no generic analytics dashboard, no avatars or story imagery unrelated to the task. Text in image is a design reference only; real app text and data will remain DOM. Visually express the existing archival folder and printed compass graphic design, not photoreal objects.
```
