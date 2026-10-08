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


def holdings_projection(state: dict) -> dict:
    """Private catalogue from existing objects/ledger; never initialize or grant."""
    holders = []
    for identifier, sheet in (state.get("investigators") or {}).items():
        if isinstance(sheet, dict):
            holders.append(
                {
                    "kind": "investigator",
                    "id": identifier,
                    "name": str(sheet.get("name") or identifier),
                }
            )
    active = stable_investigator_id(state, "pc")
    pc = state.get("pc")
    if isinstance(pc, dict) and not any(
        h["kind"] == "investigator" and h["id"] == active for h in holders
    ):
        holders.append(
            {"kind": "investigator", "id": active, "name": str(pc.get("name") or active)}
        )
    for npc in state.get("npcs") or []:
        if isinstance(npc, dict) and isinstance(npc.get("id"), str):
            holders.append(
                {"kind": "npc", "id": npc["id"], "name": str(npc.get("name") or npc["id"])}
            )
    scenes = dict(state.get("scene_catalog") or {})
    current = state.get("current_scene") or {}
    if isinstance(current, dict) and current.get("id"):
        scenes.setdefault(current["id"], current)
    for identifier, scene in scenes.items():
        if isinstance(scene, dict):
            holders.append(
                {"kind": "scene", "id": identifier, "name": str(scene.get("name") or identifier)}
            )
    holders = sorted(
        {(h["kind"], h["id"]): h for h in holders}.values(), key=lambda h: (h["kind"], h["id"])
    )
    known = {(h["kind"], h["id"]) for h in holders}
    items = []
    for item in ((state.get("item_registry") or {}).get("items") or {}).values():
        holder = item.get("holder") or {}
        quantity = item.get("quantity")
        if (
            (holder.get("kind"), holder.get("id")) in known
            and type(quantity) is int
            and quantity > 0
        ):
            items.append(
                {
                    "id": item["item_id"],
                    "label": str(item["label"]),
                    "quantity": quantity,
                    "holder": {"kind": holder["kind"], "id": holder["id"]},
                }
            )
    return {"holders": holders, "items": sorted(items, key=lambda i: i["id"])}
