"""体积敏感的工具 schema 定义（从 tools.py 拆出，守住架构行数 ratchet）。

这里只放 OpenAI tool 定义字典；执行策略与 handler 仍归 ``src.ai.tools.registry`` /
``src.ai.tools.tool_aux_handlers``。
"""

from __future__ import annotations

READ_FILE_TOOL = {
    "type": "function",
    "function": {
        "name": "read_file",
        "description": "读取项目中的文件内容。",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "相对路径，如 rules/rule_schema.json"}
            },
            "required": ["path"],
        },
    },
}

LOAD_SKILL_TOOL = {
    "type": "function",
    "function": {
        "name": "load_skill",
        "description": (
            "按 id 加载 system prompt 末尾【按需 Skill 目录】列出的非关键规则 Skill。"
            "只接受本次请求冻结目录中出现的 skill_id；常驻与引擎自动注入的规则不可用此工具。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "skill_id": {
                    "type": "string",
                    "description": "按需 Skill 目录中的 id，如 keeper.magic",
                }
            },
            "required": ["skill_id"],
        },
    },
}


END_GAME_TOOL = {
    "type": "function",
    "function": {
        "name": "end_game",
        "description": "请求结束游戏。若模组定义了结局，必须提供 ending_id；引擎会校验 required_flags，前置条件未满足时拒绝进入结局画面。",
        "parameters": {
            "type": "object",
            "properties": {
                "ending_id": {
                    "type": "string",
                    "description": "world_state.endings 中的稳定结局 ID",
                },
                "source_token": {
                    "type": "string",
                    "description": "可选：inspect_turn_state 返回的 ending_readiness.source_token；状态变化后拒绝旧来源。",
                },
                "ending_type": {
                    "type": "string",
                    "enum": ["good", "bad", "neutral", "secret"],
                    "description": "结局类型",
                },
                "title": {
                    "type": "string",
                    "description": "结局标题，如「真相大白」「疯狂之末」",
                },
                "summary": {
                    "type": "string",
                    "description": "结局概要，200字以内，总结整个冒险的关键发现和最终命运",
                },
            },
            "required": ["ending_type", "title", "summary"],
        },
    },
}
