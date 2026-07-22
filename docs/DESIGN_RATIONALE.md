# 设计依据与参考资料

本文汇总 `docs/ARCHITECTURE.md` 各项设计决策的外部参考与推导笔记。架构现状以
ARCHITECTURE.md 为准；本文回答“为什么这么设计”。

- [Anthropic 的 Agent 实践](https://www.anthropic.com/engineering/building-effective-agents)建议可预测任务优先使用确定工作流，仅把真正需要开放判断的部分交给 Agent。
- [ReAct](https://arxiv.org/abs/2210.03629)说明推理与环境行动可以交错；[ReSpAct](https://arxiv.org/abs/2411.00927)进一步把面向用户的 speaking 与 acting 分开。本项目据此区分玩家可见前置叙事、确定性规则效果和模型续写，而不是把三者塞进一个不可观察步骤。
- [Ink](https://github.com/inkle/ink/blob/master/Documentation/RunningYourInk.md) 与 [Yarn Spinner](https://github.com/YarnSpinnerTool/YSDocs/blob/main/docs/yarn-spinner-for-unity/components/dialogue-runner.md)都把叙事文本、运行时命令和变量状态分层处理。
- LangGraph [`ToolNode`](https://github.com/langchain-ai/langgraph/blob/main/libs/prebuilt/langgraph/prebuilt/tool_node.py)支持工具执行与状态注入，但工具调用后的下一轮模型推理仍需单独请求。
- LangGraph 的[持久执行说明](https://docs.langchain.com/oss/python/langgraph/functional-api#idempotency)要求产生写入的副作用可幂等，并建议将副作用放到可识别的任务边界；本项目的线索、NPC 揭示和 handout 去重遵循同一原则。
- [AG-UI 事件协议](https://docs.ag-ui.com/concepts/events)使用 Start/Content/End 生命周期、稳定 ID 与有序事件，并要求客户端能抵抗乱序；当前 `turn_id + seq` 是适合本地 WebSocket 的最小实现。
- [AWS Transactional Outbox 指南](https://docs.aws.amazon.com/prescriptive-guidance/latest/cloud-design-patterns/transactional-outbox.html)指出状态写入与通知双写会造成遗漏或乱序。当前单机版用权威世界状态、幂等素材记录、单连接 FIFO 和 `done` 后状态快照规避该问题；多人数据库阶段再升级为持久 outbox，而不是现在提前引入消息中间件。
- [SillyTavern Chat File Management](https://docs.sillytavern.app/usage/core-concepts/chatfilemanagement/)把 checkpoint 定义为复制到指定消息并保留父链接；本项目采用相同交互概念，但同时复制权威世界快照和 TurnRecord 父链。
- [SillyTavern World Info](https://docs.sillytavern.app/usage/core-concepts/worldinfo/)采用扫描深度与 context budget 动态注入条目；本项目在此基础上增加场景/NPC/线索/flag 门槛和不含正文的命中 trace。
- [SillyTavern Quick Replies](https://docs.sillytavern.app/usage/st-script/)说明快捷回复可执行脚本。本项目只借鉴入口效率，不开放任意脚本：快捷行动必须作为可见 `action` 经过原有预检与状态机。
