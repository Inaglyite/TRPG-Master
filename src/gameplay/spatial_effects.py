"""Finite, staged effects. Callers own transaction/replay and knowledge delivery."""

from __future__ import annotations

import secrets

from src.gameplay.spatial import SpatialError, authorize_clue_effect, reveal_scene, runtime
from src.gameplay.spatial_inventory import held_items, project_inventory


def apply_effects(
    world: dict, effects: list[dict], actor: str, bindings: dict | None = None, grant=None
) -> list[dict]:
    bindings = bindings if bindings is not None else {}
    receipts = []
    from src.gameplay.item_registry import ensure_item_registry

    ensure_item_registry(world)
    definitions = world["spatial_definition"]
    for effect in effects:
        kind = effect["kind"]
        if kind == "grant_clue":
            if grant is None:
                raise SpatialError("missing_clue_adapter", "缺少线索结算适配器。")
            with authorize_clue_effect(world, effect["clue_id"]):
                grant(world, effect["clue_id"], actor)
        elif kind == "give_item":
            definition = definitions["items"][effect["item_id"]]
            item_id = "item_" + secrets.token_hex(6)
            items = world.setdefault("item_registry", {"schema_version": 1, "items": {}})["items"]
            items[item_id] = {
                "item_id": item_id,
                "definition_id": effect["item_id"],
                "label": definition["label"],
                "quantity": effect.get("quantity", 1),
                "holder": {"kind": "investigator", "id": actor},
                "states": {k: s["initial"] for k, s in definition["states"].items()},
                "stack_key": f"investigator:{actor}/{item_id}",
                "legacy_label": definition["label"],
                "operations": [],
            }
            project_inventory(world, actor)
        elif kind == "adjust_item_state":
            definition, key, delta = effect["item_id"], effect["state"], effect["delta"]
            spec = definitions["items"][definition]["states"][key]
            candidates = [
                item
                for item in held_items(world, definition, actor)
                if (definition not in bindings or item["item_id"] == bindings[definition])
                and spec["minimum"] <= item["states"][key] + delta <= spec["maximum"]
            ]
            if not candidates:
                raise SpatialError(
                    "item_state_unavailable", "没有状态满足要求的自持物品，不能结算代价。"
                )
            item = candidates[0]
            bindings[definition] = item["item_id"]
            item["states"][key] += delta
        elif kind == "set_flag":
            world["flags"][effect["flag_id"]] = effect["value"]
        elif kind == "reveal_scene":
            reveal_scene(world, effect["scene_id"])
        elif kind == "unlock_connection":
            runtime(world)["connections"][effect["connection_id"]] = {
                "unlocked": True,
                "by_effect": True,
            }
        elif kind == "advance_clock":
            cid = effect["clock_id"]
            value = world["case_clocks"][cid]
            maximum = (world.get("case_clock_definitions", {}).get(cid) or {}).get("max")
            if (
                type(value) is not int
                or type(maximum) is not int
                or value + effect["amount"] > maximum
            ):
                raise SpatialError("clock_cost_unavailable", "时钟代价不能实际支付，效果未结算。")
            world["case_clocks"][cid] = value + effect["amount"]
        else:
            raise SpatialError("unknown_spatial_effect", "未知空间效果。")
        receipts.append(dict(effect))
    return receipts
