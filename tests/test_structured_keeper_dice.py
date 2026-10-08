"""Human keeper dice are independent of character control and skill checks."""

import copy

import pytest
from sqlalchemy import select
from test_structured_commands import make_structured_world
from test_structured_ws import _event_validator

from src.storage.database import GameCommand, KeeperControl, WorldMember, WorldState, session_scope
from src.storage.persistence import save_game
from src.structured.branch import create_structured_branch, restore_structured_save
from src.structured.errors import StructuredError
from src.structured.principal import Principal, bind_agent_control
from src.structured.service import StructuredPlayService
from src.structured.validation import validate_command

KEEPER = Principal(kind="keeper", user_id="u-keeper")
PLAYER = Principal(kind="player", user_id="u-alice", investigator_ids=("inv-alice",))


@pytest.fixture
def dice_world(tmp_path):
    context = make_structured_world(tmp_path)
    calls = []

    def rng(sides):
        calls.append(sides)
        return 0

    service = StructuredPlayService(context.database_url, rng=rng)
    service.session_snapshot(world_id=context.world_id, principal=KEEPER)
    return context, service, calls


def state_of(context):
    with session_scope(context.database_url) as session:
        row = session.get(WorldState, context.world_id)
        return copy.deepcopy(row.state), row.revision


def roll(context, service, command_id="host-dice", payload=None, principal=KEEPER):
    return service.execute_command(
        world_id=context.world_id,
        principal=principal,
        kind="keeper_roll",
        command_id=command_id,
        payload={"spec": "1d100"} if payload is None else payload,
        expected_revision=None,
    )


@pytest.mark.parametrize("visibility", ["keeper", "public"])
def test_unclaimed_keeper_roll_visibility_snapshot_and_replay(dice_world, visibility):
    context, service, calls = dice_world
    assert KEEPER.investigator_ids == ()
    before = state_of(context)
    payload = {"spec": "2d6+3", "visibility": visibility}
    validate_command("keeper_roll", payload)
    result = roll(context, service, payload=payload)
    assert result["result"]["total"] == 5
    assert calls == [6, 6]
    assert state_of(context) == before
    event = next(e for e in result["events"] if e["type"] == "keeper_roll_resolved")
    assert event["audience"] == {"kind": visibility}
    assert event["payload"]["command_id"] == "host-dice"
    assert event["payload"]["visibility"] == visibility
    _event_validator().validate({k: v for k, v in event.items() if k != "audience"})
    assert service.session_snapshot(world_id=context.world_id, principal=KEEPER)[
        "keeper_rolls"
    ] == [event["payload"]]
    for principal in (PLAYER, Principal(kind="viewer", user_id="u-bob")):
        visible = service.session_snapshot(world_id=context.world_id, principal=principal)[
            "keeper_rolls"
        ]
        assert visible == ([event["payload"]] if visibility == "public" else [])
        replay = service.replay_events(
            world_id=context.world_id, principal=principal, after_sequence=0
        )
        assert any(e["type"] == "keeper_roll_resolved" for e in replay) is (visibility == "public")
    assert roll(context, service, payload=payload)["deduplicated"] is True
    assert calls == [6, 6]
    assert state_of(context) == before


@pytest.mark.parametrize(
    "principal",
    [
        PLAYER,
        Principal(kind="viewer", user_id="u-bob"),
        Principal(kind="keeper", user_id="u-alice"),
        Principal(kind="agent", run_id="agent-test"),
    ],
)
def test_player_owner_viewer_and_agent_cannot_use_keeper_dice(dice_world, principal):
    context, service, calls = dice_world
    if principal.kind == "keeper" and principal.user_id == "u-alice":
        with session_scope(context.database_url) as session:
            member = session.scalar(
                select(WorldMember).where(
                    WorldMember.world_id == context.world_id, WorldMember.user_id == "u-alice"
                )
            )
            member.role = "owner"
    if principal.kind == "agent":
        with session_scope(context.database_url) as session:
            bind_agent_control(session, context.world_id, "agent-test")
    before = state_of(context)
    with pytest.raises(StructuredError):
        roll(context, service, principal=principal)
    assert calls == []
    assert state_of(context) == before


def test_private_replay_checks_fresh_authority_and_original_operator(dice_world):
    context, service, calls = dice_world
    roll(context, service)
    with session_scope(context.database_url) as session:
        member = session.scalar(
            select(WorldMember).where(
                WorldMember.world_id == context.world_id, WorldMember.user_id == "u-keeper"
            )
        )
        member.can_keeper = False
    with pytest.raises(StructuredError) as error:
        roll(context, service)
    assert error.value.code == "keeper_required"
    with session_scope(context.database_url) as session:
        member = session.scalar(
            select(WorldMember).where(
                WorldMember.world_id == context.world_id, WorldMember.user_id == "u-bob"
            )
        )
        member.can_keeper = True
        control = session.get(KeeperControl, context.world_id)
        control.controller_kind, control.controller_id = "human", "u-bob"
    with pytest.raises(StructuredError) as error:
        roll(context, service, principal=Principal(kind="keeper", user_id="u-bob"))
    assert error.value.code == "not_authorized"
    assert calls == [100]


def test_changed_payload_or_linked_player_action_cannot_roll_again(dice_world):
    context, service, calls = dice_world
    roll(context, service)
    with pytest.raises(StructuredError) as error:
        roll(context, service, payload={"spec": "2d6"})
    assert error.value.code == "duplicate_request_conflict"
    with pytest.raises(StructuredError):
        service.execute_command(
            world_id=context.world_id,
            principal=KEEPER,
            kind="keeper_roll",
            command_id="linked",
            payload={"spec": "1d100"},
            cause_id="player-action",
            expected_revision=None,
        )
    assert calls == [100]


def test_replay_is_independent_of_keepers_changed_character_claim(dice_world):
    context, service, calls = dice_world
    roll(context, service)
    same_keeper = Principal(kind="keeper", user_id="u-keeper", investigator_ids=("inv-alice",))
    assert roll(context, service, principal=same_keeper)["deduplicated"] is True
    assert calls == [100]


def test_assisted_draft_cannot_roll_on_behalf_of_a_human(dice_world):
    context, service, calls = dice_world
    draft = service.create_keeper_draft(
        world_id=context.world_id,
        summary="建议随机",
        narration="",
        proposed_commands=[{"kind": "keeper_roll", "payload": {"spec": "1d100"}}],
    )
    before = state_of(context)
    with pytest.raises(StructuredError):
        service.execute_command(
            world_id=context.world_id,
            principal=KEEPER,
            kind="resolve_draft",
            command_id="approve",
            payload={"draft_id": draft["draft_id"], "decision": "approved"},
            expected_revision=None,
        )
    assert calls == []
    assert state_of(context) == before


def test_postgame_dice_are_still_effect_free(dice_world):
    context, service, calls = dice_world
    with session_scope(context.database_url) as session:
        row = session.get(WorldState, context.world_id)
        state = copy.deepcopy(row.state)
        state["game_over"] = {
            "ending_id": "finished",
            "ending_type": "neutral",
            "title": "已结案",
            "summary": "结束。",
        }
        row.state = state
    before = state_of(context)
    roll(context, service)
    assert calls == [100]
    assert state_of(context) == before


@pytest.mark.parametrize(
    "payload",
    [
        {"spec": "0d6"},
        {"spec": "11d6"},
        {"spec": "1d101"},
        {"spec": "1d1"},
        {"spec": "1d6; rm -rf /"},
        {"spec": "1d6", "visibility": "investigators"},
        {"spec": "1d6", "investigator_id": "inv-alice"},
        {"spec": "1d6", "damage": 10},
    ],
)
def test_invalid_or_effect_injecting_payload_never_rolls(dice_world, payload):
    context, service, calls = dice_world
    before = state_of(context)
    with pytest.raises(StructuredError):
        roll(context, service, payload=payload)
    assert calls == []
    assert state_of(context) == before


def test_restore_removes_same_revision_future_roll_and_replay_cannot_resurrect_it(dice_world):
    context, service, calls = dice_world
    roll(context, service, "past")
    save_game([], "slot_005", context=context)
    roll(context, service, "future")
    restore_structured_save(context, "slot_005")
    receipts = service.session_snapshot(world_id=context.world_id, principal=KEEPER)["keeper_rolls"]
    assert [r["command_id"] for r in receipts] == ["past"]
    with pytest.raises(StructuredError) as error:
        roll(context, service, "future")
    assert error.value.code == "stale_target"
    assert calls == [100, 100]


def test_new_branch_has_no_dice_history_and_does_not_roll_parent(dice_world, tmp_path):
    context, service, calls = dice_world
    roll(context, service, "parent")
    before = state_of(context)
    branch = create_structured_branch(context, project_root=tmp_path, runtime_root=tmp_path)
    assert (
        service.session_snapshot(world_id=branch.context.world_id, principal=KEEPER)["keeper_rolls"]
        == []
    )
    with pytest.raises(StructuredError):
        roll(branch.context, service, "parent")
    assert calls == [100]
    roll(branch.context, service, "branch")
    assert calls == [100, 100]
    assert state_of(context) == before


def test_unrelated_skill_check_remains_pending(dice_world):
    context, service, calls = dice_world
    service.execute_command(
        world_id=context.world_id,
        principal=KEEPER,
        kind="request_check",
        command_id="check",
        payload={
            "investigator_id": "inv-alice",
            "skill": "说服",
            "difficulty": "regular",
            "attempt": "说服看守",
            "visibility": "public",
        },
        expected_revision=None,
    )
    pending = service.session_snapshot(world_id=context.world_id, principal=KEEPER)[
        "pending_checks"
    ]
    before = state_of(context)
    roll(context, service)
    assert (
        service.session_snapshot(world_id=context.world_id, principal=KEEPER)["pending_checks"]
        == pending
    )
    assert state_of(context) == before
    assert calls == [100]


def test_outbox_failure_leaves_no_committed_dice_record(dice_world, monkeypatch):
    context, service, _calls = dice_world
    before = state_of(context)

    def fail(*args, **kwargs):
        raise RuntimeError("outbox failed")

    monkeypatch.setattr(service, "_append_events", fail)
    with pytest.raises(RuntimeError, match="outbox failed"):
        roll(context, service)
    assert state_of(context) == before
    with session_scope(context.database_url) as session:
        assert (
            session.scalar(
                select(GameCommand).where(
                    GameCommand.world_id == context.world_id, GameCommand.kind == "keeper_roll"
                )
            )
            is None
        )
