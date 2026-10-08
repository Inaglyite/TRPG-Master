"""Real save and branch paths invalidate old combat consent, not committed HP."""

import copy

import pytest
from test_structured_combat_transactions import battle as battle

from src.app.runtime import RuntimeContext
from src.storage.database import WorldState, session_scope
from src.storage.persistence import save_game
from src.structured.branch import create_structured_branch, restore_structured_save
from src.structured.errors import StructuredError


def context(tmp_path):
    return RuntimeContext.create(
        "sp-world",
        "test-module",
        project_root=tmp_path,
        runtime_root=tmp_path,
    )


def test_restore_keeps_encounter_but_invalidates_saved_roll(battle, tmp_path):
    _, _, alice, execute, snapshot, draws = battle
    runtime = context(tmp_path)
    saved = snapshot()[0]
    old_roll = {"roll_id": saved["combat_pending_roll"]["roll_id"], "response": "roll"}
    save_game([], "slot_001", context=runtime)
    execute("combat_roll", {**old_roll, "response": "cancel"}, alice)
    restore_structured_save(runtime, "slot_001")
    restored = snapshot()
    assert restored[0]["combat_state"]["active"]
    assert restored[0]["combat_state"]["phase"] == "awaiting_action"
    assert not restored[0].get("combat_pending_roll")
    assert restored[0]["pc"]["hp"] == saved["pc"]["hp"]
    assert restored[0]["pc"]["inventory"] == saved["pc"]["inventory"]
    with pytest.raises(StructuredError, match="已失效"):
        execute("combat_roll", old_roll, alice, "stale-after-restore")
    assert snapshot() == restored
    assert not draws


def test_branch_keeps_source_wait_but_never_copies_roll_consent(battle, tmp_path):
    url, service, alice, _, snapshot, draws = battle
    before = snapshot()
    source = context(tmp_path)
    created = create_structured_branch(source, project_root=tmp_path, runtime_root=tmp_path)
    world_id = created.context.world_id
    with session_scope(url) as session:
        branched = copy.deepcopy(session.get(WorldState, world_id).state)
    assert branched["combat_state"]["encounter_id"] == before[0]["combat_state"]["encounter_id"]
    assert branched["combat_state"]["phase"] == "awaiting_action"
    assert not branched.get("combat_pending_roll")
    assert snapshot() == before  # parent and its waiting button are untouched
    with pytest.raises(StructuredError, match="已失效"):
        service.execute_command(
            world_id=world_id,
            principal=alice,
            kind="combat_roll",
            command_id="old-parent-roll",
            expected_revision=None,
            payload={"roll_id": before[0]["combat_pending_roll"]["roll_id"], "response": "roll"},
        )
    assert not draws
