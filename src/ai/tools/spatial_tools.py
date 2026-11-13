"""Classic model-tool adapter for compiled spatial rules, not arbitrary state writes."""

from __future__ import annotations

import json
from types import SimpleNamespace

from src.gameplay.spatial import SpatialError, actor_id, enabled, require_clue_authorization
from src.gameplay.spatial_actions import execute_action, inspect_spatial
from src.gameplay.spatial_checks import legacy_proof


def register_spatial_tools(runtime, state_command):
    def inspect(_args, context):
        world = context.world_store.load()
        if not enabled(world):
            return json.dumps({"ok": False, "error": "spatial_not_enabled"})
        return json.dumps({"ok": True, **inspect_spatial(world)}, ensure_ascii=False)

    def interact(args, context):
        result = {}

        def mutate(world):
            nonlocal result
            if not enabled(world):
                raise SpatialError("spatial_not_enabled", "本模组未启用空间调查。")
            actor = actor_id(world)

            def grant(staged, clue_id, recipient):
                from src.structured.domains import CommandContext, cmd_grant_clue

                ctx = CommandContext(
                    world_id=context.world_id, principal=SimpleNamespace(kind="keeper")
                )
                cmd_grant_clue(
                    staged,
                    {
                        "clue_id": clue_id,
                        "recipient_investigator_ids": [recipient],
                        "basis": "作者调查点的已结算效果",
                    },
                    ctx,
                )
                # Classic local play uses public handouts; multiplayer retains
                # the established private recipient bridge.
                definition = staged["clue_catalog"][clue_id]
                for entries in staged["clues_found"].values():
                    for clue in entries:
                        if str(clue.get("catalog_id") or clue.get("id")) == clue_id:
                            clue["asset"] = definition.get("asset")
                            clue["visibility"] = (
                                "private"
                                if len(staged.get("investigators") or {}) > 1
                                else "public"
                            )

            result = execute_action(
                world,
                str(args.get("point_id") or ""),
                str(args.get("action_id") or ""),
                actor,
                proof=legacy_proof(str(args.get("check_id") or "")),
                grant=grant,
            )
            ids = {
                effect["clue_id"] for effect in result["effects"] if effect["kind"] == "grant_clue"
            }
            result["clues"] = [
                c
                for rows in world.get("clues_found", {}).values()
                for c in rows
                if str(c.get("catalog_id") or c.get("id")) in ids
            ]

        try:
            from src.gameplay.spatial_storage import authored_mutation

            with authored_mutation():
                context.world_store.update(mutate)
            return json.dumps({"ok": True, **result}, ensure_ascii=False)
        except SpatialError as exc:
            return json.dumps(
                {"ok": False, "error": exc.code, "detail": str(exc)}, ensure_ascii=False
            )

    runtime.add("spatial_inspect", inspect)
    runtime.add("spatial_interact", interact)
    register_clue_handler(runtime, state_command)


def register_clue_handler(runtime, state_command):
    def add(args, context):
        try:
            world = context.world_store.load()
            clue_id = str(args.get("clue_id") or "")
            if not clue_id and enabled(world):
                clue_id = next(
                    (
                        cid
                        for cid, clue in world.get("clue_catalog", {}).items()
                        if str(clue.get("text") or "") == str(args.get("text") or "")
                    ),
                    "",
                )
            require_clue_authorization(world, clue_id)
        except SpatialError as exc:
            return json.dumps(
                {"ok": False, "error": exc.code, "detail": str(exc)}, ensure_ascii=False
            )
        pc = world.get("pc", {}) if isinstance(world, dict) else {}
        visibility = "private" if args.get("visibility") == "private" else "public"
        return state_command(
            context,
            "cmd_add_clue",
            args.get("text", ""),
            args.get("category", "investigation"),
            asset_id=args.get("asset_id", "") or "",
            clue_id=args.get("clue_id", "") or "",
            visibility=visibility,
            owner_investigator_id=(pc.get("investigator_id") if visibility == "private" else None),
        )

    runtime.add("state_add_clue", add)


SPATIAL_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "spatial_inspect",
            "description": "读取当前叶场景的可见调查点、作者动作与已结算记录；其他房间不能在此调查。",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "spatial_interact",
            "description": "执行当前房间的作者调查动作。只选 point_id/action_id；如需检定，先对 point_id:action_id 实际检定，再提供 check_id。不得自报成功或填写效果。",
            "parameters": {
                "type": "object",
                "properties": {
                    "point_id": {"type": "string"},
                    "action_id": {"type": "string"},
                    "check_id": {"type": "string"},
                },
                "required": ["point_id", "action_id"],
                "additionalProperties": False,
            },
        },
    },
]


def filter_spatial_tools(request_tools, context):
    store = getattr(context, "world_store", None)
    state = store.load() if store is not None and callable(getattr(store, "load", None)) else {}
    state = state if isinstance(state, dict) else {}
    if enabled(state):
        return request_tools
    return [
        tool
        for tool in request_tools
        if (tool.get("function") or {}).get("name") not in {"spatial_inspect", "spatial_interact"}
    ]
