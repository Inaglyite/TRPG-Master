"""Combat commands exercise actual transactions and freshly read control grants."""

import copy
from unittest.mock import patch

import pytest
from sqlalchemy import select
from test_structured_commands import make_structured_world

from src.storage.database import (
    EventOutbox,
    GameCommand,
    WorldInvestigator,
    WorldState,
    session_scope,
)
from src.structured.errors import StructuredError
from src.structured.gateway import StructuredGateway
from src.structured.principal import Principal
from src.structured.service import StructuredPlayService, wire_envelope
from src.structured.validation import validate_command, validate_event


@pytest.fixture
def battle(tmp_path):
    runtime = make_structured_world(tmp_path)
    url = runtime.database_url
    with session_scope(url) as session:
        row = session.get(WorldState, "sp-world")
        state = copy.deepcopy(row.state)
        alice = state["investigators"]["inv-alice"]
        alice.update(
            controller_user_id="u-alice",
            attributes={"DEX": 90, "CON": 60},
            skills={"射击": 90, "斗殴": 90},
            inventory=["手枪（3发）"],
        )
        state["investigators"]["inv-bob"]["controller_user_id"] = "u-bob"
        state["active_investigator_id"] = "inv-alice"
        state["investigator_controllers"] = {"u-alice": "inv-alice", "u-bob": "inv-bob"}
        state["pc"] = copy.deepcopy(alice)
        state["current_scene"]["npcs_present"] = ["guard"]
        state["npcs"] = [
            {
                "id": "guard",
                "name": "守卫",
                "hp": 12,
                "max_hp": 12,
                "attributes": {"DEX": 20, "CON": 50},
                "hostile_to_pc": True,
                "skills": {"闪避": 20},
                "damage_spec": "1d3",
            }
        ]
        row.state = state
    draws = []

    def rng(n):
        draws.append(n)
        return min(1, n - 1)

    service = StructuredPlayService(url, rng=rng)
    keeper = Principal(kind="keeper", user_id="u-keeper")
    alice = Principal(kind="player", user_id="u-alice", investigator_ids=("inv-alice",))
    counter = 0

    def execute(kind, payload, principal=keeper, command_id=None):
        nonlocal counter
        counter += 1
        result = service.execute_command(
            world_id="sp-world",
            principal=principal,
            kind=kind,
            payload=payload,
            command_id=command_id or f"battle-{counter}",
            expected_revision=None,
        )
        for event in result["events"]:
            validate_event(wire_envelope(event))
        return result

    def snapshot():
        with session_scope(url) as session:
            row = session.get(WorldState, "sp-world")
            commands = session.scalars(
                select(GameCommand).where(GameCommand.world_id == "sp-world")
            ).all()
            events = session.scalars(
                select(EventOutbox).where(EventOutbox.world_id == "sp-world")
            ).all()
            return copy.deepcopy(row.state), row.revision, len(commands), len(events)

    execute("combat_start", {"participants": [{"id": "guard"}]})
    execute(
        "combat_action",
        {
            "actor_id": "inv-alice",
            "target_id": "guard",
            "action_type": "firearm",
            "weapon": "手枪",
            "damage_spec": "1d3",
        },
    )
    return url, service, alice, execute, snapshot, draws


def test_wait_then_retry_draws_and_commits_only_once(battle):
    _, _, alice, execute, snapshot, draws = battle
    before = snapshot()
    assert draws == []
    items_before = [
        item
        for item in before[0]["item_registry"]["items"].values()
        if item["holder"] == {"kind": "investigator", "id": "inv-alice"}
    ]
    assert len(items_before) == 1
    payload = {"roll_id": before[0]["combat_pending_roll"]["roll_id"], "response": "roll"}
    result = execute("combat_roll", payload, alice, "roll-once")
    after = snapshot()
    count = len(draws)
    assert count > 0
    assert after[0]["pc"]["inventory"] == ["手枪（2发）"]
    updated_item = after[0]["item_registry"]["items"][items_before[0]["item_id"]]
    assert updated_item["label"] == "手枪（2发）"
    assert updated_item["quantity"] == 1
    private_inventory_events = [e for e in result["events"] if e["type"] == "inventory_changed"]
    assert len(private_inventory_events) == 2  # owner and keeper, never public
    assert all(e["audience"]["kind"] != "public" for e in private_inventory_events)
    assert after[2] == before[2] + 1
    replay = execute("combat_roll", payload, alice, "roll-once")
    assert replay["deduplicated"] is True
    assert replay["result"] == result["result"]
    assert snapshot() == after
    assert len(draws) == count


def test_revoked_controller_cannot_roll_or_replay_receipt(battle):
    url, _, alice, execute, snapshot, draws = battle
    payload = {"roll_id": snapshot()[0]["combat_pending_roll"]["roll_id"], "response": "cancel"}
    execute("combat_roll", payload, alice, "cancel-once")
    with session_scope(url) as session:
        row = session.get(WorldInvestigator, "wi-alice")
        row.controller_user_id = None
        row.status = "available"
    before = snapshot()
    with pytest.raises(StructuredError, match="控制权"):
        execute("combat_roll", payload, alice, "cancel-once")
    assert snapshot() == before
    assert not draws


def test_other_player_cannot_use_forged_investigator_ids(battle):
    _, _, _, execute, snapshot, draws = battle
    payload = {"roll_id": snapshot()[0]["combat_pending_roll"]["roll_id"], "response": "roll"}
    forged = Principal(kind="player", user_id="u-bob", investigator_ids=("inv-alice",))
    before = snapshot()
    with pytest.raises(StructuredError, match="控制该调查员"):
        execute("combat_roll", payload, forged)
    assert snapshot() == before
    assert not draws


def test_outbox_failure_rolls_back_health_ammo_command_and_events(battle):
    _, service, alice, execute, snapshot, draws = battle
    before = snapshot()
    payload = {"roll_id": before[0]["combat_pending_roll"]["roll_id"], "response": "roll"}
    with patch.object(service, "_append_events", side_effect=RuntimeError("outbox failed")):
        with pytest.raises(RuntimeError, match="outbox failed"):
            execute("combat_roll", payload, alice)
    assert draws  # Computation occurred, but no result was committed or published.
    assert snapshot() == before


def test_host_cannot_replace_player_roll(battle):
    _, _, _, execute, snapshot, draws = battle
    payload = {"roll_id": snapshot()[0]["combat_pending_roll"]["roll_id"], "response": "roll"}
    before = snapshot()
    with pytest.raises(StructuredError, match="只能由玩家"):
        execute("combat_roll", payload)
    assert snapshot() == before
    assert not draws


def test_consumed_registered_weapon_cannot_reappear_from_stale_sheet(battle):
    _, _, alice, execute, snapshot, draws = battle
    state = snapshot()[0]
    item = next(
        i
        for i in state["item_registry"]["items"].values()
        if i["holder"] == {"kind": "investigator", "id": "inv-alice"}
    )
    pending = {"roll_id": state["combat_pending_roll"]["roll_id"], "response": "roll"}
    execute(
        "use_item",
        {
            "investigator_id": "inv-alice",
            "item_id": item["item_id"],
            "quantity": 1,
            "operation": "丢弃",
            "consume": True,
        },
    )
    before = snapshot()
    with pytest.raises(StructuredError, match="条件已变化"):
        execute("combat_roll", pending, alice)
    assert snapshot() == before
    assert not draws
    execute("combat_roll", {**pending, "response": "cancel"}, alice)
    assert snapshot()[0]["pc"]["inventory"] == []
    with pytest.raises(StructuredError):
        execute(
            "combat_action",
            {
                "actor_id": "inv-alice",
                "target_id": "guard",
                "action_type": "firearm",
                "weapon": "手枪",
            },
        )
    assert not draws


def test_stacked_guns_split_without_changing_unfired_stack(battle):
    url, _, alice, execute, snapshot, _ = battle
    state = snapshot()[0]
    item_id = next(
        i["item_id"]
        for i in state["item_registry"]["items"].values()
        if i["holder"] == {"kind": "investigator", "id": "inv-alice"}
    )
    with session_scope(url) as session:
        row = session.get(WorldState, "sp-world")
        updated = copy.deepcopy(row.state)
        updated["item_registry"]["items"][item_id]["quantity"] = 2
        row.state = updated
    execute(
        "combat_roll",
        {"roll_id": state["combat_pending_roll"]["roll_id"], "response": "cancel"},
        alice,
    )
    execute(
        "combat_action",
        {
            "actor_id": "inv-alice",
            "target_id": "guard",
            "action_type": "firearm",
            "weapon": "手枪",
            "damage_spec": "1d3",
        },
    )
    execute(
        "combat_roll",
        {"roll_id": snapshot()[0]["combat_pending_roll"]["roll_id"], "response": "roll"},
        alice,
    )
    items = snapshot()[0]["item_registry"]["items"]
    assert items[item_id]["label"] == "手枪（3发）"
    assert items[item_id]["quantity"] == 1
    fired = [
        item
        for item in items.values()
        if item["holder"] == {"kind": "investigator", "id": "inv-alice"}
        and item["label"] == "手枪（2发）"
    ]
    assert len(fired) == 1
    assert fired[0]["quantity"] == 1
    assert fired[0]["item_id"] != item_id


def test_initial_registry_does_not_duplicate_active_pc_backpack(battle):
    state = battle[4]()[0]
    firearms = [
        item for item in state["item_registry"]["items"].values() if item["label"] == "手枪（3发）"
    ]
    assert len(firearms) == 1
    assert firearms[0]["holder"] == {"kind": "investigator", "id": "inv-alice"}


def test_npc_hit_emits_private_character_status_in_same_commit(battle):
    url, _, alice, execute, snapshot, _ = battle
    execute(
        "combat_roll",
        {"roll_id": snapshot()[0]["combat_pending_roll"]["roll_id"], "response": "cancel"},
        alice,
    )
    with session_scope(url) as session:
        row = session.get(WorldState, "sp-world")
        state = copy.deepcopy(row.state)
        combat = state["combat_state"]
        combat["current_actor"] = "guard"
        combat["turn_index"] = combat["turn_order"].index("guard")
        next(p for p in combat["participants"] if p["id"] == "guard")["skills"][
            "firearms_handgun"
        ] = 90
        row.state = state
    execute(
        "combat_action",
        {
            "actor_id": "guard",
            "target_id": "inv-alice",
            "action_type": "firearm",
            "damage_spec": "1d3",
        },
    )
    decision_id = snapshot()[0]["combat_state"]["pending_decision"]["id"]
    execute("combat_decide", {"decision_id": decision_id, "option_id": "no_defense"}, alice)
    service = battle[1]
    waiting = service.session_snapshot(world_id="sp-world", principal=alice)
    assert waiting["combat_roll"] is not None
    assert waiting["combat_decision"] is None
    before = snapshot()[0]["pc"]["hp"]
    result = execute(
        "combat_roll",
        {"roll_id": snapshot()[0]["combat_pending_roll"]["roll_id"], "response": "roll"},
        alice,
    )
    after = snapshot()[0]["pc"]["hp"]
    assert after < before
    assert snapshot()[0]["investigators"]["inv-alice"]["hp"] == after
    updates = [event for event in result["events"] if event["type"] == "state_changed"]
    assert len(updates) == 2
    assert all(event["payload"]["hp"] == after for event in updates)
    assert {event["audience"]["kind"] for event in updates} == {"investigators", "keeper"}


def test_ending_retry_does_not_duplicate_rewards_and_terminal_state_blocks_play(battle):
    _, _, _, execute, snapshot, _ = battle
    execute("combat_end", {"reason": "双方停战"})
    payload = {"ending_type": "good", "title": "调查结束", "summary": "达成和解"}
    result = execute("end_game", payload, command_id="ending-once")
    after = snapshot()
    assert set(after[0]["case_settlements"])  # Receipts committed together with game_over.
    assert len(result["result"]["settled_investigator_ids"]) == 2
    replay = execute("end_game", payload, command_id="ending-once")
    assert replay["deduplicated"]
    assert replay["result"] == result["result"]
    assert snapshot() == after
    with pytest.raises(StructuredError, match="游戏已结束"):
        execute("advance_time", {"minutes": 20, "reason": "继续调查"})
    assert snapshot() == after


def test_ending_outbox_failure_leaves_no_rewards_or_game_over(battle):
    _, service, _, execute, snapshot, _ = battle
    execute("combat_end", {"reason": "双方停战"})
    before = snapshot()
    with patch.object(service, "_append_events", side_effect=RuntimeError("outbox failed")):
        with pytest.raises(RuntimeError, match="outbox failed"):
            execute("end_game", {"ending_type": "good", "title": "调查结束"})
    assert snapshot() == before
    assert not snapshot()[0].get("game_over")
    assert not snapshot()[0].get("case_settlements")


def test_gateway_resolves_roll_as_player_not_keeper(battle):
    url, _, _, _, snapshot, _ = battle
    state, revision, *_ = snapshot()
    gateway = StructuredGateway(url)
    events, deduplicated = gateway._execute(
        "sp-world",
        "u-alice",
        {
            "type": "command_request",
            "protocol_version": 1,
            "world_id": "sp-world",
            "command_id": "gateway-roll",
            "expected_revision": revision,
            "kind": "combat_roll",
            "payload": {
                "roll_id": state["combat_pending_roll"]["roll_id"],
                "response": "cancel",
            },
        },
    )
    assert not deduplicated
    assert any(e["type"] == "combat_roll_resolved" for e in events)
    for event in events:
        validate_event(wire_envelope(event))
    assert not snapshot()[0].get("combat_pending_roll")


def test_snapshot_has_public_battle_but_only_own_roll_and_rewards(battle):
    _, service, alice, execute, _, _ = battle
    bob = Principal(kind="player", user_id="u-bob", investigator_ids=("inv-bob",))
    own = service.session_snapshot(world_id="sp-world", principal=alice)
    other = service.session_snapshot(world_id="sp-world", principal=bob)
    assert own["combat_roll"]["investigator_id"] == "inv-alice"
    assert other["combat_roll"] is None
    assert other["combat_decision"] is None
    assert own["combat"] == other["combat"]
    assert "skills" not in str(other["combat"])
    assert "conditions" not in (own["combat_roll"] or {})
    execute("combat_end", {"reason": "停战"})
    execute("end_game", {"ending_type": "good", "title": "调查结束"})
    for principal in (alice, bob):
        payload = service.session_snapshot(world_id="sp-world", principal=principal)
        assert {r["investigator_id"] for r in payload["case_settlements"]} == set(
            principal.investigator_ids
        )
        validate_event(
            {
                "protocol_version": 1,
                "event_id": 0,
                "world_id": "sp-world",
                "sequence": 0,
                "revision": payload["revision"],
                "type": "session_snapshot",
                "payload": payload,
            }
        )


def test_ending_refuses_new_player_intents_and_old_check_responses(battle):
    _, service, alice, execute, snapshot, draws = battle
    execute("combat_end", {"reason": "停战"})
    execute("end_game", {"ending_type": "good", "title": "调查结束"})
    before = snapshot()
    with pytest.raises(StructuredError, match="已结算"):
        service.submit_action_request(
            world_id="sp-world",
            principal=alice,
            request={
                "request_id": "after-ending-action",
                "investigator_id": "inv-alice",
                "action": {"kind": "freeform", "text": "继续搜查"},
            },
        )
    with pytest.raises(StructuredError, match="已结算"):
        service.submit_check_response(
            world_id="sp-world",
            principal=alice,
            request={
                "request_id": "after-ending-check",
                "check_request_id": "old-check",
                "decision": "roll",
            },
        )
    assert snapshot() == before
    assert not draws


@pytest.mark.parametrize(
    "kind,payload",
    [
        ("combat_start", {"participants": [{"id": "guard", "hp": 999}]}),
        ("combat_action", {"actor_id": "inv-alice", "action_type": "firearm", "bonus_dice": True}),
        (
            "combat_action",
            {"actor_id": "inv-alice", "action_type": "firearm", "damage_spec": "999d999"},
        ),
        ("combat_roll", {"roll_id": "roll-1", "response": "automatic"}),
        ("combat_roll", {"roll_id": "roll-1", "response": "roll", "principal": "keeper"}),
        ("combat_decide", {"decision_id": "decision-1"}),
        ("combat_end", {"reason": ""}),
        ("end_game", {"ending_type": "perfect"}),
    ],
)
def test_battle_schema_rejects_unbounded_or_forged_payloads(kind, payload):
    with pytest.raises(StructuredError):
        validate_command(kind, payload)
