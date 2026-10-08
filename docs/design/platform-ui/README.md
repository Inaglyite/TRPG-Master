# 云端与人类主持界面：实施与验收

本轮目标保持完整：完善不依赖 Agent 的主持路径，以及云端单人、多人相关界面。当前仍在实施，不能以单个界面或一组测试通过宣称整个目标完成。未授权发布，本目录也不是发布证明。

当前最新：G1–G5及NPC/场景实物转交已接线，产品冻结点88021898已推送实验分支。
完整96项浏览器已94通过/2跳过/0失败（28.1分钟、exit 0），后端1973、前端1331全量通过。
远端旧版本CI检出两处本机virtualenv路径依赖；只修测试解释器选择后，无virtualenv副本
6/6、前端全量1337/129文件通过。7ba1155c的quality现已终态全绿：后端1981/165子项，
前端1337，全套E2E94通过/2跳过（27.3分钟）；绿灯取自真实远端终态，不由本地专项推断。
同轮无模型猩红主线18/18；共享规则的真实模型验收仍待独立授权，未部署。
准确范围、CI查询状态、指纹与原始日志以[当前验收表](CURRENT_ACCEPTANCE.md)顶部为准。
下面“正在跑”“未提交”及“结构化无战斗/结局”是阶段历史，不是当前状态或能力声明。
新参考图：[主持线索与案件进程](keeper-progress-concept-v1.png)，[完整生成提示词](keeper-progress-prompt-v1.md)。

开发检查点 `2fd6bbeb` / `78102269` 与上一完整95项（93通过/2跳过、26.0分钟）
属于前一冻结版本；不拿旧数字代替当前96项。生成材质与真实布局沿用同一设计方向，
最新转交表单实装见[939宽度](keeper-transfer-939.png)及[390短窗](keeper-transfer-390-short.png)。
生成图和人类主持证据不代替真实模型验收，也不代表发布授权。

## 视觉方向

采用 1920 年代调查档案室：哑光深木、克制黄铜、暖灰正文。生成图提供构图与材质，真实文字、状态、按钮仍由 DOM 渲染。避免闪光、金色发光文字、繁复边框和大面积亮纸底。

- 概念效果图：[cloud-lobby-keeper-concept-v1.png](cloud-lobby-keeper-concept-v1.png)。图里的公开房间、规则库等仅是生成器的构图示意；当前产品没有这些服务，不能照图创建虚假功能入口。
- 背景原图：[archive-desk-source-v1.png](archive-desk-source-v1.png)。
- 正式界面素材：`frontend/src/assets/ui/archive-desk-v1.webp`，由生成原图进行格式压缩，约 61 KB，减少初始加载负担。
- 主持资料库效果图：[keeper-library-concept-v1.png](keeper-library-concept-v1.png)，完整生成提示词：[keeper-library-prompt-v1.md](keeper-library-prompt-v1.md)。按档案索引／阅读区／定向分发的层级实现；生成图上的文字不作为游戏事实。
- 账号过期与归档效果图：[account-archive-concept-v1.png](account-archive-concept-v1.png)，完整提示词及原图位置：[account-archive-prompt-v1.md](account-archive-prompt-v1.md)。安全的保留操作与归档操作分开，先说明影响再确认；档案夹标签为这组界面的共同视觉细节。
- 条件记录与主持交接效果图：[keeper-condition-control-concept-v1.png](keeper-condition-control-concept-v1.png)，完整提示词：[keeper-condition-control-prompt-v1.md](keeper-condition-control-prompt-v1.md)。采用宽文本区和安静的状态条，不采用图中可能暗示人类模式可交还 AI 的错误文案。
- 连接流程效果图：[server-connection-concept-v1.png](server-connection-concept-v1.png)，提示词：[server-connection-prompt-v1.md](server-connection-prompt-v1.md)。最终皮肤按用户纠正的手绘组件方向生成，横／竖文件夹与纸质指南针分开，[最终素材和完整提示词](folder-components-prompt-v1.md)。写实实体指南针草稿未采用，不再被界面引用。
- 紧凑操作面板效果图：[compact-dialog-concept-v1.png](compact-dialog-concept-v1.png)，[完整提示词与原图路径](compact-dialog-prompt-v1.md)。据此实现标题／当前场景／底部操作常驻、中段独立滚动；复用已确认的文件夹九宫格，不将整张效果图当作面板背景。
- 模型设置／模组导入效果图：[settings-import-concept-v1.png](settings-import-concept-v1.png)，[完整提示词与实现约束](settings-import-prompt-v1.md)。采用双角色配置卡与固定操作区，不采用生成图中错误的密钥存储／共享文案；实际字段和权限保持现有契约。
- 材料查看效果图：[material-viewer-concept-v1.png](material-viewer-concept-v1.png)，[完整提示词和原图位置](material-viewer-prompt-v1.md)。校准标题／完整图片／固定关闭与缩放操作；生成地图只用于示意，不进模组，不采用窄屏重绘或裁切内容。
- 存档管理效果图：[save-panel-concept-v1.png](save-panel-concept-v1.png)，[提示词与取舍](save-panel-prompt-v1.md)。沿用手绘九宫格档案夹，真实名称／时间由 DOM 渲染，读档与删除须确认，不采用虚构预览图或自动存档频率。
- 主题与尺寸：`frontend/src/styles/components/platform-ui.css`；没有改写模组主题或将世界事实烘焙进图片。
- 人类人物状态记录参考：[condition-record-concept-v1.png](condition-record-concept-v1.png)，
  [完整提示词/原件/实现边界](STRUCTURED_COMBAT_ENDINGS_IMPLEMENTATION.md#人类状态恢复生成参考与实现边界)。
  小面积暖纸人物摘要、暗色字段、只读核对值；不把图中文字当作存档或治疗事实。
- 云端单人读档参考：[solo-save-restore-concept-v1.png](solo-save-restore-concept-v1.png)，
  [完整提示词、原件及实现边界](STRUCTURED_COMBAT_ENDINGS_IMPLEMENTATION.md#云端单人结构化读档已接线)。
  使用小暖纸摘要、暗色可滚动影响说明、固定44px确认/取消，不复制生成图的
  步骤编号或手机外壳。已查看[939实装](solo-save-restore-939.png)、
  [390实装](solo-save-restore-390.png)、[390×360短窗](solo-save-restore-390-short.png)。
- 主持台存档动作复用上述读档/档案夹参考，读取禁用与保存权限分开；
  已查看[939](keeper-save-actions-939.png)、[390短窗](keeper-save-actions-390-short.png)、
  [多人禁用提示](keeper-save-multiplayer-390.png)，不为三按钮新增装饰位图。
- 游戏时间只读参考（已接线）：[game-clock-concept-v1.png](game-clock-concept-v1.png)，
  [内置imagegen原件、完整提示词与取舍](STRUCTURED_COMBAT_ENDINGS_IMPLEMENTATION.md#游戏时间只读投影已接线)。
  只采用次级时间行、小暖纸参考和暗色字段，不采用大型标题、纸叠或示例数值。
  已查看[390顶栏](game-clock-header-390.png)与[390×360表单](game-clock-form-390-short.png)；
  旧服务未提供时间时如实显示未提供，实数来自实际结算投影而非图片。
- 主持普通骰参考（已接线）：[keeper-free-dice-concept-v1.png](keeper-free-dice-concept-v1.png)，
  [完整提示词、原件与通道边界](keeper-free-dice-prompt-v1.md)。已查看生成输出，
  计划复用档案夹/小收据/固定按钮；不采用大标题/塔楼背景，不烘焙示例27。
  未认领PC的主持可用独立通道，普通骰不代替技能/战斗；实际
  [939](keeper-dice-939.png)、[390短窗](keeper-dice-390-short.png)、
  [收据](keeper-dice-receipts.png)已查看，不能把效果图里的27当真实结果。

使用内置 imagegen，未使用 CLI/API Key。生成图均为新图，没有覆盖项目既有图片；按用户反馈迭代实际组件素材，后续操作与状态界面复用已确认的构图和材质，不为小控件重复生成装饰。

## 完整范围与证据要求（早期阶段记录）

下面“本轮进度”保留最初阶段的检查记录，不代表现行能力。用户已选择扩展完整战斗/结局；最新实现、受测版本与缺口见 [当前验收表](CURRENT_ACCEPTANCE.md) 和 [扩展契约](STRUCTURED_COMBAT_ENDINGS_IMPLEMENTATION.md)。新增人类裁定、合法结局、逐人奖励及明确另存/导出角色已有实际三客户端证据；[结案生成概念图](career-save-concept-v1.png)、[实装窄屏](career-save-390.png) 与完整提示词在扩展契约中。未完成项不据旧整套数字关闭。

| 范围 | 必须证明的行为 | 本轮进度 |
|---|---|---|
| 认证与模式入口 | 登录/注册/过期恢复、服务器选择、直达单人或多人；失败可恢复 | 过期清理、重登录、真实双服务器切换、旧 401 隔离、检查取消／超时、键盘恢复入口已走浏览器；更多网络／权限组合仍待整体复核 |
| 云端单人大厅 | 新建/继续/归档、模式选择、时间线、角色选择；human 不要求 Key | 四模式可直接选择；无 Key 开局、整树归档取消/拒绝/重试、键盘时间线入口已走真实浏览器，详见第五阶段 |
| 多人大厅 | 房间列表、创建、邀请码加入；四模式选择与错误/加载态 | 四模式创建、邀请加入、三人准备/开局链路已通过；完整错误/断线场景仍待补验 |
| 房间与权限 | 邀请、成员、主持授权、选角、准备、开局、退出与房主移交 | 人类主持可不认领调查员；四客户端已走过旁观入团、移交、退出，房主身份不增加主持权限；最终联调见第三阶段 |
| 无 Agent 主持 | 叙事、私发线索/图片、检定、SAN/HP、物品、移动、时间、玩家意图处理 | 私发图片、完整玩家请求与检定已验证；道具链路见第七阶段；第八阶段补齐多行条件、可选字段真实显示与主持控制投影，不把友好表单等同于所有玩法域已完成 |
| 主持素材与记忆 | 清晰区分授权资料/未提供资料；玩家侧不出现主持秘密 | 已提供场景/NPC 作者资料、图片目录和按队员查看的完整属性/技能/持有物；正文、场景补充文档、Lorebook 按需读取，权限隔离已验证 |
| 游玩状态 | 玩家/主持/旁观者差异；等待、取消、拒绝、暂停、断线、重连 | 第十七阶段接通本人 queued 行动取消（含刷新恢复）；补同 ID／新 revision 恢复的身份及连接门禁和暂停提示；真实取消与既有冲突／拒绝通过，更多断线／权限组合仍待完整复核 |
| 存档与分支 | 单人时间线、多人房主权限、主动读档与普通重连区别 | 云端 structured/legacy 分叉、主动读档与重连、分支记忆隔离专项均通过；第十一阶段补时间线键盘；第十六阶段补本地存档浮层确认、成员只读、中文输入、短窗口及实际读档／删除回执；更多网络／权限组合仍待整体复核 |
| 角色库与导入浮层 | 当前账号／设备归属、导入编辑保留扩展、迟到操作不跨账号、失败恢复 | 第十四阶段角色库完成真实本地导入／持久化／选角、云端过期／换账号；第十五阶段补本地模组检查／重试／安装／切换、迟到回执隔离、四种尺寸与焦点恢复 |
| 模型设置与上下文 | 本地／账号／房间作用域，Key 不回显，BYOK／成员只读，真实占用口径、错误恢复 | 第十五阶段本地自定义保存／持久化、云端单人与多人 BYOK／只读真实闭环通过；页签键盘、四种尺寸、上下文读取有界重试通过；更多模式／身份组合仍须整体复核 |
| 可读与可操作 | 1280/939/640 宽度截图、控件命中、键盘、减弱动态 | 大厅/房间/工作台/云端单人创建已截三宽度；“前往／普通掷骰”新增四宽度 480px 高及 390×360 实测，Escape／焦点恢复／Tab／长列表滚动通过；其他浮层仍须复核 |

`human` 的云端单人是同一账号兼任调查员和守秘人，不承诺对本人隐藏主持资料，创建页已明确说明。结构化模式仍如实提示未覆盖完整战斗/结局命令域；界面不得伪装服务端具备不存在的能力。

已有工作区的后端/文档/截图改动保留，既有桌面 Python 修复也不并入本轮发布声明。测试只在本地临时数据库运行，不连接正式环境，不调用真实叙事模型。

## 第一阶段实现与验收边界（2026-10-03）

以当前 `ce5c02f` 上的工作区改动为实现，不是已发布版本；未提交、推送或部署。

- 全量前端：73 文件、820 项单测通过；TypeScript / Vite 构建通过，架构检查通过。构建仍有既有的大包和混合动态导入提示，不将警告写成已解决。
- 浏览器覆盖分层：7 个 spec 的 15 项在功能阶段复跑通过，包含过渡、读档、分支、私发隔离；其后追加了工作台身份候选与尺寸修正，最终版本的三客户端、云端单人、结构化基本交互 **8/8 补验通过（1.7 分钟）**，不能跨版本拼成一次全仓 E2E。
- 窄屏回归：桌面 `flex-basis:160px` 在纵向表单中变成输入框高度；现取消纵向基准，并断言普通控件高度小于 65px。主持复选框从通用表单宽度中独立出来，新增不超过 24px 的尺寸断言。叙事发布按钮有 44px 点击目标且实际滚动后可见，不使用强制点击。
- 真实缺陷：三处目标选择截断带冒号的稳定 ID；出示目标已选但校验仍查找不存在的标量字段；调查员发言身份却提供 NPC 候选。均修前端编码/校验/候选，不改变服务端裁定；新增测试直接核对发出的完整目标 ID。
- 提交反馈只读取对应命令的服务端状态。请求发出不等于执行；等待确认时防误双击，驳回时草稿保留，版本冲突可按新 revision 重提。世界切换清掉私密草稿，主持授权撤销后不再渲染秘密资料。

实现截图（真实浏览器，不是生成的效果图）：[大厅 1280](lobby-implemented-1280.png)、[大厅 640](lobby-implemented-640.png)、[工作台 1280](workspace-implemented-1280.png)、[工作台 640](workspace-implemented-640.png)、[云端单人创建 939](solo-create-implemented-939.png)。全部三宽度原截图保存在 `/tmp/trpg-flow-ui-*`，本目录只保留少量代表图，避免将整批重渲染截图混入既有文档。

最终补验的关键源文件 SHA256：

```text
79aa81ae16123c564df245464a598e73cd0a23b851956f427e20327d280bd191  KeeperConsole.tsx
d756fa4d3b235647495d18fc74c0ea55b175390b2b026a14051944b56e0555d9  StructuredActionDialog.tsx
cb13dd1104a935324f9ed1830795eb7c7da6908ac42135a420aa111d05186730  keeper-commands.ts
a4115ad8784a8afff279d29a009f88195a6c65c24cd281a910e4eb4466775c23  platform-ui.css
```

### 仍需继续实施，不能宣布整个目标完成

1. **主持队员资料与技能**：第三阶段已完成按队员查看的完整属性、技能与当前持有物，检定候选不再截成 `top_skills`；只是只读主持参考与显式命令准备，不包含完整角色编辑/导入管理。
2. 认证过期、旁观者、离线成员、房主移交/退出、归档/删除、单人时间线全部分支还需要逐条浏览器验收；现有单测不等于所有真实界面都已验收。
3. 主持命令的多行条件录入、物品的完整人工闭环仍需梳理；实时待办与检定请求/响应已验证，但不以此证明所有命令已具备友好表单。当前保留稳定 ID 的进阶输入，不猜测世界事实。
4. 结构化完整战斗/结局命令域仍未提供。这是产品能力边界，不通过 UI 或图像生成假装补齐；经典模式保留。
5. 本轮没有付费模型验收、生产或 Pi 验收。后端全仓结果见下方第二阶段，既有并行改动仍保留，未提交、推送或部署。

## 第二阶段：人类主持的资料、待办与检定反馈（2026-10-03）

这一阶段以真实后端的隔离数据库与真实浏览器为准，不调用叙事模型。

- **作者资料库**：主持快照提供场景/NPC 原始设定与稳定图片 ID；默认收起，分类与全文搜索不触发任何世界命令。图片只在选中时按权限读取；显式勾选接收者才提交，确认文案读取服务端结果。图片不进入快照、模型上下文或 outbox。
- **完整手册**：`/api/worlds/{world_id}/keeper-guide` 仅主持可读，按需展示当前安装模组的 `module.md`、`scenes/*.md`、Lorebook。界面明示“不是存档内历史快照”。单文档 256 KiB、源/展示总量 1 MiB、最多 200 条；越界文档不静默截断，显示未载入警告。固定来源、路径边界、私密缓存与纯文本显示防止任意文件读取和 HTML 执行。它不是通用文件浏览器，也不承诺递归读取模组任意目录。
- **完整待办**：此前 `intent_pending` 不进前端 store，主持要刷新才看到；自由文本摘要还只有前 80 字。现实时进入主持待办，完整请求正文仅主持投影，刷新也可恢复。“准备回应／准备裁定／准备检定”只填表，不执行，不自动批准；领域结果默认“未执行”。
- **个人角色卡**：无模型开局／重连恢复本人角色卡，未控制调查员时为空，不用任意 active PC 兜底；他人的 `state_changed` 不覆盖自己的 HP/SAN。
- **真实联调发现并修复**：`handout_presented` 被前端封闭事件入口丢弃，现入口引用唯一协议事件列表；检定／普通骰已有结果但请求卡仍等待确认，现按已提交结果与原响应请求匹配收尾，不把别人的检定或相关调查请求一起标成完成；重开编辑器后 Escape 使用过期关闭状态的问题用实际计时器引用修复。
- **冻结版本验收**：后端 `1506 passed / 8 skipped / 127 subtests passed`；前端 `841 passed / 76 files`，构建通过；三客户端、云端单人和结构化基本交互 **8/8 通过（1.9 分钟）**。真实多人同一流程覆盖手册读取、私发图片、拒绝未授权 HTTP 猜 ID、叙事、检定、SAN、长文本请求处理、移动、刷新恢复。玩家乙既没有私发事件也无法读取图片／手册。这里的数字不包含随后第三阶段的角色界面变更。

本地原始日志：`/tmp/trpg-platform-guide-{backend,frontend,e2e}.log`，构建 `/tmp/trpg-platform-guide-build.log`。已有 SQLAlchemy 删除行数 warning、大包/混合导入构建提示仍存在，没有被当作已解决。

## 第三阶段：多人身份、完整队员资料与叙事恢复

已发现结构化房间仍显示“等待某人行动／跳过行动者／指定行动”，以及旁观者、无调查员的主持拥有看似可用的玩家输入／普通骰入口。前端已改为按结构化异步语义与控制关系显示；经典模式保留轮流行动。

四客户端（主持、两名玩家、旁观者）流程已实测：旁观者邀请码入团后能恢复之前的公开叙事；玩家输入、普通骰、移动入口禁用，不出现主持台；公开阅读不提供私信、图片授权、主持手册和队员资料。房主移交后新房主不凭空获得主持秘密，原房主可以正常退出。最终版本整组复验状态见下，不跨版本拼接数字。

本阶段修复的真实缺口：

- 旁观投影的旧代码对有成员资格的 viewer 返回 `None`，公开结构化事件也被跳过。现允许公开只读投影，写入仍由执行授权独立拒绝；旧认领不能恢复旁观者写权限。修前证据 `/tmp/trpg-viewer-probe-before.log`。
- 云端单人同一账号兼主持与调查员时，keeper principal 丢掉本人调查员标识。现按服务端认领保留本人卡，不给无调查员的主持伪造一张 PC。
- 主持私有队员投影提供完整属性/技能，持有物读取已提交物品账本，不沿用选角时的背包摘要。检定/使用按钮只准备表单，不能替代主持裁定或自动扣物品。常见技能展示中文，稳定键另作小字；自定义技能保留原名。
- `state_changed` 与 `inventory_changed` 按调查员 ID 更新；别人的 HP/SAN/背包不会覆盖自己的卡。主持物品候选跟随队员的当前账本，避免快照旧物品和实时物品混用。
- 结构化模式没有 legacy Turn 历史。新增接收范围过滤的已完成叙事恢复，快照包含最近 50 条可见消息；`GET /api/worlds/{world_id}/narrative-history?before_sequence=...` 只读获取更早一页。页游标按事件顺序，新增消息不挤动旧页。该接口不提供原始事件、记忆、命令或骰子重播。
- 聊天顶部“载入更早叙事”加载后停留在旧文阅读区，新消息不强行跳回底部。世界切换和同世界读档都会使旧读取失效；恢复正文按稳定消息 ID 去重，过期会话/读取失败保留重试入口。
- 普通叙事不推进 revision，旧读档仅按 revision 删除事件，因而同版本未来台词会复活。新结构化存档在同一事务里读取世界状态与事件顺序，记录 `structured_event_cursor`；读档按版本和事件顺序同时回滚，错误游标拒绝且不改状态。反例修前失败、修后通过：`/tmp/trpg-history-rollback-before.log`、`/tmp/trpg-platform-history-restore-target-final.log`。没有这个新游标的旧存档仍沿用旧 revision 兼容行为，不能据此声称所有历史存档都已获得精确叙事回滚。

仍需继续处理：玩家自由行动输入的完整历史展示、分支的父时间线叙事阅读；快速读档的旧播放队列反例见第四阶段。这里只承诺已提交 `message_completed` 的授权阅读，不把叙事恢复称为“全事件历史”。

冻结版本验收完成：后端 **1515 passed / 8 skipped / 127 subtests passed**，前端 **872 passed / 79 files**，TypeScript / Vite 构建与架构检查通过；浏览器 **10/10 通过（2.9 分钟）**。其中四项使用真实隔离后端（四客户端多人、云端单人、主动读档、分支），六项使用脚本化 WS 验证协议和界面，不声称十项都是真后端。读档/分支用例的 legacy 初始开局使用本地脚本化模型服务，之后结构化人类主持无需模型；未调用付费或外部叙事模型。

日志：`/tmp/trpg-platform-history-certified-backend.log`、`/tmp/trpg-platform-history-certified-frontend.log`、`/tmp/trpg-platform-history-certified-final-e2e.log`；构建 `/tmp/trpg-platform-history-final3-build.log`。实现截图：[队员资料 1280](party-implemented-v1-1280.png)、[队员资料 640](party-implemented-v1-640.png)、[旁观者 640](viewer-implemented-v1-640.png)、[历史阅读 939](history-implemented-v1-939.png)、[历史阅读 640](history-implemented-v1-640.png)。未提交、推送或发布；这是阶段验收，整个目标仍未完成。

## 第四阶段：结构化实时发言边界

发现并用反例验证：结构化事件调用 legacy 的全局打字队列，却没有在每个消息完成后结束该队列。同一人物连续说话会混入一条气泡，分片未带 speaker 时也无法可靠承接本消息的身份；定稿可能无法覆盖临时文本，快速读档后队列还可能把未来文本写回来。

- 改为以 `world_id + message_id` 为独立显示单元。开始、分片、完成只更新该消息；分片沿用它自己的显式发言人，不从正文猜姓名或继承别人的发言身份。
- 定稿正文替换临时分片，并关闭流式指示；已完成/恢复的消息不会因重放被重复追加或重新打开。调查员发言是调查员气泡，系统发言是系统消息；不会统统变成守秘人旁白。
- 有效的权威历史恢复会先丢弃旧打字计时器与队列，**不 flush** 未来文本。无效历史不清空现有聊天。
- 结构化完整发布直接显示已提交正文，分片仍按接收逐步更新；不再对人类主持的整稿叠加全局二次打字。经典模式的打字速度、长按加速与旧回合行为不变。结构化分片不显示无效的长按加速反馈。
- playing 快照会清掉残留的 `gameStarting`，即使此前 `gameStarted` 已经为 true。浏览器截图必须等待云端外壳完成实际退场，不能把合法退场动画截图误判为覆盖层常驻缺陷。

新增 7 项反例修前 **7/7 失败**，修后通过；相关 51 项定向单测、全量前端 **879 passed / 80 files**，TypeScript / Vite 构建、Prettier、架构检查通过。浏览器补验 **10/10 通过（2.7 分钟）**，分层与第三阶段相同：四项真后端、六项脚本化 WS；额外钉住同 speaker / revision 的两条完整发言必须是两条已结束气泡，云端真实主持连发 53 条叙事仍是 53 个独立显示单元。主动读档、分支、多人私发与云端单人无 Key 通路均重新通过。

日志 `/tmp/trpg-structured-narrative-before.log`、`/tmp/trpg-structured-narrative-target.log`、`/tmp/trpg-structured-narrative-full-frontend.log`、`/tmp/trpg-structured-narrative-build.log`、`/tmp/trpg-structured-narrative-final-e2e.log`。1280 / 939 / 640 最新截图均已目视复核，历史按钮高度 ≥42px、左右内边距 ≥13px、真实命中且无横向溢出；代表图：[历史阅读 1280](history-implemented-v2-1280.png)、[历史阅读 640](history-implemented-v2-640.png)。该阶段未改后端，后端全量仍引用第三阶段同树实测，不把前端单测称为全流程验证。未提交、推送、发布或调用付费模型。

下一步仍须完善：请求确认与主持排队的准确反馈、认证失效后的隐私清理、归档/退出确认，以及物品使用的人类主持完整浏览器闭环；整个目标保持进行中。

冻结源文件 SHA256：

```text
928eda2a0057bd1904ddb43215e5a04fcdb34091cbcb4af9534d1359e61ed629  structured-narrative.ts
1d1c2a8612210c0162ea01641ccb795250f1a0c192beebf9216a1e7d890fa9ee  structured-effects.ts
c59e75023596e28cec616e562a2cb88e0921889890a1119c2cc059305e997913  structured-history.ts
b4bbaeb367e51867900cb353cbe1cd9e8f9d0c8b6ad9a7bd88e9a7c01f383864  MessageList.tsx
```

## 第五阶段：账号边界与明确的冒险归档

这一阶段只改前端与本地验收；不修改归档 API、主持裁定、存档内容或生产配置。

- **房间/账号离开**：清理结构化权限与身份、作者资料、队员卡、记忆查询、待办、图片、场景和历史游标，同时撤销模型配置视图、未保存的 Key 草稿、连通性测试和上下文诊断缓存，并取消模型设置定时器。不是安全擦除 JavaScript 堆的承诺；这里只保证应用不继续保存/显示这些状态。
- **迟到响应**：历史读取与图片读取使用单调递增的展示代际；reset 后即使重新进入同一个世界，旧图片也不能被追加。正常当前图片仍能显示。普通网络掉线不走账号退出清理，仍按既有恢复协议续连。
- **认证过期**：HTTP 401 与 WS 4401 保留原单人/多人入口意图。主动 `checkSession()` 的 checking 阶段也会在失败后撤销旧视图；有效同账号重校验与变更账号分别有对偶单测，不将模式探针当作访问授权。
- **归档确认**：大厅和游戏内离开入口共用同一后果说明。明确整场冒险和分支会从列表移除、进行中的回合中断、归档不代表通关、数据不是物理删除但当前 UI 无恢复入口。默认焦点是“继续保留”；取消与 Escape 不提交；失败保留确认和可重试按钮；提交期间有同操作锁与禁用反馈。
- **实际语义**：沿用服务端 `abandon_solo_world()` 的整树归档，不调用 `settle_case`、不伪造恢复入口。浏览器先真实创建分支，再验证取消零请求、拒绝保留卡、重试成功；实际时间线端点在归档前可读，归档后返回 `world_not_found`，隔离数据库根/分支两条记录仍在且状态均为 archived。
- **视觉与可访问性**：按新生成效果图使用哑光档案夹标签、暖灰说明和低饱和归档色；云端冒险卡去掉大幅亮羊皮纸边框，经典游戏内存档素材不变。卡片主体是带明确名称的原生按钮，Enter 可打开时间线；登录提交按钮的可访问名称不包含装饰符。1280/939/640 三宽度均有实际截图与按钮命中测试，归档按钮高度 ≥44px、水平内边距 ≥18px、不换成竖排、不横向溢出。

验证分两次冻结，不合并成一次全仓 E2E：

1. 账号/归档逻辑版本：前端 **890 passed / 80 files**，11 项浏览器回归全部通过（3.3 分钟）：五项真实隔离后端、六项脚本化 WS。覆盖四客户端多人、主动读档/分支、云端单人和新账号归档流程。
2. 之后仅追加云端冒险卡材质、原生键盘入口：最终前端仍 **890 passed / 80 files**，TypeScript/Vite、格式检查通过；受影响云端单人 structured/legacy 分支与账号归档 **4/4 最终补验通过（1.1 分钟）**。三宽度截图已目视复核。未声称最后一次运行是全仓浏览器套件。

反例：`/tmp/trpg-account-lifecycle-before.log`，模型草稿泄漏修前实测 `/tmp/trpg-model-private-before.log`。相关 139 项定向结果 `/tmp/trpg-account-archive-target-final.log`；最终全前端 `/tmp/trpg-account-archive-final-frontend.log`；11 项浏览器 `/tmp/trpg-account-archive-certified-e2e.log`；最终补验 `/tmp/trpg-account-archive-final-solo-e2e.log`；构建 `/tmp/trpg-account-archive-final-build.log`；架构 `/tmp/trpg-account-archive-architecture.log`。这一阶段未跑后端全量，不把第三阶段历史数字当作新一轮后端验收。

实现截图：[大厅归档 1280](archive-lobby-implemented-v1-1280.png)、[大厅归档 640](archive-lobby-implemented-v1-640.png)、[游戏内归档 640](archive-game-implemented-v1-640.png)、[过期登录 640](account-expired-implemented-v1-640.png)。全部三宽度原图在 `/tmp/trpg-archive-*` 与 `/tmp/trpg-account-expired-*`。浏览器测试只用独立临时数据库和测试账号；legacy 分支初始开局使用本地脚本化模型服务，未调用付费模型。

关键源文件 SHA256：

```text
f1e0ff58cbf2b1b20e04ad67b83b2d4f1bb930ee0c2b3c1c8df100323f83b240  online.ts
103486c8aa4b7cfb2236f88ed94c60e25303af74624331a74efc6e2f084ff6e0  room-ws.ts
4fddc29822c318dbee8ea26857ac3c66b9e2548bc29c93080194ecf9fb9c1991  settings.ts
194d20ef39701cc4a53cfa55894d0fda5ce991d7ae7c93fae50611d3c746c19b  structured-store.ts
f7185bb67ae12232b3fa772bda886fdc4757005265672fbe856736f22154a144  structured-effects.ts
f3348fa7bc0b9bc85a462f5c51cc3e696b779ab55a9a6dfb71d93c44b68cecb4  SoloLobbyScreen.tsx
da8348e35496ab86aae512bba3a20248fd1e7670f4cba872ae8bc909e96f5e4b  platform-ui.css
```

整个目标仍在进行。接下来继续区分服务器收件与主持排队反馈、完善人类主持物品与条件录入闭环；未提交、推送或发布。

## 第六阶段：收件确认与守秘人排队分开

原界面把所有 queued 都写成“已提交，等待服务端确认”。但 queued 既可能是刚发出的请求，也可能已经收到 ack，或是刷新恢复的服务端待办。人类主持还没有行动时，这句话会长期误导玩家，以为网络没有送达。

- 新增**纯前端展示元数据** `PendingRequest.serverReceived`：发送不确认收件；对应的 ack、action_status、intent_pending 或权威快照才给出正向证据。它不进入命令 payload，不改变协议 schema、数据库或裁定，也不能替代服务端权限。
- queued 已收件显示“已收件，待守秘人处理”，并说明还未执行、不需要重复提交；没有确认时仍显示等待收件，发送失败显示“未能发出”；校验拒绝不能被当作已经进入主持队列，修正/重试提示仍保留。
- 收到对应结果或恢复快照后，旧计时器不再自动把请求改成未确认/再重发。别的请求回执不会确认本请求；同一幂等请求重发不抹掉既有收件证据。没有收到任何证据时仍保留既有有界查询/同 ID 重试机制。
- 行动状态、领域结果、场景位置仍是不同投影：已收件不改变位置、不会产生成功结论、不会完成调查或自动执行移动。卡片采用已有生成效果图的哑光表面、暖灰正文和低饱和状态色，增加字号与留白；不为这一个状态重新生成装饰图片。

新增 11 项单测。首批 7 个反例修前 **7/7 失败**（`/tmp/trpg-request-receipt-before.log`），补充“拒绝≠入队 / 重发不抹收件证据”两项修前 **2/2 失败**（`/tmp/trpg-request-receipt-dual-before.log`）；另两项直接验证快照已确认后计时器不再重发。

最终前端 **901 passed / 81 files**，TypeScript/Vite、格式、架构检查通过。浏览器分两次：初版 **9/9 通过（2.6 分钟）**（四客户端多人 + 云端单人两项为真后端，六项脚本化 WS）；补充对偶与卡片视觉之后最终 **8/8 通过（1.7 分钟）**（云端单人两项真后端，六项脚本化 WS）。不将两次结果拼成一次全仓测试。真实服务上的人类主持移动请求已确认入队时，场景仍是出发地、零 scene_changed、没有成功结果或重试按钮；主持提交移动命令后才抵达。1280 / 939 / 640 三宽度截图已逐张检查，无横向溢出。

日志：`/tmp/trpg-request-receipt-final-frontend.log`、`/tmp/trpg-request-receipt-final-build.log`、`/tmp/trpg-request-receipt-e2e.log`、`/tmp/trpg-request-receipt-final-e2e.log`、`/tmp/trpg-request-receipt-final-format.log`、`/tmp/trpg-request-receipt-final-architecture.log`。实现：[收件反馈 1280](request-received-implemented-v1-1280.png)、[收件反馈 640](request-received-implemented-v1-640.png)。

最终源文件 SHA256：

```text
45faaa4b98e94fb058acb93adecb02155d022c0927d0002b800f89c3a22330bf  structured-store.ts
32e22e4f63d3fcc64854f05aa4392634d3e8a7969b561cf4111892b147a79d17  structured-transport.ts
c3903f0d9fabd4dec7531db859ff0f96312cfbb2e2d05728479ccc3b1a3ef596  StructuredCards.tsx
0daa75e4cc2fd563c56b2f06866e70992e6c7882ee57a7ff4a43394ea6e766d9  platform-ui.css
```

剩余完整范围不变：人类主持物品操作的真实多端闭环、更友好的多行条件表单、主持控制与人工/AI 模式一致的文案、完整服务器切换/网络错误边界，以及玩家输入/父分支历史的产品语义。无战斗/结局命令域仍是能力边界，不能靠 UI 宣称完整结构化跑团。未提交、推送、部署或调用付费模型，整个目标仍保持进行中。

## 第七阶段：人类主持道具闭环

这轮不是只补一个按钮：真实四客户端联调揭示，大厅快照先生成物品注册表，后续人类模式开局虽然物化了角色卡与背包，但没有将新角色的初始装备登记进去。最终开局快照中的 `items` 因而为空。修前证据是 `/tmp/trpg-human-item-e2e.log` 的真实开局断言（物品数为 0）；早期新增后端用例的两个导入名称错误是测试编写错误，不把那两次报错当作产品反证。

- 结构化房间在开局状态广播之前，将每位新物化调查员的装备一次性导入现有注册表。保留已存在的 NPC/物品 ID，不重建整表。包含空背包的一次性标记与旧堆叠来源核对；消耗为 0、完全转交后的物品都不会因再次调用初始化而复活。经典开场未修改，没有批量修写已有存档。
- 主持的道具待办新增“准备使用”：带入原请求的调查员、物品 ID、数量、用法、目标和补充做法。只填表、不发命令，默认不勾选扣减。未解析对象的描述和场景物件目标也保留，不用关键词重新推断玩家意图。
- 明确分开物品执行与请求收尾：主持确认提交 `use_item` 后才结算物品，再通过“准备裁定”说明这次请求的领域结果。不得把物品执行自动扩大成整段调查完成。
- 实机还发现“转移物品”表单即使填好来源与去向也报缺失，原因是前端校验无条件判空。修前单测 `/tmp/trpg-item-transfer-before.log` 为真失败，修后合法来源/去向通过，未填仍拒绝。
- 零数量堆叠仍留在权威注册表，但玩家道具卡不再显示一件没有数量提示的幽灵物品；真正空背包显示“暂无可使用的随身道具”，不伪装成仍在等服务端。
- 主持命令也区分收件与结算：收到对应回执后显示“等待本次命令结算”，不继续提示网络未确认。未收到完成事件时仍不能宣称成功。

本阶段增加 5 项后端测试、5 项前端测试，并补强合法转移与命令收件断言。后端全量 **1520 passed / 8 skipped / 127 subtests passed**，前端全量 **906 passed / 82 files**；TypeScript/Vite、ruff、架构与格式检查通过。使用当前工作区代码，未将历史测试数字挪作本轮结果。

真实后端四客户端用例已通过：大厅先取快照 → 无 Key 开局 → 玩家按钮申报道具（不扣减）→ 主持惰性准备 → 数量不足真实拒绝（库存与草稿保留）→ 修正并提交扣减 → 明确裁定收尾 → 向另一调查员转交另一件真实初始物品 → 接收者打开背包查看 → 刷新后库存一致。接收者侧栏原本收起，测试早期忘记打开它导致隐藏元素断言失败；已经补了真实点击，不把这一点归因为产品缺陷或用 force click 绕过。检定、私发隔离、SAN、移动、旁观与房主移交的原断言保留。

最终同工作区代码浏览器整组回归 **9/9 通过（2.6 分钟）**：四客户端多人一项、云端单人两项使用真实隔离后端，另六项使用脚本化 WS；不宣称全仓 E2E 已跑完。按钮在 1280/939/640 三宽度实测高度、内边距、文字完整与中心命中，截图逐张目视复核。沿用既有 imagegen 档案桌工作台，不为这个操作重新绘制装饰图片。实现：[道具待办 1280](item-request-implemented-v1-1280.png)、[道具待办 640](item-request-implemented-v1-640.png)；三宽度原图在 `/tmp/trpg-item-request-*.png`。

日志：`/tmp/trpg-human-item-full-backend.log`、`/tmp/trpg-human-item-final-frontend.log`、`/tmp/trpg-human-item-final-build.log`、`/tmp/trpg-human-item-certified-e2e.log`、`/tmp/trpg-human-item-final-format.log`、`/tmp/trpg-human-item-architecture.log`、`/tmp/trpg-human-item-ruff.log`。

验收基线为 `ce5c02f + 工作区改动`（不是已提交版本），本阶段关键源文件 SHA256：

```text
66439f344230297398317277f0d3fbae941c95b39cf8ee4026a60db54cc191e0  src/structured/registries.py
2b13cd1c97c1e719b04094c5ccb769d6561333cfe0b96b8090bfb834dc68ca09  src/structured/room_integration.py
1bbee0927cd4a34b5840ca6b236342aff2e0c229d7b6bd29f675fb9b2ea66a44  KeeperConsole.tsx
acbf6688dee599abab690f052e6ff87c84c4035a15383213516c0fe4d14ed600  keeper-commands.ts
f1f61936f58f70b83493e0e81f061b6ac96ccaccfd5bacd5c127701ecbd97066  investigator-panel-view.ts
```

未提交、推送、发布，未改真实存档或调用付费模型。整体目标仍在进行：多行条件表单、人工/AI 控制提示、完整服务器切换与网络边界、历史语义及已有能力缺口仍须继续，不能将这次道具闭环当作整个平台已完成。

## 第八阶段：多行裁定与真实主持控制

依据本阶段的新 imagegen 效果图实现哑光表单区、暖灰提示与低亮度主持状态条；图片仅作布局和材质参考。条件和结果仍由真实控件输入，生成图不执行任何游戏机制。

- “尚未执行什么”“已告知条件”“说明”“剩余步骤”改为真正多行输入。条件按一行一条形成协议数组，忽略空行；与服务端同口径最多 8 条、每条 200 字，超限明确拒绝而非静默截断。记录条件不执行移动、时间或道具命令。修前表单/字段边界用例在 `/tmp/trpg-ruling-fields-before.log` 中实测失败。
- 快照与已提交 `keeper_control` 事件都保存实际控制者，重连恢复，空快照清除旧控制者；部分快照不抹掉已知身份，诊断帧不擅自授予权限。事件不更改本场配置的 human/assisted/agent 模式。
- 已经持有控制权的操作者显示“你正在主持”，不再邀请其接管自己；其他人类主持持有控制权时提供显式接管。只在 AI 模式且本用户确认持有人类控制权时显示“交还 AI 主持”。人类模式不提供 AI 归还或请求重试。房主身份不代替 `can_keeper`；在线普通玩家没有控制面板。
- 控制操作按本次 `command_id` 对账：发送、收件、完成与拒绝分开，其他命令结果不能结束它；等待时防双击，拒绝后可再次提交，断开连接只读。世界/用户/权限上下文切换丢弃旧反馈。修前控制栏 4 条真实失败见 `/tmp/trpg-control-notice-before.log`。
- 截图额外揭示“可选结果未填却显示成功”：React 的空值没有对应 option，浏览器画出了第一项。现在所有可选枚举有“未填写（不提交此项）”，不伪造成功、线程操作或待办类型。反证 `/tmp/trpg-optional-enum-before.log`，不是后端落账错误。
- 640px 顶栏场景文字被挤到 0 宽、“前往”越出顶栏：既有两行紧凑顶栏的断点从 520 扩至 760，场景与工具栏分行，隐藏装饰标题但保留当前位置。修前截图 [640px](header-layout-before-v1-640.png)，真实几何断言失败在 `/tmp/trpg-header-layout-before.log`。没有扩大等待时间或 force click。
- 二次目视检查发现后置的 1080px 媒体查询仍覆盖了窄屏按钮高度，导致两行顶栏的第一行裁掉顶部。新增顶部完整落在 header 内的断言再次真红（`/tmp/trpg-header-cascade-before.log`；[中间版本](header-layout-intermediate-v1-640.png)）。中屏规则限定 761–1080px，窄屏顶栏及聊天/人物栏共同预留 84px，避免只修按钮却留下位置文字裁切。

最终同工作区版本（HEAD `ce5c02f` + 未提交改动）前端全量 **920 passed / 84 files**，TypeScript/Vite、格式、ruff 与架构检查通过。最终浏览器组 **10/10 通过（3.0 分钟）**：四客户端多人 1 项、云端单人 2 项、本地人类过渡 1 项走真实隔离后端，另六项为脚本化 WS；不宣称全仓 E2E 已跑完。人类主持链路没有模型调用；其中本地过渡用例进入结构化模式前的经典开局仍使用计数的本地模型桩，不是付费模型。

真实过渡用例证明：本次控制命令确认 → 正确显示本机主持 → 刷新由快照恢复 → 逐行填入两条条件及空行（零提交、零移动）→ 正式裁定只发两条非空条件 → 玩家追问仍不移动 → 明确决定后主持执行移动。三宽度 1280/939/640 全部有截图、按钮高度/内边距/不换行/中心命中/页面溢出断言，场景文字和“前往”完整落在顶栏内；六张最终截图逐张目视检查。实现：[控制栏 1280](control-implemented-v1-1280.png)、[控制栏 640](control-implemented-v1-640.png)、[多行条件 1280](conditions-implemented-v1-1280.png)、[多行条件 640](conditions-implemented-v1-640.png)。全部原截图在 `/tmp/trpg-host-{control,conditions}-*.png`。

日志：`/tmp/trpg-host-forms-final-frontend.log`、`/tmp/trpg-host-forms-final-e2e.log`、`/tmp/trpg-host-forms-build-certified.log`、`/tmp/trpg-host-forms-format-final.log`、`/tmp/trpg-host-forms-architecture.log`、`/tmp/trpg-host-forms-ruff.log`。早期新增收件测试误写 `request_ack`，真实协议为 `action_ack`；这是测试编写错误，修正后重跑全量，不算产品缺陷。本阶段未跑后端全量，不把上一阶段 1520 项当作新一轮后端验收。

关键源文件 SHA256：

```text
b4b4d32849c062311830d2f6f3ee196fb6c3c5ff65e80a4630facfa9e3793ff7  structured-store.ts
6ee978414392cf000db5ac5d2401d54e122ed16bd4fa5072f2cb02847457d66f  AssistedAgentCards.tsx
31aaaef2ce2d58aa4a17412d7499ddbcce40e7eb6a76395f445aadd14a7c4fab  KeeperConsole.tsx
8ac907d800353327df8144108a678074bdbc6dd77b611b6a0d45d1552112c97e  keeper-commands.ts
f7a9bdd1cc004558699f65958a857415e9a6d1e8ab2ed06999a6e7079cf4173c  responsive.css
56d04f00cab64fe0a0dfc0d1d971dc14397c2caae93d37e0501a6ab30312dfb5  platform-ui.css
```

剩余范围：服务器切换与全部网络/认证边界、多人各模式异常恢复、低高度及更小视口工具栏、所有浮层键盘交互，以及玩家输入/父分支历史的产品语义。真实 AI 归还/重试仅有确定性验证，不能由本轮人类闭环替代真实模型验收。战斗/结局命令域仍是已登记能力边界。本阶段未修改后端产品代码、模组、真实存档、模型配置或部署环境，未提交或发布，整个目标继续进行中。

## 第九阶段：服务器身份边界与可缩放文件夹

依据用户的卡片参考重新生成真正的手绘 UI 皮肤，不再用写实桌面／实体指南针代替组件。最终源图、实际像素尺寸、1x/2x 资源、完整提示词见[文件夹素材清单](folder-components-prompt-v1.md)。竖版恢复卡与宽版信息卡分别构图；九宫格固定页签和四角，纸质指南针独立等比呈现。文字与按钮保留为 DOM，网络故障用恢复卡，密码错误仍留在登录表单。等待和编辑地址时隐藏无关登录字段，而不是铺一页不可用表单。

身份边界与故障恢复：

- 请求捕获服务器 origin 与世代，在响应和 JSON 读取后复核。旧服务器的成功／401 都不能污染新服务器；本地模式请求不触发云端会话过期。认证接口的 401 由本次操作处理，不误触发全局退出。
- 更换服务器立即撤销旧房间／私密视图，哪怕两台服务器返回相同用户 ID；清空未提交账号信息。无效地址或持久化失败不切换，取消编辑保留当前连接。拒绝含账号密码的 URL，HTTP 连接如实提示未加密。
- 房间续玩书签带服务器 origin，不凭旧世界 ID 跨服务器续玩。另一标签页改变／清空 localStorage 服务器偏好会撤销旧视图并重新验证；sessionStorage 的无关清空不能误踢云端登录。后者修前新增对偶测试为 1 failed / 48 passed，修后 49/49。
- 会话检查有 15 秒预算，允许随时返回模式选择；旧请求不能在返回后强行带回大厅。超时卡的重试／改地址／返回入口保持可用。一般 HTTP 写操作未被擅自加上这一预算，不声称客户端超时等于服务端回滚。

### 实机发现的 CORS 早返回缺陷

第一次双 TLS 服务器实测中，保存 B 地址后浏览器报网络失败，不能把它说成服务器 B 不可用。保留的 trace 明确报“401 响应无 Access-Control-Allow-Origin”：认证中间件直接返回，绕过了内层 CORS。将既有 CORS 注册移到认证中间件之后，使其处于外层；没有扩大原有来源／方法／头部白名单，也没有修改身份、权限或 CSRF 判断。

新增 10 条真实 HTTP 对偶测试：修前 9 failed / 1 passed，修后全绿；覆盖可信来源可读 401、非可信来源无放行头、合法预检不要求 Cookie、真实写操作仍需认证、未知来源／方法／头预检仍拒绝、已登录的 Origin 拒绝仍为 403、本地 null 来源仍被连接信任拒绝。联合本地信任与认证测试 36/36。修前 trace 保存在 `/tmp/trpg-folder-failure-preserved-NtRZfr/`，测试在独立临时数据库运行。

### 视觉与验证边界

浏览器检查 1280/939/640/390 四宽度的边角固定尺寸、页面横向溢出、44px 热区、内边距、文字完整与实际中心命中；逐张看过地址编辑和恢复卡截图。2 倍像素密度下指南针实际选择 `@2x`，键盘可编辑地址、取消和返回。无 force click、放大超时或跳过失败用例。

最终实现：[恢复卡 1280](folder-recovery-implemented-v1-1280.png)、[恢复卡 390](folder-recovery-implemented-v1-390.png)、[地址编辑 939](folder-editor-implemented-v1-939.png)、[2x 恢复卡](folder-recovery-implemented-v1-retina.png)。不是生成效果图冒充实际界面；其余四宽度原图在 `/tmp/trpg-server-{editor,recovery}-*.png`。

首轮后端全量 16 failed / 1514 passed，失败均为备份脚本子进程走了缺少 SQLAlchemy 的系统 Python；在 PATH 前置项目虚拟环境后，同一备份专项 18/18，完整后端重跑 **1530 passed / 8 skipped / 127 subtests passed**。没有修改备份脚本或删除测试。前端全量 **948 passed / 85 files**，构建、ruff、架构与格式门禁通过。已有 SQLAlchemy 删除行数 warning 与构建大包／混合导入提示仍未解决。中间一次构建曾因新增测试误用 Testing Library 不支持的 `exact` 参数报错，已修测试并重新构建；那次不能当作产品缺陷，也不能用旧 dist 冒充最终视觉验收。

最终同工作区代码浏览器组 **13/13 通过（3.6 分钟）**：双独立 TLS 服务器 3 项、四客户端人类主持 1 项、云端单人 2 项、本地过渡 1 项使用隔离真实后端；结构化基础 6 项使用脚本化 WS。人类主持／认证路径没有付费模型调用；本地过渡在进入结构化模式前的经典开场仍使用计数的本地模型桩。没有将这组结果写成全仓 E2E 或真实 Agent 验收。

本轮关键源文件 SHA256（HEAD `ce5c02f` + 未提交代码）：

```text
076582e19fcfa5f50004f5f5bb2be1553c7506bcc64b993f64dbea0e339500fe  server.py
649ca4e36ce13ebd38e0c5401758553e7d434335d7a09b08aea5f3598b68ec78  tests/test_http_cors_auth_boundary.py
4810705d51883f86f8d2a352fca91e37a1e042d89fc6d774f487d94ca61ed100  frontend/src/api/client.ts
b7702dc337f4777e4a73a4123d04d323d3185ad6cd6925be4988858a2f30e0e8  frontend/src/online.ts
46696b79c144f7ab962f489f731a08645d5a01259d11ccb5676513b0452288e2  frontend/src/react/components/online/AuthScreen.tsx
d5ceb0caa0e4d1addbce01d75489c9c3e71d074d489ab315e16210ea4b90c2ba  frontend/src/react/components/ArchiveFolderPanel.tsx
9f39547ab637474fb919d9486e09359626efbb3784f34c395ebde3e054368c25  frontend/src/styles/components/platform-ui.css
efb0eaa2b190bf3243dd15d3370d0f2ca56120ec82f0d0284e839b4b9508897b  frontend/e2e/server-session-boundaries.spec.ts
```

日志：`/tmp/trpg-cors-auth-before.log`、`/tmp/trpg-cors-auth-after.log`、`/tmp/trpg-storage-dual-before.log`、`/tmp/trpg-folder-boundary-targeted-final.log`、`/tmp/trpg-folder-final-backend.log`、`/tmp/trpg-folder-backup-env-check.log`、`/tmp/trpg-folder-certified-backend.log`、`/tmp/trpg-folder-final-frontend.log`、`/tmp/trpg-folder-final-build.log`、`/tmp/trpg-folder-certified-e2e.log`、`/tmp/trpg-folder-format-final.log`。

未提交、推送、发布、连接生产或调用付费叙事模型。基线 `ce5c02f + 工作区改动`，整个目标仍在实施：多人各模式异常恢复、低高度工具栏、所有浮层的键盘交互、玩家输入／父分支历史语义仍须继续。不能用连接卡完成代替“所有界面完成”。

## 第十阶段：紧凑档案夹操作面板（2026-10-04）

按新的紧凑面板生成参考图实现，不使用写实道具或实体指南针占据操作区域。`CompactGameDialog` 仅共享装饰、布局、焦点与取消行为，不解析玩家意图，也不执行游戏命令。`MoveDialog` 与 `RollDialog` 保留原提交路径和权限守卫。

- **移动请求**：地点名称只来自公开投影，隐藏正文中的内部 ID；请求仍逐字发送原 `destination_scene_id`。当前场景仅取服务端场景 store，固定显示，长列表滚动不将其带走。选择地点提出出发请求，是否移动由主持与引擎决定，不跳过正常叙事或过渡。
- **短窗口布局**：标题、44px 关闭按钮和底部取消／掷骰常驻，只有中段滚动。复用宽版九宫格及 1x/2x 素材，页签和边角保持固定尺寸；长地点名称允许换行，普通操作标签不换行。地点按钮以暖灰正文配安静细线，而非整列亮金色。
- **键盘**：打开聚焦输入（没有输入则聚焦关闭）；Tab/Shift+Tab 环绕可用控件，排除隐藏祖先、disabled fieldset、aria-hidden/inert 和关闭的 details 内容；Escape 不提交，卸载时恢复仍在页面上的触发者。重渲染不重置输入焦点，关闭回调取最新值。
- **修前证据**：新增四项回归在原实现上全红，缺初始焦点／Tab 约束及当前场景展示。首次测试误将带 datalist 的输入当作 textbox，修正为真实的 combobox 后再跑，保留原日志，不把测试编写错误算成产品缺陷。修后六项通过；不涉及游戏状态落账。
- **浏览器尺寸**：脚本化 WS 提供 24 个明确标为 fixture 的公开地点，1280/939/640/390×480 及 390×360，共 5/5。断言中段确实可滚动、滚到底后底部按钮坐标不变、44px 高／至少 10px 内边距／中心命中／无横向溢出；真实按键完成 Tab 环绕、Escape、入口焦点恢复，并核对取消没有发出移动／普通骰请求。没有 force click、retry 或放宽超时。该层不是引擎验收。

九张最终真实浏览器截图已逐张检查，代表图：[前往 1280](compact-move-implemented-v1-1280.png)、[前往 390](compact-move-implemented-v1-390.png)、[掷骰 939](compact-roll-implemented-v1-939.png)、[390×360 掷骰](compact-roll-implemented-v1-390-short.png)。全部原图在 `/tmp/trpg-compact-{move,roll}-*.png`，不能与生成概念图混称。

本阶段前端全量 **954 passed / 86 files**，TypeScript/Vite 构建、Prettier、ruff、架构与 `git diff --check` 通过。没有改后端、schema、模组或状态结算；本阶段未重跑后端全量，不能将第九阶段的 1530 项称为本轮重验结果。大包／混合导入的既有构建警告仍在。

最终构建冻结后再跑回归组 **13/13（3.5 分钟）**：双独立 TLS 服务器 3 项、四客户端人类主持 1 项、云端单人 2 项、本地过渡 1 项使用隔离真实后端；结构化基本交互 6 项使用脚本化 WS。与新面板布局的 5 项分别运行，不能拼成一次完整 E2E 或真实 Agent 验收。早期 10 项回归运行过程中曾重构建 dist，虽全绿仍不作为最终认证，已在源码与构建不变后重新运行这 13 项。运行前后关键源文件指纹一致。

关键源文件 SHA256（HEAD `ce5c02f` + 工作区改动）：

```text
037dc369b1137fa4ada9e1d58def365033565a59ea33e5dce63ac44ab6098c85  frontend/src/react/components/CompactGameDialog.tsx
a935d0bc24e8c4932cd78c48f1ec57a480c2165d0e3d25aee3db2d759ce58656  frontend/src/react/components/structured/MoveDialog.tsx
fb336c48db7ab57fd6e51905d5486207558efaf5156837ef318992c3f10fc2e9  frontend/src/react/components/structured/StructuredCards.tsx
0b652dc93de4999e752681e6c7219ec5ad91bdf4086e036a44289af95a0d2584  frontend/src/styles/components/platform-ui.css
064f556b7fe5aac7348cbabb5e2ee5708ea3a36cfaf836393e435cac14fc6bd1  frontend/e2e/compact-game-dialogs.spec.ts
```

日志：`/tmp/trpg-compact-before.log`、`/tmp/trpg-compact-before-corrected.log`、`/tmp/trpg-compact-after.log`、`/tmp/trpg-compact-full-frontend-final.log`、`/tmp/trpg-compact-build-final.log`、`/tmp/trpg-compact-e2e-final.log`、`/tmp/trpg-compact-regression-e2e.log`、`/tmp/trpg-compact-certified-e2e.log`、`/tmp/trpg-compact-format.log`、`/tmp/trpg-compact-ruff.log`、`/tmp/trpg-compact-architecture.log`。

剩余范围不因此关闭：多人各模式异常恢复、其他浮层的焦点约束与短窗口操作、玩家输入／父分支历史语义仍需继续。只读巡检还发现时间线面板的焦点 effect 依赖 busy/onClose，会随操作重新聚焦，且没有 Tab 约束／关闭恢复；后续应先做可执行复现，不能把此次两个面板的结果推广成“所有浮层已完成”。未提交、推送、发布、连接生产或调用付费叙事模型，完整目标仍在实施。

## 第十一阶段：云端时间线的正常键盘操作（2026-10-04）

复用已确认的 imagegen [档案夹素材](folder-components-prompt-v1.md)和[账号／归档构图](account-archive-prompt-v1.md)，不为键盘修复重新生成装饰。时间线保留 HTTP 控制面：读取、切换、重命名、归档仍走原 API，外框不接管游戏规则。新增 `dialogFocus.ts` 从第十阶段组件提取 DOM 焦点规则，并由紧凑面板和时间线共用，避免维护两份有差异的控件过滤。

可执行复现与修复：

- 新增首批测试在旧时间线实现上 **4 failed / 14 passed**：Tab 越界、父组件回调更新时抢走输入焦点、Escape 同时取消重命名并关闭面板、删除确认不能独立取消。现打开只初始化一次焦点，Tab 围在可用控件之间，取消行内操作后回到本行入口；关闭才恢复大厅卡片焦点。回调更新不再重跑聚焦 effect。
- Escape 分层处理：编辑／删除确认先取消自身，不请求接口，不同时关闭面板；无行内操作时才关闭。提交期间不允许 Escape 假装撤回已发送的操作，并显示提交状态。
- 中文输入法新增反例 **1 failed / 19 passed**：确认候选字的 Enter 曾提交重命名。现组合输入期间 Enter/Escape 不触发提交或取消；普通 Enter 仍走原重命名请求。
- 列表刷新与禁用控件新增反例 **2 failed / 19 passed**：被删除的控件无法再承接焦点，全部按钮禁用时也不能让焦点落到外面。现提交期间聚焦面板，重新可操作时恢复有效行按钮；已删除行回到关闭按钮。第一次补丁只处理“面板外焦点”，漏掉提交结束后仍聚焦根节点的情况，专项仍有 1 failed；补齐后专项 **27/27**（时间线 21 + 紧凑面板 6）。未删除断言。
- 关闭、重命名确认／取消与行操作有 44px 热区、可见内边距与焦点轮廓。标题固定，列表单独滚动；深层分支缩进最多 48px，超过四层显示真实层级文字，不伪造层级。删除／重命名的服务端权限与语义未改变。

本阶段前端全量 **962 passed / 86 files**，TypeScript/Vite 构建、Prettier、ruff、架构与 `git diff --check` 通过。未改后端／schema／模组，不引用第九阶段后端全量数字作为本轮新验收。既有大包／混合导入构建警告仍在。当前只是实验工作区，没有提交或发布。

冻结源码与最终构建后一次回归组 **18/18（4.2 分钟）**：5 项紧凑面板 + 6 项结构化基础使用脚本化 WS；3 项双 TLS 服务器、1 项四客户端人类主持、2 项云端单人、1 项本地过渡使用隔离真实后端。本阶段增强了云端单人第二个用例：真实时间线 HTTP 数据上，1280/939/640/390×480 的编辑／列表截图、44px 按钮／内边距／中心命中、取消编辑不关闭面板、Tab 环绕、关闭恢复大厅卡片焦点，且监听确认零次 rename/archive/switch 请求。缩进 50 层的对偶目前是组件测试，不冒称浏览器或真实后端覆盖。没有 force click、扩大超时、retry 或 skip；也不是全仓 E2E 或真实 Agent 验收。

最终八张真实浏览器截图已逐张检查，代表图：[编辑 1280](timeline-edit-implemented-v1-1280.png)、[编辑 390](timeline-edit-implemented-v1-390.png)、[列表 939](timeline-list-implemented-v1-939.png)、[列表 640](timeline-list-implemented-v1-640.png)。原图在 `/tmp/trpg-timeline-{edit,list}-*.png`。早期 7 项浏览器组在补 IME 与刷新焦点前运行，虽全绿不能代表最终版本，日志保留为过程证据；本次 18 项在源码与构建都冻结后运行，关键文件指纹前后一致。

关键源文件 SHA256（HEAD `ce5c02f` + 工作区改动）：

```text
0ec95d0ba8fed21466089a4972de874e76fb9173f0b3fe46084809465df34157  frontend/src/react/components/online/SoloTimelinePanel.tsx
be63d7f1f06bba0b8f11967171b18bfea36b48592d4a213ccf38261bb4442416  frontend/src/react/components/dialogFocus.ts
403111d3fc76fa9dc3733155102122af4ec700c20d0b2704c81f589520489279  frontend/src/react/components/CompactGameDialog.tsx
2c513066ddd3ae2fb3fafce9aed3172ea8363f0f5aa87fe349d810314ab9f547  frontend/src/styles/components/platform-ui.css
5232725b2af51e8610d71f3b931a79ee1708cfca64a4d84d8910c918c6420493  frontend/e2e/structured-solo-online.spec.ts
```

日志：`/tmp/trpg-timeline-before.log`、`/tmp/trpg-timeline-ime-before.log`、`/tmp/trpg-timeline-refresh-before.log`、`/tmp/trpg-timeline-targeted-final.log`（中间一次失败）、`/tmp/trpg-timeline-targeted-certified.log`、`/tmp/trpg-timeline-frontend-frozen.log`、`/tmp/trpg-timeline-build-frozen.log`、`/tmp/trpg-timeline-e2e.log`、`/tmp/trpg-timeline-e2e-frozen.log`、`/tmp/trpg-timeline-format.log`、`/tmp/trpg-timeline-ruff.log`、`/tmp/trpg-timeline-architecture.log`。

剩余范围：其他浮层（包括归档确认、主持工作台）的完整焦点约束／短窗口、多人各模式异常恢复、玩家输入／父分支历史语义。只读巡检确认归档退出浮层仍无 Tab 约束，尚未宣称已修。真实 Agent 仍未授权，本阶段未调用付费叙事模型、连接生产、提交、推送或发布；完整目标仍在实施。

## 第十二阶段：归档与主持／行动浮层的操作边界（2026-10-04）

继续复用 [imagegen 档案夹素材](folder-components-prompt-v1.md)与[账号／归档构图](account-archive-prompt-v1.md)。归档确认采用同一宽档案夹，不把整张效果图拉伸成窗口，也不把文字烘焙进图片。后果说明独立滚动，安全取消与确认留在底部；主持台固定标题、正文独立滚动，经典编辑器将表单／预览／错误合为滚动区，底部操作不被长预览挤出。没有修改游戏规则、命令、接口、模组、存档或模型配置。

本轮实际修复：

- **世界边界**：同一账号拥有两场冒险时，仅判断“仍是单人房主”会留下上一场的归档确认。现世界身份变化即关闭旧确认，不重置已经发出的归档请求锁。
- **焦点与按键**：主持台、结构化／经典行动编辑器共用可用控件过滤，排除关闭的 details、隐藏祖先和禁用 fieldset。只有焦点属于当前浮层时才处理捕获按键；其他浮层的 Escape 不会把背后的主持台或编辑器一并关掉。关闭恢复仍连接的入口，不恢复已移除节点。
- **归档安全选择**：打开默认聚焦“继续调查”，进入确认聚焦“继续保留”；Tab 环绕。Escape 在确认层只退回菜单，再按才关闭。提交期间禁用操作、把焦点留在浮层；失败恢复安全按钮，重复点击仍只发送一次。没有假装能取消已提交请求。
- **短窗口**：正文滚动而标题／操作常驻；取消、确认、关闭具有至少 44px 热区与明确内边距。经典编辑器的布局与焦点用组件测试覆盖，本轮浏览器布局覆盖的是结构化编辑器，不能称两条路径都完成了浏览器验收。

可执行反例：主持／结构化／归档首批 **5 failed / 52 passed**，另有经典编辑器 **1 failed / 1 passed**；修后前端全量 **969 passed / 87 files**。TypeScript/Vite 构建、Prettier、ruff、架构门禁与 `git diff --check` 通过。本轮没有重跑后端全量，不能引用之前的后端数字作为本轮新验收；既有大包／混合导入警告仍在。

最终冻结源码及 dist 后，回归组 **18/18（4.5 分钟）**：5 项紧凑面板和 6 项结构化基础使用脚本化 WS；3 项双 TLS 服务器、1 项四客户端人类主持、2 项云端单人、1 项人类主持过渡使用真实隔离后端。增强出示／主持台用例，1280/939/640/390×480 实测 Tab 环绕、关闭热区、底部可点击与零意外命令；增强真实云端归档，前三宽度 480px 高、390×360 下实测说明滚动不移动底部按钮，Escape 分层退出且零归档请求，大厅内嵌确认保持原来的非模态语义。不是全仓 E2E，也不是真实 Agent 验收。

初次浏览器组 **2 failed / 6 passed**：一项在入场动画未完时量到 43.9997px，改为按既有截图规范先等稳态，保留 ≥44px 断言；另一项是新增测试补丁误改到创建页循环导致 `surface` 未定义，修正测试定位，不算产品缺陷。最终组无 force click、retry、扩大超时或新增 skip。经典单测格式首次检查有一项未格式化，修正后复检通过。

实际截图已逐张查看：出示与主持台各四宽度、游戏内／大厅归档各四宽度，共 16 张。代表图：[出示 939](action-editor-implemented-v1-939.png)、[主持台 640×480](keeper-short-implemented-v1-640.png)、[归档 1280×480](archive-folder-implemented-v2-1280.png)、[归档 390×360，说明已滚到底](archive-folder-implemented-v2-390-short.png)。原图在 `/tmp/trpg-modal-{action,keeper}-*.png` 与 `/tmp/trpg-archive-{game,lobby}-*.png`；不与 imagegen 概念图混称。

关键 SHA256（HEAD `ce5c02f` + 工作区改动，最终浏览器运行前后相同）：

```text
94683653e632d81f8b1e39249ad4b93459bac5991de923e380fcbae19192929e  SoloAdventureExitControl.tsx
1d7fc8e3416ae192c99a6c267b6b4b7639c58531805eaa8507ad70ef2c8cd9e8  AdventureArchiveConfirmation.tsx
7143b99a0d250661f0d36af468cc317031eb7f35b56b9068ab24725c51961ec7  KeeperConsole.tsx
0e01482db12d94eb4a9cc09c2c819e7c3bf2c10a546eaf1c63e50ec023ff7b04  StructuredActionDialog.tsx
9aeec4a1c17d5afc915c06285b374611a8d89ba93bc441683809bd8889396de6  PanelActionDialog.tsx
2d7dfb60774e197f7232837747e3788c4f82d46c90a5f3119936185f23158944  platform-ui.css
62c3f578d2b9705c41e0c6e3e328c8e72dbfdd66525169b1ae11959e62d382de  structured-play.spec.ts
57734775d26a8907fbf10647c56db625615b5e430be69a6a231c95b0df5d3423  structured-solo-online.spec.ts
```

日志：`/tmp/trpg-modal-before-corrected.log`、`/tmp/trpg-modal-legacy-before.log`、`/tmp/trpg-modal-after.log`、`/tmp/trpg-modal-layout-after.log`、`/tmp/trpg-modal-frontend-final.log`、`/tmp/trpg-modal-build-final.log`、`/tmp/trpg-modal-e2e-first.log`、`/tmp/trpg-modal-e2e-final.log`、`/tmp/trpg-modal-format-final.log`、`/tmp/trpg-modal-format-corrected.log`。

剩余范围不因此关闭：经典行动编辑器的真实浏览器短窗口、存档／导入／角色库等旧浮层，多人各模式异常恢复，以及玩家输入／父分支历史语义。目视还发现 390px 大厅长账号名会挤压“退出登录”使其断行、出示表单原生 fieldset 边框偏亮，留作后续实证修复，不能称全部界面已完成。未提交、推送、发布、连接生产或调用付费叙事模型；完整目标保持进行中。

## 第十三阶段：账号栏与经典编辑器的真实操作（2026-10-04）

继续沿用生成素材与哑光档案风格，修复第十二阶段目视登记的两点：账号操作不缩小／不折字，长用户名单行省略且 title 提供完整名称（单人／多人大厅共用）；结构化出示的原生 fieldset 去掉白框，保留 legend、radiogroup 与原校验。没有修改人物、道具、检定、游戏规则或 API。角色库按钮与退出操作均 44px 高、有明确内边距。

**新的经典路径证据**：为 WS 测试替身增加独立 `legacy-game` 场景，提供明确的布局测试线索／手电筒，不冒充真实模组或模型。通过真实开局与人物栏入口打开出示／使用，在 1280/939/640×480、390×360 实测中段滚动、底部坐标不变、关闭／取消／提交按钮尺寸与中心命中；真实 Tab／Escape 取消，零 `action`／`action_request`，库存仍为一件手电筒。

初次 **17 passed / 2 failed**，保留原日志：

- 经典用例在 939px 超时。开始误认为侧栏被输入区层级压住，截图与 `openEditor` 源码证实它实际已按既有设计收起。提高层级的临时尝试没有作为修复保留，已还原原值。用例改为走正式“角色／线索”入口重新打开抽屉；**真正缺陷**是取消后焦点回到收起抽屉的按钮。新增独立组件反例修前 **1 failed / 2 passed**，现回到可见的侧栏入口，桌面仍回原按钮，保持关闭抽屉的语义。不能把测试直接点击隐藏控件的超时称为道具按钮不可用。
- 账号用例量到 43.12px，实际处于大厅入场缩放动画；按截图规范等待稳态后再测，≥44px 断言保留。最初样式修改前的独立真实后端用例量到 32px 的角色库按钮而失败，属于本轮确认修复的尺寸缺口。

最终源码与 dist 不变时，一次回归组 **19/19（5.2 分钟）**：12 项脚本化 WS（5 紧凑浮层 + 7 结构化／经典界面），7 项真实隔离后端（3 双 TLS 服务器 + 1 四客户端人类主持 + 2 云端单人 + 1 人类主持过渡）。真实云端账号用例新增四宽度角色库／退出按钮的高度、内边距、不折字、中心命中、完整名称 title 与无横向溢出断言；多人沿用同一 CSS 的既有真实用例，但本轮未新增 390px 多人长账号名专项，不推广单人的尺寸结果。没有 force click、放宽断言、增加 retry 或 skip；新经典用例明确 20s 动作超时，避免单个不可交互控件一直耗到整条用例超时。

前端全量 **970 passed / 87 files**，TypeScript/Vite 构建、Prettier、ruff、架构、`git diff --check` 通过。未改后端且本阶段未重跑后端全量；既有混合导入／大包构建警告保留。账号四图、经典出示／使用八图、结构化出示四图共 16 张实际截图均已目视检查。代表图：[账号 939](account-layout-implemented-v2-939.png)、[账号 390](account-layout-implemented-v2-390.png)、[经典出示 939](legacy-present-implemented-v1-939.png)、[经典使用 390×360](legacy-use-implemented-v1-390-short.png)、[哑光出示分组](action-editor-implemented-v2-640.png)。原图 `/tmp/trpg-account-layout-*.png`、`/tmp/trpg-legacy-editor-{present,use}-*.png`、`/tmp/trpg-modal-action-*.png`。

另外使用**内置 imagegen**生成并查看 [角色库设计参考](character-library-concept-v1.png)，[完整提示词、原始路径及采用／不采用边界](character-library-prompt-v1.md)。它是下一轮实现的参考，**不是已完成界面**；采纳固定顶边与档案阅读层次，生成器添加的阵营／核心特质／示例出生地等不升级为产品字段。实际纸面应降低亮度和纹理，沿用暖灰，最终数据与文案仍用 DOM 和现有角色卡。

下一步证据路径已明确：`CharacterLibraryPanel.tsx` 当前仅按 open 触发 reload，已加载列表和编辑状态未见账号身份依赖；Escape 使用 document 捕获并 stopImmediatePropagation，也没有 Tab 约束。应先写账号／服务器变化、同账号重校验、关闭重开及迟到结果的对偶，再决定清理／失效边界，不能把这项源码巡检直接宣称为已复现的数据泄漏。之后完成角色库与其余存档／导入浮层，补多人各模式异常恢复和输入／父分支历史语义。目标仍在进行中。

最终关键 SHA256（HEAD `ce5c02f` + 工作区；浏览器运行前后一致）：

```text
4843ead4ea6a58f5844b0f35e7674fe68b73e6dcaebf0a209472328ade4c2e58  platform-ui.css
fd9569731f22d5228a5ae6e49751154cbafb9a2b6365fe7a4352f34c568b34d6  PanelActionDialog.tsx
426934e088786438f921813649382e3ae8332b635d7d774c6edcdc0716349b9c  SoloLobbyScreen.tsx
d21f8873428c6cf947c53621c00d384c22bbc538477495e8bc029d71d1777656  LobbyScreen.tsx
bc1601b0f024846c59657a656d2df507526359e2d474a26cc7fc379477d1a7b4  structured-play.spec.ts
ec3f9d70db432d1987e5d333b4967554f049b2069f6487abf6d60e8f32c9d211  structured-solo-online.spec.ts
37cae5114e549fde536995f56d0a42600206bd6ad61ceea64d9c3b51665e3cdf  structured-stub-server.cjs
```

日志：`/tmp/trpg-account-layout-before.log`、`/tmp/trpg-legacy-hidden-focus-before.log`、`/tmp/trpg-account-legacy-e2e.log`（首次失败）、`/tmp/trpg-account-legacy-e2e-final.log`、`/tmp/trpg-account-legacy-frontend-final.log`、`/tmp/trpg-account-legacy-build-final2.log`、`/tmp/trpg-account-legacy-format-final.log`。未提交、推送、发布、连接生产或调用付费游戏叙事模型；imagegen 为上述内置生成调用，不能写成“本轮没有任何模型调用”。

## 第十四阶段：角色库档案夹与账号／设备边界（2026-10-04）

按上一阶段的 [imagegen 角色库参考](character-library-concept-v1.png)实施，不是只换背景：搜索列表／档案详情分层，管理操作集中在底部，标题与关闭常驻；编辑正文独立滚动，保存／取消保持可达。复用横版文件夹 1x/2x 九宫格，不拉伸整张图、不烘焙文字；详情改为哑光暖灰，覆盖旧羊皮纸深墨色以保持对比度。窄屏单列，短窗口收起辅助文字但搜索有明确的可访问名称。没有引入生成图里的阵营、出生地、虚构履历或人像数据。

四个反例先实测为 **4 failed / 9 passed**（`/tmp/trpg-library-boundary-before.log`）：换账号的迟到列表覆盖新列表、读取完整卡面后仍向新上下文发保存、退出后弹窗继续显示旧内容、弹窗不接管键盘焦点。修法与对偶：

- **身份与窗口生命周期**：账号／服务器／模式换作用域，立即卸载旧列表和草稿，关闭重开也是新工作区。每次跨 await 的读卡／写回／刷新／通知保留原会话代次，A→B→A 不复活旧操作。会话过期关闭 open 标志，下一账号登录不自动弹出旧流程。
- **同账号复核不丢稿**：checking 保留已知身份的草稿，暂停控件与写入；认证失败才清掉旧内容。没有把“重新检查”和“换账号”混为一谈。
- **本地与云端路由**：本地所有角色库读写与导出显式走设备后端，忽略已保存的云端地址；云端仍走当前服务器与 Cookie。统一 20s 请求上限，读取失败提供“重新读取”，不伪装成空库。导出不再绕过统一请求入口，下载前再次核会话，不下载迟到的旧账号资料；JSON 版本信封与扩展资料原样保留。
- **多阶段导入**：文件读取结束前、校验结束前都检查原作用域与当前文件序号；退出导入／换账号后不继续校验或补新预览。编辑先取完整原卡，保留无法在表单呈现的扩展资料与对象型物品，再由服务端权威校验／重算。
- **键盘与忙碌**：打开聚焦关闭；Tab 只遍历可用控件，输入法 Escape 不关窗；保存期间全禁用时聚焦弹窗本身，不掉到背景。服务端响应回来后恢复操作。关闭只归还仍在 DOM 的入口。Escape 不假装取消已经发送的服务端保存。

补了 17 项定向用例（角色库组件从 9 到 20；新增 API 6 项）。最终前端全量 **987 passed / 88 files**；TypeScript/Vite 构建、Prettier、ruff、架构门禁与 `git diff --check` 通过。角色库后端原有 HTTP／存储／迁移专项 **27 passed**，没有改后端；本阶段没有重跑后端全量，不能用以前数字替代。既有大包／混合导入警告仍在。

**最终源码与 dist 冻结后的浏览器回归 22/22（5.7 分钟）**：12 项脚本化 WS（5 紧凑浮层 + 7 结构化／经典使用），10 项真实隔离后端（2 本地角色库 + 4 双 TLS 服务器 + 1 四客户端人类主持 + 2 云端单人 + 1 人类过渡）。具体新增证明：

- 真后端导入 → 预览 → 保存 → 重载持久化 → 开局选中角色；无真实游戏模型，开场采用本地模型桩。
- 1280/939×900、640×480、390×360 下关闭／创建／导入／管理按钮均 44px 高且中心命中，正文无横向溢出，保存／取消在窗口内；真实 Tab／Escape 与入口恢复。暂缓真实成功保存响应时，焦点仍在弹窗，Escape 不取消服务器已经完成的保存。
- 浏览器设置一个无关云端地址，本地角色库请求仍全部指向隔离的本地后端。
- 云端真实会话撤销 → 复制请求 401 → 旧档案立即移除 → 新账号注册 → 空库；同服务器读取旧角色 ID 得 404。不是造帧或直接改浏览器 store 的“验收”。

早期角色库 2/2、中间回归 16/16 与追加忙碌焦点前 22/22 都保留日志，但最终声明仅引用 `/tmp/trpg-library-e2e-frozen.log`。没有 force click、放宽断言、新增 retry/skip 或跨版本拼验收。

九张实际截图已查看（列表／详情与编辑各四尺寸、云端档案一张）。代表图：[本地档案夹 939](character-library-implemented-v1-939.png)、[编辑 390×360](character-library-editor-implemented-v1-390-short.png)、[云端档案 939](character-library-cloud-implemented-v1-939.png)。原图 `/tmp/trpg-library-{dossier,editor}-*.png` 与 `/tmp/trpg-library-cloud-939.png`。它们是浏览器截图，不与概念图混称。

最终 SHA256（HEAD `ce5c02f` + 原有／本轮工作区改动，浏览器运行前后相同）：

```text
5dfce7f60bbdc9926ca4e1aa8e812c389bf99831bd0e44d62922d3e6f925f87a  CharacterLibraryPanel.tsx
5ce899a8160dcd9e0235fb238c5ce2bef1e0e4e79759b45cf2ade15226625dc9  api/characterLibrary.ts
08fe804ad73b8619228eb6bf30bd287ac09e68b948e9a84b9f8f75f6241612f2  api/request-context.ts
67f8bea1cad095793b7ad4095ed3a5761408a01616246646e9e045582913c15a  platform-ui.css
6dc3ad8cecc9460e2b86bfdcdcd8554a6e96988f3b098a91f13900c9379eb098  character-library.spec.ts
5079fd94dfe4024112c4c3b7995317237cd0745961b6bc4a4de32b57178fd8ff  server-session-boundaries.spec.ts
```

日志：`/tmp/trpg-library-boundary-before.log`、`/tmp/trpg-library-boundary-final.log`、`/tmp/trpg-library-frontend-frozen.log`、`/tmp/trpg-library-build-frozen.log`、`/tmp/trpg-library-e2e-frozen.log`、`/tmp/trpg-library-backend.log`、`/tmp/trpg-library-format-frozen.log`、`/tmp/trpg-library-architecture.log`。

完整目标不因此关闭。下一步仍需复核模型设置／模组导入／存档等浮层；源码巡检发现前两者仍使用 document Escape 而没有完整焦点约束，模组上传没有显式超时，此处只登记、没有宣称已复现或修复。多人各模式异常恢复、玩家输入／父分支历史语义仍在范围内。未提交、推送、发布、改模型配置、触碰生产或用户存档；本阶段没有新增 imagegen 调用，复用上一阶段生成的参考与已确认素材，也没有调用付费游戏叙事模型。

## 第十五阶段：模型设置与模组导入档案夹（2026-10-04）

这是一轮实际组件与等待／键盘路径改造，不是完整目标或发布收口。一次内置 imagegen 生成了新的组合参考；实现复用已确认的 1x/2x 九宫格素材，正文、字段、按钮与检查结果全部为 DOM。

### 实现与边界

- 模型设置采用叙述／裁决双卡，760px 以下单列；标题、分页、底部操作常驻，中段独立滚动。保留既有本地／账号／世界／房间作用域、成员只读、Key 留空沿用／不回显、数据发送确认、BYOK 门禁、估算／提供商统计来源；未修改模型配置、密钥存储或规则引擎权威。
- 分页支持左右箭头／Home／End，只有选中页签进入 Tab 序列，tabpanel 与标签正确关联。弹窗约束 Tab、忽略输入法 composing Escape、消费内部 Escape、恢复入口焦点；保存中的关闭／取消不再看起来可点击。
- 上下文读取原先没有超时出口；现在 8 秒未确认回执就恢复刷新，保留已有诊断并说明未更新。关闭／离开账号时清除定时器，真实诊断回执清除错误，不把估算显示成模型厂商统计。
- 模组上传提取为本地二进制传输：检查最长等待 30 秒、安装 120 秒，响应必须符合实际预览／安装结构；服务端字符串明细保留，非法 payload 不落 UI。超时／断线明确“未确认，可能已安装”，不声称撤销服务器已完成的安装。
- 卸载、离开本地模式、断线后失效的上传会停止等待；迟到安装回执不得向新的 WS 会话发送切换。安装正常完成只发一次切换请求，文案不提前声称切换已完成。检查失败可重新检查原文件。
- 导入入口仍属于本地开始页；云端模组创作／导入的管理员限制未改变。真实浏览器只导入现有模板打包的测试包到独立临时 runtime，不操作用户模组库、存档或数据库。

### 反例与收敛

新增 16 项单测。旧代码上新增弹窗边界反例 **7 failed / 16 passed**，上下文等待反例另为 **1 failed / 18 passed**，日志分别是 `/tmp/trpg-settings-import-before.log`、`/tmp/trpg-context-timeout-before.log`。

真实浏览器继续抓到两个焦点问题：保存按钮禁用后焦点退到 body，内部 Escape 无效；文件选择触发检查后入口禁用，无法按原焦点恢复。这两项均已修复，未改成全局 Escape。更强的短窗口字段断言又抓到 390×360 下输入框底部被操作栏裁掉约 15px：浏览器原生焦点只保证中心可见。现按实际 focusin 在下一帧仅滚动弹窗正文，保证当前字段完整可见，不滚动底层游戏页。

本轮测试开发也修正了两个夹具问题：开始页单测显式采用 GameShell 的 local 前提；异步检查测试等待导入按钮真正 enabled 才点击。真实安装记录的 source 正本是 `user`，不是我最初假设的 `installed`。全仓格式检查暴露的上一阶段测试文件只做 Prettier 排版整理，未改断言或产品语义。

### 最终验收

- 前端全量 **1003 passed / 90 files**，TypeScript／Vite 构建、全仓 Prettier、ruff、架构门禁、`git diff --check` 全通过；构建原有大包／混合动态导入警告仍在。
- 后端相关回归 **43 passed / 5 subtests passed**（`test_model_settings.py`、`test_module_packages.py`），没有宣称后端全量通过；本阶段未改后端产品代码。
- 同一最终版本浏览器 **15/15，4.0 分钟**：5 脚本化 WS 紧凑浮层，10 真实隔离后端（2 本地角色库、2 云端模型配置、3 本地模型／模组、1 四客户端人类主持、2 云端单人）。所有模型请求仅连本地桩，人类主持与对应单人无 Key 路径不调用模型。
- 1280×800、939×620、640×520、390×360 八张实际截图逐张已看；检查底部控件至少 44px、显式内边距、不换行、窗口内完整显示与中心命中，测试真实 Tab／Escape／入口恢复、分页与长表单字段可见。没有 force click、增加 retry/skip 或降低断言。

代表截图：[模型设置 939](model-settings-implemented-v1-939.png)、[模型设置短窗口](model-settings-implemented-v1-390-short.png)、[模组导入 939](module-import-implemented-v1-939.png)、[模组导入短窗口](module-import-implemented-v1-390-short.png)。全部是实际浏览器截图，不与生成参考混称。原图为 `/tmp/trpg-model-settings-*.png` 与 `/tmp/trpg-module-import-*.png`。

最终源码／构建 SHA256（HEAD `ce5c02f` + 原有／本轮工作区，浏览器运行前后相同）：

```text
aeba52651b22fcd1438dc986b2179f4ab0e83762289ba8d99fb414605d9d6144  frontend/src/react/components/ModelSettingsPanel.tsx
86a17602868d861ffab1e04cac4f44510c69d113cd80d7a51c25855d069915d9  frontend/src/react/components/ModuleImporter.tsx
c0e18942c5dbb2e2f99fadf575750691fcec18ab4646723404f95613832e014e  frontend/src/react/components/useDialogKeyboard.ts
7f6c581c088e1a079f58577484e4b7def5f35cb0b577e3c3b626ab2a54ee7de3  frontend/src/api/modulePackages.ts
ef34887f7a202001dc02eb4fa949c0a38eace9d0b22fae8a52309d22e3774090  frontend/src/settings.ts
fb9ed9c17adb855118098ffb255d335ae2afca4ae147b32cfb2f60568ccd2f80  frontend/src/state/model-store.ts
c471a58e6928e4aa2f2d6b8901fe08aa5f47c01bf20a44cded4f5c9c4588551e  frontend/src/styles/components/platform-ui.css
aff1027b09400db5da13c688240eeab7ee43acff34d6d8ac26b7c47d7aaaa225  frontend/e2e/model-settings.spec.ts
48d2be5d89d320096ea7b6ae2100a56bc7727e63eaa629b57de5d8de53304998  frontend/dist/index.html
```

最终日志：`/tmp/trpg-settings-import-frontend-final.log`、`/tmp/trpg-settings-import-build-final.log`、`/tmp/trpg-settings-import-e2e-final.log`、`/tmp/trpg-settings-import-backend.log`、`/tmp/trpg-settings-import-format-final.log`、`/tmp/trpg-settings-import-architecture-final.log`。较早的失败／中间运行保留，但不拼接成最终通过。

完整目标仍 active。存档等剩余浮层、多人各模式异常恢复、玩家输入／父分支历史语义仍待复核；不因这 15 项变绿宣称所有界面完整。未提交、推送、部署、调用真实游戏模型、修改真实配置或触碰正式环境。

## 第十六阶段：存档档案夹与危险操作确认（2026-10-04）

这轮完成存档浮层，不宣称整个平台目标已完成；未提交、推送或部署。

- 采用 imagegen 参考图的档案夹／时间线层级，复用 wide 的 1x／2x 九宫格素材。标题、关闭、底部确认常驻，正文独立滚动；实际存档名称、场景、调查员、时间仍读取权威数据，不添加没有来源的预览图。界面限定为柔和褐灰、旧黄铜及可读的浅色文本，避免模组暗红主题把次要信息压暗。
- 读取和删除手动存档点均需二次确认，确认展示所选存档名称与影响，默认焦点为取消；取消不发命令。自动存档仍不提供删除。切换世界／模式或槽位消失时清掉确认，权限丢失时不能执行旧确认；服务端原有授权仍不变，UI 不承担替代授权的职责。
- 成员显示明确的只读说明，房主操作不伪装成可用按钮；读取模式不展示重命名／删除。各处重命名均忽略中文组词中的 Enter，Escape 只退编辑／确认，不同时关闭整层。共享键盘助手补焦点节点被卸载后的恢复，对模型设置和模组导入做回归。
- 整局归档在玩家确认时立即发送，动画仅作视觉反馈，不再由 500ms 延迟回调向后来的会话发送命令；离场恢复计时器随卸载清理。原测试改为更强的“确认时仅发一次、卸载后不再发”，没有删掉二次确认断言。
- 浏览器抓到旧的重命名回执缺口：成功后编辑器一直停留。现在只在 `save_renamed.ok` 的真实回执后收起；失败保留编辑。新增两个成功／失败对偶测试。
- 既有读档用例还依赖已隐藏的玩家侧技术 ID 文案，因而发了空目的地。改为将可见名称映射到实际公开快照，断言唯一标识非空，再保留真实移动、读档回滚与重连不回滚断言；没有恢复玩家侧技术 ID，也没有绕过产品移动工具。

最终版本分层证据：

| 检查 | 最终结果 | 范围 |
|---|---|---|
| 前端全量单测 | 1013 passed／92 文件 | 含 8 个存档边界和 2 个重命名回执新测试 |
| 浏览器同版复跑 | 14/14，3.3 分钟 | 5 个紧凑浮层测试；3 个真实本地模型设置／模组导入；2 个云端 structured／legacy 分支；1 个多人无模型主持闭环；3 个真实本地存档／读档／分支测试 |
| 存档实际操作 | 真实后端回执通过 | 创建手动点、取消零命令、IME 不提交、重命名、准确槽位读档、确认删除及刷新列表；业务步骤未调用模型，本地开局仅用模型桩 |
| 存档按钮布局 | 1280／939／640／390 宽，390×360 短窗通过 | 44px 点击目标、内边距、文本不折行、完整视口、中心命中；未用 force click |
| 后端定向 | 21 passed | `test_structured_branch.py`、`test_world_branches.py`；不是全仓后端验收 |
| TypeScript／构建／格式／ruff／架构 | 全通过 | 构建已有的资源占位及大包警告未宣称解决 |
| 冻结指纹 | 7 个源文件／构建产物前后一致 | `/tmp/trpg-save-panel-sha-frozen-{before,after}.txt`，无跨版本拼接 |

实际浏览器截图：[939×600](save-panel-implemented-939.png)、[390×360](save-panel-implemented-390.png)。四宽度原图在 `/tmp/trpg-save-panel-*.png`，已人工查看；与生成参考图分开。

最终日志：`/tmp/trpg-save-panel-frontend-frozen.log`、`/tmp/trpg-save-panel-e2e-frozen.log`、`/tmp/trpg-save-panel-build-final.log`、`/tmp/trpg-save-panel-format.log`、`/tmp/trpg-save-panel-backend.log`、`/tmp/trpg-save-panel-architecture-final.log`。最初的红测试与浏览器失败日志保留，不计为最终通过。

剩余范围：游玩等待／取消／拒绝／暂停／断线的完整组合、更多权限／网络恢复场景、玩家输入及父分支历史语义仍须继续复核；没有以这 14 项替代全平台验收。此次无后端业务语义改动，未调用真实游戏模型，未改变真实配置、存档、数据库或正式环境。

## 第十七阶段：玩家取消与恢复边界（2026-10-04）

延续 imagegen 已确认的哑光档案 UI：状态卡不另加大型装饰，正文继续 DOM 渲染，恢复按钮用 44px 点击目标、正常内边距和窄屏换行。不是整个平台目标或发布收口。

- 接通已有 `cancel_request.json` 协议：玩家只可申请取消本人、已收件、仍为 `queued` 的行动。`processing`／`awaiting_player` 仍由主持 `resolve_intent` 收尾；取消是独立请求 ID，同一个取消尚未回执时不再发第二次。原行动在收到 `action_status=cancelled` 前不变成“已取消”，也不因点击取消而移动、扣物品或宣称成功。
- 重连快照新增可选 `request_type`，并向原有授权范围内的本人／主持提供请求调查员 ID，用于恢复取消入口。没有向别的玩家扩大投影范围，也没有给玩家完整主持请求正文；后端新增本人可见、他人不可见和无正文对偶。`events.json` 同步声明可选字段，旧客户端仍可忽略。
- 原 ID 重发及显式新版本重提均核对当前连接、世界、调查员和可用协议；自动超时重发也走同一门禁。取消／完成／作废的旧请求不能重新执行；已暂停的请求提示由主持恢复，同 ID 重发不是恢复主持的工具。
- 明确区分“同 ID 查询／重试”和“新版本新 ID 提交”：拒绝态的 revision 冲突只有同步后可主动新提交，没有更新版本就显示等待同步，不自动再撞旧请求。新请求保留原意图，旧请求作废。前端保留所有服务端裁决，不猜自然语言意图。
- 断线时不再声称“正在查询”已在进行，改为暂时无法确认、重连后核对。安全重发缺少原始载荷时说明原因，不根据摘要伪造请求；操作发不出去的原因在卡片内显示。仍不声称完整断线恢复组合都已验证。

最终版本证据：

| 检查 | 结果 | 边界 |
|---|---|---|
| 前端全量 | 1022 passed／93 文件 | 新增 9 项取消／恢复测试，包括自动重发断线闸门和拒绝态冲突的新 ID 对偶 |
| 结构化后端全组 | 238 passed／1 skipped／90 subtests passed | `tests/test_structured*.py`；PostgreSQL 环境未配置的 1 项明确跳过，不是全仓后端验收 |
| 同版浏览器 | 12/12，3.7 分钟 | 真实后端：多人无模型主持、存档、刷新后取消、主动读档与分支；脚本 WS：7 项结构请求／自由文本／私发／冲突／编辑取消／协议禁用／旧事件隔离 |
| 实际取消 | 回执通过 | 独立取消 ID 指向正确原行动，位置不变、零 `scene_changed`、业务过程零模型调用；本地开局只用模型桩 |
| 按钮 | 1280／939／640 三宽度通过 | 44px、内边距、nowrap、视口内及中心命中，不用 force click；初次 28px 红断言保留 |
| TypeScript／构建／格式／ruff／架构 | 全通过 | 既有构建警告不计为修复 |
| 冻结指纹 | 9 个源文件／协议／构建产物前后一致 | `/tmp/trpg-recovery-sha-{before,after}.txt` |

实际截图：[939](action-cancel-implemented-939.png)、[640](action-cancel-implemented-640.png)。三宽度原图 `/tmp/trpg-action-cancel-*.png` 已逐张查看。最终日志：`/tmp/trpg-recovery-frontend-final.log`、`/tmp/trpg-recovery-e2e-final.log`、`/tmp/trpg-recovery-structured-backend.log`、`/tmp/trpg-recovery-build-final.log`、`/tmp/trpg-recovery-format.log`、`/tmp/trpg-recovery-architecture-final.log`；中间失败不拼接为最终通过。

未提交、推送或部署，未调用真实游戏模型，未更改真实配置／数据库／存档，未触碰正式环境。完整目标仍 active：后续须补多人／云端单人的断线恢复、角色／权限变化及暂停恢复的整体浏览器组合，以及玩家输入／父分支历史的剩余语义核对。

## 第十八阶段：云端重连的权威同步门禁（2026-10-04）

### 实际缺口与修复

- `/ws/room` 的 `onopen` 原来立即把界面标成“已连接”。此时旧调查员、旧 revision 还可能留在 store，新的房间镜像和成员快照尚未收到，结构化玩家请求便可以被提前受理并排队。
- 现在连接打开只表示“连接／同步中”。先应用房间镜像；进行中的结构化世界还须收到本连接、当前世界、可解析协议版本的 `session_snapshot`，才放行动作和发送队列。重新建立 socket 会重置同步证据，不复用上次连接的快照。
- 大厅和 legacy 不被不存在的结构化快照卡住；ACK、room_sync 和回合恢复探针仍走直连路径。已有旧协议有界队列保留，结构化玩家行动在同步前明确拒绝，不偷偷排队、不自动改写或重提。
- 成员快照可以先于房间镜像到达，但两个投影都恢复后才能提交。异世界快照不能借当前连接改绑世界；真正切世界必须走对应世界连接。
- 顶栏／房间状态区采用“连接／同步中…”；玩家工具行说明“正在连接并同步权威状态，完成后可提交”。不同于主动读档：本轮重连不恢复自动存档、不改时间线，也不伪造行动结果。

### 验证与边界

- 两个房间协议测试文件 **56 项通过**：含镜像／成员快照两种到达顺序、异世界／错误协议快照、第二次连接不能借旧快照放行，以及完整 dispatcher 下恢复后新请求实际携带新调查员 ID 和 revision。原来“能力到达即可提前排队”的断言改成明确拒绝，再由玩家主动提交。
- 前端全量 **1025 passed / 93 files**：`/tmp/trpg-room-sync-unit-final.log`；TypeScript／构建通过：`/tmp/trpg-room-sync-build-final.log`。格式、ruff、架构检查、`git diff --check` 通过。没有后端实现改动，没有把前一阶段后端数字当作本轮整仓复跑。
- 真实本地后端的四客户端人类主持闭环＋云端单人闭环＋账号过期／重新登录／归档恢复 **3/3 通过**：`/tmp/trpg-room-sync-e2e-final.log`。新增浏览器探针只延迟真实服务端重连时的成员快照，证明旧场景仍可看但前往／掷骰禁用、零新行动提交；释放快照后按钮恢复，随后完成原有人类主持链路。
- 增补三宽度截图／几何断言后，云端单人两个完整用例又 **2/2 通过**：`/tmp/trpg-room-sync-layout-e2e.log`。1280／939／640 无横向溢出、掷骰文字不换行且保留内边距。实际截图：[939](room-sync-waiting-implemented-939.png)、[640](room-sync-waiting-implemented-640.png)，已逐张查看。
- 640 截图当时未见品牌标题（此前误记为上沿裁切）；第十九阶段源码核对确认是响应式主动隐藏，连连接状态一同隐藏，已在下一阶段改进。全面网络／身份／权限组合仍未穷尽。
- 按游戏 UI／前端设计规范复用此前 imagegen 素材；本轮只增加真实同步状态，没有新生成装饰，也没有改变资产拉伸规则。

基线仍为 `experiment/keeper-platform @ ce5c02f` 上的工作区改动；未提交、未推送、未部署。全部运行在本地独立临时世界／数据库，不调用真实模型、不修改实际账号 Key 或存档、不连接正式环境。完整目标继续保持 active。

## 第十九阶段：紧凑顶栏保留连接状态（2026-10-04）

### 设计与改动

- 先更正归因：`responsive.css` 在 ≤760px 使用 `#header h1 { display: none }`，不是截图或滚动造成的裁切。标题中的连接状态点也因此完全不可见，对云端恢复状态不利。
- 保留现有 84px 顶栏预算，采用“紧凑标题＋连接点／场景／单人出口”第一行、“已有贴图工具按钮”第二行。没有额外占用叙事区域，也没有添加新的装饰背景。
- 标题文本单独可省略并保留完整 `title`；连接点不参与压缩，新增可访问名称。长标题不能挤瘪桌面工具栏，长地名也只省略自身；云端单人出口与场景保持并列，多人普通成员仍不获得存档入口。
- 同步中的琥珀连接点改为常亮，取消消失式闪烁。状态颜色与现有文本提示一起表达恢复中，不靠闪动吸引注意。
- 按游戏 UI／前端设计／按钮验收规范复用已确认的 imagegen 工具贴图；本轮是布局与状态呈现调整，没有再生成重复装饰。

### 本地证据

- 前端全量及最终补跑均为 1025 项通过（93 文件）；构建与 TypeScript 通过，格式／ruff／架构检查／`git diff --check` 通过。日志：`/tmp/trpg-header-unit.log`、`/tmp/trpg-header-unit-final.log`、`/tmp/trpg-header-build-final.log`、`/tmp/trpg-header-architecture.log`。
- 真实后端本地开局／移动／长地名＋云端单人＋多人四客户端＋过期恢复 **4/4 通过**：`/tmp/trpg-header-e2e-final.log`。所有游戏服务、数据库、模型桩均为本地隔离；没有真实模型调用。
- `e2e/header-layout.ts` 检查真实标题和连接点的可见性、子项位于顶栏边界内、工具按钮未塌缩、页面无横向溢出；最终补入连接点 opacity=1 的检查，本地短窗口专项补跑 **1/1 通过**：`/tmp/trpg-header-local-final.log`。
- 本地涵盖 1280／760／640／560／520／430／390；长标题的标签级几何压力测试另涵盖 1280／939／640／390、480px 高，不修改世界状态。云端单人及多人主持／玩家均涵盖 1280／939／640／390。标题、连接点、场景和贴图铭牌已截图并逐张检查。
- 当前实际界面：[单人 390](header-solo-implemented-390.png)、[玩家 640](header-player-implemented-640.png)、[主持 939](header-keeper-implemented-939.png)。图片是实现截图，不是 imagegen 生成的假状态。

没有改变场景、检定、线索或权限协议，不需要真实模型补验。完整目标仍在进行：全面网络／权限恢复、其他浮层和游玩状态不能由本轮顶栏检查代替。未提交、未推送、未发布，保留其他工作区改动。

## 第二十阶段：草稿审批与主持恢复的权限边界（2026-10-04）

### 发现与实施

- 辅助草稿卡原来只检查服务器能力与缓存草稿，不订阅当前 `can_keeper`。现在云端必须有本人主持授权才展示；房主身份不替代授权。撤权时立即隐藏草稿正文及批准／拒绝入口，不等待后续 WS 清理。可见性标题改为“仅主持可见”，不错误承诺多位主持之间的独占秘密。
- 草稿在断线／权威同步中保持本人可读，但审批按钮禁用。关联提交按模式／世界／账号／授权／草稿 ID 隔离，同一待确认操作不双发，世界变化不会把旧草稿按钮发到新世界。服务端驳回与本地发送错误如实显示，拒绝不是批准成功。
- 主持命令的发送和旧 ID 重发共用当前账号授权门禁，防止缓存控制卡或定时重发继续提交旧权限命令。服务端仍是最终权限权威，这不是用前端代替服务端校验。
- 完整 `session_snapshot` 没有草稿投影时清空旧草稿；当前真实服务的快照总是明确提供 `keeper_drafts`（玩家为空数组），旧字段缺失也不能保留上一权限的秘密。
- 后端 `control_keeper: retry` 除继续指定待办外，还会释放人类控制权。按钮改为“交还 AI 并重试”，说明“此前已结算的行动不会回滚”，不再把它包装成不影响控制权的普通重试。长标题自身省略、完整含义留在 `title`，390px 下不会撑破按钮。
- 草稿卡使用此前 imagegen 档案材质的哑光色系；审批／拒绝与控制按钮均有 44px 高、明确内边距，未新增亮色装饰。不调整既有服务器命令语义。

### 分层证据与限制

- 新增 6 项确定性回归：撤权立即隐藏／禁止直调、同步禁止批准／拒绝、同草稿不双发与驳回可读、旧命令失权不能重发、完整快照清除草稿、重试公开释放控制权且未收到回执前仍为 paused。前端全量 **1031 passed / 94 files**：`/tmp/trpg-keeper-guards-unit-final.log`。
- 构建／TypeScript、格式、ruff、架构检查、`git diff --check` 通过：`/tmp/trpg-keeper-guards-build-final.log`、`/tmp/trpg-keeper-guards-architecture.log`。实现中一度把授权门禁插入了同名局部变量的错误函数，全量测试捕获后已移回 `gateReason` 并全量复跑；这是本轮自身修正，不归为基线故障。
- 新增浏览器草稿／恢复用例 **1/1 通过**：`/tmp/trpg-keeper-guards-e2e.log`。使用明确标注的 WS 协议替身，不是实际 AI 草稿生成验收；覆盖 1280／939／640／390、按钮尺寸／内边距／单行标签／真实命中／无横向溢出，并核对拒绝后实际 `resolve_draft` 帧与投影撤去。
- 已逐张查看当前截图：[939](keeper-recovery-implemented-939.png)、[390](keeper-recovery-implemented-390.png)。图中暂停与草稿为脚本化协议情景，不宣称来自生产或真实模型。
- 真实后端单人／多人及其余协议替身链路联合复跑 **11/11 通过**：其中真实服务 3 条（多人四客户端、云端单人、过期／归档恢复），协议替身 8 条，记录在 `/tmp/trpg-keeper-guards-e2e-final.log`。标题改为“仅主持可见”后的布局补跑 **1/1 通过**：`/tmp/trpg-keeper-guards-layout-final.log`。

本轮后端实现、权限协议、模型配置、实际存档均未修改；未调用真实模型，未提交／推送／部署。全面控制权切换、能力组合及异常状态仍待继续复核，不能由上述定向证据宣称整个目标完成。

## 第二十一阶段：主持命令能力与审批回执（2026-10-04）

### 实施

- `assisted_draft`／`agent_takeover` 标志不替代命令目录：缺少 `resolve_draft` 时草稿只能阅读，批准／拒绝禁用并解释原因；缺少 `control_keeper` 时控制卡明确只读，不显示接管按钮。
- 主持命令直调、同 ID 重发与新 revision 重提共用命令能力检查。服务端撤下某项能力后不继续发送缓存命令；当前账号、授权、连接和协议门禁仍保留。
- 审批请求分开显示“已发送”“已收件，等待结算”“已结算，等待草稿列表更新”。ACK 不冒充执行结果；结算回执先于草稿投影更新时禁止再次批准／拒绝。失败原因可见，并允许在能力仍开放时主动重试。
- 复用既有档案卡材质与工具贴图，没有为状态变化再生成背景。按钮维持 44px 高及单行标签；本轮不改变服务器审批／控制语义。

### 证据与范围

- 前端全量 **1036 passed / 94 files**，TypeScript／构建、ruff、架构检查通过。日志：`/tmp/trpg-command-capabilities-unit-final.log`、`/tmp/trpg-command-capabilities-build.log`、`/tmp/trpg-command-capabilities-architecture.log`。
- 浏览器联合 **12/12 通过**：真实本地后端 3 条（多人四客户端、云端单人、账号过期／归档恢复），协议替身 9 条；`/tmp/trpg-command-capabilities-e2e.log`。没有把协议替身称为真实模型验收。
- 新增能力缺失的只读情景，1280／939／640／390 截图无横向溢出，批准／拒绝均禁用且零主持命令发送。已逐张检查；实际界面：[939](keeper-readonly-implemented-939.png)、[390](keeper-readonly-implemented-390.png)。卡片数据来自测试协议替身。
- 两个旧的审批正例夹具只声明了功能标志、未声明审批命令，本轮补齐能力声明；负例、权限及服务端回执断言没有放宽。

仍须继续审查主持台其他命令表单在断线／能力撤回时的可用性提示，以及全面权限恢复组合；本轮传输门禁通过不等于全部界面已完成。未提交、推送或部署，未调用真实游戏模型，未改真实配置／存档，也未访问正式环境。

## 第二十二阶段：人类主持台的当前权限与能力（2026-10-04）

### 实际问题与改动

- 云端主持台原先允许旧 `keeper` 身份投影覆盖最新成员的 `can_keeper=false`。现在以当前账号成员授权为准：撤权立即隐藏主持台／资料，关闭浮层并清除本地表单草稿；再次授权不自动打开旧表单。切世界、切账号或切模式同样清理表单上下文。本地无账号的人类主持入口保留。
- 命令目录缺失时主持操作按钮明确禁用；已打开的表单也禁用字段与提交，并解释是哪项能力尚未开放。能力恢复后可继续编辑，不把功能标志当作命令授权。
- 断线／权威同步中显示具体原因，暂停命令表单与准备操作，连接恢复后再放行。不改变服务端裁定，不在本地提前宣布成功，不用房主权限代替主持授权。
- 延续 imagegen 已实现的哑光档案背景、暖灰正文与克制黄铜分隔线。没有增加新装饰，也没有改变图片缩放规则；只读状态使用正常可读的说明与禁用控件，而不是隐藏失败原因。

### 本地验证

- 新增撤权／重新授权、能力撤回／恢复、断线／同步／恢复三项回归；主持台定向 33 项通过，前端全量 **1039 passed / 94 files**。最终日志：`/tmp/trpg-console-guards-unit-final.log`。
- TypeScript／构建通过：`/tmp/trpg-console-guards-build-final.log`；格式、ruff、架构检查、`git diff --check` 通过。构建既有大 chunk 警告仍在，没有声称已优化。
- 协议替身只读情景扩充到主持工作台：无命令目录时不能发布或发线索，零 `command_request`。1280／939／640／390 实际截图已逐张检查，短窗口关闭按钮有 44px 高、内边距、单行标签及真实指针命中，未使用 force click。实际界面：[939](console-readonly-implemented-939.png)、[390](console-readonly-implemented-390.png)，数据来自协议替身。
- 冻结最终实现后联合浏览器复跑 **12/12 通过**（4.1 分钟）：真实本地后端 3 条（多人四客户端、云端单人、过期／归档恢复），协议替身 9 条；`/tmp/trpg-console-guards-e2e-final.log`。初次联合复跑也通过，但补齐断线说明时曾重新构建，因此不把它作为最终冻结版证据。没有真实模型调用或正式环境访问。

本轮不改后端、模型配置或实际存档，未调用真实游戏模型，未提交／推送／发布。整体目标仍在进行；上述授权／能力定向测试不替代全部账号、网络与角色恢复组合。

## 第二十三阶段：跨界面整体验收与云端玩家重提（2026-10-04）

### 本轮发现

- 新版本重提已经先由 `structuredReplayReason` 按原始帧执行玩家／主持门禁，却又无条件调用主持门禁。普通云端玩家因此误被拒绝。新增真实 store／实际发送帧回归在修前失败；移除错误的重复门禁后通过，保留世界、调查员、连接、协议与旁观者校验。旁观者不能重提，也不能把旧冲突请求伪标成已作废。
- 首次全套浏览器收集 61 项：52 passed、7 failed、2 skipped（17.9 分钟）。六个失败涉及旧测试从已移除的内部 ID 文本读取空场景 ID，一个涉及旧的“人类已接管”显示文案。这是本轮整体验收实际抓出的漂移，没有把定向绿灯称作全平台通过。
- 场景按钮新增 `data-scene-id`，仍只向玩家显示地点名称。过渡／待办验收读取稳定属性，不恢复内部编号显示，也不放宽移动、时间、待办与去重断言。进一步修正“取消”匹配到“申请取消”的歧义，并按请求 ID 核对取消结果。
- 人工接管验收同时核对当前“你正在主持”说明、服务端确认和 `keeper_control` 事件的 human 控制者，而不是只换一段文字让测试通过。
- 模组检查结果的摘要可能早于 busy 清除出现。单测改为等待关闭按钮实际 enabled 后核对原有焦点要求，单文件复查及全量均已通过；没有增加固定延时或降低焦点要求。

### 已取得证据与当前边界

- 前端全量 **1041 passed / 94 files**：`/tmp/trpg-platform-joint-unit-final2.log`。构建／TypeScript、格式、ruff、架构检查与 diff 检查通过；`/tmp/trpg-platform-joint-build-final.log`。
- 结构化后端 **238 passed / 1 skipped / 90 subtests passed**：`/tmp/trpg-platform-structured-backend.log`；这是结构化组，不是整仓后端。缺 PostgreSQL 环境的用例仍明确跳过。
- 全套首次日志 `/tmp/trpg-platform-full-e2e.log`，原失败截图／trace 保存在 `/tmp/trpg-platform-initial-failures-W0ROuP/test-results/`。相关链路首次复验 10/11，剩余取消定位问题已修；最终复验结果收口时补录，不提前声明全绿。
- 相关链路最终复验 **11/11 通过**（2.6 分钟）：六项过渡／交互对偶、两项待办收尾、三项真实后端草稿／暂停／结构请求恢复，`/tmp/trpg-platform-gap-recheck-final.log`。第一次全套的红灯与复验记录分别保留；不能把修前整套 52 项和修后定向 11 项拼成整套全绿。
- 第二十三阶段构建的全套复跑最终 **59 passed / 2 skipped / 0 failed**（61 项、23 文件、15.2 分钟）：`/tmp/trpg-platform-full-e2e-final.log`。两个跳过是明确未配置的外部 staging 和未授权的 live 模型；Electron 两项实际通过。对应构建指纹 `/tmp/trpg-platform-phase23-bundle.sha256`。第二十四阶段的记忆查询改动尚未包含在该构建中，须以其独立复验为准。
- 所有服务均为本地隔离 runtime／数据库与模型桩；明确清掉外部 staging 和真实模型环境开关。外部 staging 与 live 模型两项跳过，Electron 两条实际运行且通过。没有付费游戏模型调用、真实存档修改或正式环境访问。
- 本轮新增的是状态／协议测试支撑，没有重新制作视觉装饰。继续使用已生成并落实的档案夹、指南针与哑光工作台材质。

完整目标保持 active。第二十三阶段整套复跑已取得证据，主持记忆查询的筛选输入与恢复边界在下一阶段继续实施。未提交、推送或部署。

## 第二十四阶段：主持记忆查询的输入、等待与隐私边界

### 实施

- 查询筛选不再静默截短：文本最多 200 字、最多 6 个主题且各最多 40 字，按 Unicode 码点与协议对齐；超限明确解释并保留输入，不发送被悄悄改写的查询。中文／英文逗号均可分隔主题。
- 多行文本框与已有哑光档案工作台保持一致，查询结果用角色名称、中文知识类型及筛选摘要展示，不把原始 JSON 当成用户界面。
- 查询 15 秒未收到结果时结束等待并允许手动重试，不自动重发、不修改世界；真实服务端拒绝显示实际原因。旧查询回执不能覆盖新查询结果。
- 同世界重新同步会结束未确认的查询等待；切换世界清空旧记忆结果与交互线程投影，迟到的旧结果不得重新填入新世界。
- 查询操作仍遵守当前主持授权、连接与能力门禁。复用已有背景与档案材质，没有新增装饰贴图，也没有改变后端记忆查询契约。

### 浏览器复核发现

- 最初真实截图发现查询按钮仅 28px 高，随后按组件尺寸契约修为至少 44px、左右内边距至少 10px，保持单行标签。
- 输入改为多行框后，截图又发现浏览器默认的白色窄文本框：补齐显式深色背景、暖灰文字、全宽和焦点样式。测试额外断言四种宽度下的实际配色、输入宽度和内边距，不能只凭逻辑单测判断视觉合格。
- 1280／939／640／390 均检查真实按钮命中与无横向溢出。代表图：[记忆查询 939](memory-filter-implemented-939.png)、[记忆查询 390](memory-filter-implemented-390.png)。

### 验证边界

- 当前前端全量 **1056 passed / 95 files**，构建、ruff、架构检查通过：`/tmp/trpg-memory-query-theme-unit.log`、`/tmp/trpg-memory-query-theme-build.log`、`/tmp/trpg-memory-query-theme-architecture.log`。
- 样式补齐前同版功能联合验收 **9/9**：`/tmp/trpg-memory-query-joint-e2e.log`。最终样式及新增视觉断言后重新跑同组，仍 **9 passed / 0 failed**，覆盖记忆查询、人类主持三客户端、云端单人、取消、读档与分支：`/tmp/trpg-memory-query-theme-e2e.log`；构建指纹 `/tmp/trpg-platform-phase24-bundle.sha256`。没有以前一次通过替代后一次结果。
- 查询专项实际丢弃一条服务端结果，验证超时提示与手动重试；超长文本／主题通过实际出站帧证明没有发送，不只检查界面提示。
- 第二十三阶段 **59/2** 全套结果仍仅属于此前构建；本阶段未把定向联合验收称为全仓 E2E，也未新增真实模型验收。服务仅用本地隔离世界与数据库，不连接正式环境、不改真实存档，未提交、推送或部署。

完整目标仍保持 active，其他网络／权限组合与最终整套复核须继续按顶部范围矩阵处理。

## 第二十五阶段：房间邀请的失败反馈与上下文切换

- 邀请有效期明确要求 1–168 小时整数，使用次数要求 1–16 次整数，对齐现有服务端范围。无效输入保留并解释，不再经 `Number(...) || 默认值` 偷换成另一份邀请；未改服务端既有钳制规则。
- 复制邀请码显示进行中状态并禁止重复点击。剪贴板拒绝或 3 秒内未确认完成时提示重试／选中邀请码手动复制；不把超时写成确定失败，也不让迟到成功翻成“已复制”。剪贴板系统调用本身无法撤销，界面只保证不采纳过期回执。
- 邀请码、房间或账号变化时清掉旧复制状态；房间／账号变化时取消旧的退出、删除、移除成员、授权主持和移交确认，防止同一界面保留跨房间的操作意图。
- 继续复用已生成的档案室背景、哑光面板及黄铜按钮，没有为状态反馈新增装饰图。邀请区按钮实测原为 42px，新增操作尺寸断言先红，再以局部规则补为至少 44px、左右内边距 14px，不扩大所有房间按钮的样式范围。

### 证据

- 新增 9 项单测，覆盖无效输入不提交、剪贴板拒绝后重试、无响应有界等待及换房间忽略旧复制回执／撤销旧确认。当前前端全量 **1065 passed / 95 files**：`/tmp/trpg-room-invite-full-unit.log`；最终构建、格式、ruff、架构和 diff 检查通过。
- 最终真实本地后端四客户端多人闭环 **1/1 通过**：`/tmp/trpg-room-invite-final-e2e.log`。实际 HTTP 出站记录证明无效邀请参数零提交；正常请求只提交一次，并由真实服务端生成邀请供两名玩家加入。剪贴板依赖在浏览器中替身注入拒绝与成功，不能称为系统剪贴板权限验收。
- 1280／939／640／390 检查按钮实际尺寸、文字完整、命中及无横向溢出；失败反馈代表图：[640](room-invite-implemented-640.png)、[390](room-invite-implemented-390.png)。首次浏览器失败 `/tmp/trpg-room-invite-e2e.log` 为新尺寸断言捕获 42px，不隐藏为环境问题。
- 第一次定向单测另有一项测试编写错误：把产品已有“删除房间”写成不存在的“关闭房间”；已按真实控件名称纠正后跑全量，不改产品名称配合测试。
- 本轮无后端产品改动、无付费模型调用、无真实存档操作，未提交／推送／发布。最终全仓浏览器复跑仍须以最新构建重新进行，不能把上一阶段单独的联合验收拼成全套通过。

## 第二十六阶段：取消传播隔离与完整验收反例

### 整套验收先暴露问题

- 第二十五阶段构建的整套浏览器复验 **58 passed / 2 skipped / 1 failed**（61 项、16.2 分钟）：`/tmp/trpg-platform-current-full-e2e.log`。失败为模组导入重试，trace 中实际网络错误为 `ERR_FILE_NOT_FOUND`，没有服务端拒绝响应。不能将此前定向通过写成这一轮全套通过。
- 后端第一次命令入口缺少项目 import 路径；改用 `python -m pytest` 后出现 16 条备份依赖失败与一条多人连接失败。备份子进程使用了虚拟环境外的 Python：激活 `.venv/bin` 的 PATH 后专项 **19 passed**、同树全量 **1531 passed / 8 skipped / 127 subtests passed**。未更改真实配置或绕过测试断言。
- 多人连接虽然单独复跑及该次全量通过，但原失败不能因此判成无问题。新增确定性取消测试证明：广播／直接回复／批量回复／快照调用者取消，会取消 hub 已排队的发送任务，污染仍在线成员的 `send_tail`；四项反例修前 **4 failed**，撤权对偶 **1 passed**。

### 修复

- hub 已排队的发送由 hub 负责，不继承调用者的取消；直发、批量、快照、重放、广播和依赖前序任务的等待均保护发送任务。调用者本身仍可立即被取消，不吞掉其 `CancelledError`。
- 不移除发送期限、失败连接清理、当前权限检查或私密接收者过滤；撤权后排队但尚未开始的私密回复仍被拦。此处只隔离传输取消，不把玩家取消行动改成“继续执行世界命令”。
- 模组 E2E 仍使用真实 packager 输出及真实检查／安装 API，但用原包字节缓冲区传给浏览器，避免原生临时文件路径依赖；不修改模组内容，不伪造检查成功。
- 模组读取／网络异常提供中文恢复建议；安装响应丢失明确为“未能确认，可能已安装”，不声称回滚。E2E 顺序验证服务端拒绝、网络中断、原文件重试、检查通过、安装与切换。
- 本阶段没有新增装饰素材，继续使用已生成的档案夹、指南针及哑光工作台。错误文案只增强恢复指引，未改变已有布局和主题。

### 最终版本分层证据

- 取消隔离及房间相关定向 **80 passed**：`/tmp/trpg-room-cancel-after.log`；新反例均转绿，撤权对偶保持绿。
- 后端全量 **1536 passed / 8 skipped / 127 subtests passed**：`/tmp/trpg-platform-cancellation-full-backend.log`；前端全量 **1067 passed / 95 files**：`/tmp/trpg-upload-network-full-unit.log`；构建、格式、ruff、架构及 diff 检查通过。既有 SQLAlchemy warning 与大包提示未处理，不写成已解决。
- 最新构建真实本地后端联合 **6/6**：`/tmp/trpg-upload-network-and-room-e2e.log`，包含模组检查／安装、多角色多人闭环、模型设置和记忆查询。无真实模型调用，模型路径使用本地桩。
- 冻结版本完整浏览器复跑已结束：**59 passed / 2 skipped**（16.0 分钟），`/tmp/trpg-platform-cancellation-full-e2e.log`；外部 staging 与未授权真实模型跳过。指纹 `/tmp/trpg-platform-phase26-fingerprint.sha256`。这是第二十六阶段版本的完整结果，不冒充后续修改版本的验收。

本轮仍未提交、推送或部署；正式环境、真实配置与玩家存档未触碰。完整目标保持 active，最终整套与范围审计未完成。

## 第二十七阶段（实施中）：玩家申报历史的明确语义

完整范围审计确认此前登记的玩家自由输入历史缺口仍存在：`message_history` 只投影 `message_completed`，已受理的自由申报虽然保存在 `player_requests`，刷新后原文不在聊天记录中。不能因为当前待办可恢复就宣称完整历史可恢复。

新增 `tests/test_structured_action_history.py`，用真实命令受理与身份投影验证：长原文恢复、终态请求恢复、失败后同 ID 重发的稳定去重，修前均失败；前提交者降为旁观者、主持撤权的对偶仍不应读到私下申报。原始日志 `/tmp/trpg-action-history-duals-before.log`（3 failed / 2 passed）。前端反例 `/tmp/trpg-action-history-front-before.log`（2 failed / 18 passed）。

下一步实现边界：显示为“行动申报”，不是人物已经说出口的台词，也不暗示行动已经执行；仅提交者和当前获授权主持可读，其他玩家／旁观者不新增可见性。与已提交叙事按稳定事件位置排序，重复受理不重复显示，不重放骰子、命令或记忆。分支父时间线的阅读仍需另行核对，不能由此项代替。

第二十六阶段整套结束后已实现：历史投影按同一请求的最早存活受理事件去重，避免跨分页重复；原文读取请求账本，使用内部稳定 ID 与权威调查员归属；只允许提交者／当前授权主持读取。没有存活受理事件的请求不从账本单独复活。协议新增可选 `entry_kind`，行动申报限定调查员；前端实时回显和恢复都标“行动申报 · 不代表已执行”，不改变原文、不新增执行权限。

定向后端 **13 passed**；后端全量 **1543 passed / 8 skipped / 127 subtests passed**（`/tmp/trpg-action-history-full-backend.log`）；前端全量 **1069 passed / 95 files**（`/tmp/trpg-action-history-full-unit.log`）；tsc、ruff、架构、构建及 diff 检查通过。新增分页去重与无存活受理事件对偶。

真实本地服务与浏览器验收 **1 passed**：完整输入、受理回执、刷新后原文与标识恢复，场景不变，零模型调用；四宽度均无横向越界。`/tmp/trpg-action-history-real-e2e-final.log`，截图 [390](action-history-390.png)、[939](action-history-939.png)。测试曾改用真实发送按钮排查，但不能据此归因 Enter：产品输入框支持非输入法状态的 Enter 提交。实际发现是类型标识错加骰子容器，修正消息容器并加回归，未放宽产品断言。

本阶段冻结版完整浏览器复验已结束：**59 passed / 2 skipped**（16.0 分钟），`/tmp/trpg-action-history-full-e2e-frontend.log`。一次误在仓库根目录启动的运行因 Playwright 配置错误立即退出（`/tmp/trpg-action-history-full-e2e.log`），不算产品验收。这是第二十七阶段版本，不能替代后续第二十八阶段的全套复核。完整目标继续 active；未提交、推送或发布。

## 第二十八阶段（实施中）：分叉前的只读历史档案

修前缺口：结构化分支正确地不复制 outbox，但也没有独立历史档案，分叉前的公开叙事、获准私信和行动申报均不可阅读。`tests/test_structured_branch_history.py` 用真实分支与身份快照证明四个反例：接收范围过滤后的共同经历、父世界后续变化不污染／删除父事件不损坏子档案、撤权／降为旁观者只保留公开内容、二次分支历史不重复且不含父世界未来。修前日志 `/tmp/trpg-branch-history-before.log`（4 failed），现已转绿。不是用父世界当前记录伪造共同经历。

实现采用独立、只读历史档案，而不是复制可投递事件或在阅读时递归跟随可变父世界：

- 分叉时与源状态一起冻结历史边界，保存此前已完成叙事与已受理的自由申报；不含命令、骰子、记忆、未完成流式片段或权限授予事件。
- 档案与新分支事件游标分离。新分支 outbox 仍为空、控制权仍不继承，不因阅读改变场景／物品／时间／revision。
- 持久档案保留接收范围与申报者身份，读取时按新分支当前身份重新过滤；不能把创建分支者的一份主持视图分享给所有成员。
- 嵌套分支继承已有共同档案与父分支自身的已提交历史，按共同经历排序，不依赖父世界持续存在。新表须遵守迁移、打包旧库接管、残缺 schema 拒绝的现有契约。
- UI 采用现有生成素材的档案夹视觉，聊天顶部默认收起“分叉前的历史”，与本时间线正文明确分区；长内容按需分页，操作不挤占当前叙事。不为小型阅读控件重复生成装饰图。
- 旧分支没有冻结边界和历史档案时必须如实说明未保存，不从父世界当前记录补出可能晚于分叉的内容。

实施顺序：先在第二十七阶段整套复跑期间只增加未接线的提取函数与回归，不改受测运行链路。`history_archive_capture.capture_local_history` 要求调用方持源状态锁并传冻结序号；逐页提取完整历史，同时保留原接收范围／申报者，排除未来事件，不复制 outbox。初始独立 **4 passed**（`/tmp/trpg-branch-history-capture.log`）：公开／私信与长申报、固定游标、53 条跨页不截断、真实 SAN 命令与普通骰不得进入叙事档案，读取无副作用。当时四个分支回归仍为 **4 failed**；之后才接线转绿，不能拼接成一次完整验收。

上述阶段等待已经结束，随后已接通：0019 迁移／`BranchHistoryEntry` 独立账本、源状态锁下捕获／复制祖先档案、按当前身份过滤的快照及 `scope=inherited` 分页；打包 `LATER_TABLES` 同步。前端默认收起，按需读取，使用已生成宽档案夹九宫格素材，明确区分分叉前档案与当前时间线；私密回执在世界切换／同世界权威恢复后作废。旧分支缺失档案如实说明；旧祖先缺失造成部分档案的标识随后续分支继承（反例 `/tmp/trpg-branch-history-partial-before.log`：1 failed / 6 passed，现已修）。

阶段验收：恢复／分页／打包 **36 passed**，迁移及初版分支 **8 passed**；提取／权限 **14 passed**；前端最终全量 **1075 passed / 96 files**（`/tmp/trpg-branch-history-final-unit.log`），tsc、构建、架构、全仓 ruff 通过。真实本地浏览器分支 **1 passed**（`/tmp/trpg-branch-history-real-e2e.log`），实际发布共同叙事、53 条历史分页、分支刷新及四宽度按钮命中，零付费模型调用；[939](inherited-history-939.png)、[390](inherited-history-390.png) 已目视核对。新增旧档案完整性提示和键盘折叠后，定稿后端全量 **1567 passed / 8 skipped / 127 subtests passed**（`/tmp/trpg-branch-history-final-backend.log`），三客户端／云端分支／读档／档案浏览器联合 **7 passed**（`/tmp/trpg-branch-history-joint-e2e.log`）。

随后补了 HTTP 撤权／401／scope 校验／私密缓存的真实 TestClient 回归，分支专项 **8 passed**（`/tmp/trpg-branch-history-http-final.log`）。包含该新增测试的全量后端已以 exit 0 结束：**1568 passed / 8 skipped / 127 subtests passed**（`/tmp/trpg-branch-history-final-http-full-backend.log`）；本阶段冻结版完整浏览器亦 exit 0：**59 passed / 2 skipped**，15.9 分钟（`/tmp/trpg-branch-history-full-e2e.log`）。该证据早于下一阶段运行代码变更；不能冒充下一阶段整套验收。完整目标 active，剩余范围审计未完成；未提交或发布。

## 第二十九阶段（实施中）：调查笔记与行动输入的真实反馈

范围审计发现笔记快捷行动仍调用旧 `sendAction` 并按旧回合输入锁判断，发送前就关闭窗口；输入法 Escape 也会关闭窗口。结构化自由输入静默截断至 2000 个 UTF-16 单元，导致本地回显与持久申报可能不同；主输入未显示拒绝原因，失败时还会修剪原草稿。先补反例，上一阶段整套浏览器运行期间保持运行代码冻结。

修前：`/tmp/trpg-notes-input-before.log` **4 failed**（结构化入口、失败保留、超长拒绝、Unicode 完整性），`/tmp/trpg-notes-feedback-before.log` **4 failed / 17 passed**（含重复的入口两项、拒绝提示与输入法 Escape）。不能累计成八个独立缺陷。拟沿用生成档案夹素材并接共享键盘约束，结构化门禁与实际发送共用；超限明确拒绝而非截断，草稿保留。私人笔记离线排队／回执范围另行验证，未声称已解决。

上述反例已修并接线：快捷行动走 `sendPlayerText` 的结构化申报／经典兼容入口，失败保留浮层并展示原因，成功才通过正常关闭路径收尾；结构化不再依赖旧回合锁，但仍校验当前连接、房间同步和成员角色。UI 使用已生成的宽档案夹九宫格，窄屏改两列，固定头尾与滚动正文，IME-safe Escape／Tab 约束／返回焦点。自由输入超过协议上限明确拒绝，原草稿保留；Unicode code point 与服务端 JSON Schema 口径一致，不丢半个表情字符。

私人笔记继续补了三个离线反例：`/tmp/trpg-notes-offline-before.log` **3 failed**。实现即时发送，不进入本地／房间重连队列；连接状态未同步或底层发送失败时不清未保存标识，不伪装保存中。既有 `safeSend` 队列语义不变，只有私人笔记使用新直接发送通道。新增底层故障与正控制，相关笔记／传输／房间 **57 passed**（`/tmp/trpg-notes-offline-after.log`）。仍未覆盖“已成功发出但长期没回执”的超时恢复与全部跨窗口冲突体验；不声明这些已修。

本阶段前端全量最终复跑 **1089 passed / 97 files**（`/tmp/trpg-notes-input-final-unit-rerun.log`），tsc／构建通过，ruff／架构／diff 检查通过；后端运行代码未改，1568 的上一阶段证据仍有效。必须保留一次测试波动：前一整套为 **1 failed / 1088 passed**，失败在 `ModuleImporter boundaries > restores the import entry when the file picker disabled and blurred it during upload`；未改该测试或放宽断言，独立复跑 6 passed、同代码整套再跑通过，原因尚未定性（`/tmp/trpg-notes-input-final-unit.log`、`/tmp/trpg-notes-module-focus-probe.log`），不能算成已修焦点缺陷。

真实本地后端 UI 与三客户端联合 **5 passed**（`/tmp/trpg-notes-input-final-joint-e2e.log`）：快捷行动确为 `action_request/freeform` 而非旧 `action`、位置不变、保存刷新、超长输入零提交／原草稿保留、四宽度按钮尺寸与命中、短窗口 Tab 循环／Escape 返回入口；房间私人笔记 A 保存成功，B 读为空且 B／主持帧无 A 内容。人类主持阶段零模型调用，旧开局仅本地模型替身。截图 [939](investigator-notes-939.png)、[390](investigator-notes-390.png) 已目视检查。首轮尺寸断言在 pop-in 动画未结束时测到 41.9px，按技能要求等稳态再测，未降低 44px 标准。

本阶段冻结版整套浏览器已 exit 0：**60 passed / 2 skipped**，16.2 分钟（`/tmp/trpg-notes-input-final-full-e2e.log`）。两条跳过仍为外部 staging 和需授权的 live 模型；这是材料查看器接线前的版本，不替代下一阶段全套验收。完整目标 active；后续继续图片素材交互／私人笔记恢复等范围审计，未提交、推送、部署或调用收费模型。

## 第三十阶段（实施中）：玩家收到图片后的完整阅读

现有 `HandoutLayer` 图片只有鼠标点击入口，放大层无 dialog／具名关闭／键盘约束；十秒自动收起会将正在阅读的大图一起移除，图片失败只剩空白。先增加真实组件反例，`/tmp/trpg-handout-viewer-before.log` **4 failed**，未改运行代码以维持上一阶段全套浏览器冻结。

新增独立、尚未接线的 `HandoutCard`／`HandoutImageViewer`：到达提示保留未交互时十秒收起，阅读／焦点／悬停时暂停；查看器具名关闭、IME-safe Escape／返回入口、适应窗口与原尺寸滚动、失败重试／12 秒有界等待，卸载清理定时器。只显示已授权载荷，不查询作者目录或提交行动。独立 **7 passed**（`/tmp/trpg-handout-unwired-components.log`），不算旧入口已修；接线、真实后端授权图片和四宽度截图仍待完成。

通过内置 imagegen 新生成材料查看效果图，已保存项目副本与提示词，作为比例和材质参考；正式皮肤复用已确认的生成档案夹 1x/2x 九宫格。没有新生成假线索作为运行资产，也不把示意设备框做进页面。此次不改后端 `src/` 的 handout 分发／结算／提示词；无收费模型主线授权，未声称完成真实模型主线验收。

上一阶段整套终态确认后，才将新 `HandoutCard` 接入 `PanelLayers` 并删除旧重复实现。接线相关 **36 passed**（`/tmp/trpg-handout-wired-components.log`），其中含权威清除材料后查看器立即消失、重试状态和卸载清定时器；最终前端全量 **1101 passed / 100 files**（`/tmp/trpg-handout-viewer-full-unit.log`）。tsc／构建／ruff／架构／diff 通过；后端运行代码未改。

真实人类主持四客户端链路（主持／甲／乙／旁观）**1 passed**（`/tmp/trpg-handout-viewer-real-e2e.log`，1.6 分钟）：主持定向分发真实模组图片，甲通过键盘打开，1280／939／640／390×480 四宽度关闭按钮 44px／padding≥10／命中无溢出，超过十秒仍在阅读，原尺寸切换、Escape 返回入口、查看零行动提交；乙／旁观帧仍无图片，未授权 asset HTTP 请求仍 404。复核 [939](material-viewer-939.png)、[390](material-viewer-390.png)，材料完整展示，不裁切、不将图像拉满低分辨率区域。图片加载失败与 12 秒卡住路径目前是组件故障测试证据，不伪称真实网络故障浏览器验收。

本阶段完整浏览器回归 `/tmp/trpg-handout-viewer-full-e2e.log` 终态 **60 passed / 2 skipped，16.3 分钟，exit 0**；受测运行文件指纹 `/tmp/trpg-platform-phase30-fingerprint.sha256`，运行期间接线／dist 保持不变。这是材料查看阶段的完整回归，不替代下一阶段代码的验证。未提交／推送／部署，所有运行使用本地隔离世界。

### 阶段 31：私人笔记的确认、期限与世界归属

反例先行：前端四项探针中三项真实失败（保存发出即清未保存标识、读取永远卡住、读取响应误结束保存）；发送期间继续编辑的保护原先已成立，不算新修复。记录 `/tmp/trpg-notes-confirmation-before.log`。后端独立连接探针最初暴露夹具缺少用户行，补齐真实外键夹具后再次运行，准确复现“携带 old-world 的保存仍写入当前世界”，见 `/tmp/trpg-notes-stale-room-before.log`；不把夹具错误当作产品证据。

实现：`NotesRequests` 管理请求 ID、世界／账号源范围和 15 秒期限；发送成功不等于保存确认。超时只说明“尚未收到确认”，不声称后台一定没保存，不自动重发。匹配回执到达后只清已保存版本，发送后继续编辑的草稿保留；旧世界／其他请求／超时后迟到的有标识响应不能确认当前草稿。重新读取若服务器内容与草稿一致，才确认已保存；内容不同则保留草稿和服务器新 revision，后续覆盖需用户操作。旧服务器缺少关联字段时保留明确的兼容路径，但读响应仍不能冒充保存确认。没有新增 localStorage 私人笔记缓存。

本地与房间后端均在写入前拒绝客户端声明的旧世界，并回传**服务端实际世界**和有界请求 ID；请求字段不是授权依据。房间笔记依旧只向同账号窗口广播，不发给其他玩家或主持。前端正在保存时，房间恢复快照不作为保存回执。其他窗口的有标识广播不确认本窗口操作，需要重新读取核对。笔记路由从已达上限的 server.py 提取至 `src/app/player_notes_handlers.py`；未放宽架构门禁，server.py 1670/1699。

故障浏览器验证：真实本地后端实际保存，Playwright 仅丢弃首次 saved 响应，等待期限结束，草稿仍在、保存按钮恢复、点击“重新读取”后从真实服务端确认内容，**1 passed**（`/tmp/trpg-notes-confirmation-real-e2e.log`）。不涉及付费模型，开局使用本地 bootstrap stub。初次截图发现 390px 三按钮超出文件夹内沿，修正 min-width 并加强容器边界断言；同版联合复验 **6 passed，2.8 分钟，exit 0**（`/tmp/trpg-notes-confirmation-joint-e2e.log`），包含真实四客户端私发隔离、笔记故障恢复、快捷行动和暂停／批准恢复。1280／939／640／390×480 控件命中与尺寸通过，实际复核 [939](notes-confirmation-recovery-939.png)、[390](notes-confirmation-recovery-390.png)。恢复按钮沿用现有文件夹 UI 和尺寸规则，没有为小控件额外生成装饰图。短窗口的快捷行动占据较多首屏，笔记正文和状态仍需滚动，作为下一阶段信息优先级改进，不能只凭按钮可点击就说整体阅读体验完成。

本阶段全量：后端 **1580 passed / 8 skipped / 127 subtests passed，exit 0**（`/tmp/trpg-notes-confirmation-full-backend.log`）；前端 **1110 passed / 101 files**（`/tmp/trpg-notes-confirmation-full-unit.log`）；67 项相关前端与 53 项相关后端通过。ruff／架构／tsc／格式／构建通过，既有 chunk 大小和 SQLAlchemy 删除行数警告保留登记。完整浏览器回归 `/tmp/trpg-notes-confirmation-full-e2e.log` 终态 **61 passed / 2 skipped，16.8 分钟，exit 0**，受测运行文件指纹 `/tmp/trpg-platform-phase31-fingerprint.sha256`，期间运行文件保持冻结。该结果属于本阶段，不替代下一阶段布局改动的证据。未提交／推送／部署，未修改真实存档、配置或生产环境。

执行记录：首次整套 Playwright 误在仓库根目录运行，因未使用 frontend 配置而 discovery 失败（`/tmp/trpg-notes-confirmation-e2e-wrong-cwd.log`）；已终止后从 frontend 正确启动，不将执行目录错误算作产品回归或通过证据。

## imagegen 提示词

### 阶段 32：笔记首屏层级（已接入运行界面）

依据上一阶段实际 390×480 截图，笔记正文位于快捷行动后面，状态也需滚动才看见；该问题不是按钮尺寸断言能够证明已解决的。新 [imagegen 效果图](notebook-reading-concept-v1.png) 与 [完整提示词／来源](notebook-reading-prompt-v1.md) 已保存，桌面／窄窗／短窗对照采用“正文第一、快捷行动折叠、固定操作”层级，仍复用现有 1x/2x 文件夹素材，不将效果图中文字写进游戏内容。

已实现独立 `NotebookSections`：正文先于快捷入口；切换只展开／收起，不申报行动；草稿节点不被卸载，重新打开回到笔记。独立 **3 passed**（`/tmp/trpg-notebook-sections-unit.log`），与既有 UtilityPanel 合跑 **9 passed**（`/tmp/trpg-notebook-prepared-unit.log`），tsc 通过。配套未导入 `notebook-sections.css` 保留 44px 操作、短窗隐藏英文眉题、缩减非必要边距并增加状态文字字号。尚未接入 UtilityPanel／CSS／dist，完整浏览器回归期间运行文件指纹保持一致。故这一节是已完成的组件准备，不是已完成的界面布局或尺寸验收；短窗、IME／焦点／真实申报需接线后验证。

上述为准备时点。阶段 31 整套回归终态确认后，UtilityPanel 正式接入组件与 CSS；接线前“默认应有折叠入口”的反例 **1 failed**（`/tmp/trpg-notebook-wire-before.log`），接线后相关 **10 passed**（`/tmp/trpg-notebook-wired-unit.log`）。实际应用默认进入笔记，快捷行动需主动展开，原有房间／角色／连接门禁不变；没有新模型调用或文字解析器。

新 `notebook-reading.spec.ts` 是脚本化笔记回执的**布局／键盘**测试，不是存储验收。检查 1280／939／640／390×480 与 390×360：首次进入时文本框至少 80% 可见、恢复提示首屏可读，Enter／Space 展开收起零行动提交且草稿不丢，44px 控件可命中，IME Escape 不关窗、正常 Escape 返回入口。原尺寸查看和九宫格素材仍复用，无需新位图尺寸。首次截图发现底部按钮过于贴近素材内沿，保留 24px 下部缓冲并加强 DOM 边界断言；最终 [939px](notebook-reading-939.png)、[390×360](notebook-reading-390-short.png) 已实际复核。

最终同版联合浏览器 **12 passed，3.7 分钟，exit 0**（`/tmp/trpg-notebook-final-joint-e2e.log`）：上述六项布局用例＋真实四客户端人类主持隐私／笔记保存链路＋真实本地丢回执恢复／快捷申报／刷新批准／暂停接管。快捷入口折叠后，旧快捷按钮验收先主动展开、逐个滚入中段再检查尺寸，不削弱门禁或提交断言；首屏正文可见性由新增独立断言覆盖。

前端最终全量 **1114 passed / 102 files，exit 0**（`/tmp/trpg-notebook-final-full-unit.log`），tsc／格式／构建／ruff／架构／diff 通过，后端代码与阶段 31 相同。执行失误单独保留：Testing Library 中误用了 Playwright 的 exact 参数导致首次构建失败，误接着运行旧 dist 的测试已停止（`/tmp/trpg-notebook-build.log`、`/tmp/trpg-notebook-reading-e2e.log`），不算新布局验收；另一次从根目录误调用 npx Vitest 发现错误版本／配置，终态后改用 frontend 固定本地二进制复跑（`/tmp/trpg-notebook-vitest-wrong-cwd.log`），未改项目依赖、不把该批错误计为产品回归。

新版完整浏览器回归已 exit 0：**67 passed / 2 skipped，17.4 分钟**（`/tmp/trpg-notebook-final-full-e2e.log`）。运行文件指纹 `/tmp/trpg-platform-phase32-fingerprint.sha256` 在运行期间保持一致，终态后才接入下一阶段 API 改动。目标保持 active；未提交／推送／部署，未修改真实存档与生产配置。

## 第三十三阶段（实施中）：云端 HTTP 无回执的有界恢复

发现公共 `apiFetch` 默认没有期限；显式期限仅依赖 AbortSignal，不能独立保证响应正文等待有界。修前新增五项 API 回归，有效反例记录 `/tmp/trpg-http-deadline-before.log`：四项无限等待导致用例超时，一项缺少默认取消信号。首轮测试遗漏 fetch mock 已修正，首轮夹具错误不算产品缺陷。

独立 `RequestDeadline` 七项通过后，等待阶段 32 全套终态，再接入公共 API。默认整次请求 30 秒，已有明确预算优先；响应头与成功／错误 JSON 正文共用期限，超时取消 transport，即使 transport 不响应取消仍退出等待，成功／失败均清理计时器。没有自动重发写操作，不把超时当作服务端撤销。服务器／账号 generation 变化仍优先忽略迟到旧响应；本地二进制模组上传的独立长预算未修改。

定向 **45 passed / 4 files**（`/tmp/trpg-http-deadline-focused.log`），包含实际登录／单人建局／多人建房／邀请码加入／邀请生成状态机退出 busy，零自动重发、无虚假世界或邀请码。旧迟到正文测试改为等待正文回调真正安装，再切换服务器；保留旧信息必须被拒绝的原断言，而非依赖固定一个 microtask。已有 fetchMe 的 15 秒用例从原生 API spy 改为真实有界退出／取消／计时器清理行为断言。

前端全量 **1131 passed / 105 files，exit 0**（`/tmp/trpg-http-deadline-full-unit.log`），tsc／格式／构建／ruff／架构／diff 通过。浏览器故障与边界联合 **5 passed，1.3 分钟**（`/tmp/trpg-http-deadline-browser.log`）：实际页面登录无回执恢复／手动重试、服务器切换迟到 401、2x 图与键盘、会话检查取消／超时、角色库真实账号隔离。首项只模拟 HTTP 故障，不冒充真实认证存储验收；其余已有实际本地服务链路。四宽度按钮可见，主代理实际复核 [390px 截图](http-login-recovery-390.png)。

公共 API 改动后的完整界面回归已 exit 0：**68 passed / 2 skipped，17.9 分钟**（`/tmp/trpg-http-deadline-final-full-e2e.log`）。运行期间 `/tmp/trpg-platform-phase33-fingerprint.sha256` 全部一致，终态后才接入下一阶段变更。该范围不新增美术资产，复用既有生成档案夹／指南针错误页；不涉及后端结算、生产或真实模型调用。

## 第三十四阶段（准备中）：房间管理动作不在重连后偷偷执行

继续审计发现准备按钮断线时未禁用；`toggleReady`／`startGame`／`assignActor` 都直接用可排队的 `roomSend`，没有验证权威同步或实际 socket。先添加反例：**3 failed / 103 passed**（`/tmp/trpg-room-live-intent-before.log`），分别是 disconnected／connecting 下三类命令仍入发送通道，以及断线准备控件仍可点击。此阶段运行代码尚未接线，不能声称已修。

新增未接线 `sendRoomFrameNow`，拒绝断线／未同步／旧世界 socket／实际关闭的 socket，发送抛异常返回未发送，不提供重连队列。独立 **6 passed**（`/tmp/trpg-room-send-now-prepared.log`）；第一次 tsc 发现测试夹具 readyState 被推断为字面量 1，已显式改为 number 后复核，非产品缺陷。拟与既有 transport.sendNow 合用，保持其他 legacy 消息的既有队列语义；准备／开局／指定行动改为仅即时发送，拒绝显示“尚未发送”。

已准备独立 `room-ready-recovery.spec.ts`：使用临时真实后端／真实注册建房，只延迟房间 WS 权威镜像，验证控件禁用、四宽度尺寸、同步后零自动提交、主动准备后实际广播。该用例尚未运行；不把它写成验收通过。阶段 33 全套仍在运行（session 7287），期间 `/tmp/trpg-platform-phase33-fingerprint.sha256` 运行文件全部核对一致，尚未接线或重建 dist。

沿用既有生成档案夹／安静黄铜主题，不新增装饰图；按 frontend-design／game-ui-frontend／ui-button-check 保持控件尺寸与可读反馈。game-ui 的相对提示词参考文件在当前技能安装路径缺失，采用已完整读取的主技能与项目既有视觉契约，不因此阻止修复。

以上为准备时点。阶段 33 全套终态后正式接线：`roomSendNow` 与既有 transport.sendNow 共用已同步、当前世界的真实 socket；准备／开局／指定行动仅走即时通道，不改变其他消息旧队列语义。未连接或实际 send 失败显示“尚未发送”，不乐观改 readyUserIds。房间“准备”在 connecting／disconnected 时禁用，并有原因 title；同步后仍须玩家主动操作。

相关组件／online／实际 room-ws 测试 **171 passed / 5 files**（`/tmp/trpg-room-live-intent-wired.log`），含旧队列仍可重放的正控制、即时通道拒绝后无补发、初始 open 未恢复镜像时拒绝、实际 socket 失败诊断。真实浏览器四宽度／同步恢复／多人开局与前端全量仍待收口；不使用准备版六项替代最终接线验收。

接线版前端全量 **1142 passed / 106 files，exit 0**（`/tmp/trpg-room-live-intent-full-unit.log`），tsc／格式／构建／ruff／架构／diff 通过。首次真实浏览器联合 **1 failed / 6 passed，4.2 分钟**（`/tmp/trpg-room-live-intent-browser.log`），新用例实际证明准备禁用，但在按钮 white-space 非 nowrap 的尺寸契约处失败，不能算整条同步恢复已通过。主代理实际查看失败截图另发现“删除房间”入口仅 21px 高：明确为此前遗漏，非此轮新增按钮。

保留尺寸断言，补 `.room-delete-entry` 的 44px／padding／不压缩规则、准备区不换行、确认操作至少 44px，房间说明改为已有暖灰 token／13px／1.65 行距，降低阅读困难而非增加发光装饰。新增删除入口四宽度命中，查看确认和取消必须零 DELETE 请求。最后同版联合回归待终态（`/tmp/trpg-room-live-intent-final-browser.log`），不把修前六项绿灯与修后定向用例拼成一次全绿。

联合回归已 exit 0：**7 passed，4.1 分钟**（`/tmp/trpg-room-live-intent-final-browser.log`）：真实同步延迟／手动准备、真实四客户端 human 私发／检定／道具／存档、Electron 与浏览器联机／后端生命周期、云端单人无 Key 开局／过期／归档。均为独立本地数据库，未调用外部模型。截图 [939px](room-ready-wait-939.png)、[390px](room-ready-wait-390.png) 已实际复核：删除入口完整、说明可读、窄屏操作换行有序。

联合回归终态后另修一处文案：connecting 状态不再把尚未完成同步写成“连接已断开”，独立组件断言验证准确原因。当前版全量与最终完整浏览器另行复跑，不将前述同版七项冒充这次文案更新后的全仓终态。

当前版前端全量终态 **1143 passed / 106 files，exit 0**（`/tmp/trpg-room-live-intent-final-full-unit.log`），tsc／格式／最终构建（`/tmp/trpg-room-live-intent-final-build.log`）／ruff／架构／diff 全过。后端运行代码本阶段未改，仍沿用阶段 31 同树全量证据。最终完整浏览器 `/tmp/trpg-room-live-intent-final-full-e2e.log` 已 exit 0：**69 passed / 2 skipped，18.2 分钟**，冻结指纹 `/tmp/trpg-platform-phase34-fingerprint.sha256` 在运行期间保持一致。两条跳过为外部 staging 与需授权的真实模型规格，不冒充通过。终态后才接入阶段 35；未提交、推送、发布或调用外部模型。完整目标保持 active，仍需据当前源文件逐项核对完整范围，不由局部通过宣称整个任务完成。

## 第三十五阶段：线索图片的阅读、恢复与身份边界

完整范围复核发现调查员侧线索缩略图仍走旧的无名覆盖层：没有明确关闭控件、焦点恢复、加载失败重试，切换世界或撤回线索也不清理已打开的图片。新增实际 InvestigatorPanel 回归修前 **5 failed**（`/tmp/trpg-clue-preview-before.log`），不是仅替换视觉样式。

现由 `ClueImagePreview` 复用既有 `HandoutImageViewer` 与生成的 1x/2x 九宫格档案夹，提供有名称的阅读弹窗、原尺寸查看、加载期限与重试、IME 安全的 Escape 和关闭后焦点返回。选择绑定模式、世界、当前面板路径、调查员与云端身份；当前线索投影不再包含该来源时立即隐藏并丢弃，不等退出动画。阅读不发行动、不授予新线索，也不从文字推断权限。面板同时改用当前路径的 canonical clues，避免结构化模式用 legacy 线索维护新标记。

独立组件 **6 passed**、最终接线相关 **25 passed**；当前前端全量 **1151 passed / 108 files，exit 0**（`/tmp/trpg-clue-preview-full-unit.log`）。联合浏览器 **4 passed，2.3 分钟，exit 0**（`/tmp/trpg-clue-preview-joint-browser.log`）：脚本化 legacy 图片投影的四宽度／键盘／撤回／换世界／读取失败重试，以及真实 legacy 三卡片流程、真实本地四客户端人类主持私发／检定／道具／移动／存档流程。脚本化图片两项不是服务端授权验收，不与真实多人证据混淆。主代理实际复核 [939px](clue-image-reading-939.png) 与 [390px](clue-image-reading-390.png)，边框无拉伸、阅读区域与按钮均可见；1280／939／640／390 的 44px、内边距、单行和命中由浏览器断言覆盖。阶段 35 完整浏览器回归尚待单独运行，不沿用阶段 34 的完整结果。

### 下一项已确认缺口（未实施）

阶段 35 的 tsc／Prettier／ruff／架构／diff 已通过。第一次串行格式检查中 PATH 只应用于首个命令，使随后 Prettier 找不到 node（exit 127）；改用导出的固定 Node PATH 后整串 exit 0，执行环境错误不算产品缺陷。完整浏览器日志 `/tmp/trpg-clue-preview-final-full-e2e.log` 已有终态 **71 passed / 2 skipped，20.1 分钟**；后续进程句柄已消失，按最终日志确认而非重新启动。受测文件指纹 `/tmp/trpg-platform-phase35-fingerprint.sha256` 终态复核全部一致，之后才接入阶段 36。两条跳过为外部 staging 与真实模型规格。

结构化玩家收到独立图片后，`handout_presented` 只进入短时 handouts；关闭后没有持久的“已收到素材”目录。后端虽保存 `asset_grants`，`session_snapshot` 只给主持 `keeper_assets` 作者目录，未给接收玩家投影其已获授权素材；`structuredCluesToState` 也只有 assetLabel，没有可重新读取的稳定素材标识。故本阶段修复不代表结构化图片已可在刷新后重新打开。

下一阶段应从实际接收授权账本投影只读目录，放入已有线索卡片而非增加第四张主卡，点击时仍走授权素材 HTTP；必须测试接收者可查看、其他玩家与旁观者连 ID／标题都收不到、刷新／读档／分支与身份切换无旧内容残留。不能直接把主持作者目录下放，也不能靠聊天关键词恢复素材权限。本项未改后端、未调用模型、未修改真实存档。

## 第三十六阶段（准备中）：收到的图片可重新打开

按 imagegen／frontend-design／game-ui-frontend／ui-button-check 先做 [效果图](received-materials-concept-v1.png)，[提示词与实现取舍](received-materials-prompt-v1.md) 已落项目。主代理已实际查看：三个主卡、线索卡内部素材折叠符合层级；实现继续沿用当前九宫格档案夹，而非把效果图整张铺进 UI。首版不自动下载缩略图，点击才读原图，避免授权目录每次刷新造成多张图片自动请求。

新增未接线 `ReceivedMaterials`，只接收由调用方提供的接收者目录，按稳定素材 ID 走现有授权 HTTP；读取有既有 15 秒期限，迟到旧身份／读档 generation 响应丢弃，撤回立刻隐藏，不把读失败解释成未获线索，更不自动重发游戏命令。独立组件 **4 passed**（`/tmp/trpg-received-material-prepared-unit.log`）与 tsc 通过。尚无运行入口／CSS／目录投影，因此不是功能验收完成。

真实临时数据库反例 `/tmp/trpg-received-material-before.log`：**7 failed / 1 passed**，缺少 `received_assets` 快照目录；Agent 不应获得 UI 目录的反向控制原样通过。测试要求刷新可恢复、只包含本人已提交授权、去重并排除不存在／非图片素材、不泄漏文件路径、主持控制调查员时作者目录与收到目录分离、撤回更新。后端与协议尚未接线；阶段 35 全套浏览器（session 14819）运行期间原有运行文件指纹仍全部一致。未提交／推送／发布或调用外部模型。

准备结束后已正式接线：人类/玩家/旁观者快照可选 `received_assets` 由服务器解析的 investigator ownership 与已提交授权投影，Agent 不加该 UI 字段；author-only 素材不下放，文件路径与图片字节不入快照。前端实时本人 `handout_presented` 去重补入目录（caption / ID），恢复快照替换为 canonical 作者标签；缺省字段/换世界/撤回均清理。线索卡内目录默认收起，当前世界、调查员、账号与恢复 generation 绑定阅读状态，旧回应不能回显；不改变图片授予命令或 legacy 规则。

定向后端 **27 passed**（`/tmp/trpg-received-material-backend-focused.log`）；原前端目录 **4 failed**（`/tmp/trpg-received-material-store-before.log`）接线后与阅读/调查员面板合跑 **23 passed**（`/tmp/trpg-received-material-wired-unit.log`）；前端全量 **1160 passed / 110 files**（`/tmp/trpg-received-material-full-unit.log`），tsc/构建/ruff/架构/diff 通过。

联合浏览器当前接线版 **4 passed，2.1 分钟，exit 0**（`/tmp/trpg-received-material-joint-browser.log`）：图片恢复与三卡片旧路径仍通过；真实四客户端 human 流程新增收到目录四宽度命中、主动重新读取零 action_request、刷新后的授权目录恢复与原图可重新打开、另一玩家无目录入口。服务器拒绝另一玩家猜 ID 的原 404 断言保留。已实际查看 390/939 截图，发现目录说明沿用旧金色提示导致偏暗；随后仅该说明改为 13px 暖灰正文，最终截图/浏览器尚待补验，不将前述联合结果冒充改色后的同版验收。

后端全量第一次 **16 failed / 1575 passed / 8 skipped / 127 subtests passed**（`/tmp/trpg-received-material-full-backend.log`）：首错取证（`/tmp/trpg-received-material-first-failure.log`）是备份子进程按 PATH 找到系统 python3，缺 SQLAlchemy，不是接收目录逻辑。项目 venv 加入 PATH 后该类 **18 passed**（`/tmp/trpg-received-material-backup-env-control.log`），未修改备份或测试断言。正确环境全量 `/tmp/trpg-received-material-final-full-backend.log` 已 exit 0：**1591 passed / 8 skipped / 127 subtests passed**；SQLAlchemy 的既有 DELETE 行数警告保留。未调用付费模型；本次只读 UI 投影不改变 Agent 上下文与 handout 授予/结算。

说明改色后的 tsc/Prettier/最终构建（`/tmp/trpg-received-material-final-build.log`）、ruff/架构/diff 全过。完整浏览器 `/tmp/trpg-received-material-final-full-e2e.log` 正在运行（session 83685），原有运行代码与 dist 保持冻结，终态前不能声称最终版完整验收通过。初次指纹命令误使用 frontend 的工作目录与根路径组合，打印找不到 frontend/src 与 schemas；从根目录重建 `/tmp/trpg-platform-phase36-fingerprint.sha256`，未因此重启浏览器或修改受测运行文件。

## 第三十七阶段：短窗口模型设置阅读空间

阶段36整套浏览器主动中止，exit 130，38 passed / 1 skipped / 1 interrupted / 33 did not run；不是完整通过。真实390×360截图显示模型设置正文被两行操作区挤到61px。将既有断言提高到正文至少100px后，原布局确实失败（`/tmp/trpg-model-short-reading-before.log`）。

短窗口只把操作区改为一行三列，保留44px点击尺寸；保存按钮短标签仍为“保存配置”，完整生效说明保留在title与正常高度标签中。不改变配置提交、下回合生效或权限语义。复用已生成档案夹素材，不为纯布局修复重复生成图片。

定向组件20项通过；前端全量1160 passed / 110 files（`/tmp/trpg-platform-phase37-full-unit.log`）；tsc/Prettier/构建、ruff、架构与diff检查通过。真实模型设置/模组导入浏览器5/5通过（`/tmp/trpg-model-short-browser.log`），使用本地模型桩，不调用外部模型。已实际查看 [390×360](model-settings-short-390.png) 与 [939](model-settings-reading-939.png) 截图：短窗操作区不再占两行，正文可以滚动至完整输入，正常窗口保留完整保存说明。

阶段37终态：浏览器 **71 passed / 2 skipped / 0 failed，20.2分钟，exit 0**（`/tmp/trpg-platform-phase37-full-e2e.log`）。两项环境跳过分别为外部staging与未授权真实模型。人类主持过渡链、真实多客户端私发/检定/道具/移动/存档重连、云端无Key单人及重登入口均通过；协议替身测试只作协议编码证据，不冒充真实模型。终态运行/测试指纹全部一致（`/tmp/trpg-platform-phase37-fingerprint-final.log`），后端与阶段36受测指纹仍一致（`/tmp/trpg-platform-phase37-backend-reuse-audit.log`）。

改色后的收到素材目录已实际查看 [390](received-materials-implemented-390.png) / [939](received-materials-implemented-939.png)，标签与44px按钮正常，说明暖灰可读。新增目录的持久接收授权、刷新重开与其他玩家不可见已在最终版真实服务中验证。额外命令/权限/素材/历史审计57项通过（`/tmp/trpg-platform-final-command-audit.log`）。

当前前端与现有无Agent调查/社交路径已验收，未提交、推送、部署。完整结构化战斗/结局仍缺独立后端命令域，总目标不因此关闭；等待确认是本轮扩展完整玩法，还是独立后续任务。权威入口：[当前验收表](CURRENT_ACCEPTANCE.md)。

## 第三十八阶段：本地无Key新建缺口

终态范围复核发现，`StartScreen` 没有主持模式选择，`start.ts` 固定发旧start。`structured-real-integration` 和 `boot-loader-readiness` 的本地结构化测试会在开局后改临时世界metadata，不能作真正的本地无Key新建证据。当前没有宣告总目标完成，阶段37已通过结果只对应既有用例。

新增真实入口组件反例 `/tmp/trpg-local-human-start-before.log`：1 failed / 18 passed，缺少“游玩方式：经典 AI 叙事”入口。初次从仓库根目录调用frontend二进制路径失败（exit127），修正frontend工作目录后才获得上述产品反例；未将执行目录错误算作产品缺陷。

使用内置imagegen生成 [本地模式设计参考](local-start-mode-concept-v1.png)，[完整提示词与实际取舍](local-start-mode-prompt-v1.md)。主代理已实际查看；参考图浅纸面过亮，正式实现沿用已生成深色九宫格素材，只采纳紧凑模式摘要、命名弹窗、四种模式与固定关闭操作的布局，不引入额外人物和侧栏。

已接入真实创建链：`LocalPlayStylePanel` 复用四种模式与深色档案夹，`local_start` 创建独立世界、绑定所选调查员、本地 owner/keeper；human 不走旧开场。创建载荷摘要与回执持久化，用户重试保留 nonce 与原始源世界，不清空已创建进度。无Key默认菜单使用既有失败关闭占位，不构造 SDK；缺凭据只阻止模型调用。结构化新建即列入冒险，刷新读取已提交状态，不依赖旧式 AI 自动存档。

实际用户浏览器通过：`/tmp/trpg-local-human-creation-browser9.log`，1 passed / 18.1秒。无Key选模组/角色/人类主持 → 新建 → 发布叙事 → 刷新恢复 → 再建另一局；没有事后修改metadata，模型陷阱计数0、旧start帧0、两次新建世界不同。1280×720 / 939×640 / 640×480 / 390×360均检查关闭/模式/确认按钮≥44px、padding≥10、nowrap、视口内且中心真实命中，短窗正文≥100px。主代理实际查看 [390弹窗](local-start-mode-implemented-390.png)、[939弹窗](local-start-mode-implemented-939.png)、[390操作区](local-start-footer-390.png)、[939操作区](local-start-footer-939.png)。

保留反例：无Key默认入口在下发模组列表前被 SDK 关闭；弹窗层级低于开局页；640宽度旧确认按钮占满整行挤出新按钮；390短窗隐藏菜单固有高度把操作区推到窗口外。均从浏览器取证修正，不使用force click、扩时或删除断言。第4轮构建因测试 `capabilities:null` 类型错误失败、测试误用旧dist，明确不是修复版本证据；已改undefined并使用构建成功才跑测试的链式命令。4项脚本化模型测试及timeout测试补明确测试Key，产品成败/时间/重写断言未变，不绕过缺Key门禁。

最终后端全量1605 passed / 8 skipped / 127 subtests（`/tmp/trpg-platform-phase38-full-backend2.log`），前端全量1168 passed / 111 files（`/tmp/trpg-platform-phase38-full-unit-final.log`）；ruff、架构、tsc、格式与构建通过。第一次后端全量4 failed原日志仍保留，不冒充通过。完整浏览器 **72 passed / 2 skipped / 0 failed，18.4分钟，exit 0**（`/tmp/trpg-platform-phase38-full-e2e.log`）；跳过外部staging与未授权真实模型。运行/测试指纹 `/tmp/trpg-platform-phase38-fingerprint.sha256`，终态复核 `/tmp/trpg-platform-phase38-fingerprint-final.log` 全部一致。真实人类主持三客户端、云端单人、旧式回合、本地新建/刷新/另建与过渡链在同一版本通过，不跨版拼接。未提交、推送、发布；结构化战斗/结局缺口未假称已解决。

## 第三十九阶段：本地与云端选角的实际可读性

阶段38整套绿灯后继续做可读性反例，而非只看按钮能否提交。本地390×360的首张角色卡底部为279px，列表可视底部仅153px（`/tmp/trpg-local-short-roster-before.log`）；空分类与重复标题占掉首屏。现本地/云端都不渲染空分类，短窗本地压缩重复标题并保留每卡来源，让首张卡完整可见。新浏览器实际在短窗切换另一名调查员，验证详情和服务端最终新建角色一致。

云端单人截图另外暴露939宽连接状态竖排、短窗工具条被列表覆盖、详情只剩纸边。新增四窗口反例（`/tmp/trpg-cloud-character-layout-before.log`）后，改为有界的选角容器、不可压缩的工具区/操作区、弹性中段；窄窗工具区独占一行，连接状态不缩不换行，角色库/模型设置均44px、中心真实命中，详情至少72px。最后直接在390×360认领角色并按开始，确认按钮完整可见且可点，不先切回大窗掩盖问题。

实际查看 [本地短窗切换角色](local-character-choice-short-390.png)、[云端短窗](cloud-character-choice-short-390.png)、[939云端](cloud-character-choice-939.png)、[短窗认领后](cloud-character-claimed-short-390.png)。继续使用已生成档案夹/纸质素材与原有字体、配色，不为纯排版重复生成图片；没有增加晃眼动效或烘焙按钮文字。

最新前端全量1170 passed / 111 files（`/tmp/trpg-platform-phase39-unit-final.log`）；定向26项、本地/角色导入/启动浏览器6项通过，最终本地+云端联合浏览器3/3（`/tmp/trpg-character-choice-joint-final.log`），含无Key创建/刷新/另建、真实短窗认领开局、账号隔离与归档恢复。tsc、格式、最终构建、ruff、架构、diff通过。后端395个源/测试/schema文件与阶段38指纹一致（`/tmp/trpg-platform-phase39-backend-reuse.log`），所以沿用1605项后端全量，不冒充重新运行；首次用错frontend工作目录的指纹检查日志另存，不算有效证据。

阶段39前端/浏览器指纹 `/tmp/trpg-platform-phase39-frontend-fingerprint.sha256`，终态复核一致。完整72/2浏览器属于阶段38版本，阶段39只声明上述受影响用例，不将旧整套数字混作新版本整套通过。未提交、推送、部署；真实模型与完整结构化战斗/结局仍未扩展。

## 第四十阶段：多人房间短窗真实操作

按游戏UI与按钮验收规范复核房间，而非只确认按钮存在。扩展真实隔离后端的 `room-ready-recovery`：1280×720、939×640、640×480、390×360逐按钮检查至少44px、左右内边距至少10px、标签不换行、完整处于窗口内和中心实际命中；最短窗口实际取消主持撤销、认领与释放调查员、生成与撤销邀请，最后主动准备。原同步前禁准备、零自动补发、删除需确认且取消不落库的断言均保留。

反例 `/tmp/trpg-phase40-room-before.log` 显示页头模型设置缺少nowrap；后续补测抓到刷新及窄屏生成邀请仍42px（`room2`/`room4`日志）。在platform-ui中按使用处补齐44px、不可压缩、padding与nowrap，并明确覆盖旧邀请表单的高优先级42px规则。一次测试因未限定房间而命中两个“调查员”标题失败（`room3`），只收窄定位器，不算产品缺陷、不放宽断言。

实际查看四窗口截图，保留 [390短窗](room-controls-short-390.png)、[939房间](room-controls-939.png)。沿用已生成档案背景、主题字色与静态面板；纯控件尺寸修复不新增生成图片，不增加动效。

最终同版 `/tmp/trpg-phase40-joint-final.log` **5 passed / 0 failed，3.0分钟，exit 0**：双浏览器建房邀请选角隐私开局、Electron与浏览器联机、源码后端启动回收、房间同步/短窗操作、三客户端人类主持私发检定SAN道具移动存档重连。此前修复途中另跑4项通过只作过程证据，最终声明以这次同版联合为准。

前端全量1170 passed / 111 files（`/tmp/trpg-phase40-unit-final.log`）；RoomScreen定向56项、tsc/格式/最终构建/ruff/架构/diff通过。后端395个文件与阶段38指纹一致（`/tmp/trpg-phase40-backend-reuse.log`），沿用1605项，不称重新执行。前端指纹 `/tmp/trpg-platform-phase40-frontend-fingerprint.sha256` 终态复核一致；未重新跑整套72/2浏览器，未提交、推送、部署或调用付费模型。

## 第四十一阶段：邀请表单可见标签

实际截图显示旧表单仅以“玩家 / 72 / 5”展示条件，ARIA名称不能替代视力用户可见的字段说明。新增对偶组件测试在旧版明确失败（`/tmp/trpg-phase41-label-before.log`：1 failed / 56 passed），三个字段改用含可见span的关联label，保留原ARIA名称、验证范围与生成载荷。短窗用角色一行、两个数值并排、生成按钮一行，沿用既有档案背景与温和正文颜色，不额外生成纯排版图片。

浏览器四窗口分别检查标签文字、整个label在视口内、输入至少44px与中心命中，并保留原逐按钮/同步前拒绝/主动准备/生成撤销/选角释放断言。实际查看1280/939/640/390截图，保留 [390邀请表单](room-invite-labels-390.png) 与 [939邀请表单](room-invite-labels-939.png)。

受测版本 `/tmp/trpg-phase41-joint-final.log` 联机联合5/5、exit 0；`/tmp/trpg-phase41-unit-final.log` 前端1171 passed / 111 files；RoomScreen定向57项、tsc/格式/构建/ruff/架构/diff通过。后端395个文件与阶段38一致（`/tmp/trpg-phase41-backend-reuse.log`），沿用1605项，不声称重跑。前端指纹 `/tmp/trpg-platform-phase41-frontend-fingerprint.sha256` 在用户续接后复核发现7个文件变化；`/tmp/trpg-platform-phase41-frontend-fingerprint-final.log` 保留失败结果，不称当前版终态一致或已经联合通过。当前共享改动需新验收。未重跑完整浏览器、未调用付费模型，本代理未提交推送部署。

## 第四十二阶段：共享版本复核与归档退出保护

续接复核发现阶段41后7个共享前端文件变化（云端大厅、归档确认与共享样式），不覆盖他人改动、不称旧版通过等于新版。复核归档收回动画发现CSS只屏蔽指针、确认按钮仍可键盘操作；新增 `AdventureArchiveConfirmation.test.tsx` 对偶旧版明确失败（`/tmp/trpg-phase42-archive-before.log`），关闭期间两个按钮disabled且Escape不执行，重新打开恢复并聚焦保留，未传phase的旧弹窗行为不变。组件与大厅定向20/20（`/tmp/trpg-phase42-archive-after.log`）。

共享新版初轮浏览器9 passed / 1 failed（`/tmp/trpg-phase42-joint.log`）；归档测量在入场scaleY动画中途得到40.48px，非稳定44px。`checkArchiveLayout` 现等待真实Web Animation.finished后才测，保留全部44px、padding、真实命中、账号隔离、失败重试和整树归档断言，不用强制点击/扩大超时/skip。最终冻结当前代码浏览器 `/tmp/trpg-phase42-browser-final.log` **4/4，1.7分钟，exit 0**：本地无Key新建/刷新/另建、房间短窗操作、云端结构化无Key按钮、账号过期与归档全链。没有将初轮9项与最终4项拼作完整10/10。

当前前端1173 passed / 112 files（`/tmp/trpg-phase42-unit-after.log`），tsc/格式/构建/ruff/架构/diff通过。共享CSS的一处Prettier格式差异机械格式化，保留首次格式告警日志；后端395个指纹仍与阶段38一致（`/tmp/trpg-phase42-backend-reuse.log`）。当前前端指纹 `/tmp/trpg-platform-phase42-final-frontend-fingerprint.sha256` 复核一致。实际查看当前归档390/939截图；未重新跑完整浏览器或后端。本代理未提交推送部署、未调用付费模型；用户期间已有提交不归为本代理动作。

## 第四十三阶段：当前整套收口回归

在包含共享大厅/归档/样式改动及退出保护的当前代码上，重跑后端1605 passed / 8 skipped / 127 subtests（`/tmp/trpg-platform-phase43-full-backend.log`，exit 0）、前端1173 passed / 112 files（`/tmp/trpg-platform-phase43-unit.log`）；完整浏览器 `/tmp/trpg-platform-phase43-full-e2e.log` **74收集→72 passed / 2 skipped / 0 failed，18.6分钟，exit 0**。两项跳过是外部staging恢复与未授权真实模型；非新增失败skip。人类主持三客户端、真实本地/云端无Key开局、读档与分支、按钮/私发/权限/素材链都在同一冻结版本中通过，模型配置使用本地测试服务，不称付费模型叙事验收。

构建、tsc、ruff、架构、diff通过；`/tmp/trpg-platform-phase43-fingerprint.sha256` 与终态复核一致（`/tmp/trpg-platform-phase43-fingerprint-final.log`）。核对竖横版档案夹、纸上指南针均有真正1x/2x素材，文件夹运行接九宫格，指南针接srcSet；浏览器2x断言通过。实际查看当前桌面三卡与待办截图，发现玩家说明仍使用“稳定ID/服务端投影”等实现词，下一步需转为可理解的使用说明。整体目标继续active，完整战斗/结局是否纳入待产品范围确认。未发布、未付费调用。

## 第四十四阶段：玩家说明用语与可读性

按前端设计技能的“用用户能理解的行为，而非实现机制解释功能”原则，线索提示改为出示只是展示、不会转交或消耗，道具提示明确先申请、主持确认后结算。同步/编号缺失仍禁用并给出联系主持或重新连接的方向，权限与缺ID不退文字的机制未改；稳定ID仍用于原请求载荷，不按姓名或自然语言猜对象。提示正文统一13px暖色，不新增动效；沿用生成档案素材，不为排版重复生成图片。

四项现有回归改为验证新用户文案与同样的禁用/编辑器/ID载荷行为，旧版4 failed / 14 passed（`/tmp/trpg-phase44-copy-before.log`），当前前端全量1173项通过（`/tmp/trpg-phase44-unit.log`）。实际三客户端/三卡链与旧截图4项通过（`/tmp/trpg-phase44-browser.log`）；新增四窗口正文与截图组最终3/3（`/tmp/trpg-phase44-reading-final2.log`）。首轮新测试失败是抽屉收起时DOM仍报告visible，改为实际collapsed状态+展开动画终态后再测，保留原视口/13px断言，不算产品缺陷；原失败日志保留。

实际查看1280/939/640/390，保留 [390玩家说明](player-copy-390.png)、[939玩家说明](player-copy-939.png)。tsc、格式、构建、ruff、架构、diff通过；`/tmp/trpg-platform-phase44-fingerprint.sha256` 终态一致，后端指纹与阶段43一致（`/tmp/trpg-phase44-backend-reuse.log`）。未重新跑阶段44新增后的完整75项浏览器，阶段43的72/2仍只是旧版整套证据。未提交推送发布、未付费模型调用。

### 概念图

Use case: ui-mockup. Asset type: high-fidelity design reference board for an existing Chinese-language Call of Cthulhu tabletop role-playing platform, desktop browser. Primary request: design two side-by-side polished product screens on a single landscape canvas. Left screen: cloud multiplayer investigation lobby, a quiet archive-room atmosphere, heading 联机大厅, a short account strip, three archive-file room cards with public participant counts and status, a clearly divided 创建房间 form with module selector and four understandable play-style choices 人类主持 / AI辅助 / AI主持 / 经典模式, and a small 邀请码加入 form. Right screen: a human Keeper workspace titled 主持工作台, with a clearly private small label 仅主持可见, a left vertical task navigation labeled 叙事 / 待处理行动 / 素材 / 场景 / 检定 / 进阶, a generous central story-composer area with audience control and a 发布叙事 button, and a quiet right sidebar of pending player actions and current scene. This must be a usable browser-game interface not a SaaS analytics dashboard. Visual identity: 1920s investigation desk, blackened walnut, matte charcoal-brown, restrained aged brass #baa16a, warm readable ivory #e4dccd, muted sage only for connection status. A subtle archive-folder tab and small brass binder clips are the signature detail; no enormous ornate frames. Body text is readable contemporary Chinese sans serif, display headings restrained Chinese serif. Background #181511, panel #24201b. No glowing gold lettering, no flares, no pure white panels, no garish red, no tiny text, no baked-in paragraphs, no chart decorations, no game character portraits. Keep interfaces comfortably bright enough to read but calm for long nighttime sessions. Composition: two complete 1280-ish desktop screen mockups separated by a quiet divider; straight on, no perspective, no physical device mockup, no watermark. Aim for screenshot-realistic implementable DOM components, generous whitespace and clear actionable buttons, with the keeper screen visibly broader than the existing narrow console. Do not imply model/API requirements for human mode.

### 正式背景素材

Use case: stylized-concept. Asset type: final reusable background illustration for cloud lobby and human Keeper workspace headers in a Chinese Call of Cthulhu tabletop roleplaying platform. Primary request: a broad low-contrast 1920s investigation archive desk and shelves, in the same quiet matte dark walnut, charcoal-brown and subdued aged brass visual direction as the preceding interface mockup. Scene/backdrop: a few closed case folders, old archive shelves, a plain unmarked map pinned in the distant background, soft shaded desk-lamp light. Style: sophisticated hand-painted environmental realism, no elaborate game borders, no character faces, no text, no letters, no symbols that read as writing, no UI, no logos or watermarks. Composition: a wide panoramic image 2048 by 1152, naturally dark and visually very quiet across the center and left two thirds, slightly more visible archival objects near the right edge so real DOM headings and controls can sit over a calm backdrop. Lighting: soft indirect lamp light only, absolutely no bright bulbs, no flames, no flares, no hard gold glow. Palette: background #181511, dark walnut #28221b, restrained muted brass #baa16a, desaturated sage small accent. Broad matte shadow areas, minimal grain, details remain subordinate to text. Designed as a production webpage header background, not a busy wallpaper. Keep the mood mysterious but comfortable for prolonged nighttime reading.
