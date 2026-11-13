"""Legacy check receipts for authored points, recorded only by the real roller."""

from __future__ import annotations

from src.gameplay.spatial import SpatialError, actor_id, enabled, runtime


def record_legacy_check(world, *, skill, result, target_id, required_success_level):
    if not enabled(world) or ":" not in target_id:
        return
    pid, aid = target_id.split(":", 1)
    point = world["spatial_definition"].get("points", {}).get(pid)
    action = point.get("actions", {}).get(aid) if point else None
    if not action or not action.get("check"):
        return
    checks = runtime(world)["checks"]
    checks[result["check_id"]] = {
        "point_id": pid,
        "action_id": aid,
        "investigator_id": actor_id(world),
        "scene_id": (world.get("current_scene") or {}).get("id"),
        "skill": skill,
        "difficulty": required_success_level,
        "success": result["success"] is True,
    }
    while len(checks) > 64:
        checks.pop(next(iter(checks)))


def legacy_proof(check_id):
    def verify(world, pid, aid, actor, check):
        record = runtime(world)["checks"].get(check_id)
        if (
            not record
            or any(
                record.get(k) != v
                for k, v in {
                    "point_id": pid,
                    "action_id": aid,
                    "investigator_id": actor,
                    "scene_id": world["current_scene"]["id"],
                    "skill": check["skill"],
                    "difficulty": check["difficulty"],
                }.items()
            )
            or type(record.get("success")) is not bool
        ):
            raise SpatialError("invalid_check_proof", "需要同调查员、房间、目标与难度的实际检定。")
        return {"check_id": check_id, "success": record["success"]}

    return verify


def validate_point_check(world, target_id, actor, skill, difficulty):
    """Do not roll a future room or stockpile checks before requirements hold."""
    if not enabled(world) or ":" not in target_id:
        return
    pid, aid = target_id.split(":", 1)
    point = world["spatial_definition"].get("points", {}).get(pid)
    if point is None:
        return  # Other legacy target namespaces retain their own semantics.
    from src.gameplay.spatial import condition_met

    actor = actor_id(world, actor)
    if point["scene_id"] != world["current_scene"]["id"] or not condition_met(
        world, point["visible_when"], actor
    ):
        raise SpatialError("point_not_present", "该检定的调查点不在当前已知房间。")
    action = point["actions"].get(aid)
    check = action.get("check") if action else None
    if check != {"skill": skill, "difficulty": difficulty}:
        raise SpatialError("invalid_point_check", "检定技能/难度必须与该调查动作一致。")
    if not action.get("repeat") and target_id in runtime(world)["actions"]:
        raise SpatialError("action_completed", "该调查动作已经结算。")
    if not condition_met(world, action["requires"], actor):
        raise SpatialError("action_requirements_unmet", "调查条件尚未满足，不能预先检定。")
