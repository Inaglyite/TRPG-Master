"""A branch inherits readable history, not transport events or authority."""

from test_structured_action_history import submit
from test_structured_material_library import library as library
from test_structured_message_history import publish

from src.storage.database import BranchHistoryEntry, EventOutbox, World, WorldMember, session_scope
from src.storage.persistence import save_game
from src.structured.branch import create_structured_branch, restore_structured_save
from src.structured.gateway import StructuredGateway
from src.structured.service import StructuredPlayService
from src.structured.validation import validate_event
from src.web.structured_history_http import read_message_history


def branch(context):
    return create_structured_branch(
        context,
        project_root=context.project_root,
        runtime_root=context.runtime_root,
        user_id="u-alice",
    ).context


def inherited(context, user_id):
    snapshot = StructuredGateway(context.database_url).snapshot_envelope(
        world_id=context.world_id, user_id=user_id
    )
    validate_event(snapshot)
    return snapshot["payload"].get("inherited_message_history", {"messages": []})["messages"]


def test_branch_inherits_only_current_recipient_authorized_history(library):
    context, _, service = library
    publish(context, service, "public", "共同经历", {"kind": "public"})
    publish(
        context,
        service,
        "private",
        "甲的私信",
        {"kind": "investigators", "investigator_ids": ["inv-alice"]},
    )
    publish(context, service, "keeper", "主持幕后", {"kind": "keeper"})
    submit(context, service, "declaration", "甲尚未执行的申报")
    target = branch(context)
    assert [row["text"] for row in inherited(target, "u-alice")] == [
        "共同经历",
        "甲的私信",
        "甲尚未执行的申报",
    ]
    assert [row["text"] for row in inherited(target, "u-bob")] == ["共同经历"]
    assert [row["text"] for row in inherited(target, "u-keeper")] == [
        "共同经历",
        "甲的私信",
        "主持幕后",
        "甲尚未执行的申报",
    ]
    with session_scope(target.database_url) as session:
        assert session.query(EventOutbox).filter_by(world_id=target.world_id).count() == 0


def test_parent_continuation_and_parent_history_removal_do_not_change_branch_archive(library):
    context, _, service = library
    publish(context, service, "before", "分叉之前", {"kind": "public"})
    target = branch(context)
    publish(context, service, "after", "父世界分叉之后的未来", {"kind": "public"})
    with session_scope(context.database_url) as session:
        session.query(EventOutbox).filter_by(world_id=context.world_id).delete()
    assert [row["text"] for row in inherited(target, "u-alice")] == ["分叉之前"]


def test_revoked_branch_keeper_and_old_author_as_viewer_see_only_public_archive(library):
    context, _, service = library
    publish(context, service, "public", "公开往事", {"kind": "public"})
    publish(context, service, "secret", "主持秘密", {"kind": "keeper"})
    submit(context, service, "private-intent", "私下申报")
    target = branch(context)
    with session_scope(target.database_url) as session:
        keeper = (
            session.query(WorldMember).filter_by(world_id=target.world_id, user_id="u-keeper").one()
        )
        keeper.can_keeper = False
        alice = (
            session.query(WorldMember).filter_by(world_id=target.world_id, user_id="u-alice").one()
        )
        alice.role = "viewer"
        alice.can_keeper = False
    for user_id in ("u-keeper", "u-alice"):
        assert [row["text"] for row in inherited(target, user_id)] == ["公开往事"]


def test_nested_branch_keeps_common_history_once_without_parent_future(library):
    context, _, service = library
    publish(context, service, "root-before", "根世界的过去", {"kind": "public"})
    child = branch(context)
    child_service = StructuredPlayService(child.database_url)
    publish(child, child_service, "child-before", "子世界的过去", {"kind": "public"})
    grandchild = branch(child)
    publish(context, service, "root-after", "根世界的未来", {"kind": "public"})
    publish(child, child_service, "child-after", "子世界的未来", {"kind": "public"})
    records = inherited(grandchild, "u-alice")
    assert [row["text"] for row in records] == ["根世界的过去", "子世界的过去"]
    assert len({row["message_id"] for row in records}) == len(records)
    with session_scope(grandchild.database_url) as session:
        assert session.query(EventOutbox).filter_by(world_id=grandchild.world_id).count() == 0


def test_archive_pagination_is_independent_of_live_world_cursor(library):
    context, deps, service = library
    for index in range(53):
        publish(context, service, f"message-{index}", f"共同经历 {index}", {"kind": "public"})
    target = branch(context)
    snapshot = StructuredGateway(target.database_url).snapshot_envelope(
        world_id=target.world_id, user_id="u-alice"
    )
    validate_event(snapshot)
    page = snapshot["payload"]["inherited_message_history"]
    assert [row["text"] for row in page["messages"]] == [f"共同经历 {i}" for i in range(3, 53)]
    assert snapshot["payload"]["cursor"]["sequence"] == 0
    older = read_message_history(
        deps, target.world_id, "u-alice", page["next_before_sequence"], inherited=True
    )
    assert [row["text"] for row in older["messages"]] == [f"共同经历 {i}" for i in range(3)]
    assert older["next_before_sequence"] is None
    assert not {row["message_id"] for row in older["messages"]} & {
        row["message_id"] for row in page["messages"]
    }
    assert read_message_history(deps, target.world_id, "outsider", 999, inherited=True) is None


def test_restoring_branch_checkpoint_preserves_ancestors_but_removes_live_future(library):
    context, _, service = library
    publish(context, service, "ancestor", "分叉前记录", {"kind": "public"})
    target = branch(context)
    save_game([], "slot_001", context=target)
    publish(
        target, StructuredPlayService(target.database_url), "future", "分支未来", {"kind": "public"}
    )
    restore_structured_save(target, "slot_001")
    snapshot = StructuredGateway(target.database_url).snapshot_envelope(
        world_id=target.world_id, user_id="u-alice"
    )
    assert snapshot["payload"]["message_history"]["messages"] == []
    assert [
        row["text"] for row in snapshot["payload"]["inherited_message_history"]["messages"]
    ] == ["分叉前记录"]


def test_new_branch_of_old_unsaved_branch_marks_inherited_archive_incomplete(library):
    context, _, service = library
    publish(context, service, "root-history", "旧祖先历史不可推测", {"kind": "public"})
    old_branch = branch(context)
    with session_scope(old_branch.database_url) as session:
        world = session.get(World, old_branch.world_id)
        metadata = dict(world.metadata_json)
        metadata["branch"] = dict(metadata["branch"])
        metadata["branch"].pop("history_archive_version")
        world.metadata_json = metadata
        session.query(BranchHistoryEntry).filter_by(world_id=old_branch.world_id).delete()
    publish(
        old_branch,
        StructuredPlayService(old_branch.database_url),
        "known",
        "旧分支自身的记录",
        {"kind": "public"},
    )
    target = branch(old_branch)
    snapshot = StructuredGateway(target.database_url).snapshot_envelope(
        world_id=target.world_id, user_id="u-alice"
    )
    assert [
        row["text"] for row in snapshot["payload"]["inherited_message_history"]["messages"]
    ] == ["旧分支自身的记录"]
    assert snapshot["payload"].get("inherited_history_incomplete") is True


def test_inherited_history_http_rechecks_auth_and_never_publicly_caches(library, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import patch

    from fastapi import APIRouter, FastAPI
    from fastapi.testclient import TestClient

    from src.web.structured_history_http import register_structured_history_routes

    context, deps, service = library
    publish(context, service, "public", "共同公开记录", {"kind": "public"})
    publish(context, service, "private", "仅主持记录", {"kind": "keeper"})
    target = branch(context)
    monkeypatch.setenv("TRPG_REQUIRE_AUTH", "1")
    app, router = FastAPI(), APIRouter()
    register_structured_history_routes(router, deps)
    app.include_router(router)
    url = f"/api/worlds/{target.world_id}/narrative-history"
    query = {"before_sequence": 999, "scope": "inherited"}
    with TestClient(app) as client:
        with patch("src.web.structured_history_http.request_user", return_value=None):
            assert client.get(url, params=query).status_code == 401
        with patch(
            "src.web.structured_history_http.request_user", return_value=SimpleNamespace(id="u-bob")
        ):
            response = client.get(url, params=query)
            assert response.status_code == 200
            assert [row["text"] for row in response.json()["messages"]] == ["共同公开记录"]
            assert response.headers["cache-control"] == "private, no-store"
            assert response.headers["vary"] == "Cookie"
            assert client.get(url, params={**query, "scope": "../parent"}).status_code == 422
            with session_scope(context.database_url) as session:
                session.query(WorldMember).filter_by(
                    world_id=target.world_id, user_id="u-bob"
                ).delete()
            assert client.get(url, params=query).status_code == 404
