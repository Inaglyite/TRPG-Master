"""Accepted freeform history is an action declaration, never public narration."""

import copy

from test_structured_material_library import library as library

from src.storage.database import EventOutbox, PlayerRequest, WorldMember, WorldState, session_scope
from src.structured.gateway import StructuredGateway
from src.structured.principal import Principal
from src.structured.validation import validate_event
from src.web.structured_history_http import read_message_history


def submit(context, service, request_id, text):
    with session_scope(context.database_url) as session:
        revision = session.get(WorldState, context.world_id).revision
    return service.submit_action_request(
        world_id=context.world_id,
        principal=Principal(kind="player", user_id="u-alice", investigator_ids=("inv-alice",)),
        request={
            "type": "action_request",
            "protocol_version": 1,
            "world_id": context.world_id,
            "request_id": request_id,
            "investigator_id": "inv-alice",
            "expected_revision": revision,
            "action": {"kind": "freeform", "text": text},
        },
    )


def history(context, user_id):
    return StructuredGateway(context.database_url).snapshot_envelope(
        world_id=context.world_id, user_id=user_id
    )["payload"]["message_history"]["messages"]


def test_complete_action_text_recovers_for_author_and_keeper_not_other_players(library):
    context, _, service = library
    text = "我想私下检查抽屉，但尚未打开它。" + "这是行动申报而不是已发生的事实。" * 30
    submit(context, service, "long-intent", text)
    for user_id in ("u-alice", "u-keeper"):
        records = history(context, user_id)
        assert [record["text"] for record in records] == [text]
        assert records[0]["entry_kind"] == "action_request"
        assert records[0]["speaker"]["kind"] == "investigator"
        assert records[0]["speaker"]["id"] == "inv-alice"
    assert history(context, "u-bob") == []


def test_terminal_requests_still_recover_and_reads_do_not_mutate_world(library):
    context, _, service = library
    submit(context, service, "finished", "我申报一项行动，最后没有执行。")
    with session_scope(context.database_url) as session:
        request = session.query(PlayerRequest).filter_by(request_id="finished").one()
        request.status = "completed"
        request.outcome = "not_executed"
        before = copy.deepcopy(session.get(WorldState, context.world_id).state)
        revision = session.get(WorldState, context.world_id).revision
        events = session.query(EventOutbox).count()
    records = history(context, "u-alice")
    assert len(records) == 1
    assert records[0]["entry_kind"] == "action_request"
    with session_scope(context.database_url) as session:
        assert session.get(WorldState, context.world_id).state == before
        assert session.get(WorldState, context.world_id).revision == revision
        assert session.query(EventOutbox).count() == events


def test_failed_same_id_resubmission_has_one_stable_history_entry(library):
    context, _, service = library
    submit(context, service, "retry-intent", "这是同一次申报。")
    with session_scope(context.database_url) as session:
        row = session.query(PlayerRequest).filter_by(request_id="retry-intent").one()
        stable_id = row.id
        row.status = "failed"
    submit(context, service, "retry-intent", "这是同一次申报。")
    records = history(context, "u-alice")
    assert len(records) == 1
    assert records[0]["message_id"] == f"action:{stable_id}"


def test_old_author_as_viewer_cannot_read_private_action_text(library):
    context, _, service = library
    submit(context, service, "before-viewer", "只申报给守秘人的秘密打算。")
    with session_scope(context.database_url) as session:
        member = session.query(WorldMember).filter_by(user_id="u-alice").one()
        member.role = "viewer"
        member.can_keeper = False
    assert history(context, "u-alice") == []


def test_revoked_keeper_cannot_keep_reading_other_players_private_actions(library):
    context, _, service = library
    submit(context, service, "before-revoke", "不能向无主持授权的房主公开。")
    with session_scope(context.database_url) as session:
        member = session.query(WorldMember).filter_by(user_id="u-keeper").one()
        member.can_keeper = False
    assert history(context, "u-keeper") == []


def test_retry_deduplicates_across_chronological_history_pages(library):
    context, deps, service = library
    for index in range(53):
        submit(context, service, f"paged-{index}", f"申报 {index}")
    with session_scope(context.database_url) as session:
        session.query(PlayerRequest).filter_by(request_id="paged-0").one().status = "failed"
    submit(context, service, "paged-0", "申报 0")
    snapshot = StructuredGateway(context.database_url).snapshot_envelope(
        world_id=context.world_id, user_id="u-alice"
    )
    validate_event(snapshot)
    recent = snapshot["payload"]["message_history"]
    assert [entry["text"] for entry in recent["messages"]] == [f"申报 {i}" for i in range(3, 53)]
    older = read_message_history(deps, context.world_id, "u-alice", recent["next_before_sequence"])
    assert [entry["text"] for entry in older["messages"]] == ["申报 0", "申报 1", "申报 2"]
    assert older["next_before_sequence"] is None
    assert not {entry["message_id"] for entry in older["messages"]} & {
        entry["message_id"] for entry in recent["messages"]
    }


def test_request_without_surviving_acceptance_does_not_reappear_after_restore(library):
    context, _, service = library
    submit(context, service, "future-input", "保存点之后的申报")
    with session_scope(context.database_url) as session:
        session.query(EventOutbox).filter_by(cause_request_id="future-input").delete()
    assert history(context, "u-alice") == []
