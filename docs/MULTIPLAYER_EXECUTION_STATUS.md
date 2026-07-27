# 多人功能执行状态与接续说明

更新日期：2026-07-26
开发分支：`feat/multiplayer`  
文档性质：持续更新的工程台账，不是完成报告。

本文用于在对话中断、上下文压缩或执行者切换后继续多人功能开发。每次开始工作时，先阅读
[`MULTIPLAYER_DELIVERY_BRIEF.md`](MULTIPLAYER_DELIVERY_BRIEF.md) 确认不可缩水的验收范围，再阅读
本文确定当前状态。技术路线以 [`MULTIPLAYER_PLAN.md`](MULTIPLAYER_PLAN.md) 为准；接口、架构、
数据库和部署细节分别以对应专题文档为准。

## 1. 最终目标

交付一个真正可游玩的 2–4 人 Azure 权威服务器联机版本，同时保留 Electron 单机模式。完成标准不是
“接口存在”或“页面出现”，而是浏览器和 Electron 的两个真实客户端能够完成注册、登录、建房、邀请、
选角、准备、开始游戏、轮流行动、私人事件、掉线恢复、存读档和房间管理，并通过安全、并发、迁移、
PostgreSQL、部署和恢复测试。

不得擅自把以下内容移出范围：

- Electron 单机/多人模式选择和单机回归；
- 完整认证、大厅、建房、加入、房间和多人游戏 UI；
- Azure 权威服务器，不改成 P2P；
- 多调查员状态、当前行动者裁决、幂等和单房间单引擎；
- 私人事件隔离、断线补发和完整恢复；
- PostgreSQL、Alembic、Azure staging、备份/恢复/回滚；
- 浏览器与 Electron 双客户端 E2E 和非开发者用户文档。

## 2. 已完成的后端能力

以下能力已落在当前分支并分阶段提交：

- 用户、服务端 Session、世界所有权和成员关系的数据库控制平面；
- 邀请创建、列出、撤销和接受，邀请令牌只以哈希保存；
- 成员权限修改、移除和房主移交；
- 调查员选项、占用、释放以及服务端角色绑定；
- `RoomManager`、共享 `GameRoom` 和每个世界单个 `GameEngine`；
- `/ws/room` 的 Session 认证、成员校验、当前行动者校验和串行行动；
- 持久化 `action_id` 幂等、准备/在线/选角开局门槛和房间容量限制；
- 公共、指定玩家、房主、`server_only` 可见性过滤；
- 私人决定、私人回复、玩家笔记和私人线索；
- 单调 `room_event_id`、ACK、增量同步、事件缺口后的完整状态恢复；
- 房间控制状态持久化、进程重启恢复、空闲房间安全退休；
- 多标签页在线状态去重、逐消息 Session/角色复验；
- 多调查员与旧 `state.pc` 的兼容投影，HP/SAN/物品和工具作用于当前调查员；
- 云端认证开启时拒绝旧 `/ws`，避免绕过 `/ws/room` 权限；
- staging 独立 Cookie 名、单 worker 和最大活跃房间数配置；
- Azure staging 的 systemd、Nginx、安装脚本和部署工作流骨架。

关键迁移为：

- `20260722_0002`
- `20260722_0003`
- `20260722_0004`

## 3. 当前测试基线

最近一次完整后端测试结果：

- Python：`315 passed, 1 skipped`（使用 `venv/bin/python -m pytest -q`）；
- Ruff：通过；
- 最近的多人存档控制改动已通过 `18 passed` 的针对性测试；
- 本机真实 PostgreSQL 17 已通过迁移、JSONB 深层往返、邀请/成员、调查员绑定和行动幂等测试；
- Ruff 与 `tools/check_architecture.py`：通过；
- 前端：`27` 个测试文件、`219` 个用例，TypeScript、Prettier、生产构建均通过；
- 本机 Playwright：双浏览器联机、Electron 云端往返和 Electron 单机三条 E2E 均通过；
- Azure 公网 Playwright：双浏览器 HTTPS/WSS、真实模型开场与玩家行动、回合内并发拒绝、快速
  存档/读档、真实 Electron 云端往返均通过；
- Azure 服务重启后：既有账号重新登录，公开历史、当前行动权和可读存档恢复通过。

架构门禁已经恢复通过：认证 HTTP、多人 HTTP 和多人 WebSocket 适配层已分别抽取到
`src/auth_http.py`、`src/multiplayer_http.py` 和 `src/multiplayer_ws.py`，`server.py` 已降至主线历史
基线以下。CI 已改为执行完整 `pytest`，staging 工作流会启动真实 PostgreSQL 17、运行迁移和集成测试，
不能再退回只执行 `unittest discover` 或通过放宽阈值绕开门禁。

## 4. 前端协作状态

Kimi 完成了首轮模式选择、认证、大厅、房间组件和状态管理；Codex 随后按实际 HTTP/WS 契约完成逐文件
审查、联调和安全修正。首轮中使用不存在接口、成员调查员结构错误、未接入 `/ws/room` 和 Electron
跨站 Cookie 等问题均已纠正，不再有待审的 Kimi 工作区成果。

当前实现统一使用 `/ws/room` 传输联机游戏，按数字处理事件 ID，在首次完整状态恢复前排队命令，并用
`room_full_state` 恢复公共状态和当前账号的私密状态。多人存档命令携带稳定 `action_id`；Electron 联机
模式同源加载 HTTPS 应用，并限制 origin、导航、窗口创建、权限请求和 IPC 来源。

Codex 同时修复了房间 WebSocket 生命周期、事件去重、房主权限显示、退出房间失败恢复、公开/私人
线索分类、跨账号私密面板残留、生产同源端口推导、服务端 Session 撤销失败处理，以及 Electron 从云端
安全返回内置启动器。当前前端
`27` 个测试文件、`219` 个用例、TypeScript、Prettier、生产构建和 Electron 脚本语法均通过；模式选择
与认证页已在真实 Chromium 中渲染检查。新增 Playwright E2E 已用两个隔离浏览器上下文验证注册、
建房、邀请、不同调查员选角、刷新回房、私人笔记实时/恢复隔离、双方准备和开局；另用真实 Electron
进程验证内置启动器与 HTTPS 多人页安全往返，以及选择单机后连接真实本地 FastAPI。三条 E2E 均通过
并已加入普通 CI 与 staging 部署前门禁。

Codex 已完成逐项审查、修正、提交和推送；Kimi 的工作成果不再作为未审查工作区改动保留。

## 5. Azure staging 现状

Azure VM 已完成隔离部署：

- PostgreSQL 16 仅监听 `127.0.0.1`，使用独立 staging 数据库与最小权限角色；
- Alembic 已迁移至 `20260722_0004`；
- staging 使用独立 systemd 服务、运行目录、环境文件、Cookie、备份目录和单 worker；
- 受内存约束，已配置 1 GiB 专用 swap 和 PostgreSQL 连接/内存限额；
- 旧应用服务保持停止，旧应用目录、世界目录、数据库文件和原 Nginx 配置备份均保留；
- Azure NSG 未开放 8443，因此验收通过后将可回滚的公网 443 入口切到联机 staging；
- 公网 `/api/health`、HTTPS 页面、WSS 双客户端联机和 Electron 云端往返均通过；
- 每日加密备份 timer 已启用。恢复演练发现并修复校验文件使用绝对临时路径的问题；修复后的
  GPG 归档已完成 SHA256 校验、隔离数据库 `pg_restore` 和 Alembic 版本核对，演练库随后删除；
- systemd 重启后健康检查与数据库迁移状态保持正常。
- 已在两个保留 release 之间执行 symlink 前进、回滚、再前进；三次重启后的健康检查均通过。
- Nginx 已实际回滚到旧入口并观察到旧 Basic Auth `401`，随后恢复联机配置；配置检查与公网健康
  检查均通过。
- Azure 完整回合验收发现 PostgreSQL 会将已有自动槽的 FK 更新排在新 snapshot 插入之前，导致快速
  存档失败；现已显式 flush snapshot、加入回归测试，并在公网重新验证存档与读档成功。同步协议处理
  异常也不再杀死整个共享房间驱动。

## 6. 尚未完成的工作

按当前优先级继续：

1. ~~补充非开发者最终用户操作文档；~~ 已完成：
   [`MULTIPLAYER_USER_GUIDE.md`](MULTIPLAYER_USER_GUIDE.md)；
2. 决定正式域名与受信任证书方案；
3. 完成最终安全审计与完整回归；
4. 全部验收通过后，才讨论合入 `master`。

## 7. 当前风险与禁止事项

- Electron 从 `file://` 跨站请求云端并依赖 `SameSite=Lax` Cookie 不可靠；联机模式应加载受信任的
  Azure HTTPS 同源应用，并限制导航、窗口创建和 IPC 来源。
- `RoomManager` 是进程内单例，服务必须保持一个 Uvicorn worker，除非未来引入跨进程协调层。
- 成员 HTTP 数据没有实时在线/准备状态，前端必须与 `room_state` 合并，不能凭空推断。
- 完整恢复会按连接附加当前玩家自己的角色、可见线索和私人笔记；真实双客户端 E2E 已验证交叉账号
  无法从实时消息和恢复数据看到秘密，后续协议修改必须持续运行这组隐私回归。
- staging 与生产必须使用独立数据库、运行目录、端口、Cookie 名和环境文件。
- 当前公网入口使用 IP 地址和现有自签名证书；自动化通过忽略测试证书错误验证，但面向普通玩家前
  必须配置域名和受信任证书，不能要求用户长期绕过浏览器证书警告。
- 可以停止旧应用服务，但不得误删 Nginx、PostgreSQL、备份和已有世界数据。
- 不修改用户 SSH 密码或凭据；任何密码、Session、API Key、邀请明文和数据库 DSN 都不得写进仓库、
  测试输出、日志或对话回复。
- 不允许未经测试直接替换 Azure 生产，不允许在验收前合入 `master`。

## 8. 每次继续开发时的固定流程

1. 确认当前分支为 `feat/multiplayer`，检查 `git status`，识别用户和 Kimi 的未提交改动；
2. 阅读本文件及交付约定，不根据聊天记忆自行缩减范围；
3. 若有并行协作者，先检查共享工作区并重新划分文件范围，避免覆盖未提交改动；
4. 选取一个可验证阶段，先确认协议、权限、隐私、失败恢复和单机影响；
5. 实现后运行对应测试，并同步 API/架构/数据库/部署文档；
6. 由 Codex 审核后分阶段提交和推送；
7. 更新本文的已完成项、测试基线、风险和下一步；
8. 只有 [`MULTIPLAYER_DELIVERY_BRIEF.md`](MULTIPLAYER_DELIVERY_BRIEF.md) 第 6 节全部满足，才可将
   本文标记为完成。

## 9. 相关文档

- 产品范围与最终验收：[`MULTIPLAYER_DELIVERY_BRIEF.md`](MULTIPLAYER_DELIVERY_BRIEF.md)
- 技术路线：[`MULTIPLAYER_PLAN.md`](MULTIPLAYER_PLAN.md)
- HTTP/WS 契约：[`API.md`](API.md)
- 系统边界：[`ARCHITECTURE.md`](ARCHITECTURE.md)
- 数据模型与迁移：[`DATABASE.md`](DATABASE.md)
- staging/生产部署：[`DEPLOYMENT.md`](DEPLOYMENT.md)
- 普通玩家操作：[`MULTIPLAYER_USER_GUIDE.md`](MULTIPLAYER_USER_GUIDE.md)

## 10. 上下文恢复契约

若聊天中断、上下文被压缩或换由新的执行者继续，以下规则优先于对话中的模糊记忆：

1. 先读 `MULTIPLAYER_DELIVERY_BRIEF.md` 与本文，再看 Git 分支、工作区和 Kimi tmux 状态；
2. 不得根据“页面已有”或“接口已有”宣布完成，必须以交付约定第 6 节的端到端证据为准；
3. 不得遗漏 Electron、浏览器、单机回归、双客户端、隐私隔离、PostgreSQL 和 Azure staging；
4. Kimi 负责前端实现不代表 Codex 可以跳过审查、联调、安全检查和最终修正；
5. 未完成全部验收前保持在 `feat/multiplayer`，不合入 `master`、不替换生产；
6. 任何凭据只用于获准的部署会话，不写入代码、文档、命令输出或提交，也不擅自更改；
7. 每完成一个阶段，都要在本文留下提交、测试证据、剩余风险和下一步，使后续工作不依赖聊天记忆。
