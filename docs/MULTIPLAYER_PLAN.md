# 联机功能实施计划

更新日期：2026-07-22。本文是 `feat/multiplayer` 分支的实施依据，描述从当前单连接游戏演进到
2–4 人共享房间的目标架构、阶段、数据契约、测试和上线条件。总体路线图见
[`ROADMAP.md`](ROADMAP.md)，现有运行结构见 [`ARCHITECTURE.md`](ARCHITECTURE.md)。用户确认的完整
产品范围、Electron 双模式、前端交互要求、协作分工和最终完成定义见
[`MULTIPLAYER_DELIVERY_BRIEF.md`](MULTIPLAYER_DELIVERY_BRIEF.md)；该文件是执行期不可自行缩减的
交付基线。

## 1. 结论

第一版采用 **Azure 上的权威服务器**，不采用 P2P：

```text
Browser / Electron
        │ HTTPS + WSS
        ▼
   Azure Nginx
        │
        ▼
 FastAPI authoritative server
   ├─ Auth / Session
   ├─ RoomManager / GameRoom
   ├─ shared GameEngine
   ├─ action queue
   └─ event visibility + broadcast
        │
        ▼
    PostgreSQL
```

原因：服务端持有模型 API Key、模组秘密、NPC 私密状态、骰点规则和数据库事务。P2P 无法消除
协调服务器需求，还会增加 NAT 穿透、主机掉线、作弊、版本不一致和秘密泄露风险。未来如需局域网
托管，也应运行同一套权威服务器协议，而不是设计第二套 P2P 同步逻辑。

单机模式继续保留：Electron 连接本机 FastAPI 和 SQLite；联机模式连接 Azure HTTPS/WSS 和
PostgreSQL。前端共享业务组件，仅在认证入口、后端地址和联机房间界面上区分运行模式。

## 2. 当前基线与缺口

已经具备：

- Argon2id 用户密码、可撤销服务端 Session、HttpOnly Cookie、登录限流和审计；
- `users`、`sessions`、`worlds`、`world_members` 及世界权限检查；
- WebSocket 握手与世界切换鉴权；
- PostgreSQL 生产存储、世界 revision、回合事务、快照和断线恢复记录；
- 连接内有序 `turn_id + seq` 事件流和世界级互斥锁；
- Azure 自动部署入口、Nginx/systemd/PostgreSQL 运维材料。

尚未具备：

- 登录、注册、退出和 Session 过期的完整前端流程；
- 世界邀请、成员管理、角色占用和在线状态；
- 同一世界多个连接共享的 `GameEngine`、消息历史和事件广播；
- 房间级行动队列、公平性、事件补发和私人事件；
- 多调查员状态模型及所有工具的服务端角色授权；
- 联机 staging、双客户端 E2E、压力测试与故障恢复演练。

最关键的现状约束是：当前每条 WebSocket 都创建独立 `GameEngine`。数据库锁只能防止同时写入，
不能让两个引擎共享 GM 消息历史，因此不能把“允许多个连接访问同一 `world_id`”误认为多人房间。

## 3. 第一版产品范围

### 3.1 必须实现

- 2–4 名已登录用户加入同一世界；
- 一个房主，成员角色至少包括 `owner`、`player`、`viewer`；
- 一个世界同一时刻只有一个活跃 `GameRoom` 和一个共享 `GameEngine`；
- 所有成员接收相同的公开叙事、骰点和公共状态事件；
- 同一时刻只允许当前行动者提交行动；
- 每名玩家绑定一个调查员，旁观者不绑定；
- 断线重连后恢复房间、当前行动者和缺失的公开事件；
- 房主能够邀请、移除成员、指定/跳过当前行动者；
- 服务端在发送前完成公开、指定玩家和仅服务端信息的隔离。

### 3.2 暂不实现

- P2P、NAT 穿透或玩家主机迁移；
- 语音、视频和通用即时通讯；
- 所有玩家同时驱动模型；
- 第一版内的复杂投票、行动合并或自由抢答；
- 跨进程房间迁移和多区域部署；
- 端到端加密的玩家私聊。

## 4. 目标运行时

### 4.1 房间所有权

```text
RoomManager (process singleton)
└─ rooms[world_id] -> GameRoom
   ├─ context / shared GameEngine / shared ModelSession
   ├─ members[user_id] -> one or more connections
   ├─ current_actor_user_id
   ├─ serial action queue
   ├─ monotonically increasing room_event_id
   ├─ bounded public replay buffer
   └─ lifecycle: loading | active | idle | closing
```

`RoomManager` 以 `world_id` 为唯一键原子创建房间。第一个合法成员负责触发加载，后续成员等待同一个
加载结果，不得创建第二个引擎。最后一个连接离开后进入空房宽限期；没有活动回合时才能释放内存。

### 4.2 回合提交

第一版采用“当前行动者”机制：

1. 服务端根据 Session 得到 `user_id`，不接受客户端自报身份；
2. 检查用户是房间成员、角色已绑定且是当前行动者；
3. 为请求分配稳定 `action_id`，幂等拒绝重复提交；
4. 将行动放入房间串行队列并锁定当前回合；
5. 共享引擎执行一次模型调用和一次数据库事务；
6. 公共事件广播，私人事件只发送给目标成员；
7. 完成后服务端选择或等待房主指定下一位行动者。

已有世界 revision 和数据库行锁继续作为最终一致性保护，但正常情况下同房间写入必须先经过
`GameRoom` 的单一队列。

### 4.3 事件可见性

所有房间事件在服务端内部携带可见性，序列化前过滤：

| 可见性 | 接收者 | 示例 |
|---|---|---|
| `public` | 房间全部成员 | 叙事、公开骰点、公共线索、成员在线状态 |
| `player:<user_id>` | 指定用户的全部连接 | 私人决定、仅本人可见的线索或角色结果 |
| `owner` | 当前房主 | 管理通知、需要房主处理的成员请求 |
| `server_only` | 不发送客户端 | DSML、工具参数、NPC 秘密、模型内部诊断 |

禁止先广播再依赖前端隐藏。日志、错误消息、断线补发和审计记录也必须遵守同一边界。

## 5. 数据模型计划

### 5.1 复用现有表

- `users` / `sessions`：登录身份和可撤销会话；
- `worlds` / `world_members`：世界所有权及 owner/player/viewer 权限；
- `world_states`：权威运行状态；
- `turns` / `turn_events` / `snapshots`：回合恢复、事件补发和存档；
- `audit_events`：邀请、加入、移除、角色绑定和房主操作。

### 5.2 新增邀请表

建议增加 `world_invites`：

| 字段 | 说明 |
|---|---|
| `id` | 内部 ID |
| `world_id` | 目标世界 |
| `invited_by` | 创建邀请的用户 |
| `token_hash` | 仅保存邀请令牌哈希 |
| `role` | 接受后授予 `player` 或 `viewer` |
| `expires_at` | 过期时间 |
| `max_uses` / `used_count` | 使用次数限制 |
| `revoked_at` | 撤销时间 |
| `created_at` | 创建时间 |

邀请明文只在创建响应中返回一次，不写日志和审计详情。

### 5.3 调查员绑定

建议增加关系表 `world_investigators`，不要只把账号绑定藏在 JSONB 中：

| 字段 | 说明 |
|---|---|
| `id` | 稳定调查员 ID |
| `world_id` | 所属世界 |
| `character_key` | 角色模板或创建来源 |
| `controller_user_id` | 当前控制者，可为空 |
| `status` | available/claimed/retired/dead |
| `created_at` / `updated_at` | 生命周期 |

角色数值仍可在 `world_states.state.investigators[id]` 中保存；关系表负责所有权、唯一占用和查询。
现有单数 `pc` 通过 Alembic + 世界 schema migration 转为只有一个成员的 `investigators` 集合。

房间在线连接不作为数据库事实；进程重启后由 Session 和持久成员/角色绑定重建。

## 6. 协议计划

### 6.1 HTTP

在现有认证和世界接口上补充：

- `POST /api/worlds/{id}/invites`：房主创建邀请；
- `DELETE /api/worlds/{id}/invites/{invite_id}`：撤销邀请；
- `POST /api/invites/{token}/accept`：接受邀请；
- `GET /api/worlds/{id}/members`：成员与角色占用列表；
- `PATCH /api/worlds/{id}/members/{user_id}`：修改角色或权限；
- `DELETE /api/worlds/{id}/members/{user_id}`：移除成员；
- `POST /api/worlds/{id}/investigators/{investigator_id}/claim`：占用角色；
- `DELETE /api/worlds/{id}/investigators/{investigator_id}/claim`：释放角色。

所有变更接口使用服务端 Session、权限检查、CSRF/Origin 边界、限流和审计。错误响应逐步增加稳定
`code`，避免前端依赖中文文案判断状态。

### 6.2 WebSocket

建议新增客户端消息：

- `room_join` / `room_leave`；
- `room_ack { event_id }`；
- `room_sync { after_event_id }`；
- `action_submit { action_id, text }`；
- `actor_assign { user_id }`（owner）；
- `member_kick { user_id }`（owner）。

建议新增服务端事件：

- `room_state`：成员、角色占用、当前行动者和房间状态；
- `member_joined` / `member_left`；
- `actor_changed`；
- `action_queued` / `action_rejected`；
- `room_event_gap`：缓存不足，客户端应执行完整恢复；
- `private_event`：仅在服务端已经锁定目标用户后发送。

房间事件使用独立、单调的 `event_id`。现有回合内 `turn_id + seq` 保留，用于描述一次 GM 回合的
有序事件；两者职责不同。每条客户端连接记录最后 ack，重连时优先增量补发，缓存不足才从数据库
恢复完整公开状态。

## 7. 分阶段实施

### Phase 0：契约和测试骨架

- 冻结第一版当前行动者规则和事件可见性；
- 为房间、邀请和角色绑定写数据模型与协议测试；
- 建立两个独立 Session/两个 WebSocket 的测试工具；
- 给 `docs/API.md` 预留版本化多人协议章节。

退出条件：并发、身份、可见性和恢复的预期可以由自动化测试表达。

### Phase 1：账号前端

- 添加登录、注册、退出、`/api/auth/me` 恢复；
- 云端模式未登录时不建立游戏 WebSocket；
- Session 过期返回登录页并保留安全的返回位置；
- 登录后显示当前用户和其世界列表；
- 本地模式默认跳过登录，不能因此绕过云端鉴权。

退出条件：用户 A 无法列出、连接或切换到用户 B 未授权的世界。

### Phase 2：邀请、成员和角色占用

- 增加 Alembic 迁移、邀请服务和成员管理接口；
- 增加邀请链接/房间码、成员列表和角色选择 UI；
- 所有角色 ID 由服务端根据绑定注入游戏命令；
- 补齐撤销、过期、重复接受、世界已满和并发占用测试。

退出条件：两个账号能够安全加入同一世界并绑定不同调查员，但暂不共同推进游戏。

### Phase 3：共享房间运行时

- 实现 `RoomManager` / `GameRoom` 和单飞加载；
- 将引擎生命周期从 WebSocket 移到房间；
- 多连接复用共享消息历史、上下文和回合门禁；
- 实现串行行动队列、`action_id` 幂等和空房休眠；
- 连接退出只移除成员连接，不直接销毁正在执行的回合。

退出条件：两个客户端加入同一世界只创建一个引擎，并发提交只执行一个权威回合。

### Phase 4：广播、私人事件和恢复

- 把现有 `OrderedTurnEventStream` 抽象为房间广播出口；
- 增加事件可见性和逐连接过滤；
- 增加房间 `event_id`、ack、缓冲和增量补发；
- 从 PostgreSQL 恢复房间公开历史和当前状态；
- 验证 DSML、模型参数、NPC 秘密和私人线索永不进入错误连接。

退出条件：断线重连不重复叙事，私人事件不串线，进程重启后能恢复最近完整回合。

### Phase 5：多人规则与 UI

- 迁移 `pc` 为调查员集合；
- 检定、SAN、HP、物品、战斗和决定都接受服务端授权的调查员；
- 显示成员在线状态、当前行动者、等待原因和掉线托管；
- 房主可跳过/移交行动权；
- 完成两名调查员探索、检定、战斗、存档和读档 E2E。

退出条件：两名玩家能完成一段完整模组流程，所有状态作用于正确角色。

### Phase 6：Azure staging 与上线

- 为 `feat/multiplayer` 增加手动 staging 部署，不自动覆盖生产；
- staging 使用独立数据库、Cookie 名、Origin、运行目录和端口；
- Azure 生产启用 `TRPG_REQUIRE_AUTH=1`，注册默认关闭或使用邀请码；
- PostgreSQL 仅监听回环地址，应用使用最小权限账号；
- 完成 TLS/WSS、备份、异机恢复、systemd 重启和发布回滚演练；
- 进行 2–4 人长时间测试和模型成本基线记录。

退出条件：staging 通过安全与恢复清单后，才允许合并主线并发布生产。

## 8. 测试矩阵

必须自动化覆盖：

1. 两个账号加入同一 `world_id` 时只有一个 `GameEngine`；
2. 两人同时提交，只有当前行动者的一次请求进入模型和数据库；
3. 重复 `action_id` 不会重复调用模型、工具或提交回合；
4. 客户端伪造 `user_id`、`actor_id` 或 `investigator_id` 被拒绝；
5. 私人事件只出现在目标用户的连接，日志和恢复记录不泄漏；
6. 同一用户多个标签页收到一致事件且不会重复占用角色；
7. 玩家断线、房主断线、回合执行中断线均不破坏权威提交；
8. 重连根据 ack 增量补发，缓存缺口触发完整恢复；
9. 服务重启后不会把 incomplete turn 当作完成回合重放；
10. 不同房间的引擎、事件、成员、模型历史和数据库状态完全隔离；
11. 旧单机存档迁移后仍能由一个本地调查员正常游玩；
12. Azure 发布、回滚和数据库恢复不覆盖世界或成员数据。

## 9. 运维和扩展边界

第一版只运行单个 FastAPI 应用进程，因为 `RoomManager` 和共享引擎驻留内存。不能直接增加多个
Uvicorn worker，否则同一世界可能落到不同进程并创建多个房间。需要水平扩展时再引入：

- 基于 `world_id` 的粘性路由；
- PostgreSQL advisory lock 或 Redis 分布式房间租约；
- Redis Streams/PubSub 或持久事件总线；
- 房间 owner 进程心跳与故障接管。

在完成这些机制前，扩容应优先提升单机资源、限制同时活跃房间数和为空房设置休眠策略。大型冷快照
以后可迁往对象存储，但房间、权限、回合索引和哈希继续保存在 PostgreSQL。

## 10. 风险与缓解

| 风险 | 缓解 |
|---|---|
| 多连接创建重复引擎 | `RoomManager` 原子单飞加载 + 双连接并发测试 |
| 重复模型调用和重复工具 | 当前行动者校验 + 串行队列 + `action_id` 幂等 |
| 私密剧情泄漏 | 服务端可见性过滤 + 协议防火墙 + 两用户负向测试 |
| 房主掉线导致停局 | 回合不中断、宽限期、owner 跳过/移交机制 |
| 单机存档不兼容 | Alembic 与世界 schema 双迁移、保留旧存档 fixture |
| Azure 小内存 | 空房休眠、活跃房间上限、模型流及时释放、指标告警 |
| 单进程成为瓶颈 | 先测真实负载；达到阈值后再做房间租约和事件总线 |
| 开发分支误发生产 | 独立 staging workflow，生产仍只接受主线质量门禁 |

## 11. 提交与文档策略

`feat/multiplayer` 内按可独立回滚的能力提交，建议顺序：

1. `feat: 添加云端登录与注册界面`
2. `feat: 添加世界邀请和成员管理`
3. `refactor: 按世界共享游戏房间运行时`
4. `feat: 添加房间事件广播和断线补发`
5. `feat: 添加调查员与账号绑定`
6. `feat: 添加当前行动者回合机制`
7. `test: 添加双客户端联机端到端测试`
8. `docs: 更新联机协议与部署文档`

每次数据库变化必须包含 Alembic、SQLite/PostgreSQL 测试和 [`DATABASE.md`](DATABASE.md) 更新；
每次 HTTP/WS 变化必须更新 [`API.md`](API.md)；运行时所有权变化必须更新
[`ARCHITECTURE.md`](ARCHITECTURE.md)。在 Phase 4 之前不把功能称为可用联机版本。
