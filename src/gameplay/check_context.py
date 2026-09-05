"""Persistent eligibility and already-announced stakes for pushed checks."""

from __future__ import annotations


def can_push_skill(skill: str) -> bool:
    return skill not in {"luck", "sanity", "san", "dodge", "cthulhu_mythos"} and not skill.startswith(("fighting_", "firearms_"))


def validate_push(world: dict, skill: str, context_id: str, approach: str, target_id: str) -> dict:
    if not can_push_skill(skill) or (world.get("combat_state") or {}).get("active"):
        raise ValueError("此类检定不能孤注一掷")
    previous = (world.get("pc", {}).get("_push_contexts") or {}).get(context_id)
    if not isinstance(previous, dict) or previous.get("used") or not previous.get("push_risk"):
        raise ValueError("没有可重试的失败检定，或已使用孤注一掷")
    scene_id = str((world.get("current_scene") or {}).get("id") or "")
    if previous.get("skill") != skill or previous.get("target_id", "") != target_id or previous.get("scene_id") != scene_id:
        raise ValueError("孤注一掷必须关联原技能、目标和场景")
    if not approach.strip() or approach.strip() == previous.get("approach", "").strip():
        raise ValueError("孤注一掷需要改变具体做法")
    return previous


def record_check_outcome(
    world: dict,
    *,
    skill: str,
    result: dict,
    push: bool,
    push_context_id: str,
    approach: str,
    target_id: str,
    required_success_level: str,
    push_risk: str,
) -> None:
    """Bookkeep push contexts after a check: consume the retried context,
    remember a failed first attempt, and retire contexts obsoleted by success."""
    contexts = world.setdefault("pc", {}).setdefault("_push_contexts", {})
    if push:
        validate_push(world, skill, push_context_id, approach, target_id)
        contexts[push_context_id]["used"] = True
    elif not result["success"]:
        contexts[result["check_id"]] = {
            "skill": skill, "target_id": target_id, "approach": approach,
            "scene_id": str((world.get("current_scene") or {}).get("id") or ""),
            "required_success_level": required_success_level, "push_risk": push_risk, "used": False,
        }
        while len(contexts) > 16:
            contexts.pop(next(iter(contexts)))
    if result["success"]:
        for context in contexts.values():
            if context.get("skill") == skill and context.get("target_id") == target_id:
                context["used"] = True
