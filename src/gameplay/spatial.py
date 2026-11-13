"""Opt-in spatial domain. Position remains current_scene; no model/DB dependencies."""

from __future__ import annotations

import copy
from contextlib import contextmanager
from contextvars import ContextVar

from src.gameplay.investigators import stable_investigator_id

_AUTHORIZED_CLUES = ContextVar("spatial_authorized_clues", default=frozenset())


class SpatialError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def enabled(world: dict) -> bool:
    value = world.get("spatial_definition")
    if value is None:
        return False
    if not isinstance(value, dict) or value.get("version") != 1:
        raise SpatialError("invalid_spatial_definition", "空间定义版本或格式无效。")
    return True


def actor_id(world: dict, actor: str | None = None) -> str:
    return stable_investigator_id(world, actor or "pc")


def runtime(world: dict) -> dict:
    value = world.setdefault(
        "spatial_state",
        {
            "version": 1,
            "revealed_scenes": [],
            "connections": {},
            "actions": {},
            "checks": {},
            "used_checks": [],
        },
    )
    if not isinstance(value, dict) or value.get("version") != 1:
        raise SpatialError("invalid_spatial_state", "空间进度格式无效。")
    for field, kind in (
        ("revealed_scenes", list),
        ("connections", dict),
        ("actions", dict),
        ("checks", dict),
    ):
        if not isinstance(value.get(field), kind):
            raise SpatialError("invalid_spatial_state", "空间进度记录不完整。")
    value.setdefault("used_checks", [])
    if not isinstance(value["used_checks"], list):
        raise SpatialError("invalid_spatial_state", "检定消费记录无效。")
    return value


def ancestors(world: dict, scene_id: str) -> list[str]:
    nodes = world.get("spatial_definition", {}).get("nodes", {})
    chain = []
    while scene_id:
        if scene_id in chain or len(chain) >= 8 or scene_id not in world.get("scene_catalog", {}):
            raise SpatialError("invalid_scene_tree", "场景父链无效。")
        chain.append(scene_id)
        scene_id = (nodes.get(scene_id) or {}).get("parent_scene_id")
    return list(reversed(chain))


def resolve_leaf(world: dict, scene_id: str) -> str:
    node = (world.get("spatial_definition") or {}).get("nodes", {}).get(scene_id, {})
    if node.get("kind") == "container":
        scene_id = str(node.get("entry_scene_id") or "")
    if scene_id not in world.get("scene_catalog", {}):
        raise SpatialError("unknown_destination", "目的地不可用。")
    return scene_id


def reveal_scene(world: dict, scene_id: str) -> None:
    known = runtime(world)["revealed_scenes"]
    for sid in ancestors(world, scene_id):
        if sid not in known:
            known.append(sid)


def scene_visible(world: dict, scene_id: str) -> bool:
    known = runtime(world)["revealed_scenes"]
    nodes = world["spatial_definition"].get("nodes", {})
    return all(
        not nodes.get(sid, {}).get("hidden") or sid in known for sid in ancestors(world, scene_id)
    )


def _clue_known(world: dict, clue_id: str, actor: str) -> bool:
    entry = (world.get("clue_registry") or {}).get("clues", {}).get(clue_id)
    if isinstance(entry, dict):
        return actor in entry.get("granted_to", []) or entry.get("visibility") == "public"
    for entries in (world.get("clues_found") or {}).values():
        for clue in entries if isinstance(entries, list) else []:
            if (
                not isinstance(clue, dict)
                or str(clue.get("catalog_id") or clue.get("id")) != clue_id
            ):
                continue
            return clue.get("visibility", "public") == "public" or actor in clue.get(
                "granted_to", []
            )
    return False


def condition_met(
    world: dict, condition: dict, actor: str, bindings: dict | None = None, depth=0
) -> bool:
    if depth > 8 or not isinstance(condition, dict):
        raise SpatialError("invalid_condition", "条件树格式无效。")
    bindings = bindings if bindings is not None else {}
    op = condition.get("op")
    if op == "always":
        return condition.get("value", True) is True
    if op in {"all", "any"}:
        children = condition.get("conditions")
        if not isinstance(children, list) or not children:
            raise SpatialError("invalid_condition", "条件组合不能为空。")
        selected = dict(bindings)
        for child in children:
            candidate = dict(selected if op == "all" else bindings)
            passed = condition_met(world, child, actor, candidate, depth + 1)
            if op == "any" and passed:
                bindings.update(candidate)
                return True
            if op == "all" and not passed:
                return False
            selected = candidate
        if op == "all":
            bindings.update(selected)
        return op == "all"
    if op == "not":
        return not condition_met(world, condition["condition"], actor, dict(bindings), depth + 1)
    if op == "flag":
        flags = world.get("flags") or {}
        value = flags.get(condition["flag_id"])
        return (
            condition["flag_id"] in flags
            and type(value) is type(condition["value"])
            and value == condition["value"]
        )
    if op == "clock":
        value = (world.get("case_clocks") or {}).get(condition["clock_id"])
        return type(value) is int and value >= condition["minimum"]
    if op == "clue":
        return _clue_known(world, condition["clue_id"], actor)
    if op == "completed":
        return f"{condition['point_id']}:{condition['action_id']}" in runtime(world)["actions"]
    if op == "item":
        from src.gameplay.spatial_inventory import held_items

        definition = condition["item_id"]
        for item in held_items(world, definition, actor):
            if definition in bindings and item["item_id"] != bindings[definition]:
                continue
            if type(item.get("quantity")) is not int or item["quantity"] < condition.get(
                "quantity", 1
            ):
                continue
            states = item.get("states") or {}
            if all(
                type(states.get(k)) is int and states[k] >= value
                for k, value in condition.get("minimum_states", {}).items()
            ):
                bindings[definition] = item["item_id"]
                return True
        return False
    raise SpatialError("invalid_condition", "未知的空间条件。")


def connections_from(world: dict, origin: str) -> list[tuple[str, dict, str]]:
    explicit, covered = [], set()
    for cid, edge in world["spatial_definition"].get("connections", {}).items():
        source, target = (
            resolve_leaf(world, edge["source_scene_id"]),
            resolve_leaf(world, edge["target_scene_id"]),
        )
        destination = (
            target
            if source == origin
            else source
            if edge.get("bidirectional") and target == origin
            else None
        )
        if destination:
            covered.add(resolve_leaf(world, destination))
            explicit.append((cid, edge, resolve_leaf(world, destination)))
    for destination in (world.get("scene_catalog", {}).get(origin) or {}).get("exits", []):
        leaf = resolve_leaf(world, destination)
        if leaf not in covered and leaf != origin:
            covered.add(leaf)
            explicit.append(
                (
                    f"exit:{origin}:{destination}",
                    {"visible_when": {"op": "always"}, "requires": {"op": "always"}, "costs": []},
                    leaf,
                )
            )
    return explicit


def visible_destinations(world: dict, actor: str | None = None) -> dict[str, str]:
    actor = actor_id(world, actor)
    origin = str((world.get("current_scene") or {}).get("id") or "")
    found = {}
    for _cid, edge, target in connections_from(world, origin):
        if scene_visible(world, target) and condition_met(world, edge["visible_when"], actor):
            found[target] = scene_label(world, target)
    return found


def scene_label(world: dict, scene_id: str) -> str:
    labels = []
    for sid in ancestors(world, scene_id):
        scene = world["scene_catalog"][sid]
        label = str(scene.get("public_name") or scene.get("name") or "").strip()
        labels.append(label if label and label != sid else "未命名地点")
    return " / ".join(labels)


def authorize_transition(world: dict, destination: str, actor: str | None = None):
    actor = actor_id(world, actor)
    from src.gameplay.investigators import investigator_entity

    sheet = investigator_entity(world, actor)
    if (
        not isinstance(sheet, dict)
        or sheet.get("hp", 0) <= 0
        or set(sheet.get("conditions") or []) & {"dead", "dying", "unconscious"}
    ):
        raise SpatialError("actor_unavailable", "该调查员当前无法通行。")
    leaf = resolve_leaf(world, destination)
    origin = str((world.get("current_scene") or {}).get("id") or "")
    if leaf == origin:
        return leaf, None, {}, {}
    matches = [
        (cid, edge) for cid, edge, target in connections_from(world, origin) if target == leaf
    ]
    if len(matches) != 1 or leaf not in visible_destinations(world, actor):
        raise SpatialError("unknown_destination", "当前没有通往该地点的已知通路。")
    cid, edge = matches[0]
    status = runtime(world)["connections"].get(cid, {})
    opened = status.get("unlocked") is True and (
        edge.get("remember_unlock") or status.get("by_effect")
    )
    bindings = {}
    if not opened:
        if not condition_met(world, edge["requires"], actor, bindings):
            raise SpatialError("passage_locked", "通行条件尚未满足。")
        from src.gameplay.spatial_effects import apply_effects

        # Validate costs on a copy before callers alter presence or emit arrival.
        apply_effects(copy.deepcopy(world), edge.get("costs", []), actor, dict(bindings))
    return leaf, cid, edge, bindings


def settle_transition(before: dict, after: dict, destination: str, actor: str | None = None) -> str:
    actor = actor_id(before, actor)
    candidate = copy.deepcopy(after)
    candidate["current_scene"] = copy.deepcopy(before["current_scene"])
    leaf, cid, edge, bindings = authorize_transition(candidate, destination, actor)
    if cid is not None:
        status = runtime(candidate)["connections"].get(cid, {})
        opened = status.get("unlocked") is True and (
            edge.get("remember_unlock") or status.get("by_effect")
        )
        if not opened:
            from src.gameplay.spatial_effects import apply_effects

            apply_effects(candidate, edge.get("costs", []), actor, bindings)
        if edge.get("remember_unlock"):
            runtime(candidate)["connections"].setdefault(cid, {})["unlocked"] = True
    target = copy.deepcopy(candidate["scene_catalog"][leaf])
    # An encounter resolver may already have computed authoritative leaf presence.
    if (after.get("current_scene") or {}).get("id") == leaf and "npcs_present" in after[
        "current_scene"
    ]:
        target["npcs_present"] = list(after["current_scene"].get("npcs_present", []))
    target["id"] = leaf
    candidate["current_scene"] = target
    reveal_scene(candidate, leaf)
    after.clear()
    after.update(candidate)
    return leaf


def protected_clues(world: dict) -> set[str]:
    return {
        effect["clue_id"]
        for point in world.get("spatial_definition", {}).get("points", {}).values()
        for action in point["actions"].values()
        for effect in [*action["success"], *action["failure"]]
        if effect["kind"] == "grant_clue"
    }


def require_clue_authorization(world: dict, clue_id: str, *, allow_known=False) -> None:
    if not enabled(world) or clue_id not in protected_clues(world):
        return
    if (id(world), clue_id) in _AUTHORIZED_CLUES.get():
        return
    if allow_known and any(
        str(c.get("catalog_id") or c.get("id")) == clue_id
        for rows in (world.get("clues_found") or {}).values()
        for c in rows
        if isinstance(c, dict)
    ):
        return
    raise SpatialError("point_action_required", "该线索须通过对应调查点的实际动作取得。")


@contextmanager
def authorize_clue_effect(world: dict, clue_id: str):
    token = _AUTHORIZED_CLUES.set(_AUTHORIZED_CLUES.get() | {(id(world), clue_id)})
    try:
        yield
    finally:
        _AUTHORIZED_CLUES.reset(token)
