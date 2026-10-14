"""Narrow state-repair capabilities exposed through the issued tool catalog."""

TURN_STATE_TOOLS = [
    {"type": "function", "function": {
        "name": "inspect_turn_state",
        "description": "发现场景或说话角色与叙事不一致时，查询本回合已结算场景、在场角色 ID、待决出发事项与状态冲突。无冲突应修改叙事；有冲突可请求 repair_turn_state。",
        "parameters": {"type": "object", "properties": {}, "required": []},
    }},
    {"type": "function", "function": {
        "name": "repair_turn_state",
        "description": "使用 inspect_turn_state 刚返回的 snapshot_token，把有冲突的场景 ID、名称、出口或在场角色恢复为本回合引擎已结算结果。不能提交自选值、执行新移动、解决待决事项或改写骰子、物品、线索、HP、时钟。返回执行回执后据此写结构化对话。",
        "parameters": {"type": "object", "properties": {
            "snapshot_token": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
            "reason": {"type": "string", "minLength": 1, "maxLength": 600},
        }, "required": ["snapshot_token", "reason"]},
    }},
]
