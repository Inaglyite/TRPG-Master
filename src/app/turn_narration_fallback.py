"""Close a completed tool budget with one narration-only provider request."""


def narrate_settled_tools(state: dict) -> dict:
    engine = state["engine"]
    from src.ai.model.combat_request_policy import needs_combat_tool

    if engine._combat_active() and needs_combat_tool(engine, "combat", "disabled", [{"function": {"name": "combat_action"}}]):
        raise RuntimeError("工具预算已耗尽，战斗行动仍未结算；本轮不能凭叙事提交")
    engine.messages.append({"role": "user", "content":
                            "[引擎控制指令｜非玩家发言] 本轮工具阶段已结束。仅根据已执行回执写出本轮完整正文，未执行事项留待下一轮；不能再请求工具或代替玩家行动。"})
    combat = engine._combat_active()
    text, calls = engine._stream_llm(
        engine.judgement_model if combat else engine.current_model,
        system_overlay=engine._combat_system_overlay() if combat else None,
        enable_tools=False,
        buffer_if_tools=True,
    )
    if calls or not text.strip():
        raise RuntimeError("本轮工具已结算，但模型未生成有效的最终叙述")
    return {"text": text, "tool_calls": []}
