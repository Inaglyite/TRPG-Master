# 回合性能

本页描述 `perf/optimization` 分支实现的回合延迟优化。目标是降低玩家提交动作到首段可见叙事
（TTFV）及完整提交耗时；不依赖缩短 system prompt 或关键词 Skill 路由。

## 已实现

- `TurnPerformance` 记录准备、模型、工具、实体同步、模型审计和数据库提交阶段；指标随
  `turn_performance` WebSocket 事件实时发送，并保存在回合 diagnostics 中。
- 模型设置面板显示首段可见、准备、工具、审计和提交耗时。
- 所有模型可调用的内置工具均在服务进程内执行；`tools/*.py` 继续作为人工 CLI 入口，但运行时
  不再为每次工具调用启动 Python 子进程。
- `DatabaseWorldStore.turn_cache()` 在同一条已串行化的世界回合中复用读取；每次 mutation 更新
  缓存，回合结束立即清除，避免跨回合陈旧状态。
- 完成回合时，自动存档 `slot_000` 与 TurnJournal 共用同一个不可变 Snapshot，不再重复序列化和
  写入相同世界状态。
- `TurnMutationLedger` 记录确定性领域变化和权威工具变化。已经落账的变化不再触发冗余的回合末
  模型审计；无法确定的纯叙事变化仍保守回退到审计模型。
- 已有 ActionResolution 快速路径在模型调用前完成明确移动、调查、检定、发现和战斗前确认；结算
  完成的回合只调用模型叙述结果，不再让模型重复规划相同检定。
- 相邻 WebSocket `narrative_chunk` 默认在 25ms 窗口内合并，降低小包和渲染调度开销；骰子、素材、
  NPC 身份和其他事件边界不会被跨越。每回合首个文本块立即发送，不等待合并窗口。
- 每个 `GameEngine` 复用同一个 OpenAI 客户端及其 HTTP 连接池。

## 指标

回合 diagnostics 的 `performance` 包含：

```json
{
  "turn_total_ms": 4200.0,
  "first_visible_ms": 850.0,
  "phases_ms": {
    "prepare": 12.0,
    "model_total": 3800.0,
    "tool_execution": 4.0,
    "model_audit": 0.0,
    "journal_commit": 18.0
  },
  "counters": {
    "model_call_count": 1,
    "model_tool_call_count": 0
  }
}
```

`first_visible_ms` 是玩家回合建立到首段可见文本的服务端时间。模型供应商 TTFT 另存于
`model_calls[].first_token_ms`，两者的差值反映准备、句子边界保护和传输前处理时间。

## 本地基准

基准不调用模型，不写入项目数据库：

```bash
venv/bin/python tools/benchmark_turn_performance.py 100
```

2026-07-22 在开发机上的一次 100 轮结果：进程内 `2d6+1` 平均约 0.009ms；普通 SQLite
世界读取平均约 0.618ms；turn cache 读取平均约 0.125ms。数值只用于同机版本比较，不作为其他
硬件的绝对承诺。

## 配置与回退

`TRPG_STREAM_BATCH_MS` 控制文本合并窗口，默认 `25`，允许范围 `0–100`。设置为 `0` 可关闭合并。
数据库缓存只在 `GameEngine.handle_action()` 的世界锁保护期内启用，不提供跨请求全局缓存。

性能改动的回归覆盖见 `tests/test_performance_optimization.py`、`tests/test_event_stream.py`、
`tests/test_database_persistence.py` 和 `tests/test_turn_resolution.py`。
