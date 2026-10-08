"""Ordinary dice frequency is independent of game time, world and retry ID."""

import asyncio
import copy
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import select
from test_structured_commands import make_structured_world
from test_structured_ws import _event_validator

from src.storage.database import (
    EventOutbox,
    GameCommand,
    PlayerRequest,
    WorldMember,
    WorldState,
    session_scope,
)
from src.storage.persistence import save_game
from src.structured import service as service_module
from src.structured.branch import create_structured_branch, restore_structured_save
from src.structured.errors import StructuredError
from src.structured.gateway import StructuredGateway
from src.structured.principal import Principal, current_control
from src.structured.rate_limits import OrdinaryDiceRateLimiter
from src.structured.service import StructuredPlayService

KEEPER = Principal(kind="keeper", user_id="u-keeper")
PLAYER = Principal(kind="player", user_id="u-alice", investigator_ids=("inv-alice",))


@pytest.fixture
def rate_world(tmp_path, monkeypatch):
    context = make_structured_world(tmp_path)
    clock = [100.0]
    limiter = OrdinaryDiceRateLimiter(clock=lambda: clock[0])
    monkeypatch.setenv("TRPG_ORDINARY_ROLLS_PER_MINUTE", "2")
    monkeypatch.setattr(service_module, "ORDINARY_DICE_RATE_LIMITER", limiter)
    calls = []
    service = StructuredPlayService(
        context.database_url, rng=lambda sides: calls.append(sides) or 0
    )
    service.session_snapshot(world_id=context.world_id, principal=KEEPER)
    return context, service, calls, clock, limiter


def keeper_roll(context, service, command_id, *, principal=KEEPER, spec="1d6", revision=None):
    return service.execute_command(
        world_id=context.world_id,
        principal=principal,
        kind="keeper_roll",
        command_id=command_id,
        payload={"spec": spec},
        expected_revision=revision,
    )


def free_roll(context, service, request_id, *, principal=PLAYER, spec="1d6"):
    return service.submit_free_roll(
        world_id=context.world_id,
        principal=principal,
        request={"request_id": request_id, "investigator_id": "inv-alice", "spec": spec},
    )


def world_state(context):
    with session_scope(context.database_url) as session:
        row = session.get(WorldState, context.world_id)
        return copy.deepcopy(row.state), row.revision


@pytest.mark.parametrize("roll", [keeper_roll, free_roll])
def test_denial_has_no_rng_state_receipt_or_ledger_and_same_id_can_retry(rate_world, roll):
    context, service, calls, clock, _ = rate_world
    before = world_state(context)
    roll(context, service, "one")
    roll(context, service, "two")
    with pytest.raises(StructuredError) as error:
        roll(context, service, "three")
    assert error.value.code == "rate_limited"
    assert error.value.retryable is True
    assert "60" in error.value.message
    assert calls == [6, 6]
    assert world_state(context) == before
    with session_scope(context.database_url) as session:
        assert session.scalar(select(GameCommand).where(GameCommand.command_id == "three")) is None
        assert (
            session.scalar(select(PlayerRequest).where(PlayerRequest.request_id == "three")) is None
        )
        assert (
            session.scalar(select(EventOutbox).where(EventOutbox.cause_request_id == "three"))
            is None
        )
    assert roll(context, service, "one")["deduplicated"] is True
    assert calls == [6, 6]
    clock[0] += 59.9
    with pytest.raises(StructuredError, match="1 秒"):
        roll(context, service, "three")
    clock[0] += 0.1
    assert roll(context, service, "three")["result"]["total"] == 1
    assert calls == [6, 6, 6]
    assert world_state(context) == before


def test_same_account_player_and_keeper_share_budget_but_other_player_does_not(rate_world):
    context, service, calls, _, _ = rate_world
    with session_scope(context.database_url) as session:
        member = session.scalar(select(WorldMember).where(WorldMember.user_id == "u-alice"))
        member.can_keeper = True
        current_control(session, context.world_id).controller_id = "u-alice"
    free_roll(context, service, "alice-player")
    keeper_roll(
        context, service, "alice-keeper", principal=Principal(kind="keeper", user_id="u-alice")
    )
    with pytest.raises(StructuredError) as error:
        free_roll(context, service, "alice-again")
    assert error.value.code == "rate_limited"
    bob = Principal(kind="player", user_id="u-bob", investigator_ids=("inv-bob",))
    service.submit_free_roll(
        world_id=context.world_id,
        principal=bob,
        request={"request_id": "bob", "investigator_id": "inv-bob", "spec": "1d6"},
    )
    assert calls == [6, 6, 6]


def test_new_service_world_branch_restore_and_game_clock_do_not_reset_budget(rate_world, tmp_path):
    context, service, calls, _, _ = rate_world
    save_game([], "slot_006", context=context)
    keeper_roll(context, service, "one")
    keeper_roll(context, service, "two")
    # Time commands are not ordinary rolls and do not expire the wall-clock guard.
    service.execute_command(
        world_id=context.world_id,
        principal=KEEPER,
        kind="advance_time",
        command_id="days",
        payload={"minutes": 2880, "reason": "观察"},
        expected_revision=None,
    )
    branch = create_structured_branch(context, project_root=tmp_path, runtime_root=tmp_path)
    another = StructuredPlayService(
        context.database_url, rng=lambda sides: calls.append(sides) or 0
    )
    for target in (context, branch.context):
        with pytest.raises(StructuredError) as error:
            keeper_roll(target, another, "new-id")
        assert error.value.code == "rate_limited"
    restore_structured_save(context, "slot_006")
    with pytest.raises(StructuredError) as error:
        keeper_roll(context, service, "after-restore")
    assert error.value.code == "rate_limited"
    assert calls == [6, 6]


def test_invalid_unauthorized_conflict_and_replay_do_not_consume_slots(rate_world):
    context, service, calls, _, _ = rate_world
    for spec in ("11d6", "1d101", "garbage"):
        with pytest.raises(StructuredError):
            keeper_roll(context, service, "invalid", spec=spec)
        with pytest.raises(StructuredError):
            free_roll(context, service, "invalid-player", spec=spec)
    with pytest.raises(StructuredError):
        keeper_roll(context, service, "unauthorized", principal=PLAYER)
    with pytest.raises(StructuredError):
        keeper_roll(context, service, "old-revision", revision=999)
    keeper_roll(context, service, "one")
    with pytest.raises(StructuredError):
        keeper_roll(context, service, "one", spec="2d6")
    for _ in range(8):
        assert keeper_roll(context, service, "one")["deduplicated"] is True
    keeper_roll(context, service, "two")
    assert calls == [6, 6]


def test_formal_check_is_not_blocked_by_ordinary_roll_budget(rate_world):
    context, service, calls, _, _ = rate_world
    free_roll(context, service, "one")
    free_roll(context, service, "two")
    result = service.execute_command(
        world_id=context.world_id,
        principal=KEEPER,
        kind="request_check",
        command_id="formal",
        payload={
            "investigator_id": "inv-alice",
            "skill": "说服",
            "difficulty": "regular",
            "attempt": "询问看守",
            "visibility": "public",
        },
        expected_revision=None,
    )
    service.submit_check_response(
        world_id=context.world_id,
        principal=PLAYER,
        request={
            "request_id": "response",
            "check_request_id": result["result"]["check_request_id"],
            "decision": "roll",
        },
    )
    assert len(calls) > 2


def test_gateway_denial_is_private_persisted_and_retries_same_id_after_window(rate_world):
    context, service, calls, clock, _ = rate_world
    keeper_roll(context, service, "one")
    keeper_roll(context, service, "two")
    before = world_state(context)
    gateway = StructuredGateway(context.database_url)
    gateway.service = service
    delivered, broadcast = [], []

    async def deliver(event):
        delivered.append(event)

    async def publish(event):
        broadcast.append(event)

    frame = {
        "type": "command_request",
        "protocol_version": 1,
        "world_id": context.world_id,
        "command_id": "retry-later",
        "expected_revision": before[1],
        "kind": "keeper_roll",
        "payload": {"spec": "1d6"},
    }
    asyncio.run(
        gateway.handle_frame(
            world_id=context.world_id,
            user_id=KEEPER.user_id,
            frame=frame,
            deliver=deliver,
            broadcast=publish,
        )
    )
    assert broadcast == []
    assert len(delivered) == 1
    error = delivered[0]
    assert error["type"] == "request_error"
    assert error["payload"]["code"] == "rate_limited"
    assert error["payload"]["command_id"] == frame["command_id"]
    assert error["payload"]["retryable"] is True
    _event_validator().validate(error)
    assert error["event_id"] > 0
    assert calls == [6, 6]
    assert world_state(context) == before
    clock[0] += 60
    asyncio.run(
        gateway.handle_frame(
            world_id=context.world_id,
            user_id=KEEPER.user_id,
            frame=frame,
            deliver=deliver,
            broadcast=publish,
        )
    )
    assert len(calls) == 3
    assert any(event["type"] == "keeper_roll_resolved" for event in broadcast)
    assert world_state(context) == before


def test_concurrent_last_slot_is_admitted_once(monkeypatch):
    monkeypatch.setenv("TRPG_ORDINARY_ROLLS_PER_MINUTE", "1")
    limiter = OrdinaryDiceRateLimiter(clock=lambda: 100.0)

    def admit(_):
        try:
            limiter.check("isolated-db", "account")
            return True
        except StructuredError as error:
            assert error.code == "rate_limited"
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(admit, range(16))) == 1


def test_bounded_cache_preserves_active_keys_and_reclaims_expired_ones(monkeypatch):
    monkeypatch.setenv("TRPG_ORDINARY_ROLLS_PER_MINUTE", "1")
    clock = [0.0]
    limiter = OrdinaryDiceRateLimiter(clock=lambda: clock[0], max_accounts=1)
    limiter.check("db", "one")
    with pytest.raises(StructuredError):
        limiter.check("db", "two")
    with pytest.raises(StructuredError):
        limiter.check("db", "one")
    clock[0] = 60
    limiter.check("db", "two")
    assert len(limiter._attempts) == 1


@pytest.mark.parametrize("configured", ["invalid", "0", "-3", "100000"])
def test_configuration_is_bounded_and_independent_database_scope(monkeypatch, configured):
    monkeypatch.setenv("TRPG_ORDINARY_ROLLS_PER_MINUTE", configured)
    limiter = OrdinaryDiceRateLimiter(clock=lambda: 0)
    limiter.check("db-one", "account")
    limiter.check("db-two", "account")
    with pytest.raises(StructuredError):
        limiter.check("db-one", "")
    assert all("db-" not in key[0] for key in limiter._attempts)
