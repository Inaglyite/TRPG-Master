"""Persistent eligibility and already-announced stakes for pushed checks."""

from __future__ import annotations

import re


def can_push_skill(skill: str) -> bool:
    return skill not in {
        "luck",
        "sanity",
        "san",
        "dodge",
        "cthulhu_mythos",
    } and not skill.startswith(("fighting_", "firearms_"))


def validate_push(world: dict, skill: str, context_id: str, approach: str, target_id: str) -> dict:
    if not can_push_skill(skill) or (world.get("combat_state") or {}).get("active"):
        raise ValueError("此类检定不能孤注一掷")
    previous = (world.get("pc", {}).get("_push_contexts") or {}).get(context_id)
    if not isinstance(previous, dict) or previous.get("used") or not previous.get("push_risk"):
        raise ValueError("没有可重试的失败检定，或已使用孤注一掷")
    scene_id = str((world.get("current_scene") or {}).get("id") or "")
    if (
        previous.get("skill") != skill
        or previous.get("target_id", "") != target_id
        or previous.get("scene_id") != scene_id
    ):
        raise ValueError("孤注一掷必须关联原技能、目标和场景")
    if not approach.strip() or approach.strip() == previous.get("approach", "").strip():
        raise ValueError("孤注一掷需要改变具体做法")
    return previous


def _normalized(text: object) -> str:
    return " ".join(str(text or "").split())


def approach_fingerprint(approach: str, input_quote: str = "") -> str:
    """裁决模型改写措辞不改变玩家输入的逐字锚点：做法指纹合并两者。
    两条执行路径（裁决/确定性兜底）共用同一构造，保证同一次玩家输入
    无论裁决如何措辞都能匹配到同一历史记录。"""
    approach = _normalized(approach)
    quote = _normalized(input_quote)
    if quote and quote not in approach:
        return f"{approach}⟦{quote}⟧"
    return approach


class RepeatCheckError(ValueError):
    """重复检定闸门拒绝。与模型调用失败严格区分：被拒绝的行动不得退回
    可掷骰的确定性流程，只能按 not_executed 落账。"""


def find_repeat_check(world: dict, *, skill: str, target_id: str, approach: str) -> dict | None:
    """Locate an earlier check under identical conditions in the current scene.

    目标边界：双方目标都已知且不同 → 不是重复，直接放行；"目标未知"不当作
    "同一目标"。做法指纹为 approach⟦input_quote⟧ 或玩家原文：完全相同或短长
    包含即同一动作——裁决模型改写措辞不改变玩家输入的逐字锚点。调用方给不出
    做法（空）时无法证明"做法已改变"，只要同技能+目标场景有记录即按重复处理。
    """
    scene_id = str((world.get("current_scene") or {}).get("id") or "")
    wanted_approach = _normalized(approach)
    wanted_target = str(target_id or "")
    history = world.get("pc", {}).get("_check_history") or []
    for record in reversed(history):
        if not isinstance(record, dict) or record.get("scene_id") != scene_id:
            continue
        if record.get("skill") != skill:
            continue
        record_target = str(record.get("target_id") or "")
        if wanted_target and record_target and wanted_target != record_target:
            continue
        record_approach = _normalized(record.get("approach"))
        if not wanted_approach:
            return record
        if not record_approach:
            continue
        # 指纹按 ⟦⟧ 拆成做法/引文分量交叉比对：裁决改写措辞、兜底传玩家原文，
        # 只要共享同一逐字锚点（或做法本身相同/包含）即同一动作。
        wanted_parts = _approach_parts(wanted_approach)
        record_parts = _approach_parts(record_approach)
        if any(
            wanted == known or (wanted in known) or (known in wanted)
            for wanted in wanted_parts
            for known in record_parts
        ):
            return record
    return None


def _approach_parts(text: str) -> list[str]:
    return [part for part in re.split(r"[⟦⟧]", text) if len(part) >= 2]


def validate_repeat_check(
    world: dict, *, skill: str, target_id: str, approach: str, push: bool = False
) -> None:
    """相同条件不重复掷骰：换做法、付出代价（孤注一掷）或局势变化后才能重试。"""
    if push:
        return
    record = find_repeat_check(world, skill=skill, target_id=target_id, approach=approach)
    if record is None:
        return
    if record.get("success"):
        raise RepeatCheckError("相同方法与目标的检定已成功，局势未变前不再重复掷骰")
    raise RepeatCheckError("相同做法的检定已失败：改变具体做法，或由玩家明确要求孤注一掷")


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
    """Bookkeep one check: consume a retried push context, remember a failed
    first attempt, retire contexts obsoleted by success, and append the
    condition key to the repeat-check history."""
    contexts = world.setdefault("pc", {}).setdefault("_push_contexts", {})
    if push:
        validate_push(world, skill, push_context_id, approach, target_id)
        contexts[push_context_id]["used"] = True
    elif not result["success"] and approach and push_risk and can_push_skill(skill):
        contexts[result["check_id"]] = {
            "skill": skill,
            "target_id": target_id,
            "approach": approach,
            "scene_id": str((world.get("current_scene") or {}).get("id") or ""),
            "required_success_level": required_success_level,
            "push_risk": push_risk,
            "used": False,
        }
        while len(contexts) > 16:
            contexts.pop(next(iter(contexts)))
    if result["success"]:
        for context in contexts.values():
            if context.get("skill") == skill and context.get("target_id") == target_id:
                context["used"] = True
    history = world["pc"].setdefault("_check_history", [])
    history.append(
        {
            "skill": skill,
            "target_id": target_id,
            "approach": approach,
            "scene_id": str((world.get("current_scene") or {}).get("id") or ""),
            "success": bool(result["success"]),
            "check_id": str(result.get("check_id") or ""),
        }
    )
    while len(history) > 24:
        history.pop(0)
