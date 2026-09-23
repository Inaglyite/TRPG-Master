# 协议入口

现行入口，整理于 2026-09-16；汇总基线 `46d5760`。字段契约按 schema 和实际 handler 核对，不以历史交接报告替代。

## 1. 三类协议不要混用

| 范围 | 正本/详细参考 | 入口 |
|---|---|---|
| 账号、模组、设置、世界等 HTTP；legacy WS 与房间控制 | [HTTP 与 legacy API](reference/API.md)；实际 HTTP OpenAPI 与路由 | `server.py`、`src/auth`、`src/web`、`src/multiplayer` |
| structured_v1 行动、命令、事件 | [JSON Schema](../schemas/structured-play/v1/) + [语义详表](reference/STRUCTURED_PROTOCOL_V1.md) | `src/structured/{gateway,validation,service}.py` |
| 模组作者格式 | [模组格式](MODULE_FORMAT.md) + [模组 schema](../schemas/trpgmod/) | `src/modules/` |

`/ws` 和 `/ws/room` 是传输入口，不直接表示运行模式。以服务端世界元数据与 `server_capabilities` 协商；结构化协议缺失/不支持时明确报错，不静默回退旧文字动作。

## 2. 请求与命令

玩家帧：`action_request`（move/present_clue/use_item/freeform）、`free_roll_request`、`check_response`、`cancel_request`。主持帧：`command_request`、`memory_query`。每帧字段、必填项、枚举和权限见 schema/permission-matrix；不在此复制易漂移的全部字段。

- 按钮提交稳定对象 ID、目标和数量，不拼接文本再解释。
- freeform 原样交主持理解，不直接修改状态。
- 出示区分说明、图片、原件；原件要求持有实物，出示不转移所有权。
- 普通骰不推进剧情，不代替已经请求的检定。检定回应只引用待办 ID 和 roll/decline，参数与随机数由服务端控制。
- 主持命令包含发言、移动、检定、线索/素材、物品、属性/时间、NPC 在场、事实、请求/草稿收尾、角色记忆；以 command schema 列表为准。
- assisted 批准草稿不等于自动执行；具体命令仍独立提交。

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

- 重连：恢复当前状态与合法待办，不当作主动读档。
- structured 读档：走专用 reconcile；不能直接调用旧 engine.load 后继续使用旧请求。
- `solo_branch_create`：legacy 需要有效 turn_id；structured 从当前 committed 状态分支，不要求伪造 turn_id，可携带 expected_revision；模式不匹配或版本冲突要拒绝。
- 不因云端单人分支已支持就宣称多人房间任意读档/分支入口均支持；边界见[状态](STATUS.md)。

## 7. 协议变更检查

同时核对 schema/fixtures、后端校验与领域授权、Agent 可见命令说明、前端 builder、`server-message.ts` 外层白名单、结构化事件解析、实时投影、快照恢复、错误提示。

参考测试：`tests/test_structured_play_protocol.py`、`test_structured_command_catalog.py`、前端 `protocol/m0-fixtures.test.ts` 及真实后端 E2E。详表中的阶段说明不代表最新完成度；完成度只在 [STATUS](STATUS.md) 更新。
