"""体积敏感的工具 schema 定义（从 tools.py 拆出，守住架构行数 ratchet）。

这里只放 OpenAI tool 定义字典；执行策略与 handler 仍归 ``src.tools`` /
``src.tool_aux_handlers``。
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
