"""Goal continuity and authored clock triggers for pre-roll adjudication."""

from __future__ import annotations

import copy
import json


def check_conditions(world: dict) -> dict:
    """Observable resources/people/environment; neither wording nor clock ticks."""
    from src.gameplay.guest_speakers import registered_guest_ids

    scene = world.get("current_scene") or {}
    return {
        "inventory": sorted(str(x) for x in (world.get("pc") or {}).get("inventory") or []),
        "scene": copy.deepcopy({k: scene.get(k) for k in ("id", "description", "tags", "items", "npcs_present")}),
        "guests": sorted(registered_guest_ids(world, str(scene.get("id") or ""))),
    }


def policy_instruction(engine) -> str:
    """Use this world's frozen skill; old worlds retain their original pins."""
    from src.ai.skills.skill_activation import skill_pins

    # Tiny test hosts do not own a catalog. Runtime pin failures must propagate:
    # replacing a damaged pin with current disk content would change the rules.
    if not hasattr(engine, "_skill_pins_cache"):
        return ""
    pins = skill_pins(engine)
    pin = (pins or {}).get("keeper.adjudication")
    return pin.content if pin else ""


def recent_goal_checks(world: dict) -> list[dict]:
    scene_id = str((world.get("current_scene") or {}).get("id") or "")
    return [
        {key: row.get(key) for key in (
            "check_id", "skill", "target_id", "approach", "success", "goal_id", "goal",
            "changed_conditions", "outcome", "goal_achieved", "condition_snapshot", "discovery_ids",
        )}
        for row in ((world.get("pc") or {}).get("_check_history") or [])
        if isinstance(row, dict) and row.get("scene_id") == scene_id
    ][-24:]


def validate_goal_check(proposal, world: dict, content: str, dialogue: list[dict]) -> None:
    """Check stable goal reuse without guessing player intent from keywords."""
    from src.gameplay.action_adjudication import ProposalValidationError

    if not proposal.goal_id or not proposal.check:
        return  # Old frozen plans predate the goal contract.
    if not proposal.goal.strip() or not proposal.check.obstacle.strip():
        raise ProposalValidationError(
            "检定必须明确玩家目标和真实阻力；日常行为请使用 check=null",
            code="missing_check_stakes", field="check.obstacle",
        )
    discovery_ids = {str(ref).rsplit(":", 1)[0] for ref in proposal.discovery_refs}
    previous = next(
        (row for row in reversed(recent_goal_checks(world))
         if row.get("goal_id") == proposal.goal_id
         or discovery_ids.intersection(row.get("discovery_ids") or [])), None,
    )
    if previous is None or proposal.check.push_context_id:
        return
    if proposal.changed_conditions:
        evidence = proposal.change_evidence_quote.strip()
        sources = [content, *(str(m.get("content") or "") for m in dialogue)]
        # Only facts already exposed in the current private state are evidence.
        sources.append(json.dumps({
            key: world.get(key) for key in ("current_scene", "combat_state", "last_action_outcome")
        }, ensure_ascii=False))
        before = previous.get("condition_snapshot")
        changed = isinstance(before, dict) and before != check_conditions(world)
        if (changed and len(evidence) >= 4 and any(evidence in source for source in sources)
                and proposal.changed_conditions != previous.get("changed_conditions")):
            return
        raise ProposalValidationError(
            "局势变化必须有物品、在场人物或环境的真实变化及来源引文；换摸法、换技能不是新条件，请按孤注一掷处理",
            code="ungrounded_check_change", field="change_evidence_quote",
        )
    if previous.get("success") or previous.get("goal_achieved"):
        raise ProposalValidationError(
            "该目标已经成功，条件未变请沿用结果，check=null，不重复结算原有后果",
            code="goal_already_resolved", field="goal_id", value=proposal.goal_id,
        )
    raise ProposalValidationError(
        "同一目标已失败；不能换技能免费重试，需要明确孤注一掷或有证据的局势变化",
        code="goal_requires_push", field="goal_id", value=proposal.goal_id,
    )


def record_goal_check(world: dict, plan, check_result: dict | None, description: str,
                      *, goal_achieved: bool | None = None, turn_id: str | None = None,
                      resolution_id: str | None = None) -> None:
    """Enrich the executed check's durable record in the outcome transaction."""
    if not plan.goal_id or not plan.check or not check_result or not check_result.get("check_id"):
        return
    for row in reversed((world.get("pc") or {}).get("_check_history") or []):
        if row.get("check_id") == check_result["check_id"]:
            row.update(goal_id=plan.goal_id, goal=plan.goal,
                       changed_conditions=plan.changed_conditions, outcome=description,
                       condition_snapshot=check_conditions(world),
                       discovery_ids=[ref.rsplit(":", 1)[0] for ref in plan.discovery_refs],
                       goal_achieved=bool(check_result.get("success")) if goal_achieved is None else goal_achieved)
            if turn_id:
                row["source_turn_id"] = turn_id
            if resolution_id:
                row["resolution_id"] = resolution_id
            if row["goal_achieved"]:
                context = ((world.get("pc") or {}).get("_push_contexts") or {}).get(check_result["check_id"])
                if isinstance(context, dict):
                    context["used"] = True
            return
