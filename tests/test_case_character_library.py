"""Case cards use trusted world receipts and fresh control, never client card data."""

import copy
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_structured_combat_transactions import battle as battle

from src.gameplay.character_library import (
    CharacterLibraryError,
    create_entry,
    delete_entry,
    get_entry,
)
from src.storage.database import (
    CharacterLibraryEntry,
    GameCommand,
    World,
    WorldInvestigator,
    WorldState,
    session_scope,
)
from src.structured.bootstrap import LOCAL_OPERATOR_USER_ID
from src.structured.case_characters import (
    export_case_character,
    preview_case_character,
    save_case_character,
)
from src.web.character_library_http import (
    CharacterLibraryHttpDependencies,
    create_character_library_router,
)


@pytest.fixture
def finished_case(battle):
    url, _, _, execute, snapshot, _ = battle
    execute("combat_end", {"reason": "结束遭遇"})
    with session_scope(url) as session:
        row = session.get(WorldState, "sp-world")
        state = copy.deepcopy(row.state)
        attrs = {key: 60 for key in ("STR", "DEX", "CON", "INT", "POW", "SIZ", "APP", "EDU")}
        for key in ("inv-alice", "inv-bob"):
            state["investigators"][key].update(attributes=attrs, occupation="调查员")
        state["pc"] = copy.deepcopy(state["investigators"]["inv-alice"])
        row.state = state
    execute("end_game", {"ending_type": "good", "title": "案件结束"})
    state, revision, *_ = snapshot()
    source = {
        "world_id": "sp-world",
        "investigator_id": "inv-alice",
        "case_id": next(iter(state["case_settlements"])),
        "expected_revision": revision,
    }
    return url, source, snapshot


def test_preview_and_export_are_read_only_and_rewards_come_from_receipt(finished_case):
    url, source, snapshot = finished_case
    before = snapshot()
    preview = preview_case_character(url, "u-alice", **source)
    export = export_case_character(url, "u-alice", **source)
    assert export["format"] == "trpg-character-card"
    assert export["card"] == preview["card"]
    receipt = before[0]["case_settlements"][source["case_id"]]["inv-alice"]
    assert preview["card"]["career"] == receipt["career"]
    assert "controller_user_id" not in export["card"]
    assert "owner_user_id" not in export["card"]
    assert "npcs" not in export["card"]
    assert preview["card"]["trpg_case_record"]["final_state"]["hp"] == before[0]["pc"]["hp"]
    assert snapshot() == before
    with session_scope(url) as session:
        assert session.scalars(select(CharacterLibraryEntry)).all() == []


def test_save_creates_a_new_copy_and_retries_keep_original_and_world_unchanged(finished_case):
    url, source, snapshot = finished_case
    preview = preview_case_character(url, "u-alice", **source)
    original = create_entry(url, "u-alice", preview["card"])["entry"]
    old = get_entry(url, "u-alice", original["id"])
    before = snapshot()[0:2]
    saved = save_case_character(url, "u-alice", **source, name="结案后的爱丽丝")
    replay = save_case_character(url, "u-alice", **source, name="不应覆盖第一次保存的名字")
    assert saved["entry"]["id"] != original["id"]
    assert replay["deduplicated"]
    assert replay["entry"]["id"] == saved["entry"]["id"]
    assert replay["entry"]["name"] == "结案后的爱丽丝"
    assert get_entry(url, "u-alice", original["id"]) == old
    assert snapshot()[0:2] == before
    assert (
        preview_case_character(url, "u-alice", **source)["saved_entry"]["id"]
        == saved["entry"]["id"]
    )


@pytest.mark.parametrize("owner", ["u-keeper", "u-bob", "", "unknown"])
def test_keeper_other_player_anonymous_and_unknown_cannot_save_alices_card(finished_case, owner):
    url, source, _ = finished_case
    with pytest.raises(CharacterLibraryError) as error:
        save_case_character(url, owner, **source)
    assert error.value.status == 404


def test_revoked_control_cannot_replay_a_saved_case_card(finished_case):
    url, source, _ = finished_case
    save_case_character(url, "u-alice", **source)
    with session_scope(url) as session:
        row = session.scalar(
            select(WorldInvestigator).where(
                WorldInvestigator.world_id == "sp-world",
                WorldInvestigator.character_key == "inv-alice",
            )
        )
        row.controller_user_id = None
        row.status = "unclaimed"
    with pytest.raises(CharacterLibraryError) as error:
        save_case_character(url, "u-alice", **source)
    assert error.value.status == 404


def test_explicitly_deleted_copy_is_not_silently_resurrected(finished_case):
    url, source, _ = finished_case
    saved = save_case_character(url, "u-alice", **source)
    delete_entry(url, "u-alice", saved["entry"]["id"])
    with pytest.raises(CharacterLibraryError) as error:
        save_case_character(url, "u-alice", **source)
    assert error.value.code == "saved_copy_deleted"


def test_revision_and_missing_case_refuse_without_writes(finished_case):
    url, source, snapshot = finished_case
    before = snapshot()
    with pytest.raises(CharacterLibraryError) as error:
        save_case_character(url, "u-alice", **{**source, "expected_revision": 1})
    assert error.value.code == "revision_conflict"
    with pytest.raises(CharacterLibraryError):
        save_case_character(url, "u-alice", **{**source, "case_id": "invented"})
    assert snapshot() == before


def test_concurrent_saves_create_one_library_copy(finished_case):
    import src.structured.case_characters as cases

    url, source, _ = finished_case
    barrier = Barrier(2)
    original = cases._card

    def overlap(*args):
        result = original(*args)
        barrier.wait(timeout=5)
        return result

    with (
        patch.object(cases, "_card", side_effect=overlap),
        ThreadPoolExecutor(max_workers=2) as pool,
    ):
        futures = [pool.submit(save_case_character, url, "u-alice", **source) for _ in range(2)]
        results = [future.result(timeout=15) for future in futures]
    assert len({result["entry"]["id"] for result in results}) == 1
    with session_scope(url) as session:
        assert len(session.scalars(select(CharacterLibraryEntry)).all()) == 1


def test_local_world_can_save_local_actor_but_not_a_foreign_controller(finished_case):
    url, source, _ = finished_case
    with session_scope(url) as session:
        session.get(World, "sp-world").created_by = None
        row = session.get(WorldState, "sp-world")
        state = copy.deepcopy(row.state)
        state["pc"]["controller_user_id"] = LOCAL_OPERATOR_USER_ID
        state["investigators"]["inv-alice"]["controller_user_id"] = LOCAL_OPERATOR_USER_ID
        state["investigator_controllers"] = {
            LOCAL_OPERATOR_USER_ID: "inv-alice",
            "u-bob": "inv-bob",
        }
        row.state = state
        claim = session.scalar(
            select(WorldInvestigator).where(
                WorldInvestigator.world_id == "sp-world",
                WorldInvestigator.character_key == "inv-alice",
            )
        )
        claim.controller_user_id = None
        claim.status = "unclaimed"
    saved = save_case_character(url, "", **source)
    assert saved["entry"]["name"] == "爱丽丝"
    assert get_entry(url, "", saved["entry"]["id"])
    with pytest.raises(CharacterLibraryError):
        save_case_character(url, "", **{**source, "investigator_id": "inv-bob"})


def test_http_case_endpoints_reject_client_authority_and_export_real_card(finished_case):
    url, source, snapshot = finished_case
    app = FastAPI()

    # Represents an already-authenticated middleware identity, not an auth test.
    @app.middleware("http")
    async def authenticated_test_identity(request, next_handler):
        request.state.user = SimpleNamespace(id=request.headers.get("x-test-user", "u-alice"))
        return await next_handler(request)

    app.include_router(
        create_character_library_router(CharacterLibraryHttpDependencies(database_url=lambda: url))
    )
    with (
        patch("src.web.character_library_http.auth_required", return_value=True),
        TestClient(app) as client,
    ):
        before = snapshot()
        rejected = client.post(
            "/api/character-library/from-case", json={**source, "owner_user_id": "secret-canary"}
        )
        assert rejected.status_code == 400
        assert "secret-canary" not in rejected.text
        assert snapshot() == before
        assert (
            client.post(
                "/api/character-library/from-case", json=source, headers={"x-test-user": "u-keeper"}
            ).status_code
            == 404
        )
        preview = client.post("/api/character-library/from-case/preview", json=source)
        assert preview.status_code == 200
        exported = client.post("/api/character-library/from-case/export", json=source)
        assert exported.status_code == 200
        assert "attachment" in exported.headers["content-disposition"]
        assert exported.json()["card"] == preview.json()["card"]
        saved = client.post("/api/character-library/from-case", json={**source, "name": "案件副本"})
        assert saved.status_code == 201
        replay = client.post("/api/character-library/from-case", json=source)
        assert replay.status_code == 200 and replay.json()["deduplicated"]
        assert replay.json()["entry"]["id"] == saved.json()["entry"]["id"]


def test_long_authored_ending_id_keeps_the_full_case_identifier(battle):
    url, _, _, execute, snapshot, _ = battle
    execute("combat_end", {"reason": "完成案件"})
    ending_id = "e" * 160
    with session_scope(url) as session:
        row = session.get(WorldState, "sp-world")
        state = copy.deepcopy(row.state)
        state["endings"] = [
            {"id": ending_id, "title": "长编号结局", "ending_type": "good", "required_flags": {}}
        ]
        row.state = state
    execute("end_game", {"ending_id": ending_id})
    assert next(iter(snapshot()[0]["case_settlements"])) == f"sp-world:{ending_id}"


def test_case_card_uses_immutable_ending_snapshot_not_later_world_sheet(finished_case):
    url, source, _ = finished_case
    before = preview_case_character(url, "u-alice", **source)["card"]
    with session_scope(url) as session:
        row = session.get(WorldState, "sp-world")
        state = copy.deepcopy(row.state)
        state["pc"]["name"] = "后来分支中的名字"
        state["pc"]["skills"]["射击"] = 1
        state["investigators"]["inv-alice"]["name"] = "后来分支中的名字"
        row.state = state
    after = preview_case_character(url, "u-alice", **source)["card"]
    assert after == before
    assert "controller_user_id" not in after


@pytest.mark.parametrize("operation", [save_case_character, export_case_character])
def test_changed_receipt_cannot_use_an_old_preview_digest(finished_case, operation):
    url, source, snapshot = finished_case
    digest = preview_case_character(url, "u-alice", **source)["receipt_digest"]
    with session_scope(url) as session:
        row = session.get(WorldState, "sp-world")
        state = copy.deepcopy(row.state)
        state["case_settlements"][source["case_id"]]["inv-alice"]["case"]["summary"] = "新凭证"
        row.state = state
    before = snapshot()
    with pytest.raises(CharacterLibraryError) as error:
        operation(url, "u-alice", **source, receipt_digest=digest)
    assert error.value.code == "revision_conflict"
    assert snapshot() == before
    with session_scope(url) as session:
        assert session.scalars(select(CharacterLibraryEntry)).all() == []


def test_failed_save_rolls_back_both_library_entry_and_audit(finished_case):
    from sqlalchemy import event
    from sqlalchemy.orm import Session

    url, source, snapshot = finished_case
    before = snapshot()

    def fail_audit(session, _context, _instances):
        if any(
            isinstance(row, GameCommand) and row.kind == "case_card_save" for row in session.new
        ):
            raise RuntimeError("simulated audit storage failure")

    event.listen(Session, "before_flush", fail_audit)
    try:
        with pytest.raises(RuntimeError, match="audit storage failure"):
            save_case_character(url, "u-alice", **source)
    finally:
        event.remove(Session, "before_flush", fail_audit)
    assert snapshot() == before
    with session_scope(url) as session:
        assert session.scalars(select(CharacterLibraryEntry)).all() == []
    assert not save_case_character(url, "u-alice", **source)["deduplicated"]


def test_legacy_receipt_fallback_only_accepts_the_latest_case(finished_case):
    url, source, snapshot = finished_case
    with session_scope(url) as session:
        row = session.get(WorldState, "sp-world")
        state = copy.deepcopy(row.state)
        receipt = state["case_settlements"][source["case_id"]]["inv-alice"]
        receipt.pop("character_snapshot")
        for settled in state["case_settlements"][source["case_id"]].values():
            settled["case"]["completed_at"] = "2026-10-01T01:00:00"
        row.state = state
    assert preview_case_character(url, "u-alice", **source)["card"]["name"] == "爱丽丝"
    with session_scope(url) as session:
        row = session.get(WorldState, "sp-world")
        state = copy.deepcopy(row.state)
        later = copy.deepcopy(state["case_settlements"][source["case_id"]]["inv-alice"])
        later["case"].update(case_id="later", completed_at="2026-10-02T01:00:00")
        state["case_settlements"]["later"] = {"inv-alice": later}
        row.state = state
    before = snapshot()
    with pytest.raises(CharacterLibraryError) as error:
        preview_case_character(url, "u-alice", **source)
    assert error.value.code == "historical_card_missing"
    assert snapshot() == before


def test_incomplete_character_is_not_filled_with_invented_attributes(finished_case):
    url, source, snapshot = finished_case
    with session_scope(url) as session:
        row = session.get(WorldState, "sp-world")
        state = copy.deepcopy(row.state)
        sheet = state["case_settlements"][source["case_id"]]["inv-alice"]["character_snapshot"]
        sheet["attributes"].pop("STR")
        row.state = state
    before = snapshot()
    with pytest.raises(CharacterLibraryError) as error:
        save_case_character(url, "u-alice", **source)
    assert error.value.code == "invalid_card"
    assert snapshot() == before
