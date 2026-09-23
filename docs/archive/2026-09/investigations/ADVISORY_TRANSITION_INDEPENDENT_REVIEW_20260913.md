# 过渡节拍独立复核

> 历史归档（2026-09-16 整理）：正文保留当时的结论与适用版本，不作为当前完成状态或执行授权。现行入口见 [README](../../../../README.md)，未完成事项见 [STATUS](../../../STATUS.md)。正文中的仓库路径按仓库根目录理解，`/tmp` 产物不保证仍存在。

日期：2026-09-13。基于当前未提交工作区；仅检查和临时世界离线探针，未修改业务代码、真实存档或生产。

## 结论

旧 entry_beat 的 supersedes 精确字典匹配、剥离迁移元数据与分支同步钩子均已实现。移动函数返回 None/异常时阻止过渡叙事的修改也存在。但是“结算后播出”仅是回合缓存内结算，并非数据库事务提交成功；最终交付仍需保留这一限制。

## 主要发现：提交前公开过渡事实

- `src/app/engine.py:handle_action` 在 `world_store.turn_cache()` 中执行图。
- `src/app/agent_graph.py` 调用 `_resolve_scene_transition` 后立即调用叙事回调。
- `src/storage/database_store.py:turn_cache` 缓冲写入，异常时丢弃工作状态。
- 真正的最终回合提交发生在 graph finalize 调用 `_complete_turn_record`，后者调用 `journal.complete`，之后才 accept_turn_commit。
- `server.py:on_narrative` 将回调转成 narrative_chunk 发送；不是等最终提交才释放的缓冲。
- `tests/test_discovery.py:test_transition_beat_is_announced_only_after_settlement` 直接调用 `_prepare_turn`，且用同一个 store.load 观察状态。没有覆盖生产完整回合的缓存/最终提交边界。

独立探针：使用 TemporaryDirectory 创建独立 RuntimeContext，复用 DiscoveryResolutionTests._preview_engine（模型相关步骤替身），在真实 DatabaseWorldStore.turn_cache 中执行真实 _prepare_turn 和移动函数。第二个 DatabaseWorldStore 实例读取同一临时数据库，不共享工作缓存。在准备阶段完成后、最终提交前注入 RuntimeError。

实际输出：

```text
before miskatonic_university
working_scene miskatonic_medical
persisted_scene miskatonic_university
notification_streamed True
handouts_streamed 1
injected simulated failure before final commit
after_rollback miskatonic_university
```

这证明过渡通知与图片已送至回调时，移动尚未持久化，后续异常会撤销工作状态。该探针不是完整浏览器/网络故障复现，也未直接向 journal.complete 注入数据库异常；代码时序与探针共同确认了提交前外发窗口。

## 建议与补测

1. 将“移动函数成功”和“持久提交成功”区分，修正报告中的保证范围。
2. 推荐在既有原子回合事务边界内缓冲权威过渡事件与素材，提交成功后按事件顺序发布。若保留早期流式展示，则需设计明确的暂定/作废协议；它不能撤回玩家已看到的秘密素材。
3. 不要仅调用 flush_turn 提前保存场景：必须同时考虑时间、遭遇、状态、回合记录、快照及重试语义。若要拆成阶段提交，需要独立设计，不能作为局部补丁悄悄引入。
4. 新增完整 handle_action + 真实临时数据库集成测试：在 journal.complete 提交前故障，独立连接检查状态未变，断言权威叙事/handout 未作为成功结果发布；重试只生效一次。
5. 对成功提交后推送失败单独测试：恢复应重放既有结果，不能重新移动、扣时间或掷骰。

## 其他核对

精确迁移仍不保护整体静态刷新替换 scene_catalog 的路径，报告已承认这一点。当前工作区还含场景指示器及多人改动，最终集成结果应以冻结版本为准。本次未独立执行真实模型主线，不认可将先前两次 32/33 写成全绿。
