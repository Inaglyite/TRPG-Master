# 后端问题：agent 世界缺 BYOK 时，暂停状态没有回帧给客户端（交给 Kimi）

**文件**：`src/structured/agent_runtime.py` → `_run_keeper_agent` 的「模型路由不可用」分支（`build_byok_caller` 抛错后的降级路径）。

## 复现（无模型，秒级）

```bash
OPENAI_API_KEY=probe-placeholder OPENAI_BASE_URL=http://127.0.0.1:9/v1 \
env -u PYTHONPATH .venv/bin/python - <<'PY'
# 建隔离世界 → 切 structured_v1 + keeper_mode=agent（不写 BYOK）→ 提交一条 action_request
PY
```

实测输出（2026-09-14）：

```
提交前 keeper_control: None
keeper_agent_needed: agent
提交后 keeper_control: ('agent', 'run_b6b916c1745ddc6b', 1)
请求状态: paused | detail: 守秘人助手未配置模型服务（BYOK），请求已暂停；请房主配置模型或改由人类主持。
事件类型: ['action_ack', 'intent_pending']      ← 少了 action_status{status: paused}
```

## 问题

该分支里：

```python
gateway.service.execute_command(
    world_id=..., principal=..., kind="resolve_intent",
    payload={"request_id": trigger_request_id, "resolution": "paused", "note": ...},
    command_id=..., expected_revision=None,
)   # ← 返回的 outcome（含 events）没有投递，也没有 deliver/broadcast 可用
```

命令**提交成功、世界状态正确**（`player_requests.status = paused`），但 `outcome["events"]` 被丢弃，
客户端收不到 `action_status{paused}`。

## 影响（前端侧可复现）

- 本地真实后端 E2E `e2e/structured-interaction-duals.spec.ts` 的用例
  「agent 缺 BYOK：实时帧必须让请求离开『处理中』」**失败**：卡片在 120s 内一直停在
  `queued`（`data-status=queued`），玩家看到永久「已提交，等待服务端确认」。
- 已把该用例登记为 `test.fixme`（原因即本文），并保留通过的半边断言：
  **刷新后**能从快照恢复到「已暂停（可恢复）」+ 输入可用 + 可接管。

## 建议改法（不指定实现细节）

1. 该分支把 `execute_command` 的 `outcome["events"]` 按连接投递（本地 `deliver`、房间 `broadcast`
   按 principal 过滤），与其它暂停路径（模型失败/预算/空输出）保持一致；
2. 或者统一走一个「置暂停并投递」的内部函数，避免不同降级分支行为不一致。

## 顺带一条（非阻塞）

暂停/失败的**原因文本**（`player_requests.detail`）目前不在 `session_snapshot.requests[]` 投影里，
所以刷新后前端只能显示「已暂停（可恢复）」，看不到「缺 BYOK / 输出被截断」这类可操作说明。
建议投影里带上 `detail`（或一个公开安全的 `reason` 字段）。
