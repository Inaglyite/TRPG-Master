"""Pure multi-PC rewards, idempotency and per-recipient ending events."""

import copy
from unittest.mock import patch

import pytest

from src.gameplay.case_settlement import settle_roster_case
from src.structured.combat_endings import finish_game
from src.structured.domains import CommandContext
from src.structured.errors import StructuredError


@pytest.fixture
def state():
    return {
        "module": "test-module",
        "investigators": {
            "alice": {
                "name": "爱丽丝",
                "character_id": "card-alice",
                "hp": 9,
                "max_hp": 12,
                "san": 42,
                "max_san": 60,
                "character_session": {"starting_hp": 11, "starting_san": 50},
                "career": {"reputation": 2, "known_contacts": ["爱丽丝的私密联系人"]},
            },
            "bob": {
                "name": "鲍勃",
                "hp": 6,
                "max_hp": 10,
                "san": 38,
                "max_san": 45,
                "career": {"reputation": 8, "known_contacts": ["鲍勃的私密联系人"]},
            },
        },
        "pc": {"name": "非参与占位卡"},
        "flags": {"sealed": True},
        "npcs": [{"name": "不应分发给所有人的NPC", "revealed": {"level": 1}}],
        "endings": [
            {
                "id": "seal",
                "title": "封印完成",
                "ending_type": "good",
                "required_flags": {"sealed": True},
            }
        ],
    }


ENDING = {"id": "seal", "type": "good", "title": "封印完成", "summary": "案件结束"}


def settle(state, **kwargs):
    return settle_roster_case(
        state, world_id="world-a", ending=ENDING, completed_at="2026-10-05T12:00:00Z", **kwargs
    )


def test_each_investigator_receives_own_deltas_and_no_contacts_leak(state):
    receipts = settle(state)
    assert set(receipts) == {"alice", "bob"}
    assert receipts["alice"]["case"]["hp_delta"] == -2
    assert receipts["alice"]["case"]["san_delta"] == -8
    assert receipts["bob"]["case"]["hp_delta"] == -4
    assert receipts["alice"]["career"]["reputation"] == 5
    assert receipts["bob"]["career"]["reputation"] == 11
    assert receipts["alice"]["career"]["known_contacts"] == ["爱丽丝的私密联系人"]
    assert "career" not in state["pc"]


def test_retry_returns_original_without_extra_rewards_or_aliases(state):
    first = settle(state)
    before = copy.deepcopy(state)
    second = settle_roster_case(state, world_id="world-a", ending=ENDING, completed_at="later")
    assert second == first and state == before
    second["alice"]["career"]["reputation"] = 999
    assert state == before


def test_missing_ledger_does_not_duplicate_existing_career_entry(state):
    first = settle(state)
    del state["case_settlements"]
    assert settle(state) == first
    assert len(state["investigators"]["alice"]["career"]["case_history"]) == 1


def test_active_pc_is_authoritative_and_roster_career_synchronizes(state):
    state["active_investigator_id"] = "alice"
    state["pc"] = copy.deepcopy(state["investigators"]["alice"])
    state["pc"]["hp"] = 4
    receipts = settle(state)
    assert receipts["alice"]["case"]["hp_delta"] == -7
    assert state["pc"]["career"] == state["investigators"]["alice"]["career"]


def test_invalid_later_sheet_leaves_all_rewards_unmodified(state):
    state["investigators"]["bob"]["hp"] = "not-a-number"
    before = copy.deepcopy(state)
    with pytest.raises(ValueError):
        settle(state)
    assert state == before


@pytest.mark.parametrize(
    "field,value",
    [
        ("case_settlements", []),
        ("case_settlements", {"world-a:seal": "corrupt"}),
        ("investigators", ["alice"]),
    ],
)
def test_corrupt_ledgers_and_rosters_are_not_silently_replaced(state, field, value):
    state[field] = value
    before = copy.deepcopy(state)
    with pytest.raises(ValueError):
        settle(state)
    assert state == before


@pytest.mark.parametrize(
    "ending_type,delta", [("good", 3), ("secret", 2), ("neutral", 1), ("bad", 0)]
)
def test_same_reputation_rules_as_existing_settlement(state, ending_type, delta):
    result = settle_roster_case(
        state, world_id="world-a", ending={**ENDING, "type": ending_type}, completed_at="now"
    )
    assert result["alice"]["career"]["reputation"] == 2 + delta


def test_no_runtime_or_profile_io_and_settlements_are_private(state):
    ctx = CommandContext(world_id="world-a", principal=None)
    with (
        patch("src.gameplay.characters._runtime_context", side_effect=AssertionError("no runtime")),
        patch(
            "src.gameplay.characters.load_profile",
            side_effect=AssertionError("no profile read"),
        ),
        patch(
            "src.gameplay.characters.save_profile", side_effect=AssertionError("no profile write")
        ),
    ):
        result = finish_game(state, {"ending_id": "seal"}, ctx)
    public = [event for event in result.events if event.audience == {"kind": "public"}]
    assert [event.type for event in public] == ["game_ended"]
    assert "私密联系人" not in str(public) and "career" not in str(result.result)
    alice = [
        event
        for event in result.events
        if event.audience == {"kind": "investigators", "investigator_ids": ["alice"]}
    ]
    assert len(alice) == 1 and alice[0].type == "case_settled"
    assert "鲍勃的私密联系人" not in str(alice)
    assert len([event for event in result.events if event.audience == {"kind": "keeper"}]) == 2


def test_finish_failure_does_not_mark_game_over_or_award_first_pc(state):
    state["investigators"]["bob"]["character_session"] = "corrupt"
    before = copy.deepcopy(state)
    with pytest.raises(StructuredError):
        finish_game(
            state, {"ending_id": "seal"}, CommandContext(world_id="world-a", principal=None)
        )
    assert state == before
