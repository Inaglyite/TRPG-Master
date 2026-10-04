# 当前范围验收表（尚未宣告完成）

目标：不依赖 Agent 的主持路径、云端单人/多人界面，以及贴题、舒适的生成美术与可操作布局。下面不是发版批准；正式环境没有用于本轮测试。

## 当前冻结版本

阶段40前端与浏览器指纹：`/tmp/trpg-platform-phase40-frontend-fingerprint.sha256`；后端与阶段38受测版本一致，以下阶段37/38结果保留追溯。当前未提交工作区包含其他人的改动，不能把全部 dirty 文件认作本轮新增内容。

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

| 范围 | 当前实现来源 | 实际验收入口与边界 |
|---|---|---|
| 登录、注册、服务器、会话过期 | AuthScreen、OnlineShell、api/client、连接档案夹 | server-session-boundaries / character-library / http-request-recovery；实际换服务器、旧401、过期账号隔离。HTTP故障注入不冒充真实存储 |
| 本地与云端单人创建与继续 | StartScreen/LocalPlayStylePanel/local_creation、SoloLobbyScreen/PlayStylePicker | local-human-start真实无Key新建/发言/刷新/另建、零模型；structured-solo-online云端无Key开局与重登；本人兼主持的秘密边界明确提示 |
| 多人大厅与房间 | LobbyScreen、RoomScreen、OnlineRoomDock、roomSendNow | multiplayer / room-ready-recovery / structured-human-3p；真实建房邀请选角开局、旁观加入、退出移交、同步前禁用准备、同步后不补发旧意图 |
| 人类主持工作台 | KeeperConsole、KeeperLibrary、服务端能力和命令目录 | structured-human-3p / structured-real-integration / structured-play；叙事、私发、检定、SAN/HP、道具、移动、时间、待办；友好候选不把标签冒充稳定ID |
| 玩家状态、线索、道具 | InvestigatorPanel三卡、StructuredActionDialog | investigator-panel / structured-human-3p；分类折叠、出示/使用完整载荷、服务端回执才确认，不预扣、不靠自然语言解析按钮意图 |
| 图片阅读与持久收到目录 | HandoutImageViewer、ReceivedMaterials、received_assets投影 | clue-image-reading / structured-human-3p；44px四宽度、失败重试、关闭回焦点、IME、撤回/换世界、刷新重开；另一玩家HTTP猜ID仍404；独立后端测试覆盖旁观/无角色主持/房主无接收授权 |
| 主持资料、记忆与知识隔离 | KeeperLibrary、只读手册HTTP、记忆查询与线程投影 | structured-context-memory / structured-human-3p / 后端material与history tests；玩家帧不含主持目录/记忆，素材按需读取，Agent不增加UI资料负载 |
| 行动等待、拒绝、取消、暂停 | StructuredCards、KeeperControlNotice、请求账本 | structured-interaction-duals / structured-pending-sync / structured-load-branch / structured-real-integration；等待不移动、抵达只收尾已满足子意图、刷新可取消、缺Key暂停可接管 |
| 存档、主动读档、重连、分支 | SavePanel、SoloTimelinePanel、历史读取与分支服务 | structured-load-branch / structured-branch-online；实际读档删除回执、普通重连不回滚、structured不用legacy turn_id、分支记忆隔离、继承历史按身份过滤 |
| 人物与模组导入 | CharacterLibraryPanel、ModuleImporter | character-library / model-settings；真实文件检查安装、失败重试保留文件、账号归属、扩展字段与迟到操作隔离 |
| 模型设置与上下文 | ModelSettingsPanel、账号/房间绑定与上下文摘要 | model-settings / model-settings-online；本地与云端入口、Key不回显、成员只读、失败恢复；模型调用采用测试服务，不宣称真实模型叙事质量验收 |
| 美术与操作舒适性 | 生成档案夹/纸质指南针1x/2x与九宫格、platform-ui.css | 本目录prompt/原图/实际截图；1280/939/640/390和短窗、键盘焦点、IME、reduced-motion；布局截图与权限验收分开声明 |

## 不能用绿灯替代的事项

1. 当前版界面与已有调查/社交路径整套浏览器已通过，最终说明改色截图已实际查看。阶段37新增正文至少100px的真实浏览器断言，修复前实测61px，失败证据 `/tmp/trpg-model-short-reading-before.log`。总目标仍 active：完整战斗/结局是否纳入本轮需要产品范围确认，不能据前端绿灯宣称完整无Agent跑团能力已齐全。
2. staging与需授权的真实模型规格不属于本轮本地无Agent验收。不会调用付费模型或以脚本化调用冒充模型验收，不因此宣称部署就绪。
3. 结构化完整战斗/结局命令域仍是既有产品能力边界，UI明示未提供，经典模式保留。没有用前端伪造这些状态或把高级表单称为完整新玩法域。
4. 当前新增目录仅覆盖结构化已提交的接收授权；legacy已有线索图片继续原路径，不将全局seen_assets冒充多人私发授权。
5. 未提交、推送或部署。后端初次PATH污染导致备份测试失败的原日志仍保留，正确环境全量复跑通过，不改断言掩盖。
6. 阶段38已补本地显式模式与真实创建；原先修改隔离世界metadata的旧测试只证明既有路径。新用例经真实用户入口、最终整套浏览器通过。[设计与验收要求](local-start-mode-prompt-v1.md)、[实际短窗操作区](local-start-footer-390.png)。完整结构化战斗/结局仍是单独能力边界，不能据界面与调查/社交绿灯宣称其已经提供。

## 最终源代码复核补充

- 对照历史前端任务的九项要求，当前表中的入口覆盖线索/道具/移动/掷骰/检定、状态与重试、主持台、三种运行入口和辅助/接管界面。该历史任务不是本轮付费模型或发布授权。尤其 NPC 发言不是由正文解析：`KeeperConsole.test.tsx` 的明确 speaker 载荷测试与 `test_structured_commands.py::test_publish_message_speaker_rules` 分别验证 UI 编码和真实临时数据库提交/冒充拒绝；时间推进同样走命令与 revision 校验。补充权限/命令/素材/历史定向复跑57 passed（`/tmp/trpg-platform-final-command-audit.log`），不冒充浏览器整套终态。
- `online.ts` 的退出/会话401/服务器变更均先 `disconnectRoom`；`room-ws.ts:disconnectRoom` 清空请求队列、结构化运输游标、结构化store和场景store。所以 `receivedAssets` 不是仅靠阅读器隐藏，账号边界会清掉数据本身。
- `structured-transport.ts` 先执行游标/foreign_world 判定，再 applyEvent 和显示效果；素材目录不会直接接受来自旧世界的帧。只收到其他调查员 `handout_presented` 的脚本反例也不会给自己补目录。
- `responsive.css` 的 reduced-motion 全局覆盖含伪元素，将非必要动画/过渡限制为0.01ms；不是只让某个按钮停动。新目录本身没有新增动画。
- 生成档案夹有 portrait/wide 各1x/2x，指南针亦有1x/2x，运行引用和既有2x浏览器断言对应。新目录在三卡中只作一层折叠，不按收图次数生成新美术或新主卡。
- 本轮目标不自动授予发布、付费模型测试或生产冒烟权限；后续是否包含完整结构化战斗/结局，已另向用户确认，未擅自扩展后端玩法语义。
- 阶段37只改前端短窗布局与浏览器断言，阶段36后端指纹复核一致；1591项全量对应阶段37后端。阶段38增加真实本地创建与无Key菜单门禁后，后端结果以1605项新全量为准，不能沿用旧数字。

过程与反例见 [README](README.md)，最新设计提示词见 [received-materials-prompt-v1.md](received-materials-prompt-v1.md)。完成判定要以当前日志终态、当前受测指纹、实际截图和上表对应行为为依据，不以“找不到TODO”代替。
