"""Player-visible receipts persist and never copy NPC skills/attributes."""

from test_structured_combat_transactions import battle as battle

from src.structured.principal import Principal


def test_result_visible_to_actor_and_keeper_but_not_other_players(battle):
    _, service, alice, execute, snapshot, _ = battle
    roll_id = snapshot()[0]["combat_pending_roll"]["roll_id"]
    outcome = execute("combat_roll", {"roll_id": roll_id, "response": "roll"}, alice)
    results = [e for e in outcome["events"] if e["type"] == "combat_roll_resolved"]
    assert {e["audience"]["kind"] for e in results} == {"keeper", "investigators"}
    receipt = results[0]["payload"]["result"]
    assert receipt["roll_id"] == roll_id
    assert receipt["rolls"][0]["roll"] >= 1
    for roll in receipt["rolls"]:
        assert set(roll) == {"actor_id", "role", "roll", "level"}
    assert "skill_value" not in str(receipt)
    assert "attribute_value" not in str(receipt)
    own = service.session_snapshot(world_id="sp-world", principal=alice)
    other = service.session_snapshot(
        world_id="sp-world",
        principal=Principal(kind="player", user_id="u-bob", investigator_ids=("inv-bob",)),
    )
    keeper = service.session_snapshot(
        world_id="sp-world", principal=Principal(kind="keeper", user_id="u-keeper")
    )
    assert own["combat_results"] == keeper["combat_results"] == [receipt]
    assert other["combat_results"] == []
    execute(
        "combat_roll",
        {"roll_id": roll_id, "response": "roll"},
        alice,
        command_id=outcome["command_id"],
    )
    assert snapshot()[0]["combat_results"] == [receipt]


def test_cancel_receipt_has_no_dice_and_keeps_resources(battle):
    _, service, alice, execute, snapshot, draws = battle
    before = snapshot()[0]
    count = len(draws)
    outcome = execute(
        "combat_roll",
        {"roll_id": before["combat_pending_roll"]["roll_id"], "response": "cancel"},
        alice,
    )
    receipt = next(
        e["payload"]["result"] for e in outcome["events"] if e["type"] == "combat_roll_resolved"
    )
    assert receipt["response"] == "cancel"
    assert receipt["rolls"] == []
    assert receipt["damage"] is None
    assert len(draws) == count
    assert snapshot()[0]["pc"]["inventory"] == before["pc"]["inventory"]
    execute("combat_end", {"reason": "停战"})
    assert service.session_snapshot(world_id="sp-world", principal=alice)["combat_results"] == [
        receipt
    ]
