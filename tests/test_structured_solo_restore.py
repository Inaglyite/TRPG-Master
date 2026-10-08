"""Isolated service-layer restoration controls; not browser/UI acceptance."""

import copy
import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import jsonschema
import pytest
from sqlalchemy import select
from test_structured_commands import make_structured_world

from src.storage.database import (
    GameCommand,
    KeeperControl,
    PlayerRequest,
    World,
    WorldInvestigator,
    WorldMember,
    WorldState,
    session_scope,
)
from src.storage.database_store import StaleRevisionError
from src.storage.persistence import delete_save, save_game
from src.structured.branch import restore_structured_save
from src.structured.errors import StructuredError
from src.structured.principal import Principal, bind_agent_control
from src.structured.restore_receipts import OwnerRestoreRequest
from src.structured.service import StructuredPlayService


@pytest.fixture
def solo(tmp_path):
    context = make_structured_world(tmp_path)
    url = context.database_url
    with session_scope(url) as session:
        world = session.get(World, "sp-world")
        world.metadata_json = {**world.metadata_json, "play_mode": "solo"}
        for member in session.scalars(
            select(WorldMember).where(WorldMember.world_id == "sp-world")
        ):
            if member.user_id != "u-owner":
                session.delete(member)
            else:
                member.can_keeper = True
        alice = session.get(WorldInvestigator, "wi-alice")
        alice.controller_user_id = "u-owner"
        session.get(WorldInvestigator, "wi-bob").status = "released"
        row = session.get(WorldState, "sp-world")
        state = copy.deepcopy(row.state)
        state["investigators"].pop("inv-bob")
        state["pc"] = copy.deepcopy(state["investigators"]["inv-alice"])
        state["active_investigator_id"] = "inv-alice"
        state["investigator_controllers"] = {"u-owner": "inv-alice"}
        row.state = state
    context.world_store.invalidate_cache()
    service = StructuredPlayService(url)
    counter = 0

    def execute(kind, payload, principal=None):
        nonlocal counter
        counter += 1
        principal = principal or Principal(kind="keeper", user_id="u-owner")
        return service.execute_command(
            world_id="sp-world",
            principal=principal,
            kind=kind,
            payload=payload,
            command_id=f"solo-test-{counter}",
            expected_revision=None,
        )

    def snapshot():
        with session_scope(url) as session:
            row = session.get(WorldState, "sp-world")
            control = session.get(KeeperControl, "sp-world")
            requests = session.scalars(
                select(PlayerRequest).where(PlayerRequest.world_id == "sp-world")
            ).all()
            return (
                copy.deepcopy(row.state),
                row.revision,
                (control.epoch, control.controller_kind, control.controller_id)
                if control
                else None,
                [(r.request_id, r.status) for r in requests],
            )

    return context, service, execute, snapshot


def request(solo, **over):
    return OwnerRestoreRequest(
        user_id="u-owner",
        action_id="restore-once",
        slot_id="slot_001",
        expected_revision=solo[3]()[1],
        **over,
    )


def test_solo_restores_exact_checkpoint_and_reconciles_open_requests(solo):
    context, service, execute, snapshot = solo
    save_game([], "slot_001", context=context)
    saved = snapshot()[0]
    execute(
        "adjust_stat",
        {"investigator_id": "inv-alice", "field": "hp", "delta": -3, "reason": "已结算的伤害"},
    )
    service.submit_action_request(
        world_id="sp-world",
        principal=Principal(kind="player", user_id="u-owner", investigator_ids=("inv-alice",)),
        request={
            "request_id": "discarded-intent",
            "investigator_id": "inv-alice",
            "action": {"kind": "freeform", "text": "稍后的行动"},
        },
    )
    req = request(solo)
    result = restore_structured_save(context, req.slot_id, owner_restore=req)
    state, revision, _, pending = snapshot()
    assert result == {"slot_id": "slot_001", "revision": revision}
    assert state["pc"]["hp"] == saved["pc"]["hp"]
    assert state["pc"]["inventory"] == saved["pc"]["inventory"]
    assert pending == [("discarded-intent", "failed")]
    with session_scope(context.database_url) as session:
        receipt = session.scalar(
            select(GameCommand).where(GameCommand.command_id == req.command_id)
        )
        assert receipt.principal == {"kind": "room_owner", "user_id": "u-owner"}
        assert receipt.payload == req.payload and receipt.result == result


def test_replay_does_not_rewind_later_progress_even_after_checkpoint_deleted(solo):
    context, _, execute, snapshot = solo
    save_game([], "slot_001", context=context)
    req = request(solo)
    restore_structured_save(context, req.slot_id, owner_restore=req)
    execute(
        "adjust_stat",
        {"investigator_id": "inv-alice", "field": "hp", "delta": -2, "reason": "读档之后的新伤害"},
    )
    delete_save(req.slot_id, context=context)
    before = snapshot()
    assert restore_structured_save(context, req.slot_id, owner_restore=req)["deduplicated"]
    assert snapshot() == before
    with pytest.raises(StructuredError, match="不同内容"):
        restore_structured_save(
            context,
            req.slot_id,
            owner_restore=replace(req, expected_revision=req.expected_revision + 1),
        )
    assert snapshot() == before


def test_restore_receipt_failure_rolls_back_state_requests_and_epoch(solo):
    context, _, execute, snapshot = solo
    save_game([], "slot_001", context=context)
    execute(
        "adjust_stat",
        {"investigator_id": "inv-alice", "field": "hp", "delta": -2, "reason": "伤害"},
    )
    req = request(solo)
    before = snapshot()
    with patch(
        "src.structured.branch.record_restore_receipt", side_effect=RuntimeError("receipt failed")
    ):
        with pytest.raises(RuntimeError, match="receipt failed"):
            restore_structured_save(context, req.slot_id, owner_restore=req)
    assert snapshot() == before
    with session_scope(context.database_url) as session:
        assert (
            session.scalar(select(GameCommand).where(GameCommand.command_id == req.command_id))
            is None
        )


def test_same_revision_restore_revokes_a_late_agent_instance(solo):
    context, _, execute, snapshot = solo
    with session_scope(context.database_url) as session:
        bind_agent_control(session, "sp-world", "old-run")
    save_game([], "slot_001", context=context)
    saved_revision = snapshot()[1]
    epoch = snapshot()[2][0]
    execute(
        "advance_time",
        {"minutes": 1, "reason": "将被回滚"},
        Principal(kind="agent", run_id="old-run"),
    )
    req = request(solo)
    restore_structured_save(context, req.slot_id, owner_restore=req)
    after = snapshot()
    assert after[1] == saved_revision and after[2] == (epoch + 1, "none", "")
    with pytest.raises(StructuredError, match="不再受理"):
        execute(
            "advance_time",
            {"minutes": 1, "reason": "旧实例迟到"},
            Principal(kind="agent", run_id="old-run"),
        )
    assert snapshot() == after


def test_restore_capability_matches_strict_wire_schema(solo):
    """Real capability projection, not a synthetic permissive snapshot stub."""
    context, service, _, _ = solo
    schema = json.loads(
        (Path(__file__).resolve().parents[1] / "schemas/structured-play/v1/events.json").read_text(
            encoding="utf-8"
        )
    )
    validator = jsonschema.Draft202012Validator(schema["$defs"]["server_capabilities"])
    for mode, expected in (("solo", True), ("multiplayer", False)):
        with session_scope(context.database_url) as session:
            world = session.get(World, context.world_id)
            world.metadata_json = {**world.metadata_json, "play_mode": mode}
        capabilities = service.session_snapshot(
            world_id=context.world_id,
            principal=Principal(kind="keeper", user_id="u-owner"),
        )["server_capabilities"]
        assert capabilities["structured_solo_restore"] is expected
        validator.validate(capabilities)
        malformed = {**capabilities, "structured_solo_restore": 1}
        assert list(validator.iter_errors(malformed))
        assert list(validator.iter_errors({**capabilities, "invented_capability": True}))


@pytest.mark.parametrize("expected", [True, False, "0", 0.0, -1, None])
def test_revision_is_not_coerced(expected):
    with pytest.raises(StructuredError):
        OwnerRestoreRequest("u-owner", "r", "slot_001", expected)


def test_stale_revision_refuses_all_changes(solo):
    context, _, execute, snapshot = solo
    save_game([], "slot_001", context=context)
    req = request(solo)
    execute("advance_time", {"minutes": 1, "reason": "竞争中的新进度"})
    before = snapshot()
    with pytest.raises(StaleRevisionError):
        restore_structured_save(context, req.slot_id, owner_restore=req)
    assert snapshot() == before


@pytest.mark.parametrize(
    "fault", ["multi", "legacy", "archived", "revoked", "extra_member", "changed_claim"]
)
def test_owner_rechecked_before_private_replay(solo, fault):
    context, _, _, snapshot = solo
    save_game([], "slot_001", context=context)
    req = request(solo)
    restore_structured_save(context, req.slot_id, owner_restore=req)
    with session_scope(context.database_url) as session:
        world = session.get(World, "sp-world")
        if fault in {"multi", "legacy"}:
            key, value = (
                ("play_mode", "room") if fault == "multi" else ("execution_profile", "legacy")
            )
            world.metadata_json = {**world.metadata_json, key: value}
        elif fault == "archived":
            world.status = "archived"
        elif fault == "revoked":
            session.get(WorldMember, "m-owner").role = "player"
        elif fault == "extra_member":
            session.add(
                WorldMember(
                    id="extra-member", world_id="sp-world", user_id="u-alice", role="viewer"
                )
            )
        else:
            session.get(WorldInvestigator, "wi-alice").controller_user_id = "u-alice"
    before = snapshot()
    with pytest.raises(StructuredError):
        restore_structured_save(context, req.slot_id, owner_restore=req)
    assert snapshot() == before


def test_authority_changes_during_checkpoint_read_are_rejected_at_commit(solo):
    context, _, _, snapshot = solo
    save_game([], "slot_001", context=context)
    req = request(solo)
    from src.structured import branch

    original = branch.load_game_artifacts

    def revoke(*args, **kwargs):
        result = original(*args, **kwargs)
        with session_scope(context.database_url) as session:
            session.get(WorldMember, "m-owner").role = "player"
        return result

    before = snapshot()
    with patch.object(branch, "load_game_artifacts", side_effect=revoke):
        with pytest.raises(StructuredError, match="唯一房主"):
            restore_structured_save(context, req.slot_id, owner_restore=req)
    assert snapshot() == before


def test_pre_roster_checkpoint_is_not_silently_materialized_or_healed(solo):
    context, _, _, snapshot = solo
    original = snapshot()[0]
    with session_scope(context.database_url) as session:
        row = session.get(WorldState, "sp-world")
        row.state = {**copy.deepcopy(row.state), "active_investigator_id": ""}
    context.world_store.invalidate_cache()
    save_game([], "slot_001", context=context)
    with session_scope(context.database_url) as session:
        session.get(WorldState, "sp-world").state = original
    context.world_store.invalidate_cache()
    before = snapshot()
    with pytest.raises(StructuredError, match="早于调查员就绪"):
        restore_structured_save(context, "slot_001", owner_restore=request(solo))
    assert snapshot() == before
