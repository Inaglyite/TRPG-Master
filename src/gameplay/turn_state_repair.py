"""Model-requested repair of a scene projection, backed by settled turn facts.

No model supplied values are written. The checkpoint is frozen after action,
travel and encounter resolution, before the narrator can request tools.
"""

from __future__ import annotations

import copy
import hashlib
import json

FIELDS = ("id", "name", "exits", "npcs_present")
TOOL_NAMES = frozenset({"inspect_turn_state", "repair_turn_state"})


def _projection(scene: dict) -> dict:
    return {key: copy.deepcopy(scene.get(key)) for key in FIELDS}


def freeze_turn_state(engine) -> None:
    world = engine.context.world_store.load()
    scene = world.get("current_scene") or {}
    authored = (world.get("scene_catalog") or {}).get(scene.get("id")) or {}
    expected = _projection(scene)
    # Metadata comes from the module; presence remains the executed encounter
    # result. In particular, never reroll luck or resurrect absent NPCs.
    for field in ("name", "exits"):
        if field in authored:
            expected[field] = copy.deepcopy(authored[field])
    action = getattr(engine, "_action_resolution", None)
    phase = getattr(action, "phase", None)
    ledger = getattr(engine, "_turn_mutations", None)
    engine._turn_state_checkpoint = {
        "turn_id": getattr(engine, "active_turn_id", None),
        "expected": expected,
        "phase": str(getattr(phase, "value", phase) or "unknown"),
        "proof": [entry for entry in (ledger.snapshot() if ledger else [])
                  if entry.get("source") == "domain" and entry.get("name") == "scene_transition"],
    }


def _view(engine, world: dict) -> dict:
    checkpoint = getattr(engine, "_turn_state_checkpoint", None)
    if not checkpoint or checkpoint["turn_id"] != getattr(engine, "active_turn_id", None):
        return {"ok": False, "error": "no_settled_turn_checkpoint"}
    current = _projection(world.get("current_scene") or {})
    expected = checkpoint["expected"]
    pending = world.get("pending_departure")
    token_input = [world.get("revision"), current, checkpoint, pending]
    token = hashlib.sha256(json.dumps(token_input, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    present = set(expected.get("npcs_present") or [])
    return {
        "ok": True, "snapshot_token": token,
        "revision": world.get("revision"), "current_scene": current,
        "settled_scene": copy.deepcopy(expected), "phase": checkpoint["phase"],
        "pending_departure": copy.deepcopy(pending),
        "executed_transitions": copy.deepcopy(checkpoint["proof"]),
        "speakers": [{"id": npc["id"], "name": npc.get("name")}
                     for npc in world.get("npcs", []) if npc.get("id") in present],
        "conflicts": [field for field in FIELDS if current[field] != expected[field]],
        "instruction": "有冲突时可用本次 snapshot_token 请求 repair_turn_state；无冲突时修正叙事。不能以叙事为依据移动、增加人物或改写检定。",
    }


def execute_turn_state_tool(engine, name: str, args: dict) -> str:
    store = engine.context.world_store
    world = store.load()
    result = _view(engine, world)
    if name == "repair_turn_state" and result["ok"]:
        if args.get("snapshot_token") != result["snapshot_token"]:
            result = {"ok": False, "error": "stale_state_snapshot", "instruction": "重新调用 inspect_turn_state 后再请求修正。"}
        elif not str(args.get("reason") or "").strip():
            result = {"ok": False, "error": "repair_reason_required"}
        elif not result["conflicts"]:
            result = {"ok": True, "status": "unchanged", "changed_fields": []}
        else:
            expected = result["settled_scene"]

            def restore(state):
                # Recheck under the store's lock, including mutations that did
                # not advance revision inside a buffered turn transaction.
                if _view(engine, state)["snapshot_token"] != args["snapshot_token"]:
                    raise ValueError("stale_state_snapshot")
                scene = state.setdefault("current_scene", {})
                for field in result["conflicts"]:
                    if expected[field] is None:
                        scene.pop(field, None)
                    else:
                        scene[field] = copy.deepcopy(expected[field])

            store.update(restore, expected_revision=world.get("revision"))
            changed = result["conflicts"]
            ledger = getattr(engine, "_turn_mutations", None)
            if ledger:
                ledger.record_domain("scene_projection_repair", {"changed_fields": changed, "snapshot_token": args["snapshot_token"], "reason": args["reason"]})
            result = {"ok": True, "status": "repaired", "changed_fields": changed,
                      "current_scene": _projection(store.load().get("current_scene") or {})}
    return json.dumps(result, ensure_ascii=False)


def register_engine_bound_tools(runtime) -> None:
    def engine_required(_args, _context):
        return json.dumps({"ok": False, "error": "active_turn_engine_required"})
    for name in TOOL_NAMES:
        runtime.add(name, engine_required)
