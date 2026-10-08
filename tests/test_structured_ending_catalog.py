"""Private authored ending notes use actual rules and committed live updates."""

import copy
from unittest.mock import patch

import pytest
from test_structured_combat_transactions import battle as battle
from test_structured_rulings import ruling_world as ruling_world

from src.gameplay.endings import validate_ending
from src.structured.domains import CommandContext, CommandResult
from src.structured.execution import execute_domain
from src.structured.principal import Principal
from src.structured.rulings import ending_catalog, ruling_projection
from src.structured.service import audience_visible, wire_envelope
from src.structured.validation import validate_event


def test_catalogue_matches_validator_and_does_not_mutate_flags_or_author_notes():
    state = {
        "flags": {"sealed": False, "chapter": 0, "password": ""},
        "endings": [
            {"id": "good", "title": "真相", "required_flags": {"sealed": True}},
            {"id": "leave", "title": "离开", "required_flags": {"chapter": 0, "password": ""}},
            {"id": "missing", "title": "还需调查", "required_flags": {"unknown": False}},
            {"id": "open", "title": "开放收尾", "required_flags": {}},
        ],
    }
    before = copy.deepcopy(state)
    entries = ending_catalog(state)
    for entry in entries:
        assert entry["eligible"] == validate_ending(state, {"ending_id": entry["id"]})["ok"]
    assert entries[0]["conditions"][0]["current_text"] == "false"
    assert entries[1]["conditions"][0]["current_text"] == "0"
    assert entries[1]["conditions"][1]["current_text"] == '""'
    missing = entries[2]["conditions"][0]
    assert missing == {
        "flag_id": "unknown",
        "expected_text": "false",
        "current_text": "",
        "recorded": False,
        "satisfied": False,
    }
    assert entries[3]["can_prepare"] and entries[3]["conditions"] == []
    assert state == before


def test_duplicate_legacy_ids_follow_validator_last_definition_not_first_eligible():
    state = {
        "flags": {"sealed": False},
        "endings": [
            {"id": "same", "title": "旧定义", "required_flags": {}},
            {"id": "same", "title": "有效定义", "required_flags": {"sealed": True}},
        ],
    }
    assert len(ending_catalog(state)) == 1
    assert ending_catalog(state)[0]["title"] == "有效定义"
    assert not ending_catalog(state)[0]["can_prepare"]


@pytest.mark.parametrize(
    "state_extra, reason",
    [
        ({"combat_state": {"active": True}}, "战斗"),
        ({"game_over": {"id": "done"}}, "已结算"),
    ],
)
def test_eligibility_does_not_mean_immediate_prepare(state_extra, reason):
    state = {"flags": {}, "endings": [{"id": "open", "title": "收尾"}], **state_extra}
    entry = ending_catalog(state)[0]
    assert entry["eligible"] and not entry["can_prepare"]
    assert reason in entry["blocked_reason"]


def test_shared_executor_updates_privately_after_fact_change_not_every_narrative():
    state = {
        "flags": {"sealed": False},
        "endings": [{"id": "seal", "title": "封印", "required_flags": {"sealed": True}}],
    }
    ctx = CommandContext(world_id="world", principal=Principal(kind="keeper", user_id="gm"))

    def change(state, _payload, _ctx):
        state["flags"]["sealed"] = True
        return CommandResult(result={})

    outcome = execute_domain("declared-effect", change, state, {}, ctx)
    assert len(outcome.events) == 1
    event = outcome.events[0]
    assert event.type == "ending_catalog_updated" and event.audience == {"kind": "keeper"}
    assert event.payload["ending_catalog"][0]["can_prepare"]
    assert state["flags"]["sealed"] is True and not state.get("keeper_rulings")
    result = execute_domain("publish_message", lambda *_: CommandResult(result={}), state, {}, ctx)
    assert result.events == []


def test_real_combat_close_changes_readiness_and_only_keeper_receives_catalogue(ruling_world):
    _, service, alice, execute, _, _ = ruling_world
    execute(
        "record_ruling",
        {"flag_id": "sealed", "value": True, "expected_before": False, "basis": "主持核验"},
    )
    started = execute("combat_start", {"participants": [{"id": "guard"}]})
    update = [e for e in started["events"] if e["type"] == "ending_catalog_updated"]
    assert len(update) == 1
    assert not update[0]["payload"]["ending_catalog"][0]["can_prepare"]
    ended = execute("combat_end", {"reason": "双方停手"})
    update = [e for e in ended["events"] if e["type"] == "ending_catalog_updated"]
    assert len(update) == 1 and update[0]["payload"]["ending_catalog"][0]["can_prepare"]
    validate_event(wire_envelope(update[0]))
    for principal in [alice, Principal(kind="viewer", user_id="u-bob")]:
        assert not audience_visible(update[0]["audience"], principal)
        assert "keeper_rulings" not in service.session_snapshot(
            world_id="sp-world", principal=principal
        )
    assert audience_visible(update[0]["audience"], Principal(kind="keeper", user_id="u-keeper"))


def test_outbox_failure_rolls_back_readiness_and_no_catalogue_replay_duplicates(ruling_world):
    _, service, _, execute, snapshot, _ = ruling_world
    before = snapshot()
    with patch.object(service, "_append_events", side_effect=RuntimeError("failed")):
        with pytest.raises(RuntimeError):
            execute("combat_start", {"participants": [{"id": "guard"}]})
    assert snapshot() == before
    result = execute("combat_start", {"participants": [{"id": "guard"}]}, command_id="start-once")
    assert len([e for e in result["events"] if e["type"] == "ending_catalog_updated"]) == 1
    after = snapshot()
    assert execute("combat_start", {"participants": [{"id": "guard"}]}, command_id="start-once")[
        "deduplicated"
    ]
    assert snapshot() == after


def test_ruling_already_contains_catalogue_without_duplicate_refresh(ruling_world):
    _, _, _, execute, snapshot, _ = ruling_world
    outcome = execute(
        "record_ruling",
        {"flag_id": "sealed", "value": True, "expected_before": False, "basis": "主持核验"},
    )
    assert [e["type"] for e in outcome["events"] if e["type"] != "action_status"] == ["ruling_recorded"]
    assert outcome["events"][0]["payload"] == ruling_projection(snapshot()[0])
