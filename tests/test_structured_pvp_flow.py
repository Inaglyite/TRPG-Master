"""Two actual controllers consent separately; no dice/resource effect until both ready."""

import copy
from unittest.mock import patch

import pytest
from sqlalchemy import select
from test_structured_combat_transactions import battle as battle

from src.gameplay.investigators import normalize_legacy_combat_investigator_ids
from src.storage.database import WorldInvestigator, WorldMember, WorldState, session_scope
from src.structured.combat_flow import invalidate_combat_wait
from src.structured.errors import StructuredError
from src.structured.principal import Principal


@pytest.fixture
def pvp(battle):
    url, service, alice, execute, snapshot, draws = battle
    execute("combat_end", {"reason": "重新准备调查员间的遭遇"})
    execute("combat_start", {"participants": [{"id": "inv-bob"}]})
    bob = Principal(kind="player", user_id="u-bob", investigator_ids=("inv-bob",))
    return battle, bob


def prepare(pvp, action_type="melee", **extra):
    battle, _ = pvp
    _, _, _, execute, _, _ = battle
    return execute(
        "combat_action",
        {
            "actor_id": "inv-alice",
            "target_id": "inv-bob",
            "action_type": action_type,
            "damage_spec": "1d3",
            **extra,
        },
    )


def choose(pvp, principal, option, command_id=None):
    battle, _ = pvp
    _, _, _, execute, snapshot, _ = battle
    return execute(
        "combat_decide",
        {
            "decision_id": snapshot()[0]["combat_state"]["pending_decision"]["id"],
            "option_id": option,
        },
        principal,
        command_id,
    )


def ready(pvp, principal, response="roll", command_id=None):
    battle, _ = pvp
    _, _, _, execute, snapshot, _ = battle
    return execute(
        "combat_roll",
        {"roll_id": snapshot()[0]["combat_pending_roll"]["roll_id"], "response": response},
        principal,
        command_id,
    )


def to_defense_roll(pvp, action_type="melee"):
    battle, bob = pvp
    _, _, alice, _, _, _ = battle
    prepare(pvp, action_type)
    choose(pvp, alice, "confirm_confrontation")
    choose(pvp, bob, "confirm_confrontation")
    choose(pvp, bob, "dodge" if action_type == "melee" else "take_cover")


@pytest.mark.parametrize("action_type", ["melee", "firearm"])
def test_both_consent_choose_and_ready_before_one_atomic_resolution(pvp, action_type):
    battle, bob = pvp
    url, service, alice, execute, snapshot, draws = battle
    before = snapshot()[0]
    to_defense_roll(pvp, action_type)
    target_roll = snapshot()[0]["combat_pending_roll"]["roll_id"]
    assert snapshot()[0]["combat_pending_roll"]["source"] == "pvp_defense"
    first = ready(pvp, bob, command_id="defender-ready")
    assert draws == []
    waiting = snapshot()[0]
    assert waiting["combat_pending_roll"]["investigator_id"] == "inv-alice"
    assert waiting["pc"]["hp"] == before["pc"]["hp"]
    assert waiting["pc"]["inventory"] == before["pc"]["inventory"]
    assert waiting.get("combat_results", []) == []
    assert all(
        "result" not in e["payload"] for e in first["events"] if e["type"] == "combat_roll_resolved"
    )
    assert execute(
        "combat_roll", {"roll_id": target_roll, "response": "roll"}, bob, "defender-ready"
    )["deduplicated"]
    second = ready(pvp, alice, command_id="attacker-ready")
    after = snapshot()
    assert draws
    assert "combat_pvp" not in after[0] and "combat_pending_roll" not in after[0]
    assert after[0]["combat_state"]["active"] is True
    assert len(after[0]["combat_results"]) == 2
    if action_type == "firearm":
        assert after[0]["pc"]["inventory"] == ["手枪（2发）"]
    for principal in [alice, bob]:
        own = service.session_snapshot(world_id="sp-world", principal=principal)
        assert len(own["combat_results"]) == 1
        assert own["combat_results"][0]["investigator_id"] in principal.investigator_ids
    count = len(draws)
    actor_roll = next(
        e["payload"]["roll_id"]
        for e in second["events"]
        if e["type"] == "combat_roll_resolved" and e["payload"]["investigator_id"] == "inv-alice"
    )
    assert execute(
        "combat_roll", {"roll_id": actor_roll, "response": "roll"}, alice, "attacker-ready"
    )["deduplicated"]
    assert snapshot() == after and len(draws) == count


@pytest.mark.parametrize("stage", ["attacker", "target", "defense_roll", "attack_roll"])
def test_either_side_can_cancel_without_any_dice_cost(pvp, stage):
    battle, bob = pvp
    _, _, alice, _, snapshot, draws = battle
    before = snapshot()[0]
    if stage in {"defense_roll", "attack_roll"}:
        to_defense_roll(pvp)
        if stage == "attack_roll":
            ready(pvp, bob)
        ready(pvp, bob if stage == "defense_roll" else alice, "cancel")
    else:
        prepare(pvp)
        if stage == "target":
            choose(pvp, alice, "confirm_confrontation")
        choose(pvp, alice if stage == "attacker" else bob, "cancel_confrontation")
    state = snapshot()[0]
    assert "combat_pvp" not in state and "combat_pending_roll" not in state
    assert state["combat_state"]["pending_decision"] is None
    assert state["pc"] == before["pc"]
    assert state["investigators"] == before["investigators"]
    assert draws == []


def test_keeper_cannot_inject_defense_and_attacker_cannot_respond_for_target(pvp):
    battle, bob = pvp
    _, _, alice, execute, snapshot, draws = battle
    before = snapshot()
    with pytest.raises(StructuredError, match="被攻击玩家"):
        prepare(pvp, defender_choice="no_defense")
    assert snapshot() == before
    prepare(pvp)
    choose(pvp, alice, "confirm_confrontation")
    before = snapshot()
    for principal in [alice, Principal(kind="keeper", user_id="u-keeper")]:
        with pytest.raises(StructuredError):
            choose(pvp, principal, "confirm_confrontation")
    assert snapshot() == before and draws == []
    choose(pvp, bob, "confirm_confrontation")


@pytest.mark.parametrize("change", ["control", "membership", "conditions"])
def test_final_roll_revalidates_the_other_players_authority_and_conditions(pvp, change):
    battle, bob = pvp
    url, _, alice, _, snapshot, draws = battle
    to_defense_roll(pvp)
    ready(pvp, bob)
    with session_scope(url) as session:
        if change == "control":
            claim = session.scalar(
                select(WorldInvestigator).where(WorldInvestigator.character_key == "inv-bob")
            )
            claim.status, claim.controller_user_id = "unclaimed", None
        elif change == "membership":
            member = session.scalar(select(WorldMember).where(WorldMember.user_id == "u-bob"))
            session.delete(member)
        else:
            row = session.get(WorldState, "sp-world")
            state = copy.deepcopy(row.state)
            state["investigators"]["inv-bob"]["hp"] -= 1
            row.state = state
    before = snapshot()
    with pytest.raises(StructuredError):
        ready(pvp, alice)
    assert snapshot() == before and draws == []
    ready(pvp, alice, "cancel")
    assert "combat_pvp" not in snapshot()[0]


def test_outbox_failure_rolls_back_both_ready_roles_and_resources(pvp):
    battle, bob = pvp
    _, service, alice, _, snapshot, _ = battle
    to_defense_roll(pvp, "firearm")
    ready(pvp, bob)
    before = snapshot()
    with patch.object(service, "_append_events", side_effect=RuntimeError("outbox failure")):
        with pytest.raises(RuntimeError):
            ready(pvp, alice)
    assert snapshot() == before
    ready(pvp, alice)
    assert snapshot()[0]["pc"]["inventory"] == ["手枪（2发）"]


def test_restore_invalidates_both_players_old_consent_and_rolls(pvp):
    battle, bob = pvp
    _, _, _, _, snapshot, _ = battle
    to_defense_roll(pvp)
    ready(pvp, bob)
    state = snapshot()[0]
    assert invalidate_combat_wait(state)
    assert "combat_pvp" not in state and "combat_pending_roll" not in state
    assert state["combat_state"]["phase"] == "awaiting_action"


def test_threat_requires_both_players_but_never_rolls_or_spends_ammo(pvp):
    battle, bob = pvp
    _, _, alice, _, snapshot, draws = battle
    prepare(pvp, "threat")
    choose(pvp, alice, "confirm_confrontation")
    result = choose(pvp, bob, "confirm_confrontation")
    assert result["result"]["outcome"] == "threat_established"
    assert "combat_pvp" not in snapshot()[0] and draws == []


def test_id_compatibility_preserves_explicit_target_and_upgrades_old_pc_alias(pvp):
    battle, _ = pvp
    state = battle[4]()[0]
    state["combat_state"]["pending_decision"] = {
        "kind": "pvp_consent",
        "responding_investigator_id": "inv-bob",
        "action": {"actor_id": "pc", "target_id": "inv-bob"},
    }
    normalize_legacy_combat_investigator_ids(state)
    decision = state["combat_state"]["pending_decision"]
    assert decision["responding_investigator_id"] == "inv-bob"
    assert decision["action"]["actor_id"] == "inv-alice"
    decision["responding_investigator_id"] = "pc"
    normalize_legacy_combat_investigator_ids(state)
    assert decision["responding_investigator_id"] == "inv-alice"


def test_target_cancellation_preserves_the_attackers_original_request_binding(pvp):
    battle, bob = pvp
    _, service, alice, _, snapshot, draws = battle
    service.submit_action_request(
        world_id="sp-world",
        principal=alice,
        request={
            "request_id": "original-pvp",
            "investigator_id": "inv-alice",
            "action": {"kind": "freeform", "text": "与同伴对抗后再核对线索。"},
        },
    )
    service.execute_command(
        world_id="sp-world",
        principal=Principal(kind="keeper", user_id="u-keeper"),
        kind="combat_action",
        payload={"actor_id": "inv-alice", "target_id": "inv-bob", "action_type": "melee"},
        command_id="prepare-linked-pvp",
        cause_id="original-pvp",
        expected_revision=None,
    )
    choose(pvp, alice, "confirm_confrontation")
    cancellation = choose(pvp, bob, "cancel_confrontation")
    assert cancellation["result"]["source_request_id"] == "original-pvp"
    assert snapshot()[0].get("combat_pvp") is None and draws == []
    own = service.session_snapshot(world_id="sp-world", principal=alice)
    assert (
        next(r for r in own["requests"] if r["request_id"] == "original-pvp")["status"] == "queued"
    )


def test_pure_investigator_encounter_ends_only_when_one_side_cannot_act(pvp):
    from src.gameplay.combat import _check_combat_end

    state = pvp[0][4]()[0]
    combat = state["combat_state"]
    _check_combat_end(combat)
    assert combat["active"]
    target = next(p for p in combat["participants"] if p["id"] == "inv-bob")
    target["hp"] = 0
    _check_combat_end(combat)
    assert not combat["active"]
    assert combat["outcome"] == "confrontation_resolved"
