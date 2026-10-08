"""Exact, existing custodians; narrative interpretation stays with the keeper."""

from src.gameplay.investigators import investigator_entity, stable_investigator_id

from .errors import StructuredError


def validated_holder(state: dict, raw: object) -> dict:
    if not isinstance(raw, dict):
        raise StructuredError("unknown_target", "物品持有者格式不合法。")
    kind, identifier = raw.get("kind"), raw.get("id")
    if not isinstance(identifier, str) or not identifier.strip() or len(identifier) > 160:
        raise StructuredError("unknown_target", "物品持有者编号不合法。")
    if kind == "investigator":
        identifier = stable_investigator_id(state, identifier)
        exists = investigator_entity(state, identifier) is not None
    elif kind == "npc":
        exists = any(
            isinstance(npc, dict) and npc.get("id") == identifier for npc in state.get("npcs") or []
        )
    elif kind == "scene":
        current = state.get("current_scene") or {}
        exists = isinstance((state.get("scene_catalog") or {}).get(identifier), dict) or (
            isinstance(current, dict) and current.get("id") == identifier
        )
    else:
        exists = False
    if not exists:
        raise StructuredError("unknown_target", "物品持有者不存在，未执行转交。")
    # Existence is not presence: a human keeper may explicitly arrange remote
    # delivery or place an object elsewhere. Do not invent a same-scene rule.
    return {"kind": kind, "id": identifier}
