"""Deterministic optional-Skill injection controlled by the engine."""

from __future__ import annotations

from typing import Any

from .config import OPTIONAL_SKILL_HINTS
from .skill_resources import load_optional_skill_resource

KEYWORD_SKILL_MAP = {
    "skills/keeper/keeper_items.skill": (
        "鸣枪", "开枪", "射击", "扣动扳机", "子弹", "装弹", "换弹", "喝下", "服用", "点燃",
        "烧掉", "使用钥匙", "打开手电筒", "急救包", "消耗道具", "使用物品",
    ),
    "skills/keeper/keeper_combat.skill": (
        "开枪", "射击", "攻击", "挥拳", "拔枪", "持枪", "用枪", "枪指", "瞄准", "威胁", "拔刀",
        "砍", "刺", "砸", "战斗", "搏斗", "斗殴", "反击", "闪避", "伤害", "受伤", "倒地",
        "武器", "手枪", "左轮", "刀", "棍", "枪", "弹药",
    ),
    "skills/keeper/keeper_psychology.skill": (
        "疯狂", "崩溃", "失控", "幻觉", "尖叫", "发疯", "恐惧症", "躁狂",
    ),
    "skills/keeper/keeper_magic.skill": (
        "魔法", "咒语", "施法", "仪式", "召唤", "神话典籍", "诅咒", "克苏鲁神话",
    ),
}


def inject_optional_skill(engine: Any, skill_path: str, *, log_error: Any) -> None:
    """Append one allowlisted resource exactly once before model execution."""
    if skill_path in engine._loaded_optional_skills:
        return
    content = load_optional_skill_resource(engine.context.project_root, skill_path)
    if content is None:
        log_error(f"可选 Skill 加载失败: {skill_path}")
        return
    engine._loaded_optional_skills.add(skill_path)
    engine.append_control_instruction(
        f"以下 Skill 规则已经由引擎加载，请在本回合应用：{skill_path}\n\n{content}"
    )


def hint_optional_skill(engine: Any, tool_name: str, *, log_error: Any) -> None:
    skill_path = OPTIONAL_SKILL_HINTS.get(tool_name)
    if skill_path:
        inject_optional_skill(engine, skill_path, log_error=log_error)


def inject_skills_for_player_content(engine: Any, content: str, *, log_error: Any) -> None:
    for skill_path, keywords in KEYWORD_SKILL_MAP.items():
        if skill_path not in engine._loaded_optional_skills and any(word in content for word in keywords):
            inject_optional_skill(engine, skill_path, log_error=log_error)
