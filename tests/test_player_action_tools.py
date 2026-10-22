import copy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from src.ai.model.investigation_digest import extract_investigation_digest
from src.gameplay.player_tools import player_skill_options, visited_scene_map
from src.storage.database import (
    PlayerNote,
    User,
    World,
    WorldInvestigator,
    WorldMember,
    WorldState,
    initialize_database,
    session_scope,
)
from src.web import player_tools_http


def sample_state():
    return {
        "current_scene": {"id": "shop", "name": "铺子", "exits": ["study", "vault"]},
        "scene_catalog": {
            "shop": {"name": "铺子", "exits": ["study", "vault"]},
            "study": {"name": "藏有证据的书房", "public_name": "书房", "exits": ["shop"]},
            "vault": {"name": "秘密金库", "description": "凶手藏在这里", "exits": ["shop"]},
        },
        "encounter_history": {"study": {}},
        "pc": {"name": "本地调查员", "skills": {"spot_hidden": 73}},
    }


def test_visited_graph_filters_before_layout_and_never_mutates_state():
    state = sample_state()
    before = copy.deepcopy(state)
    graph = visited_scene_map(state)
    assert graph == {"nodes": [{"id": "shop", "name": "铺子", "current": True},
                                {"id": "study", "name": "书房", "current": False}],
                     "edges": [{"from": "shop", "to": "study"}]}
    for secret in ("vault", "秘密", "证据", "凶手", "description"):
        assert secret not in json.dumps(graph, ensure_ascii=False)
    assert state == before


def test_legacy_unknown_visit_history_shows_only_current_no_fabrication():
    state = sample_state()
    del state["encounter_history"]
    assert len(visited_scene_map(state)["nodes"]) == 1
    assert visited_scene_map({"current_scene": {"id": "raw_internal_id"}})["nodes"] == []
    assert visited_scene_map({"current_scene": "broken", "encounter_history": []}) == {"nodes": [], "edges": []}


def test_visits_without_npcs_remain_visible_after_moving_away():
    state = sample_state()
    state["encounter_history"] = {}
    state["scene_visits"] = ["study", "missing"]
    graph = visited_scene_map(state)
    assert {node["id"] for node in graph["nodes"]} == {"shop", "study"}


def test_pending_move_does_not_explore_but_arrival_records_both_places(tmp_path, monkeypatch):
    from tests.test_departure_turn import INPUT, make_engine, moving, preparing

    engine, _ = make_engine(tmp_path, monkeypatch, [preparing(), moving()],
                            ["【npc:guard】是否需要我同行？【/npc】", "你独自抵达大学。"])
    engine.handle_action(INPUT)
    assert engine.context.world_store.load().get("scene_visits", []) == []
    engine.handle_action(moving()["input_quote"])
    state = engine.context.world_store.load()
    assert set(state["scene_visits"]) == {"hall", "university"}
    assert {node["id"] for node in visited_scene_map(state)["nodes"]} == {"hall", "university"}


def test_failed_arrival_rolls_back_map_visits(tmp_path, monkeypatch):
    from tests.test_departure_turn import make_engine, moving

    engine, _ = make_engine(tmp_path, monkeypatch, [moving()], ["unused"])
    engine._stream_llm = Mock(side_effect=RuntimeError("intentional narrative failure"))
    with pytest.raises(RuntimeError):
        engine.handle_action(moving()["input_quote"])
    state = engine.context.world_store.load()
    assert state["current_scene"]["id"] == "hall"
    assert state.get("scene_visits", []) == []


def test_skills_use_effective_values_and_exclude_attributes_unusable_values():
    pc = {"attributes": {"DEX": 40}, "skills": {"spot_hidden": 73, "custom": 61, "history": "bad"}}
    entries = {entry["id"]: entry for entry in player_skill_options(pc)}
    assert entries["spot_hidden"] == {"id": "spot_hidden", "name": "侦查", "value": 73, "trained": True}
    assert entries["dodge"]["value"] == 20
    assert entries["custom"]["value"] == 61
    assert "history" not in entries and "DEX" not in entries
    assert "cthulhu_mythos" not in entries


def digest_role(content, *, finish="stop", window=262144):
    response = SimpleNamespace(choices=[SimpleNamespace(finish_reason=finish, message=SimpleNamespace(content=content))])
    create = Mock(return_value=response)
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    client.with_options = Mock(return_value=client)
    return SimpleNamespace(model_id="script", client=client, max_output_tokens=2000, window_tokens=window), create


def test_digest_only_sends_displayed_source_without_tools_and_preserves_quotes():
    text = "门房说：昨晚我听到三声钟响。"
    role, create = digest_role(json.dumps({"items": [{"text": "门房声称听到钟声，待查。", "quote": "昨晚我听到三声钟响"}]}))
    result = extract_investigation_digest(role, text, world_id="w", user_id="u")
    assert result["items"][0]["quote"] in text
    sent = create.call_args.kwargs
    assert "tools" not in sent and len(sent["messages"]) == 2
    assert json.loads(sent["messages"][1]["content"]) == {"displayed_turn": text}
    role.client.with_options.assert_called_once_with(timeout=35, max_retries=0)


@pytest.mark.parametrize("content,finish", [
    ('{"items":[{"text":"凭空捏造","quote":"未出现的证据"}]}', "stop"),
    ('{"items":[]}', "length"),
    ('{"items":[], "hidden_world":true}', "stop"),
])
def test_digest_rejects_ungrounded_or_incomplete_results(content, finish):
    role, _ = digest_role(content, finish=finish)
    with pytest.raises(ValueError):
        extract_investigation_digest(role, "本轮只有普通闲谈。", world_id="w", user_id="u")


def test_small_model_window_is_checked_before_api_call():
    role, create = digest_role('{"items":[]}', window=1024)
    with pytest.raises(ValueError, match="窗口"):
        extract_investigation_digest(role, "说话" * 1000, world_id="w", user_id="u")
    create.assert_not_called()


@pytest.fixture
def tools_api(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'tools.db'}"
    initialize_database(url)
    with session_scope(url) as session:
        for uid in ("alice", "bob", "viewer"):
            session.add(User(id=uid, username=uid, password_hash="not-a-real-password"))
        session.add(World(id="w", module_name="test", status="active", metadata_json={}))
        session.flush()
        for uid, role in (("alice", "owner"), ("viewer", "viewer")):
            session.add(WorldMember(id=f"m-{uid}", world_id="w", user_id=uid, role=role))
        state = sample_state()
        state["investigators"] = {"claim-row": {"name": "Alice的调查员", "skills": {"spot_hidden": 81}},
                                  "card-key": {"name": "结构化调查员", "skills": {"spot_hidden": 64}}}
        session.add(WorldState(world_id="w", schema_version=1, revision=4, state=state))
        session.add(WorldInvestigator(id="claim-row", world_id="w", character_key="card-key",
                                      controller_user_id="alice", status="claimed"))
    monkeypatch.setattr(player_tools_http, "auth_required", lambda: True)
    monkeypatch.setattr(player_tools_http, "request_user", lambda req, _url: SimpleNamespace(id=req.headers["x-user"]) if req.headers.get("x-user") else None)
    deps = SimpleNamespace(database_url=lambda: url, runtime_root=Path(tmp_path))
    app, router = FastAPI(), APIRouter()
    player_tools_http.register_player_tools_routes(router, deps)
    app.include_router(router)
    with TestClient(app) as client:
        yield client, url


def test_http_member_and_claim_scoping_and_structured_identity(tools_api):
    client, url = tools_api
    path = "/api/worlds/w/player-tools"
    assert client.get(path).status_code == 401
    assert client.get(path, headers={"x-user": "bob"}).status_code == 403
    response = client.get(path, headers={"x-user": "alice"})
    assert response.status_code == 200 and response.headers["cache-control"] == "private, no-store"
    assert response.json()["character_name"] == "Alice的调查员"
    assert client.get(path, headers={"x-user": "viewer"}).json()["skills"] == []
    with session_scope(url) as session:
        session.get(World, "w").metadata_json = {"execution_profile": "structured_v1"}
    assert client.get(path, headers={"x-user": "alice"}).json()["character_name"] == "结构化调查员"


def test_digest_rejects_viewer_and_cross_world_without_model_call(tools_api, monkeypatch):
    client, _ = tools_api
    generate = Mock(side_effect=AssertionError("must not call model"))
    monkeypatch.setattr(player_tools_http, "extract_investigation_digest", generate)
    body = {"source_id": "turn-1", "source_text": "可见的本轮文字。"}
    path = "/api/worlds/w/investigation-digest"
    assert client.post(path, json=body, headers={"x-user": "viewer"}).status_code == 403
    assert client.post(path, json=body, headers={"x-user": "bob"}).status_code == 403
    assert client.post(path, json={**body, "source_text": "x" * 12001}, headers={"x-user": "alice"}).status_code == 400
    generate.assert_not_called()


def test_digest_does_not_advance_game_or_save_notes_and_redacts_errors(tools_api, monkeypatch):
    client, url = tools_api
    with session_scope(url) as session:
        original = copy.deepcopy(session.get(WorldState, "w").state)
    monkeypatch.setattr(player_tools_http, "room_route_resolver", lambda *_: lambda: "settings")
    monkeypatch.setattr(player_tools_http, "resolve_routes", lambda _: SimpleNamespace(judgement="role"))
    monkeypatch.setattr(player_tools_http, "extract_investigation_digest", lambda *_args, **_kwargs: {"items": []})
    path = "/api/worlds/w/investigation-digest"
    body = {"source_id": "turn-1", "source_text": "可见的本轮文字。"}
    assert client.post(path, json=body, headers={"x-user": "alice"}).json() == {"world_id": "w", "source_id": "turn-1", "items": []}
    with session_scope(url) as session:
        row = session.get(WorldState, "w")
        assert row.state == original and row.revision == 4
        assert session.query(PlayerNote).count() == 0
    monkeypatch.setattr(player_tools_http, "extract_investigation_digest", Mock(side_effect=RuntimeError("sk-private-secret")))
    response = client.post(path, json=body, headers={"x-user": "alice"})
    assert response.status_code == 503 and "sk-private-secret" not in response.text


def test_anonymous_local_cannot_read_cloud_owned_world(tools_api, monkeypatch):
    client, _ = tools_api
    monkeypatch.setattr(player_tools_http, "auth_required", lambda: False)
    assert client.get("/api/worlds/w/player-tools").status_code == 404
    assert client.get("/api/worlds/w/player-tools", headers={"Origin": "https://untrusted.example"}).status_code == 403
