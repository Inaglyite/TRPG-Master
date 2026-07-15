# TRPG Master 架构重构计划（2026-07-15）

## 目标

把当前“可以运行的单体编排器”演进为可插拔模组平台。重构完成后，新增模组应只需声明内容与规则配置，不得修改 `GameEngine`、WebSocket 服务器或核心工具分发代码。

本计划把以下内容视为硬约束：

- 核心规则与具体模组内容零反向依赖；
- 剧情事实必须由结构化状态决定，LLM 只负责理解意图和叙述；
- 所有世界变更具备单写者语义、修订号和可恢复的提交记录；
- 旧存档通过显式迁移升级，禁止读取时静默猜测；
- WebSocket 协议、模组格式和存档格式都有版本与契约测试；
- 阶段性改造始终保持可运行、可回滚、可定位。

## 当前基线

| 热点 | 规模 | 主要职责混合 |
| --- | ---: | --- |
| `src/engine.py` | 2426 行 / 74 个方法 | 模型会话、规则编排、工具执行、回合提交、存档恢复 |
| `server.py` | 1288 行 / 25 个函数 | HTTP API、WS 协议、并发控制、会话状态、业务处理器 |
| `src/tools.py` | 1094 行 | 工具 schema、参数归一化、分发、领域副作用 |
| `tools/state_manager.py` | 792 行 | 多领域状态命令与兼容逻辑 |

问题的本质不是总代码量，而是变化原因没有隔离：改一条模组规则可能穿过协议层、引擎层和存储层。

## 目标依赖方向

```text
Transport (HTTP / WebSocket)
        ↓
Application (session / turn / save use-cases)
        ↓
Domain (action, encounter, discovery, consequence)
        ↓
Ports (model, repository, clock, random, asset)
        ↓
Adapters (OpenAI-compatible API, JSON store, filesystem)

Module package ──data/declared rules──> Domain
```

任何下层不得 import 上层。模组包不得包含服务器分支逻辑；它只能实现有版本的声明式契约或受控扩展点。

## 分阶段实施

### 1. 会话与并发内核

- 提取世界回合 `TurnLease`、连接级 `SessionTurnGate`、带关联 ID 的 `PendingReply`；
- 每个异常、断线和切世界路径都必须精确释放自己取得的资源；
- 将 WebSocket 消息改为注册式 handler，并对未知消息返回版本化错误；
- 提取 `WsSessionContext`，服务器入口只负责组装依赖和运行消息循环。

退出条件：并发行动、断线重连、跨分支切换、询问超时与乱序回复均有确定性测试；`run_ws_session` 不再含业务 `if/elif` 链。

### 2. 应用用例层

- 提取 `StartGame`、`ResumeGame`、`PerformAction`、`RewriteTurn`、`SwitchWorld`、`ManageSave`；
- 用例返回类型化结果/领域错误，由 transport 统一映射协议消息；
- 回合开始、事件记录、提交/失败和自动存档形成一个事务模板。

退出条件：用例无需 FastAPI、WebSocket 或真实模型即可单元测试；服务器不直接调用存储细节。

### 3. 模型会话与工具运行时

- `ModelSession` 管理消息历史、上下文压缩、模型选择和流式响应；
- `ToolRuntime` 管理工具注册、schema、参数校验、权限、执行和审计；
- 每个工具成为独立 command handler，移除中央字符串分发；
- 工具副作用只能通过 repository port 提交，禁止任意脚本修改世界 JSON。

退出条件：增加工具不修改 dispatcher；模型 provider 可替换；每次工具调用都有输入、结果、状态 revision 与 turn ID。

### 4. 领域引擎瘦身

- `ActionResolver`：arrival / interaction / contact 阶段与检定意图；
- `EncounterResolver`：人物是否在场、强制/随机/幸运遭遇与落空结果；
- `DiscoveryResolver`：前置条件、检定、保底线索、失败代价；
- `ConsequenceResolver`：理智、伤害、时间和关系变化；
- `TurnOrchestrator` 只组合上述服务，不持有具体模组 ID。

退出条件：`GameEngine` 降为兼容 facade 或移除；领域测试完全不调用 LLM。

### 5. 模组格式 v2

- 明确定义 scene、presence、encounter、clue、check、consequence、fallback；
- 用 JSON Schema + 语义编译器验证引用、可达性、线索死锁和保底路径；
- 包含 capability 声明，未知能力在安装阶段失败；
- 提供 v1→v2 编译/迁移器，运行时只消费规范化 IR。

退出条件：两个风格不同的模组仅靠数据接入；编译器能证明主线不存在单点随机失败导致的永久卡死。

### 6. 状态与存档 v2

- 聚合拆分为 character、scene、knowledge、encounter、clock、journal；
- 每个世界状态携带 `schema_version` 与单调 revision；
- 原子提交采用临时文件 + fsync + replace，关键日志可重放；
- 迁移器备份原存档并产生迁移报告，不在读取路径散落兼容分支。

退出条件：崩溃恢复、并发 revision 冲突、旧存档迁移和回滚均有集成测试。

### 7. 工程治理与体积

- 建立 Ruff、类型检查、前后端测试、schema fixture、E2E 的统一 CI；
- 对生成物、运行时世界、构建包和日志制定 Git 边界；
- 在工作树干净并完成备份后重写历史，清除约 15 GB 的历史大对象；
- 用依赖规则和复杂度阈值阻止巨型编排器回归。

退出条件：干净 clone 可复现构建与测试，运行数据不进入源码历史，架构依赖在 CI 自动验证。

## 交付规则

每一阶段必须同时交付：实现、单元测试、跨层契约测试、迁移说明、架构文档更新。禁止以“大重构”为理由长期维护两套可写路径；兼容层必须只读或单向委托，并标明删除版本。

性能不是当前首要矛盾，但需保持：网络/模型调用不占用事件循环；同一世界单写，不同世界可并行；事件流严格有序；状态提交的磁盘 I/O 可观测。

## 已开始

- 已新增独立的会话并发内核；
- 回合资源改为显式、幂等的 lease；
- 切换世界不再改变既有 lease 的释放目标；
- suggest / decision 回复改为封装握手，decision 使用请求 ID 防止乱序串答；
- 已补充相应并发和断线回归测试。

## 当前实施状态

- WebSocket 消息已全部注册式路由，消息循环不再包含业务分支；
- ToolRuntime 已覆盖全部声明工具并消除中央字符串分发；
- ModelSession 已接管历史、活动流、取消和诊断；
- ModelStreamer 已隔离供应商请求、流式 chunk、usage、thinking 与重试协议；
- GameApplication 已提取开始、继续、行动、改写与存档槽位用例；
- 确定性领域层已包含行动阶段、遭遇、发现与后果分类；
- 模组格式 v2 已提供主线可达性和失败保底证明，并有 v1→v2 迁移器；
- 世界状态 v2 已提供领域所有权、迁移历史、迁移前备份与迁移报告。
- GameEngine 已降至 1954 行的兼容 facade，并由 CI 的 2000 行门禁阻止回涨；
- 239 项后端测试、全仓 Ruff、架构/schema 复现检查与前端生产构建均已通过。
