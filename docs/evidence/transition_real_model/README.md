# 过渡回合真实模型验收：轨迹证据

本目录保存**隔离测试世界**里真实模型调用的脱敏轨迹，用于复核「平台到底看到了什么」。

## 文件

| 文件 | 说明 |
|---|---|
| `trace_early_iteration.json` | 早期迭代（命令留痕尚未包到运行器内部 service；预算 16000）。用于对照「同一场景在不同代码版本下的行为」，**不作为验收结论** |
| `trace_budget16000.json` | 收口版代码 + 预算 16000：S1/S2/S3/S5 通过，S4 截断中止，S6 因截断失败（见交付记录 §7） |
| `trace_acceptance_frozen.json` | **验收轮**：冻结版代码（8 个文件 sha256 记在报告里） + 预算 32768，一次跑完 S1–S6（含换说法/取消/普通直接移动的对偶）。判定见交付记录 §7.5 |
| `trace_acceptance_frozen.console.txt` | 同一轮的控制台逐场景摘要（含被拒命令与叙事片段） |

跑法（世界与库都在临时目录，不碰任何真实存档）：

```bash
env -u PYTHONPATH .venv/bin/python tools/transition_real_model_check.py \
    --report /tmp/transition_trace_final.json
# 需要更大输出预算时：TRPG_TRACE_MAX_OUTPUT=32768 前缀
```

## 每次调用记录了哪些字段

```jsonc
{
  "request": {            // 实际发给模型的请求
    "model": "…", "max_tokens": 32768, "temperature": 0.4,
    "response_format": {"type": "json_object"},
    "messages": [ {"role": "system", "content": "…"}, {"role": "user", "content": "{…上下文…}"} ]
  },
  "response": { "content": "…", "reasoning_chars": 62880, "finish_reason": "length" },
  "usage": { "prompt_tokens": …, "completion_tokens": …, "total_tokens": … },
  "diagnostics": { /* 见下 */ }
}
```

`user` 上下文里含：`snapshot`（场景/目的地/线索/物品/角色/未决请求）、`pending_requests`
（含 `deferred_player_intent` 与 `deferred_player_intent_is_authorization=false`）、
`recent_public_messages`（最近公开对话）、`trigger_request_id`、`run_log`（本运行内命令结果与被拒原因）。

每次命令尝试记录在 `command_attempts`：

```jsonc
{ "kind": "resolve_intent", "payload": {…}, "command_id": "…",
  "result": {…}            // 提交成功
  // 或
  "rejected": { "code": "invalid_action", "message": "…" }   // 拒绝原因（含 schema 拒绝）
}
```

每回合记录：`player_message`（最新玩家消息）、`scene_before/scene_after`、`moved`、
`request`（状态 + detail + 待办）、`events`（服务端投递的全部事件）、`pending_after`、`verdict`。

脱敏：报告只记 `api_key_present: true/false`，不含任何明文密钥；`code_revision` 记录本次
验收对应的 HEAD 与工作区是否 dirty，避免把不同版本的通过结果拼在一起。

## 复核用的四个检查（`diagnostics` 字段）

| 检查 | 字段 | 结论 |
|---|---|---|
| 历史截断 | `recent_message_count`、`recent_message_chars[]`、`truncated_recent_messages`、`recent_limit_note` | 上限为「最多 8 条、每条 400 字符」；本轮实测各条均 < 400，未发生截断 |
| 角色顺序 | `roles` | 每次调用都是 `["system", "user"]`，无混序 |
| 提示词冲突 | `prompt_has_question_rule`、`prompt_has_insist_rule`、`prompt_has_deferred_not_authorization_rule`、`system_chars` | 三条规则都在实际 system 里；目录与 schema 的 4 处不一致已修（交付记录 §7.3） |
| 待办是否被当成已授权任务 | `deferred_entries`、`deferred_intent_marked_not_authorization` | 只要出现待办，该标记逐次为 `true`（字段名与布尔位同时写明「不是执行授权」） |
| 快照投影 | `snapshot_keys` | 与 `session_snapshot` 一致（不含主持专用字段） |
