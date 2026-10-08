"""Ending closes outstanding work atomically, without inventing success or dice."""

import copy
from unittest.mock import patch

import pytest
from sqlalchemy import select
from test_structured_combat_transactions import battle as battle

from src.storage.database import CheckRequest, InteractionThread, PlayerRequest, session_scope
from src.structured.errors import StructuredError
from src.structured.principal import Principal


@pytest.fixture
def closing_case(battle):
    url, service, alice, execute, snapshot, draws = battle
    execute("combat_end", {"reason": "准备结案"})
    for request_id, action in [
        ("wait-move", {"kind": "move", "destination_scene_id": "library"}),
        ("finished-before", {"kind": "freeform", "text": "我已经记录观察。"}),
    ]:
        service.submit_action_request(
            world_id="sp-world",
            principal=alice,
            request={"request_id": request_id, "investigator_id": "inv-alice", "action": action},
        )
    execute(
        "resolve_intent",
        {
            "request_id": "finished-before",
            "resolution": "completed",
            "outcome": "success",
            "note": "之前已落实",
        },
    )
    execute(
        "resolve_intent",
        {
            "request_id": "wait-move",
            "resolution": "awaiting_player",
            "outcome": "not_executed",
            "note": "等回应",
        },
    )
    service.create_keeper_draft(world_id="sp-world", summary="未批准的建议", proposed_commands=[])
    check = execute(
        "request_check",
        {
            "investigator_id": "inv-alice",
            "skill": "斗殴",
            "attempt": "尚未掷骰的观察",
            "visibility": "keeper",
            "related_request_id": "wait-move",
        },
    )["result"]["check_request_id"]
    return battle, check


def work(url):
    with session_scope(url) as session:
        return (
            [
                (r.request_id, r.status, r.outcome, copy.deepcopy(r.payload), r.detail)
                for r in session.scalars(select(PlayerRequest).order_by(PlayerRequest.id)).all()
            ],
            [
                (r.check_request_id, r.status, copy.deepcopy(r.result))
                for r in session.scalars(select(CheckRequest)).all()
            ],
            [
                (r.thread_id, r.status, r.updated_revision, r.note)
                for r in session.scalars(select(InteractionThread)).all()
            ],
        )


def test_ending_cancels_remaining_work_and_retains_completed_receipts(closing_case):
    (url, service, alice, execute, snapshot, draws), check = closing_case
    completed = next(r for r in work(url)[0] if r[0] == "finished-before")
    result = execute(
        "end_game", {"ending_type": "neutral", "title": "离开调查"}, command_id="close-once"
    )
    requests, checks, threads = work(url)
    assert next(r for r in requests if r[0] == "finished-before") == completed
    pending = next(r for r in requests if r[0] == "wait-move")
    assert pending[1:3] == ("cancelled", "")
    assert "awaiting" not in pending[3]
    assert "此前已提交" in pending[4]
    assert all(r[1] in {"completed", "cancelled"} for r in requests)
    assert checks == [(check, "declined", {})]
    assert threads and all(r[1] == "cancelled" for r in threads)
    assert all(r[2] == result["revision"] for r in threads)
    assert draws == []
    cancellation = [e for e in result["events"] if e["type"] == "check_cancelled"]
    assert len(cancellation) == 1 and cancellation[0]["audience"] == {"kind": "keeper"}
    assert "roll" not in cancellation[0]["payload"]
    statuses = [
        e
        for e in result["events"]
        if e["type"] == "action_status" and e["payload"]["request_id"] == "wait-move"
    ]
    assert len(statuses) == 1
    assert statuses[0]["audience"] == {"kind": "investigators", "investigator_ids": ["inv-alice"]}
    assert "outcome" not in statuses[0]["payload"]
    keeper = Principal(kind="keeper", user_id="u-keeper")
    for principal in [alice, keeper]:
        restored = service.session_snapshot(world_id="sp-world", principal=principal)
        assert restored["requests"] == []
        assert restored["pending_checks"] == []
        assert not any(t["status"] == "open" for t in restored["interactions"])
    before = (snapshot(), work(url))
    assert execute(
        "end_game", {"ending_type": "neutral", "title": "离开调查"}, command_id="close-once"
    )["deduplicated"]
    assert (snapshot(), work(url)) == before
    with pytest.raises(StructuredError, match="已结算"):
        service.create_keeper_draft(world_id="sp-world", summary="过期建议", proposed_commands=[])
    assert (snapshot(), work(url)) == before


def test_ending_storage_failure_rolls_back_rewards_and_all_work(closing_case):
    (url, service, _, execute, snapshot, draws), _ = closing_case
    before = (snapshot(), work(url))
    with patch.object(service, "_append_events", side_effect=RuntimeError("end outbox failure")):
        with pytest.raises(RuntimeError, match="outbox failure"):
            execute("end_game", {"ending_type": "neutral", "title": "离开调查"})
    assert (snapshot(), work(url)) == before
    assert draws == []
