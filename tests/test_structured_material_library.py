from __future__ import annotations

import base64
import copy
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from test_structured_commands import make_structured_world

from src.modules.module_registry import ModuleRegistry
from src.storage.database import EventOutbox, WorldMember, WorldState, session_scope
from src.structured.errors import StructuredError
from src.structured.gateway import StructuredGateway
from src.structured.materials import asset_entries, keeper_investigators
from src.structured.principal import Principal
from src.structured.service import StructuredPlayService, audience_visible
from src.structured.validation import validate_event
from src.web.structured_asset_http import read_world_asset, register_structured_asset_routes
from src.web.structured_guide_http import (
    MAX_DOCUMENT_BYTES,
    read_keeper_guide,
    register_structured_guide_routes,
)

PIXEL = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+nmV8AAAAASUVORK5CYII="
)


def test_keeper_who_controls_a_character_retains_only_their_own_player_identity(library):
    context, _, service = library
    with session_scope(context.database_url) as session:
        session.query(WorldMember).filter_by(user_id="u-bob").one().can_keeper = True
    principal = StructuredGateway(context.database_url).connection_principal(
        context.world_id, "u-bob"
    )
    assert principal.kind == "keeper"
    assert principal.investigator_ids == ("inv-bob",)
    snapshot = service.session_snapshot(world_id=context.world_id, principal=principal)
    assert snapshot["investigator_id"] == "inv-bob"
    assert snapshot["character"]["name"] == "鲍勃"
    assert snapshot["character"]["skills"] == {"斗殴": 60}
    assert "keeper_material" in snapshot


def test_structured_viewer_can_observe_public_events_but_has_no_action_or_secret_authority(library):
    context, deps, service = library
    with session_scope(context.database_url) as session:
        member = session.query(WorldMember).filter_by(user_id="u-bob").one()
        member.role = "viewer"
    gateway = StructuredGateway(context.database_url)
    observer = gateway.connection_principal(context.world_id, "u-bob")
    assert observer is not None
    assert observer.kind == "viewer"
    assert observer.investigator_ids == ()  # no stale prior claim authority
    assert audience_visible({"kind": "public"}, observer)
    assert not audience_visible({"kind": "keeper"}, observer)
    assert not audience_visible(
        {"kind": "investigators", "investigator_ids": ["inv-bob"]}, observer
    )
    snapshot = service.session_snapshot(world_id=context.world_id, principal=observer)
    assert snapshot["character"] is None
    assert "keeper_material" not in snapshot
    assert "keeper_assets" not in snapshot
    assert "keeper_investigators" not in snapshot
    assert read_keeper_guide(deps, context.world_id, "u-bob") is None
    assert read_world_asset(deps, context.world_id, "asset_note", "u-bob") is None
    with pytest.raises(StructuredError, match="玩家成员"):
        gateway._execute(
            context.world_id,
            "u-bob",
            {
                "type": "action_request",
                "protocol_version": 1,
                "world_id": context.world_id,
                "request_id": "viewer-write",
                "investigator_id": "inv-bob",
                "expected_revision": 0,
                "action": {"kind": "freeform", "text": "尝试操作"},
            },
        )


@pytest.fixture
def library(tmp_path):
    context = make_structured_world(tmp_path)
    assets = context.assets_dir
    assets.mkdir()
    (assets / "note.png").write_bytes(PIXEL)
    with session_scope(context.database_url) as session:
        row = session.get(WorldState, context.world_id)
        state = copy.deepcopy(row.state)
        state["npcs"][0]["secret"] = "主持专属：旧钥匙藏在暗格。"
        row.state = state
    deps = SimpleNamespace(
        database_url=lambda: context.database_url,
        module_registry=ModuleRegistry(tmp_path, tmp_path),
    )
    return context, deps, StructuredPlayService(context.database_url)


def test_keeper_projection_has_full_authored_references_but_player_and_agent_do_not(library):
    context, deps, service = library
    keeper = service.session_snapshot(
        world_id=context.world_id, principal=Principal(kind="keeper", user_id="u-keeper")
    )
    assert keeper["keeper_material"][0]["current"] is True
    assert "旧钥匙藏在暗格" in str(keeper["keeper_material"])
    assert keeper["keeper_assets"] == [{"id": "asset_note", "label": "asset_note"}]
    assert "note.png" not in str(keeper["keeper_assets"])
    assert [person["name"] for person in keeper["keeper_investigators"]] == ["爱丽丝", "鲍勃"]
    assert keeper["keeper_investigators"][0]["skills"] == {"说服": 55, "侦查": 70}
    assert "controller_user_id" not in str(keeper["keeper_investigators"])
    validate_event(
        {
            "protocol_version": 1,
            "event_id": 0,
            "world_id": context.world_id,
            "sequence": 0,
            "revision": keeper["revision"],
            "cause_request_id": None,
            "type": "session_snapshot",
            "payload": keeper,
        }
    )
    for principal in [
        Principal(kind="player", user_id="u-alice", investigator_ids=("inv-alice",)),
        Principal(kind="player", user_id="u-owner"),
        Principal(kind="agent", run_id="test"),
    ]:
        snapshot = service.session_snapshot(world_id=context.world_id, principal=principal)
        assert "keeper_material" not in snapshot
        assert "keeper_assets" not in snapshot
        assert "keeper_investigators" not in snapshot
        assert "旧钥匙藏在暗格" not in str(snapshot)


def test_party_reference_uses_latest_active_projection_without_leaking_sheet_extensions():
    state = {
        "investigators": {"left": {"name": "旧卡", "hp": 9}, "right": {"name": "乙"}},
        "active_investigator_id": "left",
        "pc": {
            "name": "甲",
            "hp": 3,
            "skills": {"rare_skill": 21},
            "controller_user_id": "secret-owner",
            "private_memory": "私密扩展",
        },
    }
    reference = keeper_investigators(state)
    assert reference == [
        {"investigator_id": "left", "name": "甲", "hp": 3, "skills": {"rare_skill": 21}},
        {"investigator_id": "right", "name": "乙"},
    ]
    reference[0]["skills"]["rare_skill"] = 99
    assert state["pc"]["skills"]["rare_skill"] == 21


def test_author_guide_is_private_complete_read_only_and_not_in_snapshots(library):
    context, deps, service = library
    root = context.assets_dir.parent
    (root / "module.md").write_text("# 完整手册\n主持秘密，不能公开。", encoding="utf8")
    (root / "scenes").mkdir()
    (root / "scenes" / "书房.md").write_text("补充空间构造", encoding="utf8")
    (root / "lorebook.json").write_text(
        json.dumps(
            {
                "data": {
                    "entries": [
                        {"name": "旧传闻", "content": "传闻不是事实", "enabled": False},
                    ]
                }
            }
        ),
        encoding="utf8",
    )
    with session_scope(context.database_url) as session:
        before = copy.deepcopy(session.get(WorldState, context.world_id).state)
        revision = session.get(WorldState, context.world_id).revision
    guide = read_keeper_guide(deps, context.world_id, "u-keeper")
    assert [doc["text"] for doc in guide["documents"]] == [
        "# 完整手册\n主持秘密，不能公开。",
        "补充空间构造",
        "传闻不是事实",
    ]
    assert "作者停用" in guide["documents"][-1]["title"]
    assert guide["warnings"] == []
    for user in ["u-alice", "u-bob", "u-owner", "outsider"]:
        assert read_keeper_guide(deps, context.world_id, user) is None
    with session_scope(context.database_url) as session:
        assert session.get(WorldState, context.world_id).state == before
        assert session.get(WorldState, context.world_id).revision == revision
        assert session.query(EventOutbox).count() == 0
    # Snapshot's existing stable-ID bootstrap is a separate operation, not a
    # document read. Do not confuse its registry migration with guide mutation.
    snapshot = service.session_snapshot(
        world_id=context.world_id, principal=Principal(kind="keeper", user_id="u-keeper")
    )
    assert "完整手册" not in str(snapshot)
    with session_scope(context.database_url) as session:
        session.query(WorldMember).filter_by(user_id="u-keeper").one().can_keeper = False
    assert read_keeper_guide(deps, context.world_id, "u-keeper") is None


def test_guide_rejects_external_symlinks_and_oversize_without_silent_truncation(library, tmp_path):
    context, deps, _ = library
    root = context.assets_dir.parent
    outside = tmp_path / "outside.txt"
    outside.write_text("模块之外的秘密", encoding="utf8")
    (root / "module.md").unlink()
    (root / "module.md").symlink_to(outside)
    (root / "scenes").mkdir()
    (root / "scenes" / "超大.md").write_text("x" * (MAX_DOCUMENT_BYTES + 1), encoding="utf8")
    # Registry resolves an installed record independently of untrusted content.
    record = SimpleNamespace(path=root, title="安全测试", version="1")
    deps.module_registry = SimpleNamespace(resolve=lambda _: record)
    guide = read_keeper_guide(deps, context.world_id, "u-keeper")
    assert guide["documents"] == []
    assert len(guide["warnings"]) == 2
    assert "模块之外的秘密" not in str(guide)
    assert "x" * 100 not in str(guide)


def test_guide_http_requires_session_and_keeper_not_just_owner(library, monkeypatch):
    context, deps, _ = library
    monkeypatch.setenv("TRPG_REQUIRE_AUTH", "1")
    app = FastAPI()
    router = APIRouter()
    register_structured_guide_routes(router, deps)
    app.include_router(router)
    with TestClient(app) as client:
        url = f"/api/worlds/{context.world_id}/keeper-guide"
        with patch("src.web.structured_guide_http.request_user", return_value=None):
            assert client.get(url).status_code == 401
        for user, code in [("u-keeper", 200), ("u-owner", 404), ("u-bob", 404)]:
            with patch(
                "src.web.structured_guide_http.request_user", return_value=SimpleNamespace(id=user)
            ):
                response = client.get(url)
                assert response.status_code == code
                assert response.headers["cache-control"] == "private, no-store"


def test_preview_is_read_only_owner_is_not_keeper_and_grants_are_recipient_scoped(library):
    context, deps, service = library
    with session_scope(context.database_url) as session:
        before = copy.deepcopy(session.get(WorldState, context.world_id).state)
        revision = session.get(WorldState, context.world_id).revision
    assert read_world_asset(deps, context.world_id, "asset_note", "u-keeper")[
        "asset_data_uri"
    ].startswith("data:image/png;base64,")
    for user in ["u-alice", "u-bob", "u-owner", "outsider"]:
        assert read_world_asset(deps, context.world_id, "asset_note", user) is None
    with session_scope(context.database_url) as session:
        assert session.get(WorldState, context.world_id).state == before
        assert session.get(WorldState, context.world_id).revision == revision
        assert session.query(EventOutbox).count() == 0
    service.execute_command(
        world_id=context.world_id,
        principal=Principal(kind="keeper", user_id="u-keeper"),
        kind="present_handout",
        payload={"asset_id": "asset_note", "recipient_investigator_ids": ["inv-alice"]},
        command_id="present",
        expected_revision=revision,
    )
    assert read_world_asset(deps, context.world_id, "asset_note", "u-alice") is not None
    assert read_world_asset(deps, context.world_id, "asset_note", "u-bob") is None
    with session_scope(context.database_url) as session:
        assert "base64" not in str([row.payload for row in session.query(EventOutbox).all()])
        member = session.query(WorldMember).filter_by(user_id="u-keeper").one()
        member.can_keeper = False
    assert read_world_asset(deps, context.world_id, "asset_note", "u-keeper") is None


def test_start_and_reconnect_snapshot_contains_only_the_controlled_character(library):
    context, _, service = library
    for key, name in [("inv-alice", "爱丽丝"), ("inv-bob", "鲍勃")]:
        snapshot = service.session_snapshot(
            world_id=context.world_id,
            principal=Principal(kind="player", user_id="test", investigator_ids=(key,)),
        )
        assert snapshot["character"]["name"] == name
        assert isinstance(snapshot["character"]["hp"], int)
        assert "controller_user_id" not in snapshot["character"]
        assert snapshot["character"]["name"] != "占位"
    keeper = service.session_snapshot(
        world_id=context.world_id, principal=Principal(kind="keeper", user_id="u-keeper")
    )
    assert keeper["character"] is None
    assert (
        service.session_snapshot(
            world_id=context.world_id, principal=Principal(kind="player", user_id="viewer")
        )["character"]
        is None
    )
    assert "character" not in service.session_snapshot(
        world_id=context.world_id, principal=Principal(kind="agent", run_id="test")
    )


def test_complete_player_request_is_keeper_only_and_survives_snapshot(library):
    context, _, service = library
    text = "我说明来意，并仔细描述自己的做法。" * 12 + "最后询问医生是否愿意让我们查看记录。"
    request = {
        "request_id": "long-player-question",
        "investigator_id": "inv-alice",
        "expected_revision": 1,
        "action": {"kind": "freeform", "text": text},
    }
    result = service.submit_action_request(
        world_id=context.world_id,
        principal=Principal(kind="player", user_id="u-alice", investigator_ids=("inv-alice",)),
        request=request,
    )
    pending = next(event for event in result["events"] if event["type"] == "intent_pending")
    assert pending["audience"] == {"kind": "keeper"}
    assert pending["payload"]["action"]["text"] == text
    from src.structured.service import wire_envelope

    validate_event(wire_envelope(pending))
    keeper = service.session_snapshot(
        world_id=context.world_id, principal=Principal(kind="keeper", user_id="u-keeper")
    )
    assert keeper["requests"][0]["action"]["text"] == text
    for user, ids in [("u-alice", ("inv-alice",)), ("u-bob", ("inv-bob",))]:
        snapshot = service.session_snapshot(
            world_id=context.world_id,
            principal=Principal(kind="player", user_id=user, investigator_ids=ids),
        )
        assert all("action" not in entry for entry in snapshot["requests"])
    assert (
        service.session_snapshot(
            world_id=context.world_id,
            principal=Principal(kind="player", user_id="u-bob", investigator_ids=("inv-bob",)),
        )["requests"]
        == []
    )


@pytest.mark.parametrize(
    "filename", ["../../world_state_initial.json", "script.svg", "/etc/passwd"]
)
def test_registered_but_unsafe_paths_cannot_be_read(library, filename):
    context, deps, _ = library
    with session_scope(context.database_url) as session:
        row = session.get(WorldState, context.world_id)
        state = copy.deepcopy(row.state)
        state["assets"]["asset_note"]["file"] = filename
        row.state = state
    assert read_world_asset(deps, context.world_id, "asset_note", "u-keeper") is None


@pytest.mark.parametrize("empty", [False, True])
def test_ambiguous_or_missing_registry_never_authorizes_arbitrary_asset(library, empty):
    context, _, service = library
    if empty:
        with session_scope(context.database_url) as session:
            row = session.get(WorldState, context.world_id)
            state = copy.deepcopy(row.state)
            state["assets"] = {}
            row.state = state
    assert not asset_entries(
        {"assets": {"same": {"file": "a.png"}}, "asset_map": {"clues": {"same": {"file": "b.png"}}}}
    )
    with pytest.raises(StructuredError, match="素材不存在"):
        service.execute_command(
            world_id=context.world_id,
            principal=Principal(kind="keeper", user_id="u-keeper"),
            kind="present_handout",
            payload={"asset_id": "made-up", "recipient_investigator_ids": ["inv-alice"]},
            command_id="bad",
            expected_revision=1,
        )


def test_http_requires_session_and_never_publicly_caches_secret_image(library):
    context, deps, _ = library
    app = FastAPI()
    router = APIRouter()
    register_structured_asset_routes(router, deps)
    app.include_router(router)
    with (
        TestClient(app) as client,
        patch("src.web.structured_asset_http.auth_required", return_value=True),
    ):
        url = f"/api/worlds/{context.world_id}/handouts/asset_note"
        with patch("src.web.structured_asset_http.request_user", return_value=None):
            assert client.get(url).status_code == 401
        with patch(
            "src.web.structured_asset_http.request_user",
            return_value=SimpleNamespace(id="u-keeper"),
        ):
            response = client.get(url)
            assert response.status_code == 200
            assert response.headers["cache-control"] == "private, no-store"
            assert response.headers["vary"] == "Cookie"
        with patch(
            "src.web.structured_asset_http.request_user", return_value=SimpleNamespace(id="u-bob")
        ):
            assert client.get(url).status_code == 404
