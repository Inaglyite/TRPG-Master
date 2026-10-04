"""Archive extraction has no transport, model, or state-writing side effects."""

import copy

from sqlalchemy import func, select
from test_structured_action_history import submit
from test_structured_material_library import library as library
from test_structured_message_history import publish

from src.storage.database import EventOutbox, WorldState, session_scope
from src.structured.history_archive_capture import capture_local_history
from src.structured.principal import Principal


def test_capture_preserves_recipient_constraints_and_complete_declaration(library):
    context, _, service = library
    public = {"kind": "public"}
    private = {"kind": "investigators", "investigator_ids": ["inv-alice"]}
    publish(context, service, "public", "公开内容", public)
    publish(context, service, "private", "甲的私信", private)
    text = "这是尚未执行的自由申报。" * 50
    submit(context, service, "action", text)
    with session_scope(context.database_url) as session:
        row = session.get(WorldState, context.world_id)
        before = copy.deepcopy(row.state)
        revision = row.revision
        count = session.query(EventOutbox).count()
        sequence = session.scalar(select(func.max(EventOutbox.sequence)))
        entries = capture_local_history(session, context.world_id, before, sequence)
        assert [entry["message"]["text"] for entry in entries] == ["公开内容", "甲的私信", text]
        assert entries[0]["audience"] == public
        assert entries[1]["audience"] == private
        assert entries[2]["message"]["entry_kind"] == "action_request"
        assert entries[2]["submitted_by"] == "u-alice"
        assert session.query(EventOutbox).count() == count
        assert row.revision == revision and row.state == before


def test_capture_excludes_parent_events_after_the_frozen_cursor(library):
    context, _, service = library
    publish(context, service, "before", "共同过去", {"kind": "public"})
    with session_scope(context.database_url) as session:
        state = copy.deepcopy(session.get(WorldState, context.world_id).state)
        cursor = session.scalar(select(func.max(EventOutbox.sequence)))
    publish(context, service, "after", "不应继承的未来", {"kind": "public"})
    with session_scope(context.database_url) as session:
        entries = capture_local_history(session, context.world_id, state, cursor)
    assert [entry["message"]["text"] for entry in entries] == ["共同过去"]


def test_capture_all_history_across_pages_without_truncation(library):
    context, _, service = library
    for index in range(53):
        submit(context, service, f"page-{index}", f"申报 {index}")
    with session_scope(context.database_url) as session:
        state = session.get(WorldState, context.world_id).state
        cursor = session.scalar(select(func.max(EventOutbox.sequence)))
        entries = capture_local_history(session, context.world_id, state, cursor)
    assert [entry["message"]["text"] for entry in entries] == [f"申报 {i}" for i in range(53)]
    assert len({entry["source_key"] for entry in entries}) == 53


def test_capture_does_not_turn_commands_or_dice_into_readable_replay(library):
    context, _, service = library
    service.execute_command(
        world_id=context.world_id,
        principal=Principal(kind="keeper", user_id="u-keeper"),
        kind="adjust_stat",
        command_id="stat-only",
        expected_revision=None,
        payload={"investigator_id": "inv-alice", "field": "san", "delta": -1, "reason": "目击"},
    )
    service.submit_free_roll(
        world_id=context.world_id,
        principal=Principal(kind="player", user_id="u-alice", investigator_ids=("inv-alice",)),
        request={"request_id": "roll-only", "investigator_id": "inv-alice", "spec": "1d100"},
    )
    with session_scope(context.database_url) as session:
        row = session.get(WorldState, context.world_id)
        sequence = session.scalar(select(func.max(EventOutbox.sequence)))
        count = session.query(EventOutbox).count()
        assert count > 0
        before = copy.deepcopy(row.state)
        assert capture_local_history(session, context.world_id, row.state, sequence) == []
        assert row.state == before
        assert session.query(EventOutbox).count() == count
