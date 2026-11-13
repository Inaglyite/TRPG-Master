"""Authored investigation actions with staged effects and one-use check proofs."""

from __future__ import annotations

import copy

from src.gameplay.investigators import investigator_entity
from src.gameplay.spatial import SpatialError, actor_id, condition_met, runtime
from src.gameplay.spatial_effects import apply_effects


def public_points(world: dict, actor: str | None = None) -> list[dict]:
    actor = actor_id(world, actor)
    scene = (world.get("current_scene") or {}).get("id")
    return [
        {"kind": "scene_object", "id": pid, "name": point["name"]}
        for pid, point in world.get("spatial_definition", {}).get("points", {}).items()
        if point["scene_id"] == scene and condition_met(world, point["visible_when"], actor)
    ]


def inspect_spatial(world: dict, actor: str | None = None) -> dict:
    actor = actor_id(world, actor)
    from src.gameplay.spatial import scene_label, visible_destinations

    scene = (world.get("current_scene") or {}).get("id")
    points = public_points(world, actor)
    from src.gameplay.spatial_inventory import bind_definitions

    bind_definitions(world)
    items = [
        copy.deepcopy(item)
        for item in world["item_registry"]["items"].values()
        if item.get("holder") == {"kind": "investigator", "id": actor}
        and item.get("quantity", 0) > 0
    ]
    return {
        "scene": {"id": scene, "name": scene_label(world, scene)},
        "destinations": visible_destinations(world, actor),
        "points": [
            {
                **point,
                "actions": copy.deepcopy(
                    world["spatial_definition"]["points"][point["id"]]["actions"]
                ),
            }
            for point in points
        ],
        "completed": copy.deepcopy(runtime(world)["actions"]),
        "items": items,
    }


def execute_action(
    world: dict, point_id: str, action_id: str, actor: str | None = None, *, proof=None, grant=None
) -> dict:
    staged = copy.deepcopy(world)
    actor = actor_id(staged, actor)
    sheet = investigator_entity(staged, actor)
    if (
        not isinstance(sheet, dict)
        or sheet.get("hp", 0) <= 0
        or set(sheet.get("conditions") or []) & {"dead", "dying", "unconscious"}
    ):
        raise SpatialError("actor_unavailable", "该调查员当前无法调查。")
    point = staged.get("spatial_definition", {}).get("points", {}).get(point_id)
    if not isinstance(point, dict) or point["scene_id"] != (staged.get("current_scene") or {}).get(
        "id"
    ):
        raise SpatialError("point_not_present", "该调查点不在当前房间。")
    if not condition_met(staged, point["visible_when"], actor):
        raise SpatialError("point_not_present", "当前没有这个已知调查点。")
    action = point["actions"].get(action_id)
    if action is None:
        raise SpatialError("unknown_point_action", "该调查点没有此动作。")
    key = f"{point_id}:{action_id}"
    progress = runtime(staged)
    if not action.get("repeat") and key in progress["actions"]:
        raise SpatialError("action_completed", "这个调查动作已经结算，请查看已有结果。")
    bindings = {}
    if not condition_met(staged, action["requires"], actor, bindings):
        raise SpatialError("action_requirements_unmet", "调查条件尚未满足。")
    passed, check_id = True, ""
    if action.get("check"):
        if proof is None:
            raise SpatialError("check_required", "该动作需要对应的真实检定。")
        checked = proof(staged, point_id, action_id, actor, action["check"])
        check_id, passed = checked["check_id"], checked["success"]
        if check_id in progress["used_checks"] or any(
            record.get("check_id") == check_id for record in progress["actions"].values()
        ):
            raise SpatialError("check_already_used", "该检定已经结算过调查效果。")
    effects = action["success"] if passed else action["failure"]
    receipts = apply_effects(staged, effects, actor, bindings, grant)
    # Runtime may have been augmented by grants, but remains one state object.
    progress = runtime(staged)
    receipt = {
        "point_id": point_id,
        "action_id": action_id,
        "investigator_id": actor,
        "scene_id": point["scene_id"],
        "success": passed,
        "check_id": check_id,
        "effects": receipts,
    }
    if check_id:
        progress["used_checks"].append(check_id)
    progress["actions"][key] = receipt
    world.clear()
    world.update(staged)
    return copy.deepcopy(receipt)
