"""Stable weapon choices survive probes, consent and exact ammo settlement."""

import copy
from unittest.mock import patch

import pytest
from sqlalchemy import select
from test_structured_combat_transactions import battle as battle

from src.storage.database import PlayerRequest, WorldState, session_scope
from src.structured.errors import StructuredError
from src.structured.principal import Principal


def weapons(battle, label="手枪（5发）", quantity=1):
    url, _, alice, execute, snapshot, _ = battle
    execute(
        "combat_roll",
        {"roll_id": snapshot()[0]["combat_pending_roll"]["roll_id"], "response": "cancel"},
        alice,
    )
    with session_scope(url) as session:
        row = session.get(WorldState, "sp-world")
        state = copy.deepcopy(row.state)
        original = next(
            i
            for i in state["item_registry"]["items"].values()
            if i["holder"] == {"kind": "investigator", "id": "inv-alice"}
        )
        second = copy.deepcopy(original)
        second.update(item_id="weapon-second", label=label, legacy_label=label, quantity=quantity)
        state["item_registry"]["items"][second["item_id"]] = second
        row.state = state
    return original["item_id"], second["item_id"]


def action(item_id, **over):
    return {
        "actor_id": "inv-alice",
        "target_id": "guard",
        "action_type": "firearm",
        "weapon_item_id": item_id,
        "damage_spec": "1d3",
        **over,
    }


def test_same_base_different_ammo_uses_exact_selected_id_without_preparation_spend(battle):
    _, _, alice, execute, snapshot, draws = battle
    original, chosen = weapons(battle)
    before = snapshot()[0]
    execute("combat_action", action(chosen))
    prepared = snapshot()[0]
    assert not draws and prepared["item_registry"] == before["item_registry"]
    assert prepared["combat_pending_roll"]["weapon_item_id"] == chosen
    assert prepared["combat_pending_roll"]["weapon_label"] == "手枪（5发）"
    assert prepared["pc"]["inventory"] == ["手枪（3发）", "手枪（5发）"]
    result = execute(
        "combat_roll",
        {"roll_id": prepared["combat_pending_roll"]["roll_id"], "response": "roll"},
        alice,
    )
    items = snapshot()[0]["item_registry"]["items"]
    assert items[original]["label"] == "手枪（3发）"
    assert items[chosen]["label"] == "手枪（4发）"
    assert result["result"]["ammo"]["weapon_item_id"] == chosen
    assert result["result"]["ammo"]["used_item_id"] == chosen
    after, count = snapshot(), len(draws)
    last = result["command_id"]
    execute(
        "combat_roll",
        {"roll_id": prepared["combat_pending_roll"]["roll_id"], "response": "roll"},
        alice,
        last,
    )
    assert snapshot() == after and len(draws) == count


def test_identical_labels_use_selected_registry_entry_not_first_matching_name(battle):
    _, _, alice, execute, snapshot, _ = battle
    original, chosen = weapons(battle, "手枪（3发）")
    execute("combat_action", action(chosen))
    execute(
        "combat_roll",
        {"roll_id": snapshot()[0]["combat_pending_roll"]["roll_id"], "response": "roll"},
        alice,
    )
    items = snapshot()[0]["item_registry"]["items"]
    assert items[original]["label"] == "手枪（3发）" and items[chosen]["label"] == "手枪（2发）"


def test_empty_selected_gun_does_not_fall_back_to_loaded_first_gun(battle):
    _, _, _, execute, snapshot, draws = battle
    _, chosen = weapons(battle, "手枪（0发）")
    before = snapshot()
    with pytest.raises(StructuredError, match="弹药不足"):
        execute("combat_action", action(chosen))
    assert snapshot() == before and not draws


@pytest.mark.parametrize("over", [{"weapon_item_id": "unknown"}, {"weapon": "另一把手枪"}])
def test_missing_or_contradictory_weapon_refused_before_dice(battle, over):
    _, _, _, execute, snapshot, draws = battle
    _, chosen = weapons(battle)
    before = snapshot()
    with pytest.raises(StructuredError):
        execute("combat_action", action(chosen, **over))
    assert snapshot() == before and not draws


def test_unbound_multi_weapon_command_requires_an_id_instead_of_guessing(battle):
    _, _, _, execute, snapshot, draws = battle
    weapons(battle)
    before = snapshot()
    with pytest.raises(StructuredError, match="明确选择"):
        execute(
            "combat_action",
            {
                "actor_id": "inv-alice",
                "target_id": "guard",
                "action_type": "firearm",
                "weapon": "手枪",
            },
        )
    assert snapshot() == before and not draws


def test_selected_stack_splits_one_used_item_and_preserves_unfired_pieces(battle):
    _, _, alice, execute, snapshot, _ = battle
    original, chosen = weapons(battle, "手枪（5发）", 2)
    execute("combat_action", action(chosen))
    result = execute(
        "combat_roll",
        {"roll_id": snapshot()[0]["combat_pending_roll"]["roll_id"], "response": "roll"},
        alice,
    )
    items = snapshot()[0]["item_registry"]["items"]
    used = result["result"]["ammo"]["used_item_id"]
    assert used not in {chosen, original}
    assert items[chosen]["label"] == "手枪（5发）" and items[chosen]["quantity"] == 1
    assert items[used]["label"] == "手枪（4发）" and items[used]["quantity"] == 1
    assert items[original]["label"] == "手枪（3发）"


def test_transfer_after_preparation_invalidates_roll_but_cancel_remains_legal(battle):
    _, _, alice, execute, snapshot, draws = battle
    _, chosen = weapons(battle)
    execute("combat_action", action(chosen))
    roll_id = snapshot()[0]["combat_pending_roll"]["roll_id"]
    execute(
        "transfer_item",
        {
            "item_id": chosen,
            "quantity": 1,
            "from": {"kind": "investigator", "id": "inv-alice"},
            "to": {"kind": "investigator", "id": "inv-bob"},
        },
    )
    before = snapshot()
    with pytest.raises(StructuredError, match="条件已变化"):
        execute("combat_roll", {"roll_id": roll_id, "response": "roll"}, alice)
    assert snapshot() == before and not draws
    execute("combat_roll", {"roll_id": roll_id, "response": "cancel"}, alice)
    assert (
        not draws and snapshot()[0]["item_registry"]["items"][chosen]["holder"]["id"] == "inv-bob"
    )


def test_nonhostile_confirmation_keeps_binding_and_actual_roll_spends_only_selected(battle):
    url, _, alice, execute, snapshot, draws = battle
    original, chosen = weapons(battle)
    with session_scope(url) as session:
        row = session.get(WorldState, "sp-world")
        state = copy.deepcopy(row.state)
        state["npcs"][0]["hostile_to_pc"] = False
        next(p for p in state["combat_state"]["participants"] if p["id"] == "guard")[
            "hostile_to_pc"
        ] = False
        row.state = state
    execute("combat_action", action(chosen))
    decision = snapshot()[0]["combat_state"]["pending_decision"]
    assert decision["weapon_item_id"] == chosen
    execute(
        "combat_decide", {"decision_id": decision["id"], "option_id": "confirm_violence"}, alice
    )
    assert not draws and snapshot()[0]["combat_pending_roll"]["weapon_item_id"] == chosen
    execute(
        "combat_roll",
        {"roll_id": snapshot()[0]["combat_pending_roll"]["roll_id"], "response": "roll"},
        alice,
    )
    assert snapshot()[0]["item_registry"]["items"][chosen]["label"] == "手枪（4发）"
    assert snapshot()[0]["item_registry"]["items"][original]["label"] == "手枪（3发）"


def test_typed_request_binding_cannot_be_replaced_or_omitted_by_keeper(battle):
    url, service, alice, _, snapshot, draws = battle
    original, chosen = weapons(battle)
    frame = {
        "request_id": "weapon-intent",
        "investigator_id": "inv-alice",
        "action": {
            "kind": "combat",
            "encounter_id": snapshot()[0]["combat_state"]["encounter_id"],
            "action_type": "firearm",
            "target_id": "guard",
            "weapon_item_id": chosen,
        },
    }
    service.submit_action_request(world_id="sp-world", principal=alice, request=frame)
    before = snapshot()
    for body in [
        action(original),
        {k: v for k, v in action(chosen).items() if k != "weapon_item_id"},
    ]:
        with pytest.raises(StructuredError, match="原战斗申报不一致"):
            service.execute_command(
                world_id="sp-world",
                principal=Principal(kind="keeper", user_id="u-keeper"),
                kind="combat_action",
                payload=body,
                command_id="wrong-weapon",
                expected_revision=None,
                cause_id="weapon-intent",
            )
    assert snapshot() == before and not draws
    with session_scope(url) as session:
        assert (
            session.scalar(
                select(PlayerRequest).where(PlayerRequest.request_id == "weapon-intent")
            ).status
            == "queued"
        )


def test_outbox_failure_rolls_back_exact_item_and_health(battle):
    _, service, alice, execute, snapshot, _ = battle
    _, chosen = weapons(battle)
    execute("combat_action", action(chosen))
    before = snapshot()
    with patch.object(service, "_append_events", side_effect=RuntimeError("failed")):
        with pytest.raises(RuntimeError):
            execute(
                "combat_roll",
                {"roll_id": before[0]["combat_pending_roll"]["roll_id"], "response": "roll"},
                alice,
            )
    assert snapshot() == before


def test_pvp_weapon_binding_survives_both_consents_and_readiness(battle):
    _, _, alice, execute, snapshot, draws = battle
    original, chosen = weapons(battle)
    bob = Principal(kind="player", user_id="u-bob", investigator_ids=("inv-bob",))
    execute("combat_action", action(chosen, target_id="inv-bob"))
    for player in (alice, bob):
        decision = snapshot()[0]["combat_state"]["pending_decision"]
        execute(
            "combat_decide",
            {"decision_id": decision["id"], "option_id": "confirm_confrontation"},
            player,
        )
    decision = snapshot()[0]["combat_state"]["pending_decision"]
    execute("combat_decide", {"decision_id": decision["id"], "option_id": "no_defense"}, bob)
    assert snapshot()[0]["combat_pending_roll"]["weapon_item_id"] == chosen
    execute(
        "combat_roll",
        {"roll_id": snapshot()[0]["combat_pending_roll"]["roll_id"], "response": "roll"},
        bob,
    )
    assert not draws and snapshot()[0]["item_registry"]["items"][chosen]["label"] == "手枪（5发）"
    execute(
        "combat_roll",
        {"roll_id": snapshot()[0]["combat_pending_roll"]["roll_id"], "response": "roll"},
        alice,
    )
    items = snapshot()[0]["item_registry"]["items"]
    assert items[chosen]["label"] == "手枪（4发）" and items[original]["label"] == "手枪（3发）"


def test_empty_pvp_gun_cannot_even_create_participation_wait(battle):
    _, _, _, execute, snapshot, draws = battle
    _, chosen = weapons(battle, "手枪（0发）")
    before = snapshot()
    with pytest.raises(StructuredError, match="弹药不足"):
        execute("combat_action", action(chosen, target_id="inv-bob"))
    assert snapshot() == before and not draws
