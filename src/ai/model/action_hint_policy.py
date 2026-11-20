"""Per-turn generation preference; never changes game decisions or saved history."""
from __future__ import annotations

import json

DISABLED_INSTRUCTION = """本轮玩家已关闭行动提示，此设置优先于模组、Skill 和旧对话中的选项要求。
只输出本轮叙述和人物台词，不主动提供建议行动、编号菜单或“你可以——”。
顶层 choices、choice_skill_hints、choice_targets 必须全部为空数组 []。
必要的检定、玩家确认与待决问题仍按引擎交互处理，不得因此替玩家作决定。"""


def action_hints_enabled(host) -> bool:
    return getattr(host, "_turn_generate_action_hints", True) is not False


def generation_instruction(host) -> str:
    return "" if action_hints_enabled(host) else DISABLED_INSTRUCTION


def history_without_choices(content: str) -> str:
    """Remove menu examples on the provider copy, preserving speaker identities."""
    if not content:
        return content
    try:
        value = json.loads(content)
    except (ValueError, TypeError):
        return content
    if not isinstance(value, dict) or "segments" not in value:
        return content
    value.update(choices=[], choice_skill_hints=[], choice_targets=[])
    return json.dumps(value, ensure_ascii=False)
