"""Spatial command adapter: retain existing Principal/CAS/receipt/outbox boundaries."""

from __future__ import annotations

import copy

from sqlalchemy import select

from src.gameplay.spatial import SpatialError, enabled, require_clue_authorization
from src.gameplay.spatial_actions import execute_action
from src.storage.database import CheckRequest, EventOutbox
from src.structured.domains import CommandResult, EventSpec, _inventory_projection, cmd_grant_clue
from src.structured.errors import StructuredError


def structured_proof(ctx, check_id):
    def verify(world, pid, aid, actor, check):
        row = (
            ctx.session.execute(
                select(CheckRequest).where(
                    CheckRequest.world_id == ctx.world_id, CheckRequest.check_request_id == check_id
                )
            ).scalar_one_or_none()
            if ctx.session and check_id
            else None
        )
        live = (
            ctx.session.execute(
                select(EventOutbox.id)
                .where(
                    EventOutbox.world_id == ctx.world_id,
                    EventOutbox.event_type == "check_resolved",
                    EventOutbox.payload["check_request_id"].as_string() == check_id,
                )
                .limit(1)
            ).scalar_one_or_none()
            if row
            else None
        )
        conditions = row.conditions or {} if row else {}
        result = row.result or {} if row else {}
        if (
            row is None
            or live is None
            or row.status != "resolved"
            or row.investigator_id != actor
            or row.skill != check["skill"]
            or row.difficulty != check["difficulty"]
            or conditions.get("scene_id") != world["current_scene"]["id"]
            or conditions.get("target") != {"kind": "scene_object", "id": f"{pid}:{aid}"}
            or result.get("outcome") not in {"success", "failure"}
        ):
            raise SpatialError(
                "invalid_check_proof", "需要同调查员、房间、调查动作与难度的当前时间线检定。"
            )
        return {"check_id": check_id, "success": result["outcome"] == "success"}

    return verify


def interact_point(state, payload, ctx):
    if not enabled(state):
        raise StructuredError("invalid_action", "本模组未启用空间调查。")
    actor = str(payload.get("investigator_id") or "")
    if not actor:
        raise StructuredError("invalid_action", "缺少调查员。")
    staged = copy.deepcopy(state)
    events = []

    def grant(world, clue_id, recipient):
        outcome = cmd_grant_clue(
            world,
            {
                "clue_id": clue_id,
                "recipient_investigator_ids": [recipient],
                "basis": "作者调查点的已结算效果",
            },
            ctx,
        )
        events.extend(outcome.events)
        definition = (world.get("clue_catalog") or {}).get(clue_id) or {}
        asset = definition.get("asset") or {}
        if asset.get("id"):
            from src.structured.domains import cmd_present_handout

            events.extend(
                cmd_present_handout(
                    world, {"asset_id": asset["id"], "recipient_investigator_ids": [recipient]}, ctx
                ).events
            )

    try:
        receipt = execute_action(
            staged,
            str(payload.get("point_id") or ""),
            str(payload.get("action_id") or ""),
            actor,
            proof=structured_proof(ctx, str(payload.get("check_request_id") or "")),
            grant=grant,
        )
    except SpatialError as exc:
        raise StructuredError(exc.code, str(exc)) from exc
    state.clear()
    state.update(staged)
    audience = {"kind": "investigators", "investigator_ids": [actor]}
    inventory = {"investigator_id": actor, "items": _inventory_projection(state, actor)}
    events.extend(
        [
            EventSpec("inventory_changed", inventory, audience),
            EventSpec("inventory_changed", copy.deepcopy(inventory), {"kind": "keeper"}),
        ]
    )
    return CommandResult({"status": "success", **receipt}, events)


def guard_grant(state, clue_id):
    try:
        require_clue_authorization(state, clue_id, allow_known=True)
    except SpatialError as exc:
        raise StructuredError(exc.code, str(exc)) from exc


def guard_asset(state, asset_id):
    if not enabled(state):
        return
    from src.gameplay.spatial import protected_clues

    for clue_id in protected_clues(state):
        definition = state.get("clue_catalog", {}).get(clue_id) or {}
        if (definition.get("asset") or {}).get("id") == asset_id:
            guard_grant(state, clue_id)
