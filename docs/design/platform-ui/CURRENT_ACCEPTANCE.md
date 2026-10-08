# 当前范围验收表（无模型全量通过，真实模型待验收）

目标：不依赖 Agent 的主持路径、云端单人/多人界面，以及贴题、舒适的生成美术与可操作布局。下面不是发版批准；正式环境没有用于本轮测试。

## 当前验证版本：7ba1155c（实验分支，未发布）

7ba1155c继承88021898的产品代码，后续只修CI测试解释器选择；其后的文档提交未改产品/测试/协议/模组。
88021898本地完整浏览器验收已终态：**96项收集，94 passed /2 skipped /0 failed，28.1分钟，exit 0**。
日志 `test-results/keeper-platform/transfer-full-browser.log`，末行 `ACCEPTANCE_EXIT_CODE=0`；
源代码（含e2e/测试/协议/模组）与构建产物的跑前指纹 `transfer-full-source.sha256`、
`transfer-full-dist.sha256` 在终态均逐项核对通过，受测产品代码没有中途变更。
两项跳过是外部staging与未授权真实模型，不计通过。本次包含Electron联机/源码后端、
人类主持过渡回合、NPC/场景转交及战斗/结案生命周期六项；不是多个版本的专项拼接。

同轮猩红文档真实三客户端主线 **18/18、3.1分钟、零模型、gaps=[]**：
`scarlet-human-mainline/1791466037473/`。独立复核终态14个主线flag为true、
`truth_and_seal`及两名调查员各自结案账本；玩家A/B的私有进度/记忆/结局条件事件均为0，
快照不含主持私有进度。主线不冒充付费Agent验收，NPC反击的人工主持裁定与正式战斗命令
各按测试实际路径声明。测试改写的旧脏截图及他人的生命周期证据已恢复跑前副本；
本轮新生成生命周期证据另留 `transfer-full-generated/keeper-combat-lifecycle/`。

产品冻结点88021898已推送实验分支（其后9ea81ce8只更新验收文档）。quality运行
[37783505206](https://github.com/Inaglyite/TRPG-Master/actions/runs/37783505206)
已终态：后端成功、前端E2E失败（89通过/2失败/2跳过/3未执行），不是CI全绿。
失败日志已取证：两处验收脚本写死仓库`.venv/bin/python`，CI使用PATH Python，
分别在双服务器启动与归档后只读查库时ENOENT/进程status=null。其他单测/格式/构建均成功。
原日志摘录 `ci-transfer-failure-excerpt.log`保留，不把失败归因于“机器慢”。

已补共享测试解释器选择：显式TRPG_E2E_PYTHON优先（错误不静默换解释器），
其次真实`.venv`/`venv`，否则PATH `python`；同一用例的启动与只读证据使用同一选择。
在独立源码副本`/tmp/trpg-ci-python-QYesid`（没有两个virtualenv目录、没有配置覆盖）
同版双向复测：修前 **1通过/2失败/3未执行，exit 1、1.6分钟**；修后
**6/6、exit 0、2.6分钟**，原服务器切换/账号隔离/归档数据库断言未放宽。
日志`ci-python-before.log`、`ci-python-after.log`及各自trace/截图保留；修后副本与
工作区三个脚本逐文件SHA256相同。新6项单测纳入前端全量 **1337/129文件**（29.08秒），
类型/整仓格式/ruff/架构通过。只改验收脚本与测试，没有改产品源码/构建/后端/模组；
88021898的本地完整96项仍是其冻结测试版本，不将修后6项拼成新一次完整运行；
7ba1155c新版的完整运行证明来自下面的单次远端CI，不是多个专项的数字相加。
修复版本7ba1155c的远端quality运行
[37789025824](https://github.com/Inaglyite/TRPG-Master/actions/runs/37789025824)：
backend已终态成功，原始pytest日志为 **1981 passed /165 subtests /1 warning，868.13秒**，
没有跳过；摘要`ci-7ba-backend-summary.log`。frontend已终态成功：**1337单测/129文件；
完整96项E2E为94 passed /2 skipped /0 failed，27.3分钟**。两个job均completed/success，
整个quality已终态success；前端摘要`ci-7ba-frontend-summary.log`。
同次CI原两个失败spec及原来未执行的三个服务器边界用例全部通过，猩红人类主线2.9分钟通过；
仍只跳过外部staging与未授权真实模型，不能把CI绿色解释成模型验收或部署批准。
另外在7ba1155c独立源码副本、一次性本地PostgreSQL 17中从空库迁移到0019，
六项PostgreSQL集成及记忆幂等约束共 **7/7、2.09秒、exit 0**；
日志`postgres-7ba-targeted.log`。这是补充真实数据库证明，不与1973相加伪造一次本地全量。
数据库只绑定127.0.0.1，使用测试账号与独立运行目录；测试后移除本轮容器及临时库，
没有连接或改写现有数据库。后续文档提交与7ba1155c的产品、协议、测试、模组差异为零；
本页明确区分实际绿色的受测SHA与文档记录SHA，不宣称未来文档提交的CI也已完成。
无master合并、部署或生产测试。
共享规则变更要求的真实模型主线仍待本轮独立授权，因此整体目标尚未关闭。

## 当前增量：物品转交

后端持有者存在性、拆分来源保留、定向背包回执已修。新增11项对偶；
相关92项通过、战斗库存/事务/协议组合53项及128子项通过（两组不相加计数）。
ruff/架构/diff通过；旧版本独立内存探针证实不存在NPC/丢来源/公开背包三个缺陷。
日志 `test-results/keeper-platform/transfer-backend-third.log`、`transfer-backend-combat.log`、
`transfer-baseline-probe-fixed.log`。测试方法名/文件路径错误日志保留，未算通过。
主持私有 `keeper_progress.holdings`、NPC/场景候选、选物品带入来源及实时/刷新已接线；
转交不授予新线索知情，“使用”只列所选调查员持有物，未扩大玩家权限。
当前后端全量 **1973/8跳过/165子项**（169.52秒），前端 **1331/128文件**（最终25.90秒）；
类型/构建/ruff/架构通过，整仓格式检查已修两个旧测试文件，AST前后相同。
最终联合浏览器 **2/2、2.4分钟、exit 0**，源/dist终态核对一致：
`transfer-browser-fourth.log`；真实3p环路含NPC/场景转交、玩家帧隔离、刷新及原战斗/结案。
布局专项是替身；1280/939/640/390及390×360的44px/内边距/nowrap/实际命中均通过。
已查看[939实际布局](keeper-transfer-939.png)、[短窗口](keeper-transfer-390-short.png)。
第一次两项失败是替身投影接线/关闭了后续要读取的主持台；第二次布局仍缺角色接线，
真实3p通过但整组不算绿；第三次布局抓出34px真实产品缺陷，补44px后联合重跑2/2。
失败/trace保留，未加skip/retry/force。终态后仅格式化两个未被该专项调用的测试文件，
AST不变并重跑前端全量；产品源码与dist未变。完整96项终态见顶部，不再以专项代替全量。
视觉沿用已确认的生成档案夹，不为现有表单新增装饰位图；付费真实模型仍待独立授权。

## 上一冻结版本（b6cf6fee）

2026-10-08 当前增量：Kimi 的独立无模型猩红主线 18/18 暴露 G1–G5，不视为实物工具链完整通过。
已补主持私有模组线索目录/案件时钟、显式发现与实物取得、实际持有物品的作者效果、已访问场景记录及原件出示候选。
当前冻结源码后端 **1961 passed/8 skipped/165子项**，前端 **1324/127文件**，构建/类型/格式/ruff/架构通过。
实际三客户端主线 **18/18、2.8分钟、零模型**（`discovery-scarlet-ui7.log`），含玩家出示真实原件及不转交/消耗，封印不走flag裁定兜底。
完整95项已终态：**93 passed /2 skipped /0 failed，26.0分钟、exit 0**，`discovery-full-browser4.log`。
源/dist（含e2e和模组）终态指纹一致；外部staging/未授权模型两项跳过不计通过，不是专项拼接。
第二次因会话中断失去进程，只有30项进度、无终态；第三次后台启动未存活，不计通过。
旧缓存脚本完整运行已终止（51过/1失败/1中断/1跳过/41未执行，exit130），失败是错误分类定位；日志与独立证据保留。
真实模组首轮抓到新投影误读 `requires_flags` 列表导致主持 WS 报错，已修并补直接使用作者数据的回归；失败证据保留。
模组正文未修改，信息发放不等于取得原件；缺少物品绑定的作者效果由人类主持选择并记录依据，Agent 不获此覆盖权限。
新增原生图像参考 [主持档案参考图](keeper-progress-concept-v1.png) / [完整提示词](keeper-progress-prompt-v1.md)，内容只作布局示例。
最新范围、残余与原始日志见[人类主持发现收口](SCARLET_HUMAN_DISCOVERY_ACCEPTANCE.md)。
此版本曾证实主持候选遗漏NPC库存、转交接受不存在的NPC目标；隔离临时库诊断
`transfer-holder-audit.log`保留。两项现已由顶部转交增量修复，不再是当前未修问题。
中文开发检查点：`2fd6bbeb`（后端/协议）、`78102269`（前端）；提交不代表发布或Agent验收。
完整运行中的猩红主线再次18/18、零模型、无缺口；证据 `scarlet-human-mainline/1791457936376/`。
测试重渲染的旧脏截图及Kimi生命周期证据已按跑前保护副本恢复，不混入本次提交。

## 阶段历史（以下进行中与未提交声明保留当时含义）

上一滚动边界版完整 94 项已终态：**92 passed /2 skipped /0 failed，23.2 分钟、exit 0**。
证据 `test-results/keeper-platform/root-scroll-full-browser.log`，源/dist 终态核对通过。
这是新 G1–G5 改动之前的版本，不据此宣布当前增量已完成。下面各次失败/修复记录为历史，不再是当前“正在运行”状态。

当前滚动修复版：同版组合10/10（4.5分钟，exit0，源/dist终态一致），前端
1307/125文件（22.17秒），类型/构建/格式/ruff/架构/diff通过。后台源码/schema/
测试指纹与1931/8跳过/162子项时相同；未重跑后台，不借此声称新后台验收。
组合覆盖本地真实无Key创建/限频/原ID重试、四宽度及390×360、紧凑弹窗五项、
草稿审阅、真实三客户端原战斗/结案/私发/正式检定及云端单人/过期/归档。
flow-root消除84px父margin穿透，待办scroll-padding-block=4px解决贴边；
新断言要求根节点无偏移/滚动/额外高度，整个按钮位于待办区内，且保留原
44px/内边距/nowrap/中心命中。修前反例/两次失败日志保留，不减断言。
已看[939](keeper-dice-rate-939.png)、[390短窗](keeper-dice-rate-390.png)，只修DOM/
滚动边界，沿用生成档案夹和纸收据，不新增装饰图。单项1/1（19.4秒）有独立
日志，不拼接为完整通过。修后完整94项正在跑，`root-scroll-full-*`。
下面的91/2跳过/1失败是原全套，不被组合10/10抹去；目标与发布尚未完成。

最新全套不是全绿：94项终态91 passed /2 skipped /1 failed（25.0分钟，exit1，
源/dist一致）。本地人类主持的限频重试按钮中心没有命中，trace证实body
被滚了84px、按钮在固定顶栏后；不是掷骰机制失败或用例超时。早段失败曾
被进度尾读漏掉，现按终态更正，不采用旧专项4/4代替此次全套失败。
新增root/app偏移、滚动区全边界断言；修前稳定app.top=84。
布局flow-root修后10项组合9/1（4.7分钟），原根偏移消除；新增全边界又检出
0.3125px贴边，正在补滚动焦点留白，未放宽原断言、不加retry/skip/强制点击。
后端1918之后的1931证据：后台src/schema/tests指纹未变；当前前端1307通过，
类型/构建/格式通过。原失败证据及trace保留，目标仍未关闭。

2026-10-08 普通骰限频补齐：13项新对偶、相关36项；后端1931/8跳过/162子项
（219.60秒），前端1307/125文件，类型/构建/格式/ruff/架构/diff通过。
补丁后组合4/4（4.4分钟，exit0），源/dist终态一致：本地无Key真实新建、
人类三客户端原战斗/结案/私发/检定、云端单人及归档/过期/恢复。
真实限频回帧可见、没有新骰点、不自动重掷、手动重发相同命令ID；四宽度及
390×360按钮44px完整命中。实际看图[939](keeper-dice-rate-939.png)、
[短窗](keeper-dice-rate-390.png)，沿用已生成档案夹/小纸收据，不做新位图。
当前源/dist指纹与日志 `test-results/keeper-platform/ordinary-dice-rate-*`。
首轮两个新测试夹具失败已纠正，未降断言；前端73项定向全部通过。
按账号跨角色/世界/连接/服务实例共享60秒，默认30次/可配；权限、载荷和
查重先行，已提交重放不占额度；游戏时间、读档/分支不重置，正式检定/战斗骰
不受影响。单进程内存实现，重启清空，多worker仍需共享存储。

上一个冻结版完整94项已92 passed /2 skipped /0 failed（25.4分钟，exit0，
源/dist终态一致），日志 `keeper-dice-full-browser.log`，跳过外部staging/未授权
真实模型不算通过。限频补丁改变产品源码，因此不将旧全套和上述4项拼成新版
完整全绿；目标尚未关闭。测试覆盖写回的旧脏截图与Kimi原报告/证据已恢复。

独立主持普通骰：后端1918/8跳过/162子项（208.17秒），前端1303/125文件，
类型/构建/格式/ruff/架构通过。最终组合5/5（4.3分钟，exit0，源/dist一致），
真实本地无Key/云端单人/无角色主持+两玩家、私发帧隔离/公开一致/刷新、
原战斗/结案闭环、四宽度与390×360。23项后端新对偶覆盖新权限、变化认领
不误拒重放、默认私有、幂等、事务回滚、同版本号读档清未来和分支。
初轮3/1失败是真布局漏约束；修复后5/5仍看图发现部分标签被滚动裁掉，
逐控件标签专项通过不冒充视觉完成。短窗并排并加整组标签断言后重新5/5。
采用内置imagegen参考与原档案夹九宫格，不采用示例值/大标题/背景插画。
已看[939](keeper-dice-939.png)、[390短窗](keeper-dice-390-short.png)，实际公开/
私有收据[同屏](keeper-dice-receipts.png)。证据 `test-results/keeper-platform/keeper-dice-*`。
当前仅组合不是新版全套；不标目标完成、未提交发布、未调用付费模型。

时间活动类型增量：后端1895/8跳过/159子项（144.72秒），前端1295/123文件，
类型/构建/格式/ruff/架构通过。最终浏览器5/5（3.7分钟，exit0），源/dist
终态一致，证据 `test-results/keeper-platform/time-activity-*`。19项后端新对偶
含真实作者等待规则、不误计非等待、幂等与事务回滚；表单/草稿中文，默认
等待明示，只计时不代执行。真实三客户端显式wait、云端单人显式travel及
原读档/保密链通过，四宽度/390×360下拉与输入44px。已看939及短窗截图。
首轮浏览器精确label定位错误、第二轮真实控件34px，日志保留；修复不减
断言/加skip/retry/强制点击。旧脏截图已恢复，真实模型主线待本轮独立授权。
内置imagegen生成主持普通骰参考，尚未实现独立主持骰，不宣称目标完成。

游戏时间增量最终版：后端1876/8跳过/159子项（145.72秒，既有warning），
前端1292/123文件，类型/构建/格式/ruff/架构通过。浏览器5/5（3.8分钟，
exit0），源/dist终态一致，日志/指纹 `test-results/keeper-platform/game-clock-*`。
四宽度/390×360、真实三客户端累计200分钟实时/刷新、云端双标签页按保存值
回滚时间及原读档授权通过。旧服务缺字段如实未提供；截图已看939/390/短窗，
保护的旧脏截图已恢复。复用内置imagegen参考，不使用示例值/大图作界面数据。
首次布局越界是真缺陷；后续两项脚本失败分别是假定初始0、异步列表到达前
跳过管理入口。失败日志保留，修复后5项同版完整重跑，不拼接结果。
该增量未重跑完整浏览器；以下93项是此前版本。未提交/发布/调用付费模型。

主持台存档入口策略对齐：多人/缺读取能力只禁读，房主保存/管理不误关；
主持非房主、断线或快照未齐禁用并显示理由，不补发旧操作。组件新增11项、
相关68项，前端1276/121文件；类型/构建/格式/ruff/架构通过。真实三客户端/
云端读档/四宽度及短窗组合4/4（3.3分钟）。已看939/390/短窗及多人390，
44px/等高/内边距/完整命中；复用已有imagegen参考与材质，非新位图。
完整93项最终版91 passed /2 skipped /0 failed（22.2分钟），源/dist终态一致：
`save-policy-full-e2e2.log`。两项跳过仅外部staging/未授权模型，不算通过。
测试前保护截图已恢复。首轮命令浏览器路径有
笔误、启动前失败，exit130已终止；保留日志，不计产品失败或通过。
日志及当前源/dist指纹在 `test-results/keeper-platform/`，未触碰生产/存档。

目标复核中发现的两项已补：创建模式的过时能力说明已校正，累计游戏时间
已接快照/store/顶栏/主持表单。只读权威结算、不解析叙事/伪造日历；新版
增量证据另列，不用上述旧版完整93项代替此次增量验收。
独立主持普通骰缺口现已关闭，玩家原普通骰/本人待检定响应权限保持不变；
不要求主持抢占调查员。准确证据见顶部，不以旧绿灯或生成图代替实际功能。
此前自由原因/固定活动类型混用的缺口现已分离并定向验证；作者规则未改，
仍不将无模型结果冒充真实模型主线通过。

恢复边界新增：单人选角后先写开局自动点，失败不宣布开局；普通重连/重复
开局不覆写。实际云端两个标签页读取未被快速存档覆盖的自动槽位2/2
（1.4分钟），恢复真实选角/场景、清未来历史。HTTP/WS切换和建分支提交
成功先退役再释放控制锁；失败保持运行。52项对偶，最终整仓1866/8跳过/
159子项（143.23秒，既有1项warning），ruff/架构通过。前端产品源/dist未改，
不冒称重跑前端全量。受影响16项浏览器组合16/16（6.4分钟），源/dist终态
指纹一致；覆盖三客户端与Kimi生命周期全部六项，但不是当前完整92项。日志
`test-results/keeper-platform/{restore-boundaries-*,initial-checkpoint-browser*}`。
未修改真实存档；旧角色未就绪存档仍拒绝，没有迁移补角色或自动治疗。

2026-10-08：Kimi原版生命周期5/6中的503已修，不改原断言整组6/6（1.7分钟）。
结构化时间线各自保有认领，legacy迁移不变，混合配置切换拒绝；HTTP15/15。
原报告/指纹/失败trace保留；C首次复跑同名服务日志被覆盖，备份如实标为部分。
云端结构化单人读档真实UI2/2（1.3分钟）：两个同主
标签页、回滚HP/待检定/未来叙事、旧读档重放不回滚新进度、模型陷阱零调用。
四宽度/390×360按钮44px完整命中；截图已实际查看。前端全量1265/120文件，
构建/类型/格式/ruff/架构通过。后端初轮12失败为新能力漏登记严格事件schema，
补字段而非放宽未知字段，实际能力投影对偶+相关113项通过。最终整仓后端
1863 passed /8 skipped /159 subtests（138.78秒，既有1项warning）；
完整92项已终态89/2跳过/1失败（21.9分钟，源/dist一致）。唯一失败为三客户端
PvP回执跨连接同步断言；失败截图显示防御方已确认，代码原先只等攻击方
按钮就读防御方数组。只改等待本人同roll_id唯一回执、保留无结果/HP不变
断言；产品源码/dist未变，修正后完整三客户端专项1/1（1.7分钟）。不将
原整套89通过加专项拼成一次新版全绿；不提前宣告目标/发版完成。
证据 `test-results/keeper-platform/{solo-restore-*,timeline-claims-*,lifecycle-fixed-*,restore-full-*}`，
细节见[读档实现](STRUCTURED_COMBAT_ENDINGS_IMPLEMENTATION.md#云端单人结构化读档已接线)。
旧服务端无能力、多人结构化、角色未就绪存档明确拒绝；普通重连不回滚。

最新人类状态记录已完成本地增量：`record_condition`及只读前状态表单，后端
1832 passed /8 skipped /159 subtests（157.20秒，既有1项warning），前端1252/
119文件，类型/构建/ruff/架构/diff通过。最终草稿/短窗/真实三客户端7/7
（2.6分钟）：真实按钮记录/移除倒地，核对值漂移不自动改草稿，刷新HP与状态
一致，零额外骰点/余弹消耗、另一玩家零私有人物/依据帧。四宽度及390×360
按钮完整命中，下拉/按钮均≥44px；源码/dist指纹终态一致。27项条件对偶及
辅助生产者拒绝覆盖HP0、死亡不可逆、坏副本、撤权重放、无变化不改世界、
outbox回滚；解除昏迷资格是后台/组件证据，不能冒称浏览器治疗全流程。
日志 `test-results/keeper-platform/condition-record-*`；已查看
[939实装](condition-record-reading-939.png)、[390实装](condition-record-reading-390.png)、
[390短窗](condition-record-390-short.png)。新增命令之后完整86项已84/2跳过
终态（20.5分钟，`condition-full-*`源/dist一致）；不代替之后读档增量的全套。

最新状态同步（2026-10-07）：HP/最大HP/条件投影与人物卡一致，变化撤销旧
准备且不扣骰源/弹药；倒地跳过/结束遭遇，增加HP不移除条件。人物完整状态
只发本人/主持；公共战况保持脱敏。迟到 legacy 空帧不再覆盖结构化卡面。
后台16个新对偶、相关155项通过；全量1802/8跳过/156子项。最新前端1238/118文件，
其中4项协议隔离、2项待办中文名称。真实三客户端1/1（2.0分钟，中文化前）
验证状态变化撤销→刷新→重新批准→原左轮6变5发，截图
[人物/战况同步](combat-vitals-synced.png)已实际查看。初轮完整E2E为83/2跳过/1失败
（20.6分钟），指纹一致：长草稿溢出使父聊天面板被焦点滚动，标题不稳定。
新增独立待办滚动边界、短窗工具行并排、44px主持入口并入工具行；最新前端
1238/118、构建/类型/格式通过，新版草稿/短窗/真实三客户端7/7（2.5分钟）通过。
更强断言包括按钮全边界在滚动区内、输入框全可见、父面板不滚；实际查看
[939](draft-review-reading-939.png)、[390短窗](draft-review-reading-390-short.png)及操作图。
修复版完整E2E已终态：86收集→84 passed /2 skipped /0 failed（20.5分钟），
源码与dist指纹终态一致；跳过外部staging/未授权模型，不计通过。Kimi新增
生命周期规格独立验收，未混入本套。测试前已保护的既有脏截图在结束后恢复。
日志/指纹
`test-results/keeper-platform/{combat-vitals,draft-dock}-*`。

最新武器增量（2026-10-07）：稳定物品编号选择/精确扣弹已接线；同名枪不串账，
拆堆只扣使用的一件，失去持有物品后旧准备失效。后端全量1786/8跳过/156子项，
前端1232/117文件；最终三规格3/3（2.2分钟），含申报/审阅四宽度与短窗、
真实认证三客户端原模组左轮按钮申报→主持批准→本人掷骰→6变5发，另一玩家
零库存更新。日志/23源文件指纹 `test-results/keeper-platform/weapon-binding-*`。
复用生成文件夹/九切边缘，实际截图已查看；不是真实模型或整个新版套件。

最新结局目录增量（2026-10-07）：所有作者结局及未满足条件仅主持可见，
prepare 资格与实际战斗/结案门禁分开，条件变化实时私发，不自动补事实。
后端全量1773/8跳过/155子项、前端1230/117文件；最新三规格3/3（1.9分钟），
含四宽度/短窗阅读、按钮深墨色/中心命中、真实认证后端三客户端的未齐→
裁定→已齐→仅准备→实际结算及玩家帧/快照秘密隔离。不是付费模型或全套E2E。
日志 `test-results/keeper-platform/ending-catalog-*`；实现/提示词/原图来源见
[扩展实现契约](STRUCTURED_COMBAT_ENDINGS_IMPLEMENTATION.md)。下面是旧阶段证明。

最新申报增量（2026-10-07）：玩家类型化战斗申报/主持准备已实现；旧服务端
没有显式 `combat_action_request` 时拒绝，不退文字。受理与重放查新控制权，
申报不结算，关联批准不换目标/类型。整仓后端1765/8跳过/154子项、前端1226/116文件；
最终申报布局+真实三客户端2/2（1.7分钟），包括辅助脚本生产→人类批准→双方
参与/防御/确认→结果→明确收尾→结案/保存。不是付费真实模型或全套浏览器证明。
四宽度和390×360截图已逐张实际查看，沿用生成文件夹/固定九切边框。
当前源指纹的既有五条战斗展示/PvP/只读/结局/记录规格5/5另行通过（31.9秒）。
本轮合计7项浏览器专项，不是完整新版套件。
日志 `test-results/keeper-platform/combat-declaration-*`；下面是此前阶段结果。

2026-10-07 最新实际版本为工作区代码（未提交、未发布）。人类状态裁定、
合法模组结局、逐人奖励及所属账号另存/导出结案卡已实现。真实三客户端
1/1（1.7分钟）通过，已含脚本化辅助建议→实际审批按钮→双方对抗/防御/分别确认→统一结算与刷新，
不通过数据库改前置条件、不调用模型；本人角色库隔离与刷新已保存状态均
验证，结局原子取消剩余待办/检定。四宽度/390×360审批读标题/按钮替身1/1，
截图实际查看；后端辅助/旧Agent/事务定向63项、整仓1748/8跳过/150子项，前端全量1222/116文件，类型/
构建及ruff/架构/diff/源指纹通过。保留143中断，不将跳过/中断算通过。
证据 `test-results/keeper-platform/assisted-current-*.log`、`assisted-draft-real-3p-final.log`、
`draft-review-current-frontend-full.log`和`draft-review-reading-layout.log`，完整边界见
[扩展实现契约顶部](STRUCTURED_COMBAT_ENDINGS_IMPLEMENTATION.md)。

仍待：Agent作者效果与真实模型新域链、
当前完整新版浏览器终态。战斗存读档/分支/结案专项见顶部，不再记为未执行。
下面按时间保留旧阶段，不覆盖本段新完成度。

### 历史阶段结果

最新可核验增量：整仓后端1682 passed /8 skipped /145 subtests、exit 0，
前端1201/114文件；真实无模型三客户端骰点记录1/1通过（原请求关联增量前），
关联增量有数据库对偶与整仓覆盖，新增提示词后Agent/目录21项通过。日志
在test-results/keeper-platform。前置裁定、个人生涯保存/导出、双方授权与
完整终局全链未完成，不把旧整套浏览器数字或无模型测试冒充真实模型验收。

整仓后端复跑终态：正确PATH下1678 passed /8 skipped /145 subtests，exit 0，
97.42秒，日志 `/tmp/trpg-combat-platform-backend-full-venv.log`。保留原错误
环境16失败日志与备份18/18正控制，不删除/放宽断言。以下“仍在运行”已被
本条取代；新域浏览器只声明真实三客户端扩展1/1，不冒充新版完整套件。

最新增量：战斗等待屏障/响应后再调度/最近八条提交回执上下文、移动候选实时
刷新已接线；结构化+旧战斗/结局379 passed /1 skipped /108 subtests，ruff/
架构/diff通过。真实无模型三客户端扩展1/1通过（1.5分钟）：主持准备→玩家
非敌对确认→待骰刷新恢复→本人掷骰→停止遭遇；他人帧级隔离与无角色主持不能
代掷，有 `/tmp/trpg-human-real-combat-browser3.log`。整仓后端仍在运行，已出现
尚未归因的失败，暂不报全绿。人类界面骰点回执、合法结局前置/奖励全链、
个人角色库写回与双方授权待办，目标没有关闭。

2026-10-06 最新战斗/结局 UI：新域已接协议、typed store、玩家按钮与主持
四项命令，服务端能力暴露；前端1199 passed /114文件，后端结构化+旧战斗/结局
374 passed /1 skipped /108 subtests。新增浏览器3/3，仅替身后端，明确不算
真实后端三客户端完整闭环。四宽度战斗及结局截图已实际查看；新旧结案事件
同名回归已修复并保留旧通知测试。详见扩展实现契约C3段落。
Agent调度、双方授权、可审计结局前置裁定、个人角色库写回和完整链仍待办；
下面历史阶段数字不证明新增域完成，不宣告目标完成或授权发布。

最新后端接线：弹药稳定 ID / 私人资源事件、active pc 名册同步与读档/分支
战斗授权失效均有对偶。374 passed / 1 skipped / 108 subtests passed，exit 0
（`/tmp/trpg-structured-combat-recovery-verified.log`），ruff/格式/架构/diff 通过。
前端新增战斗/结局域没有据此验收完成；后续仍需组件、浏览器及全链验证。

2026-10-06 战斗/结局协议进展：正式六命令/六事件、玩家身份路由、角色过滤
快照及真实数据库对偶通过；结构化全套 + 旧战斗/结局 368 passed /
1 skipped / 108 subtests passed，exit 0。尚未适配前端事件、恢复与 Agent
调度，不把下面旧版前端全量证据用于证明新增域；完整范围仍在实现。

2026-10-05范围决定更新：用户已选择本轮继续完整结构化战斗与结局，下面历史“待产品确认”的记录已被此决定取代，目标继续而非按调查/社交缩小收口。详见[扩展实现契约](STRUCTURED_COMBAT_ENDINGS_IMPLEMENTATION.md)。独立领域适配及15项新对偶落码，与旧战斗/结局联合43/43通过，ruff/格式/架构/diff通过；协议、玩家掷骰授权、界面、奖励及恢复全链仍待实现，不在此宣告完成。

阶段55当前定稿完整浏览器回归已结束：76收集→74 passed / 2 skipped / 0 failed，18.9分钟，exit 0（原session 45273，`/tmp/trpg-platform-phase55-full-e2e.log`）。跳过未配置外部staging与未授权真实模型，不计通过。包含最新归档纸面/按钮、真实无Key人类主持三客户端、云端单人/多人、Electron、存档/分支、权限隔离与四窗口/短窗阅读。沿用阶段54reading指纹，开跑与终态核对一致（`/tmp/trpg-phase55-fingerprint-final.log`），期间未改代码、未加skip/retry。前端1181/112、构建/类型/局部格式/diff此前同版通过；后端未变，阶段48全量1605/8仍是对应后端证据。不代替战斗/结局产品范围确认，未提交推送发布。

阶段54定稿补验已结束：最新前端1181 passed / 112 files（`/tmp/trpg-phase54-final-unit.log`，exit 0），浏览器3/3、exit 0（`/tmp/trpg-phase54-reading-browser.log`，1.5分钟），含真实云端单人无Key、账号过期资料隔离、归档失败重试/整棵分支归档，以及四窗口主持长标题。构建/类型、局部Prettier与diff通过；终态指纹 `/tmp/trpg-phase54-reading-fingerprint-final.log` 一致。实际查看新640/390截图，窄屏纸面文字恢复可读，桌面生成素材未改；见[390截图](archive-reading-390.png)、[640截图](archive-reading-640.png)。未跑此定稿整套76项，不将阶段52旧整套冒充新版；未提交、推送、发布。

阶段54视觉复核追加：尺寸修复后的浏览器3/3、exit 0（`/tmp/trpg-phase54-cloud-after.log`），指纹一致。逐张查看1280/939/640/390截图，发现窄屏透明纸面导致深色文字不可读，不能以几何通过交付。仅将窄屏fallback底色改为不透明暖纸色，桌面生成卡保持不变；补了窄屏不透明背景断言。新构建exit 0，新浏览器正在运行（`/tmp/trpg-phase54-reading-browser.log`）；尚未对新版本报绿。

阶段54当前工作区补验：云端大厅的三处后续改动原样保留。组件20/20和构建通过；浏览器首次2 passed / 1 failed（`/tmp/trpg-phase54-cloud-browser.log`，exit 1），真实归档抽屉按钮40px，未达到原有44px断言，主代理查看真实反例截图。仅恢复该局部选择器的44px高度与18px左右内边距，保留纸面素材、动画和布局，不放宽断言。修改后构建exit 0；浏览器复验正在运行（session 42263，`/tmp/trpg-phase54-cloud-after.log`），定稿指纹 `/tmp/trpg-platform-phase54-after-fingerprint.sha256`。尚不报通过。

阶段52完整浏览器已终态：76收集→74 passed / 2 skipped，19.2分钟（`/tmp/trpg-platform-phase52-full-e2e.log`），跳过外部staging与未授权真实模型。原进程62504已不存在，日志包含完整总结；未记录独立退出码，不补造退出码。收尾指纹发现 SoloLobbyScreen.tsx、online.css、platform-ui.css 三文件改变，文件修改时间均晚于测试结束。因此此结果属于开跑版本，不能冒充当前工作区整套通过。阶段54不覆盖这些变化，补验云端存档卡/归档抽屉/时间线；新指纹 `/tmp/trpg-platform-phase54-fingerprint.sha256`。不因此新增战斗/结局协议或宣告整体目标完成。

最新阶段49：真实协议替身的160字符连续标题在1280宽度就越出待办卡片并撑开网格（`/tmp/trpg-phase49-long-before.log`，exit 1），主代理实际查看反例截图；新增 `.keeper-pending-label` 局部完整换行后，1280/939/640/390正文范围、按钮命中/尺寸/内边距/不换行及零命令提交断言通过。前端1181/112（`/tmp/trpg-phase49-unit.log`），新版联合11/11、exit 0（`/tmp/trpg-phase49-joint.log`），含真实三客户端人类主持和原有主持草稿/命令/私发/冲突/载荷链；不是将协议替身冒充真实后端。构建、类型、格式、ruff、架构、diff和 `/tmp/trpg-platform-phase49-fingerprint.sha256` 终态一致；后端阶段48指纹不变。四截图已逐张查看，保留 [390](keeper-long-title-implemented-390.png) / [939](keeper-long-title-implemented-939.png)，沿用生成主持桌素材，无新增动效。阶段48完整73/2属于此CSS修改前，不声称新增后的完整76项已重跑；未提交发布。

整套回归阶段48已终态：后端1605 passed / 8 skipped / 127 subtests passed，104.08秒、exit 0（`/tmp/trpg-platform-phase48-backend-final.log`）；7项未配置PostgreSQL与1项特权恢复环境跳过，不计通过。第一次直接pytest入口导入src失败的原始日志 `/tmp/trpg-platform-phase48-backend.log` 保留，随后使用明确虚拟环境Python模块入口，不修改代码/断言。前端1181 passed / 112 files（`/tmp/trpg-phase47-unit.log`），完整浏览器75收集→73 passed / 2 skipped / 0 failed，18.8分钟、exit 0（`/tmp/trpg-platform-phase48-full-e2e.log`，原session 68783已结束）。跳过外部staging恢复与未授权真实模型；云端单人无Key、人类主持真实三客户端、真实Electron联机、读档/分支/待办、权限隔离、图片读取、导入和四窗口/短窗均在本套中执行。生成档案夹/指南针的2x素材选择与键盘操作亦通过；实际查看本套390×360笔记/普通掷骰截图与阶段47四宽度主持待办截图。类型/构建/格式/ruff/架构/diff通过；`/tmp/trpg-platform-phase48-fingerprint.sha256`终态核对一致（`/tmp/trpg-platform-phase48-fingerprint-final.log`）。运行期间未改代码、不重试或增加skip掩盖失败；未提交推送发布。完整战斗/结局仍属待确认的产品范围，不以整套绿灯宣称提供。

主持折叠标题补充：授权名称替代已知场景/线索/物品编号式标题，数量仅为本次申请；未知对象保留编号，自由行动保留原标题，不解析玩家文字。完整请求保留原编号和载荷语义，custom有中文说明；标题与全文共享同一申请角色的物品目录。组件41项、定稿前端1181/112（`/tmp/trpg-phase47-unit.log`），真实三客户端与界面联合4/4、exit 0（`/tmp/trpg-phase47-browser.log`），真实道具按钮申报后标题断言通过、原命令和不预扣库存断言保持；新增390宽度道具待办按钮几何验收。1280/939/640/390真实截图 `/tmp/trpg-item-request-*.png` 已逐张查看，沿用生成主持档案桌、无新增动效。tsc/构建/格式/ruff/架构/diff通过，`/tmp/trpg-platform-phase47-fingerprint.sha256`终态一致；后端阶段43指纹不变，未冒充整套重跑、未提交发布或调用模型。阶段46登记的折叠标题问题关闭。

主持完整请求摘要补充：授权候选名称与稳定ID并列；目标按类型和编号精确匹配，自由文字原样显示。原件绑定实物与本次申请数量不再遗漏；摘要道具名称按申请调查员的库存清单取，不借用多角色候选里的库存数量。新增4项对偶，修复前3项失败记录 `/tmp/trpg-phase46-summary-before.log`；定稿前端1179/112（`/tmp/trpg-phase46-final-unit.log`），最终联合浏览器4/4、exit 0（`/tmp/trpg-phase46-joint-final.log`），含真实三客户端道具名称/申请数量断言与原权限/申报不扣库存闭环。实际查看1280/939/640的 `/tmp/trpg-item-request-*.png`，沿用生成档案桌素材和暖色正文；不为文本识别生成新位图。tsc、构建、格式、ruff、架构、diff与 `/tmp/trpg-platform-phase46-final-fingerprint.sha256` 终态核对通过，后端与阶段43一致。早期跑测不冒充此定稿结果；未跑整套浏览器/后端全量，未提交发布。截图还显示折叠标题保留编号式文案，登记为下一步可读性工作，不宣称主持台整体已完成。

检定卡姓名补充：只使用公开 `targets` 中 `kind=investigator` 且编号精确匹配的姓名；未知编号保留原值，不借用NPC/相似编号，也不读取主持私密角色卡。新增姓名更新/只读权限和目标类型对偶，修复前姓名用例失败、修复后30项组件用例通过。前端1175 passed / 112 files（`/tmp/trpg-phase45-unit.log`），真实三客户端与截图/四窗口阅读联合4/4、exit 0（`/tmp/trpg-phase45-browser.log`），桌面截图实际查看姓名显示。tsc/构建/格式/ruff/架构/diff通过，前端指纹 `/tmp/trpg-platform-phase45-fingerprint.sha256` 终态一致；后端阶段43指纹保持，不冒充整套浏览器/后端重跑。沿用已有生成美术，不为纯姓名显示重复生成位图；未提交、推送、发布或调用付费模型。

阶段44玩家说明：线索/道具提示改为出示与使用的实际后果，不再解释稳定ID、投影或服务端实现；记录缺失仍禁用，不改变ID、权限或请求结构。提示正文13px暖色，四窗口实际查看。前端1173项全过；真实三客户端与三卡原链4项通过，新增四窗口阅读与截图组3/3（`/tmp/trpg-phase44-reading-final2.log`）。阶段44指纹 `/tmp/trpg-platform-phase44-fingerprint.sha256` 终态一致，后端与阶段43一致。阶段43完整72/2属于改文案前版本，未称新75项整套已跑。

阶段43整套回归已结束，指纹 `/tmp/trpg-platform-phase43-fingerprint.sha256` 终态复核一致。当前后端1605 passed / 8 skipped / 127 subtests（`/tmp/trpg-platform-phase43-full-backend.log`，exit 0），前端1173 passed / 112 files（`/tmp/trpg-platform-phase43-unit.log`）；完整浏览器74收集→72 passed / 2 skipped / 0 failed，18.6分钟、exit 0（`/tmp/trpg-platform-phase43-full-e2e.log`），跳过外部staging与未授权真实模型。构建/tsc/ruff/架构/diff通过。本輪单独询问是否包括完整战斗与结局命令域，等待产品口径；截图复核另发现玩家三卡仍暴露稳定ID等实现文案，下一步改为玩家视角说明，不能据回归绿灯直接关闭整体目标。

阶段42当前前端指纹：`/tmp/trpg-platform-phase42-final-frontend-fingerprint.sha256`，终态复核一致。阶段41指纹复核曾发现7个共享文件变化，旧结果只证明旧受测版本；阶段42已在包含共享改动的当前版补验，下列历史结果不混作当前整套通过。

- 阶段42归档退出保护：关闭动画期间确认/取消/Escape不可执行，重新打开恢复操作并聚焦保留。新增2项对偶，当前前端1173项/112文件全过，最终真实本地+房间+云端浏览器4/4；包括账号过期、重登、归档失败重试和整棵分支归档。初跑9/10因测量未等入场动画完毕，最终只等待真实动画结束、不放宽44px/命中/权限断言。详见README第四十二阶段。没有声称当前全仓浏览器全部通过。

- 阶段41邀请表单：角色、有效期（小时）、使用次数都有可见且关联输入的标签；窄窗角色独占一行、两项数值并排、生成按钮独占一行。57项房间测试、1171项前端全量、最终联机联合5/5通过；四窗口标签与输入实际命中、原权限与请求载荷保持，截图已实际查看。详见README第四十一阶段。不将该定向联合冒充整套浏览器或后端重跑。

- 阶段40多人房间：统一页头、成员、选角、邀请控件的44px尺寸与不换行规则。四窗口逐按钮实际命中和390×360选择/释放/生成撤销邀请/取消主持权限变更/主动准备均通过。最终同版联合浏览器5/5（`/tmp/trpg-phase40-joint-final.log`），含双浏览器、Electron联机与启动回收、三客户端人类主持闭环；前端全量1170项，tsc/格式/构建/ruff/架构通过。后端395个指纹一致，沿用1605项，不冒充重新跑后端或整套72/2浏览器。详见README第四十阶段。

- 阶段39选角可读性：前端1170项全过，本地/云端空分类对偶与四窗口角色选择通过；最终真实本地+云端浏览器3/3，直接390×360认领开局，不先换大窗。截图、反例、完整声明见README第三十九阶段。该布局新版未重跑完整72/2整套，不将旧版整套数字冒充新版全量；后端395项指纹复核一致，沿用1605项全量。

- 阶段38后端全量：`/tmp/trpg-platform-phase38-full-backend2.log`，1605 passed / 8 skipped / 127 subtests；前端全量：`/tmp/trpg-platform-phase38-full-unit-final.log`，1168 passed / 111 files。ruff、架构、格式、tsc、构建通过。
- 阶段38本地真实新建：`/tmp/trpg-local-human-creation-browser9.log`，1 passed；无Key真实用户创建/发言/刷新/另建，零模型请求、零旧start；四窗口按钮与短窗阅读断言通过。
- 阶段38最终完整浏览器：`/tmp/trpg-platform-phase38-full-e2e.log`，**72 passed / 2 skipped / 0 failed，18.4分钟，exit 0**。两项跳过为外部staging和未授权真实模型规格。新增本地创建、人类主持三客户端闭环、云端单人、恢复与过渡链均在同一冻结版本中通过。终态指纹 `/tmp/trpg-platform-phase38-fingerprint-final.log` 全部一致。

- 后端全量：`/tmp/trpg-received-material-final-full-backend.log`，1591 passed / 8 skipped / 127 subtests passed。
- 阶段37前端全量：`/tmp/trpg-platform-phase37-full-unit.log`，1160 passed / 110 files。
- 模型设置短窗口修复后：`/tmp/trpg-model-short-browser.log`，5 passed，覆盖本地配置与回合切换、云端单人、多人房主/成员、四种窗口和真实模组导入。390×360正文至少100px且输入完整可滚动到视口，底部按钮仍至少44px。已实际查看 [短窗口](model-settings-short-390.png) 与 [939宽度](model-settings-reading-939.png) 截图。
- tsc、Prettier、ruff、架构、diff 与最终构建通过；既有 chunk 和 SQLAlchemy 警告未隐去。
- 后端跳过核对：`/tmp/trpg-platform-final-skip-audit.log`，40 passed / 7 skipped，逐条证明6项PostgreSQL集成和1项PostgreSQL记忆并发依赖未配置 `TRPG_TEST_POSTGRES_URL`；第8项是 `test_restore_drill.py` 的真实特权恢复演练，需要 `/var/backups` 权限。没有把这些环境测试算成通过。
- 阶段36完整浏览器：`/tmp/trpg-received-material-final-full-e2e.log`，主动中止（exit 130）：38 passed / 1 skipped / 1 interrupted / 33 did not run。原因是截图复核发现模型设置短窗口正文过小，先修布局再重新验收；不能沿用上一阶段完整通过结果。
- 阶段37最终整套浏览器：`/tmp/trpg-platform-phase37-full-e2e.log`，**71 passed / 2 skipped / 0 failed，20.2分钟，exit 0**。两项跳过是外部staging和需单独授权的真实模型规格；人类主持过渡链已在真实前端/后端、零模型调用下通过。终态指纹复核 `/tmp/trpg-platform-phase37-fingerprint-final.log` 全部一致。另加 [滚动到输入后的短窗截图](model-settings-short-field.png)，独立补验1/1通过（`/tmp/trpg-model-short-field-browser.log`），主代理实际查看确认标签和输入完整可见。
- 读图目录接线联合：`/tmp/trpg-received-material-joint-browser.log`，4 passed。说明改色后，阶段37整套中的 `structured-human-3p` 已通过（1.5分钟），同版覆盖收到目录四宽度、零行动读图、刷新恢复、另一玩家HTTP猜ID仍404。主代理已实际查看 [390](received-materials-implemented-390.png) / [939](received-materials-implemented-939.png) 暖灰说明与按钮截图；阶段37整套已得到通过终态。

## 范围 → 实现与必须保留的验收

### 目标原文核对

| 用户要求 | 当前可核实证据 | 尚不能宣称的部分 |
|---|---|---|
| 完善无Agent路径及前端 | 本地/云端无Key新建；真实三客户端私发/检定/SAN/移动、双方战斗参与/防御/分别确认、精确选枪扣弹、合法结局/逐人奖励/另存卡；统一终态收尾、人物/战况同步；生命周期6/6含恢复/分支；云端双标签页读档与开局自动点；游戏时间/类型化活动/主持普通骰与限频；作者线索发现/实物取得/使用、NPC与场景转交 | 7ba1155c单次quality全绿：后端1981/165子项，前端1337，全套E2E94通过/2跳过；人类主线闭环。真实模型兼容性主线仍待授权，不宣称已发布或Agent全链验收 |
| 云端单人/多人所有相关界面 | 下表各类入口及账号/服务器边界，云端无Key创建、房间权限/邀请/选角/移交、单人归档/分支、四宽度与短窗均有实际浏览器证据 | 不将缺少服务端领域命令的界面按钮伪装成可用，也不把环境跳过算通过 |
| 深度用imagegen效果图/素材开发 | README链接的独立概念图与提示词、手绘档案夹/纸质指南针源图；真实1x/2x素材、九宫格/等比呈现，2x选图和实际截图通过 | 概念图不是运行截图；不采用图里的虚构状态/秘密授权文案，不将装饰图烘焙成真实数据 |
| 美观、贴题、可读、不晃眼 | 已实际查看生成素材落地截图；棕黑底/暖色正文/档案材质，按钮命中、键盘、中文输入、短窗阅读、reduced-motion与长标题反例修复 | 视觉舒适性仍允许用户复看反馈；没有以自动绿灯代替看图 |

现行命令以 `structured.ts`、`service.py` 与 schema 为准：已注册26项领域命令，
含六项战斗/结局、人类 `record_ruling/record_condition/keeper_roll`；两项战斗
响应仅玩家本人可调用。`record_fact` 仍不改变 flags，不能冒充裁定；角色保存
是明确 HTTP 账号操作，不是 Agent 命令。用户范围选择已确认，历史“未选择/
无战斗命令”说明不再表示现行能力。目标尚未标记完成，受测版本按顶部核对。

| 范围 | 当前实现来源 | 实际验收入口与边界 |
|---|---|---|
| 登录、注册、服务器、会话过期 | AuthScreen、OnlineShell、api/client、连接档案夹 | server-session-boundaries / character-library / http-request-recovery；实际换服务器、旧401、过期账号隔离。HTTP故障注入不冒充真实存储 |
| 本地与云端单人创建与继续 | StartScreen/LocalPlayStylePanel/local_creation、SoloLobbyScreen/PlayStylePicker | local-human-start真实无Key新建/发言/刷新/另建、零模型；structured-solo-online云端无Key开局与重登；本人兼主持的秘密边界明确提示 |
| 多人大厅与房间 | LobbyScreen、RoomScreen、OnlineRoomDock、roomSendNow | multiplayer / room-ready-recovery / structured-human-3p；真实建房邀请选角开局、旁观加入、退出移交、同步前禁用准备、同步后不补发旧意图 |
| 人类主持工作台 | KeeperConsole、KeeperLibrary、服务端能力和命令目录 | structured-human-3p / scarlet-human-mainline / structured-real-integration / structured-play；叙事、私发、检定、SAN/HP、作者线索发现与实物取得、使用、NPC/场景转交、私有案件时钟、移动、时间、待办；友好候选不把标签冒充稳定ID |
| 玩家状态、线索、道具 | InvestigatorPanel三卡、StructuredActionDialog | investigator-panel / structured-human-3p；分类折叠、出示/使用完整载荷、服务端回执才确认，不预扣、不靠自然语言解析按钮意图 |
| 图片阅读与持久收到目录 | HandoutImageViewer、ReceivedMaterials、received_assets投影 | clue-image-reading / structured-human-3p；44px四宽度、失败重试、关闭回焦点、IME、撤回/换世界、刷新重开；另一玩家HTTP猜ID仍404；独立后端测试覆盖旁观/无角色主持/房主无接收授权 |
| 主持资料、记忆与知识隔离 | KeeperLibrary、只读手册HTTP、记忆查询与线程投影 | structured-context-memory / structured-human-3p / 后端material与history tests；玩家帧不含主持目录/记忆，素材按需读取，Agent不增加UI资料负载 |
| 行动等待、拒绝、取消、暂停 | StructuredCards、KeeperControlNotice、请求账本 | structured-interaction-duals / structured-pending-sync / structured-load-branch / structured-real-integration；等待不移动、抵达只收尾已满足子意图、刷新可取消、缺Key暂停可接管 |
| 战斗、双方授权与精确资源 | CombatAndEndingCards、类型化申报、共享规则/库存桥 | structured-human-3p真实双方参与/防御/各自准备/原子骰点与伤害、持有编号精确扣弹、伤势变化撤销旧准备与重连一致；后台重发/撤权/outbox回滚；生命周期A/B/C/F已验证存读档、分支与多人授权失效 |
| 私有结局条件、裁定与本人结案卡 | KeeperEndingCatalogue、record_ruling、CaseCharacterActions | 真实三客户端目录未齐→裁定→已齐→准备不执行→实际结案/每人另存；另一玩家/旁观不收条件；后台终态取消与凭证快照；生命周期D/E/F已验证奖励、重放、回滚后的旧结案卡拒绝 |
| 存档、主动读档、重连、分支 | SavePanel、SoloTimelinePanel、历史读取与分支服务 | structured-load-branch / structured-branch-online / structured-solo-online；实际读档删除回执、普通重连不回滚、structured不用legacy turn_id、分支记忆隔离、继承历史按身份过滤；两个同主标签页真实读档及未被覆盖的开局自动槽位恢复 |
| 人物与模组导入 | CharacterLibraryPanel、ModuleImporter | character-library / model-settings；真实文件检查安装、失败重试保留文件、账号归属、扩展字段与迟到操作隔离 |
| 模型设置与上下文 | ModelSettingsPanel、账号/房间绑定与上下文摘要 | model-settings / model-settings-online；本地与云端入口、Key不回显、成员只读、失败恢复；模型调用采用测试服务，不宣称真实模型叙事质量验收 |
| 美术与操作舒适性 | 生成档案夹/纸质指南针1x/2x与九宫格、platform-ui.css | 本目录prompt/原图/实际截图；1280/939/640/390和短窗、键盘焦点、IME、reduced-motion；布局截图与权限验收分开声明 |

## 不能用绿灯替代的事项

1. 阶段48/55等整套数字属于其旧版本，不能替代顶部新域回归。用户已选择完整战斗/结局；不能把“没有失败”替代范围逐项验收。模型设置正文至少100px的反例仍保留（`/tmp/trpg-model-short-reading-before.log`），画面需实际查看，不只靠自动绿灯。
2. staging与需授权的真实模型规格不属于本轮本地无Agent验收。不会调用付费模型或以脚本化调用冒充模型验收，不因此宣称部署就绪。
3. 结构化战斗/结局域已接线，能力按服务端显式协商；旧服务端缺能力不提供假按钮。合法结局、各自奖励及新域存读档/分支已由真实人类链和生命周期六项验证，但不等于真实模型新域主线通过。经典模式继续保留。
4. 当前新增目录仅覆盖结构化已提交的接收授权；legacy已有线索图片继续原路径，不将全局seen_assets冒充多人私发授权。
5. 已提交/推送的实验检查点见顶部；未部署。后端初次PATH污染导致备份测试失败的原日志仍保留，正确环境全量复跑通过，不改断言掩盖。
6. 阶段38已补本地显式模式与真实创建；原先修改隔离世界metadata的旧测试只证明既有路径。新用例经真实用户入口验证，[设计与验收要求](local-start-mode-prompt-v1.md)、[实际短窗操作区](local-start-footer-390.png)。完整战斗/结局按新域直接验收，不由调查/社交旧绿灯代替。

## 最终源代码复核补充

2026-10-08 当前冻结版复核：

- `local_creation.py` 只对显式legacy调用旧AI开局；human路径创建角色/稳定物品
  标识后返回快照。云端开局先物化本人认领/库存并写开局自动存档，失败不宣布
  playing；当前全量包含真实本地/云端无Key创建与零模型陷阱断言。
- `service._capabilities` 与 `_KIND_HANDLERS` 均列26项命令；主持目录包含发言身份、
  线索/图片、检定、人物、时间、移动、战斗/结局，而不是只保留文字转解析器。
  `cmd_move_party` 提交后才发scene_changed；公开目的地是出口/已访问场景，
  不把模组完整秘密地图给玩家。检定/双方战斗响应仍由本人完成。
  对当前猩红文档模板做出口图核对：从miskatonic_university可达全部11个
  已声明场景，未发现“人类只能选公开出口，所以主线目的地永远到不了”的
  机制缺口；这只是图可达性证据，不能代替Kimi的实际整场人类主线验收。
- `room_restore` 在恢复提交后、释放控制锁前退役旧运行时，再广播所有同主
  标签页重建；`solo_timeline_ws` 对structured保留各世界认领，legacy保持旧语义。
  Kimi原失败报告不改，修后原六项通过；原报告中版本漂移不当成稳定验收。
- `ArchiveFolderPanel` 及CSS真实使用横/竖独立绘制的1x/2x九宫格；AuthScreen
  指南针srcSet与retina用例核对2x命中，不仅是仓库里存在图片。reduced-motion
  全局包含伪元素。已实际复看限频回执939/短窗、读档确认和结局阅读界面；
  短窗通过滚动阅读完整内容，不能把截图可见片段说成整张卡同时全可见。
- 普通骰限频使用真实单调时钟与账号键，不使用故事文本或游戏时间；跨世界/
  人物/服务实例与恢复不能绕过。拒绝、同ID人工重试及原正式检定/战斗链已由
  新对偶和真实四项组合核对；单进程、重启清空的限制如实保留。

下面的阶段37/38与57项定向结果是历史说明，不能替代顶部当前受测指纹与门禁。

- 对照历史前端任务的九项要求，当前表中的入口覆盖线索/道具/移动/掷骰/检定、状态与重试、主持台、三种运行入口和辅助/接管界面。该历史任务不是本轮付费模型或发布授权。尤其 NPC 发言不是由正文解析：`KeeperConsole.test.tsx` 的明确 speaker 载荷测试与 `test_structured_commands.py::test_publish_message_speaker_rules` 分别验证 UI 编码和真实临时数据库提交/冒充拒绝；时间推进同样走命令与 revision 校验。补充权限/命令/素材/历史定向复跑57 passed（`/tmp/trpg-platform-final-command-audit.log`），不冒充浏览器整套终态。
- `online.ts` 的退出/会话401/服务器变更均先 `disconnectRoom`；`room-ws.ts:disconnectRoom` 清空请求队列、结构化运输游标、结构化store和场景store。所以 `receivedAssets` 不是仅靠阅读器隐藏，账号边界会清掉数据本身。
- `structured-transport.ts` 先执行游标/foreign_world 判定，再 applyEvent 和显示效果；素材目录不会直接接受来自旧世界的帧。只收到其他调查员 `handout_presented` 的脚本反例也不会给自己补目录。
- `responsive.css` 的 reduced-motion 全局覆盖含伪元素，将非必要动画/过渡限制为0.01ms；不是只让某个按钮停动。新目录本身没有新增动画。
- 生成档案夹有 portrait/wide 各1x/2x，指南针亦有1x/2x，运行引用和既有2x浏览器断言对应。新目录在三卡中只作一层折叠，不按收图次数生成新美术或新主卡。
- 本轮目标不自动授予发布、付费模型测试或生产冒烟权限；后续是否包含完整结构化战斗/结局，已另向用户确认，未擅自扩展后端玩法语义。
- 阶段37只改前端短窗布局与浏览器断言，阶段36后端指纹复核一致；1591项全量对应阶段37后端。阶段38增加真实本地创建与无Key菜单门禁后，后端结果以1605项新全量为准，不能沿用旧数字。

过程与反例见 [README](README.md)，最新设计提示词见 [received-materials-prompt-v1.md](received-materials-prompt-v1.md)。完成判定要以当前日志终态、当前受测指纹、实际截图和上表对应行为为依据，不以“找不到TODO”代替。
