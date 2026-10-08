"""Human-only authored-state rulings are audited, atomic and private."""

import copy
from unittest.mock import patch

import pytest
from sqlalchemy import select
from test_structured_combat_transactions import battle as battle

from src.storage.database import GameCommand, WorldMember, WorldState, session_scope
from src.structured.errors import StructuredError
from src.structured.principal import Principal
from src.structured.service import wire_envelope
from src.structured.validation import validate_event


@pytest.fixture
def ruling_world(battle):
    url, service, alice, execute, snapshot, draws = battle
    execute("combat_end", {"reason": "转入案件收尾"})
    with session_scope(url) as session:
        row = session.get(WorldState, "sp-world")
        state = copy.deepcopy(row.state)
        state["flags"] = {"sealed": False, "chapter": 1}
        state["endings"] = [
            {
                "id": "seal",
                "title": "封印完成",
                "ending_type": "good",
                "required_flags": {"sealed": True},
            }
        ]
        row.state = state
    return battle


def payload(**over):
    return {
        "flag_id": "sealed",
        "value": True,
        "expected_before": False,
        "basis": "玩家完成了仪式；人类主持确认其效果。",
        **over,
    }


def test_ruling_unlocks_configured_ending_without_bypassing_validation(ruling_world):
    _, service, alice, execute, snapshot, _ = ruling_world
    with pytest.raises(StructuredError, match="前置"):
        execute("end_game", {"ending_id": "seal"})
    outcome = execute("record_ruling", payload(), command_id="ruling-seal")
    for event in outcome["events"]:
        validate_event(wire_envelope(event))
        assert event["audience"] == {"kind": "keeper"}
    keeper = service.session_snapshot(
        world_id="sp-world", principal=Principal(kind="keeper", user_id="u-keeper")
    )
    assert keeper["keeper_rulings"]["eligible_endings"][0]["id"] == "seal"
    assert keeper["keeper_rulings"]["recent"][0]["before"] is False
    assert keeper["keeper_rulings"]["recent"][0]["user_id"] == "u-keeper"
    assert "keeper_rulings" not in service.session_snapshot(world_id="sp-world", principal=alice)
    before = snapshot()
    replay = execute("record_ruling", payload(), command_id="ruling-seal")
    assert replay["deduplicated"]
    assert snapshot() == before
    execute("end_game", {"ending_id": "seal"})
    assert snapshot()[0]["game_over"]["id"] == "seal"
    with pytest.raises(StructuredError, match="结束"):
        execute("record_ruling", payload(value=False, expected_before=True))


@pytest.mark.parametrize(
    "principal",
    [
        Principal(kind="agent", run_id="forged"),
        Principal(kind="player", user_id="u-alice", investigator_ids=("inv-alice",)),
        Principal(kind="viewer", user_id="u-bob"),
    ],
)
def test_non_human_principals_cannot_rule_even_with_a_known_flag(ruling_world, principal):
    _, _, _, execute, snapshot, _ = ruling_world
    before = snapshot()
    with pytest.raises(StructuredError, match="人类主持"):
        execute("record_ruling", payload(), principal)
    assert snapshot() == before


@pytest.mark.parametrize(
    "change",
    [
        {"flag_id": "invented"},
        {"value": 1},
        {"expected_before": 0},
        {"basis": " "},
        {"value": {"hp": 999}},
    ],
)
def test_invalid_ruling_changes_nothing(ruling_world, change):
    _, _, _, execute, snapshot, _ = ruling_world
    before = snapshot()
    with pytest.raises(StructuredError):
        execute("record_ruling", payload(**change))
    assert snapshot() == before


def test_outbox_failure_rolls_back_flag_audit_and_command(ruling_world):
    url, service, _, execute, snapshot, _ = ruling_world
    before = snapshot()
    with patch.object(service, "_append_events", side_effect=RuntimeError("outbox failure")):
        with pytest.raises(RuntimeError):
            execute("record_ruling", payload(), command_id="failed-ruling")
    assert snapshot() == before
    with session_scope(url) as session:
        assert (
            session.scalar(select(GameCommand).where(GameCommand.command_id == "failed-ruling"))
            is None
        )


def test_revoked_keeper_cannot_replay_private_ruling(ruling_world):
    url, _, _, execute, snapshot, _ = ruling_world
    execute("record_ruling", payload(), command_id="revoke-ruling")
    with session_scope(url) as session:
        member = session.scalar(
            select(WorldMember).where(
                WorldMember.world_id == "sp-world", WorldMember.user_id == "u-keeper"
            )
        )
        member.can_keeper = False
    before = snapshot()
    with pytest.raises(StructuredError):
        execute("record_ruling", payload(), command_id="revoke-ruling")
    assert snapshot() == before
