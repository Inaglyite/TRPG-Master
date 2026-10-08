"""Shared domain execution for direct commands and approved draft batches.

Identity, schema, CAS, replay and commit remain service/transport duties. Domain
side effects use the same terminal gate and combat inventory bridge in every path.
"""

import copy

from .clue_custody import clue_choices, sync_choices
from .combat_inventory import project_combat_inventory, settle_combat_inventory
from .combat_vitals import reconcile_stat_adjustment
from .domains import CommandContext, CommandResult, EventSpec
from .errors import StructuredError
from .keeper_progress import keeper_progress
from .rulings import ending_catalog, ruling_projection

POST_GAME_COMMANDS = frozenset(
    {"publish_message", "control_keeper", "record_memory", "keeper_roll"}
)
INVENTORY_COMBAT_COMMANDS = frozenset(
    {"combat_start", "combat_action", "combat_decide", "combat_roll"}
)


def combat_waiting(state: dict) -> bool:
    return bool(
        state.get("combat_pending_roll")
        or (state.get("combat_state") or {}).get("pending_decision")
    )


def allowed_during_combat_wait(kind: str, payload: dict) -> bool:
    return kind == "publish_message" or (
        kind == "resolve_intent" and payload.get("resolution") == "awaiting_player"
    )


def execute_domain(
    kind: str, handler, state: dict, payload: dict, ctx: CommandContext
) -> CommandResult:
    if state.get("game_over") and kind not in POST_GAME_COMMANDS:
        raise StructuredError("invalid_action", "游戏已结束，不能继续改变游戏状态。")
    before = None
    # Nested drafts execute each command here; the outer batch must not publish
    # a second catalogue update. A ruling already carries the same projection.
    catalogue_before = (
        ending_catalog(state) if kind not in {"resolve_draft", "record_ruling"} else None
    )
    progress_before = keeper_progress(state) if kind != "resolve_draft" else None
    custody_before = (
        clue_choices(state)
        if kind in {"grant_clue", "use_item", "transfer_item", "present_handout"}
        else None
    )
    if kind in INVENTORY_COMBAT_COMMANDS:
        project_combat_inventory(state)
        before = copy.deepcopy(state)
    outcome = handler(state, payload, ctx)
    if not isinstance(outcome, CommandResult):
        raise StructuredError("internal_error", f"命令 {kind} 返回了非法结果。")
    if before is not None:
        settle_combat_inventory(state, before, outcome)
    if custody_before is not None:
        sync_choices(state, custody_before, outcome)
    if kind == "adjust_stat" or (kind == "record_condition" and outcome.bump_revision):
        reconcile_stat_adjustment(state, outcome)
    if catalogue_before is not None and catalogue_before != ending_catalog(state):
        outcome.events.append(
            EventSpec("ending_catalog_updated", ruling_projection(state), {"kind": "keeper"})
        )
    if progress_before is not None and progress_before != keeper_progress(state):
        outcome.events.append(
            EventSpec("keeper_progress_updated", keeper_progress(state), {"kind": "keeper"})
        )
    return outcome
