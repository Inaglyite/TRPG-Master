"""Bind combat execution to an explicit request, never infer prose completion."""

from sqlalchemy import select

from src.gameplay import combat as rules
from src.gameplay.investigators import investigator_entity
from src.storage.database import PlayerRequest

from .combat_weapons import selected_weapon
from .errors import StructuredError
from .validation import validate_action


def fact_check_combat_request(state: dict, action: dict, investigator_id: str) -> None:
    """Check only committed facts, without deciding success or rolling dice."""
    validate_action(action)
    combat = state.get("combat_state") or {}
    if not combat.get("active") or combat.get("encounter_id") != action["encounter_id"]:
        raise StructuredError("stale_target", "战斗已结束或遭遇已变化，请刷新后重新申报。")
    if combat.get("pending_decision") or state.get("combat_pending_roll"):
        raise StructuredError("invalid_action", "请先处理当前决定或掷骰，不要重复申报。")
    if combat.get("current_actor") != investigator_id:
        raise StructuredError("not_actor", "还没有轮到这名调查员行动。")
    participants = {p["id"]: p for p in combat.get("participants", [])}
    actor = participants.get(investigator_id)
    # Both the roster and encounter must permit action. A stale encounter sheet
    # must never resurrect an investigator downed by a committed stat command.
    sheet = investigator_entity(state, investigator_id) or {}
    for entry in (actor, sheet, (state.get("investigators") or {}).get(investigator_id, sheet)):
        if (
            not entry
            or entry.get("hp", 0) <= 0
            or set(entry.get("conditions") or [])
            & {
                "dead",
                "dying",
                "unconscious",
            }
        ):
            raise StructuredError("invalid_action", "这名调查员当前无法行动。")
    if actor.get("kind") != "pc":
        raise StructuredError("not_authorized", "玩家只能申报自己的调查员行动。")
    target_id = action["target_id"]
    if action.get("weapon_item_id"):
        selected_weapon(state, investigator_id, action["weapon_item_id"])
    if target_id is not None:
        target = participants.get(target_id)
        if not target or target_id == investigator_id:
            raise StructuredError("unknown_target", "请选择本场遭遇中的另一名参战者。")
        try:
            target_sheet = rules._entity_for(state, target_id)[0]
        except rules.CombatError as exc:
            raise StructuredError("stale_target", str(exc)) from exc
        if any(
            t.get("hp", 0) <= 0 or "dead" in (t.get("conditions") or [])
            for t in (target, target_sheet)
        ):
            raise StructuredError("stale_target", "目标已无法参与本次战斗动作。")


def validate_linked_combat_action(state: dict, payload: dict, ctx) -> None:
    """A linked typed declaration cannot silently change target/action/encounter."""
    source = linked_request_id(ctx, payload["actor_id"])
    if not source:
        return
    row = ctx.session.scalar(
        select(PlayerRequest).where(
            PlayerRequest.world_id == ctx.world_id,
            PlayerRequest.request_id == source,
        )
    )
    action = (row.payload or {}).get("action") or {}
    if action.get("kind") != "combat":
        return
    if (
        action.get("encounter_id") != (state.get("combat_state") or {}).get("encounter_id")
        or action.get("action_type") != payload.get("action_type")
        or action.get("target_id") != payload.get("target_id")
        or (
            action.get("weapon_item_id")
            and action["weapon_item_id"] != payload.get("weapon_item_id")
        )
    ):
        raise StructuredError(
            "stale_target", "批准动作与原战斗申报不一致，请明确处理原请求后重新准备。"
        )


def linked_request_id(ctx, investigator_id: str, inherited: str = "") -> str:
    if ctx.session is None:
        return ""
    candidate = inherited
    if not candidate and ctx.principal.kind in {"keeper", "agent"}:
        candidate = ctx.cause_id
    if not candidate:
        return ""
    row = ctx.session.scalar(
        select(PlayerRequest).where(
            PlayerRequest.world_id == ctx.world_id,
            PlayerRequest.request_id == candidate,
            PlayerRequest.request_type == "action_request",
            PlayerRequest.investigator_id == investigator_id,
            PlayerRequest.status.in_(["queued", "processing", "awaiting_player", "paused"]),
        )
    )
    return row.request_id if row else ""
