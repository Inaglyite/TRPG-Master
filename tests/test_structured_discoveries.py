"""Authored discoveries require explicit rule selection, not narrative parsing."""

import copy
import json

import pytest
from test_structured_commands import make_structured_world

from src.app.config import PROJECT_ROOT
from src.storage.database import CheckRequest, WorldState, session_scope
from src.storage.persistence import save_game
from src.structured.branch import create_structured_branch, restore_structured_save
from src.structured.errors import StructuredError
from src.structured.principal import Principal, bind_agent_control
from src.structured.service import StructuredPlayService, wire_envelope
from src.structured.validation import validate_event

KEEPER = Principal(kind="keeper", user_id="u-keeper")
ALICE = Principal(kind="player", user_id="u-alice", investigator_ids=("inv-alice",))
BOB = Principal(kind="player", user_id="u-bob", investigator_ids=("inv-bob",))


@pytest.fixture
def game(tmp_path):
    context = make_structured_world(tmp_path)
    with session_scope(context.database_url) as session:
        row = session.get(WorldState, context.world_id)
        state = copy.deepcopy(row.state)
        state["clue_catalog"].update(
            {
                "diary": {
                    "text": "私人日记",
                    "granted_item": "唯一日记",
                    "related_scenes": ["study"],
                    "discovery_rules": [
                        {
                            "intent": "search",
                            "requires_success": True,
                            "skill": "侦查",
                            "difficulty": "regular",
                        }
                    ],
                },
                "document": {
                    "text": "审判文档",
                    "granted_item": "文档原件",
                    "related_scenes": ["study"],
                    "flag_effects": {"document_owned": True},
                    "discovery_rules": [{"intent": "take"}],
                },
                "seal": {
                    "text": "银徽章",
                    "granted_item": "唯一银徽章",
                    "related_scenes": ["study"],
                    "flag_effects": {"seal_owned": True},
                    "discovery_rules": [{"intent": "search"}],
                },
                "sealed": {
                    "text": "封印完成",
                    "related_scenes": ["study"],
                    "flag_effects": {"sealed": True},
                    "discovery_rules": [
                        {
                            "intent": "use",
                            "requires_flags": {"document_owned": True, "seal_owned": True},
                        }
                    ],
                },
                "hidden": {
                    "text": "秘密夹层",
                    "related_scenes": ["library"],
                    "flag_effects": {"hidden_found": True},
                    "discovery_rules": [
                        {"intent": "search", "requires_flags": {"door_open": True}}
                    ],
                },
            }
        )
        state["flags"] = {"sealed": False, "door_open": False}
        state["case_clocks"] = {"clue_clarity": 0, "danger": 0}
        state["case_clock_definitions"] = {
            "clue_clarity": {"name": "线索清晰度", "max": 5, "levels": {"1": "已知一条"}},
            "danger": {
                "name": "秘密危险",
                "max": 6,
                "time_advance": {
                    "every_minutes": 2880,
                    "advance": 1,
                    "activities": ["wait"],
                    "carry_remainder": True,
                },
            },
        }
        row.state = state
    return context, StructuredPlayService(context.database_url, rng=lambda _: 1)


def command(game, kind, payload, command_id=None, principal=KEEPER):
    context, service = game
    import uuid

    return service.execute_command(
        world_id=context.world_id,
        principal=principal,
        kind=kind,
        payload=payload,
        command_id=command_id or uuid.uuid4().hex,
        expected_revision=None,
    )


def state_of(game):
    context, _ = game
    with session_scope(context.database_url) as session:
        row = session.get(WorldState, context.world_id)
        return copy.deepcopy(row.state), row.revision


def grant(game, clue="seal", **extra):
    return command(
        game,
        "grant_clue",
        {
            "clue_id": clue,
            "recipient_investigator_ids": ["inv-alice"],
            "basis": "当场确认",
            **extra,
        },
    )


def acquire(game, clue="seal", **extra):
    return grant(
        game,
        clue,
        discovery_rule_index=0,
        discovery_investigator_id="inv-alice",
        acquire_item=True,
        **extra,
    )


def check(game, target="diary", *, skill="侦查", actor="inv-alice"):
    request = command(
        game,
        "request_check",
        {
            "investigator_id": actor,
            "skill": skill,
            "difficulty": "regular",
            "attempt": "搜查此对象",
            "visibility": "public",
            "target": {"kind": "scene_object", "id": target},
        },
    )
    check_id = request["result"]["check_request_id"]
    command(game, "resolve_check", {"check_request_id": check_id})
    return check_id


def use(game, item_id, **extra):
    return command(
        game,
        "use_item",
        {
            "investigator_id": "inv-alice",
            "item_id": item_id,
            "quantity": 1,
            "operation": "封印",
            "effect_clue_id": "sealed",
            "effect_rule_index": 0,
            "basis": "主持核对徽章覆在原件上，物品和情境吻合",
            **extra,
        },
    )


def test_keeper_catalog_contains_undiscovered_clues_but_players_never_receive_it(game):
    context, service = game
    keeper = service.session_snapshot(world_id=context.world_id, principal=KEEPER)
    assert {c["id"] for c in keeper["keeper_progress"]["clues"]} >= {"diary", "seal", "sealed"}
    assert (
        next(c for c in keeper["keeper_progress"]["clues"] if c["id"] == "seal")["discovered"]
        is False
    )
    for player in (ALICE, BOB):
        snapshot = service.session_snapshot(world_id=context.world_id, principal=player)
        assert "keeper_progress" not in snapshot
        assert "seal" not in {c["id"] for c in snapshot["clues"]}


def test_information_and_reading_do_not_acquire_or_set_possession_flags(game):
    grant(game, "document", discovery_rule_index=0, discovery_investigator_id="inv-alice")
    state, _ = state_of(game)
    assert "document_owned" not in state["flags"]
    assert not state.get("structured_acquisitions")
    assert not any(i["label"] == "文档原件" for i in state["item_registry"]["items"].values())
    assert state["case_clocks"]["clue_clarity"] == 1


def test_explicit_acquisition_gives_single_stable_object_only_to_selected_holder(game):
    result = acquire(game)
    item_id = result["result"]["acquired_item_id"]
    state, _ = state_of(game)
    assert state["flags"]["seal_owned"] is True
    assert state["item_registry"]["items"][item_id]["holder"] == {
        "kind": "investigator",
        "id": "inv-alice",
    }
    assert state["pc"].get("inventory", []).count("唯一银徽章") <= 1
    context, service = game
    for event in result["events"]:
        validate_event(wire_envelope(event))
    assert not any(
        e["type"] in {"inventory_changed", "keeper_progress_updated"}
        for e in service.replay_events(world_id=context.world_id, after_sequence=0, principal=BOB)
    )
    snapshot = service.session_snapshot(world_id=context.world_id, principal=ALICE)
    assert item_id in {i["id"] for i in snapshot["items"]}
    assert item_id not in {
        i["id"] for i in service.session_snapshot(world_id=context.world_id, principal=BOB)["items"]
    }
    acquire(game)
    assert (
        len(
            [
                i
                for i in state_of(game)[0]["item_registry"]["items"].values()
                if i["label"] == "唯一银徽章"
            ]
        )
        == 1
    )
    assert state_of(game)[0]["case_clocks"]["clue_clarity"] == 1


def test_transfer_preserves_acquisition_and_prevents_duplicate_generation(game):
    item_id = acquire(game)["result"]["acquired_item_id"]
    command(
        game,
        "transfer_item",
        {
            "item_id": item_id,
            "quantity": 1,
            "from": {"kind": "investigator", "id": "inv-alice"},
            "to": {"kind": "investigator", "id": "inv-bob"},
        },
    )
    before = state_of(game)
    with pytest.raises(StructuredError, match="已取得"):
        acquire(game)
    assert state_of(game) == before
    assert state_of(game)[0]["item_registry"]["items"][item_id]["source_clue_id"] == "seal"


def test_check_required_discovery_cannot_be_replaced_by_ordinary_dice(game):
    before = state_of(game)
    with pytest.raises(StructuredError, match="已成功检定"):
        acquire(game, "diary")
    assert state_of(game) == before
    check_id = check(game)
    result = acquire(game, "diary", check_request_id=check_id)
    assert result["result"]["acquired_item_id"]


@pytest.mark.parametrize("target,skill", [("document", "侦查"), ("diary", "说服")])
def test_other_target_or_skill_check_cannot_unlock_diary(game, target, skill):
    check_id = check(game, target, skill=skill)
    before = state_of(game)
    with pytest.raises(StructuredError):
        acquire(game, "diary", check_request_id=check_id)
    assert state_of(game) == before


def test_rolled_back_success_receipt_cannot_unlock_future_discovery(game):
    context, _ = game
    save_game([], "slot_008", context=context)
    check_id = check(game)
    restore_structured_save(context, "slot_008")
    with session_scope(context.database_url) as session:
        assert (
            session.query(CheckRequest).filter_by(check_request_id=check_id).one().status
            == "resolved"
        )
    before = state_of(game)
    with pytest.raises(StructuredError):
        acquire(game, "diary", check_request_id=check_id)
    assert state_of(game) == before


def test_first_global_record_advances_clock_once_not_per_recipient_or_replay(game):
    payload = {"clue_id": "seal", "recipient_investigator_ids": ["inv-alice"], "basis": "分享信息"}
    first = command(game, "grant_clue", payload, "first")
    command(game, "grant_clue", {**payload, "recipient_investigator_ids": ["inv-bob"]})
    replay = command(game, "grant_clue", payload, "first")
    assert replay["deduplicated"] and not replay["events"]
    assert state_of(game)[0]["case_clocks"]["clue_clarity"] == 1
    event = next(e for e in first["events"] if e["type"] == "keeper_progress_updated")
    assert event["audience"] == {"kind": "keeper"}
    assert event["payload"]["clocks"][0]["value"] == 1


def test_actual_use_settles_authored_seal_effect_without_ruling_or_consumption(game):
    acquire(game, "document")
    item_id = acquire(game)["result"]["acquired_item_id"]
    result = use(game, item_id)
    assert state_of(game)[0]["flags"]["sealed"] is True
    assert state_of(game)[0]["item_registry"]["items"][item_id]["quantity"] == 1
    assert result["result"]["effect_clue_id"] == "sealed"
    assert not state_of(game)[0].get("keeper_rulings")
    before = state_of(game)
    with pytest.raises(StructuredError, match="已经落实"):
        use(game, item_id)
    assert state_of(game) == before


def test_possession_flags_without_real_document_cannot_seal(game):
    item_id = acquire(game)["result"]["acquired_item_id"]
    context, _ = game
    with session_scope(context.database_url) as session:
        row = session.get(WorldState, context.world_id)
        state = copy.deepcopy(row.state)
        state["flags"]["document_owned"] = True
        row.state = state
    before = state_of(game)
    with pytest.raises(StructuredError, match="原件"):
        use(game, item_id)
    assert state_of(game) == before


def test_ordinary_item_or_wrong_holder_cannot_apply_authored_effect(game):
    acquire(game, "document")
    item_id = acquire(game)["result"]["acquired_item_id"]
    unrelated = next(
        i["item_id"]
        for i in state_of(game)[0]["item_registry"]["items"].values()
        if i["label"] == "绷带"
    )
    before = state_of(game)
    with pytest.raises(StructuredError):
        use(game, unrelated)
    with pytest.raises(StructuredError):
        use(game, item_id, investigator_id="inv-bob")
    assert state_of(game) == before


def test_use_effect_cannot_be_invoked_via_plain_grant_discovery(game):
    with pytest.raises(StructuredError, match="使用型"):
        grant(game, "sealed", discovery_rule_index=0, discovery_investigator_id="inv-alice")


def test_unbound_item_effect_requires_human_authorization_not_agent(game):
    acquire(game, "document")
    item_id = acquire(game)["result"]["acquired_item_id"]
    context, _ = game
    with session_scope(context.database_url) as session:
        bind_agent_control(session, context.world_id, "agent-test")
    with pytest.raises(StructuredError, match="人类主持"):
        command(
            game,
            "use_item",
            {
                "investigator_id": "inv-alice",
                "item_id": item_id,
                "quantity": 1,
                "operation": "封印",
                "effect_clue_id": "sealed",
                "effect_rule_index": 0,
                "basis": "模型猜测",
            },
            principal=Principal(kind="agent", run_id="agent-test"),
        )


@pytest.mark.parametrize("change", ["dead", "hp", "scene", "flags"])
def test_invalid_discovery_does_not_mutate_world(game, change):
    context, _ = game
    with session_scope(context.database_url) as session:
        row = session.get(WorldState, context.world_id)
        state = copy.deepcopy(row.state)
        if change == "dead":
            state["investigators"]["inv-alice"]["conditions"] = ["dead"]
        elif change == "hp":
            state["investigators"]["inv-alice"]["hp"] = 0
        elif change == "scene":
            state["current_scene"]["id"] = "library"
        row.state = state
    before = state_of(game)
    with pytest.raises(StructuredError):
        if change == "flags":
            grant(game, "hidden", discovery_rule_index=0, discovery_investigator_id="inv-alice")
        else:
            acquire(game)
    assert state_of(game) == before


def test_outbox_failure_rolls_back_card_object_flag_and_clock(game, monkeypatch):
    _, service = game
    before = state_of(game)

    def fail(*a, **kw):
        raise RuntimeError("outbox failure")

    monkeypatch.setattr(service, "_append_events", fail)
    with pytest.raises(RuntimeError):
        acquire(game)
    assert state_of(game) == before


def test_actual_scarlet_catalogue_and_take_use_chain_without_module_edits(game):
    authored = json.loads(
        (PROJECT_ROOT / "mod" / "猩红文档" / "world_state_initial.json").read_text()
    )
    context, service = game
    with session_scope(context.database_url) as session:
        row = session.get(WorldState, context.world_id)
        state = copy.deepcopy(row.state)
        for key in (
            "clue_catalog",
            "scene_catalog",
            "case_clocks",
            "case_clock_definitions",
            "flags",
        ):
            state[key] = copy.deepcopy(authored[key])
        state["current_scene"] = copy.deepcopy(authored["scene_catalog"]["trivial_pursuits"])
        state["current_scene"]["id"] = "trivial_pursuits"
        state["flags"].update({"feldman_ambushed": True, "deep_basement_found": True})
        row.state = state
    projection = service.session_snapshot(world_id=context.world_id, principal=KEEPER)[
        "keeper_progress"
    ]
    rule = next(c for c in projection["clues"] if c["id"] == "monster_sealed")["rules"][0]
    assert rule["approach"].startswith("你将银质徽章")
    assert {c["flag_id"] for c in rule["conditions"]} == {"documents_recovered", "seal_obtained"}
    # Reading the actual read-only author entry does not create either original.
    grant(
        game,
        "witch_trial_documents_read",
        discovery_rule_index=0,
        discovery_investigator_id="inv-alice",
    )
    assert state_of(game)[0]["flags"]["documents_recovered"] is False
    acquire(game, "witch_trial_documents")
    item_id = acquire(game, "abner_seal")["result"]["acquired_item_id"]
    use(game, item_id, effect_clue_id="monster_sealed")
    state, _ = state_of(game)
    assert state["flags"]["monster_defeated"] is True
    assert state["case_clocks"]["clue_clarity"] == 4
    assert len(state["structured_acquisitions"]) == 2
    assert not state.get("keeper_rulings")


def test_list_prerequisites_match_authored_truthy_convention(game):
    context, _ = game
    with session_scope(context.database_url) as session:
        row = session.get(WorldState, context.world_id)
        state = copy.deepcopy(row.state)
        state["clue_catalog"]["seal"]["discovery_rules"][0]["requires_flags"] = ["progress"]
        state["flags"]["progress"] = 1
        row.state = state
    assert acquire(game)["result"]["acquired_item_id"]


def test_acquired_artifacts_restore_and_branch_with_their_own_custody(game, tmp_path):
    context, service = game
    item_id = acquire(game)["result"]["acquired_item_id"]
    save_game([], "slot_008", context=context)
    branch = create_structured_branch(context, project_root=tmp_path, runtime_root=tmp_path)
    branch_game = (branch.context, service)
    command(
        branch_game,
        "transfer_item",
        {
            "item_id": item_id,
            "quantity": 1,
            "from": {"kind": "investigator", "id": "inv-alice"},
            "to": {"kind": "investigator", "id": "inv-bob"},
        },
    )
    assert state_of(game)[0]["item_registry"]["items"][item_id]["holder"]["id"] == "inv-alice"
    assert state_of(branch_game)[0]["item_registry"]["items"][item_id]["holder"]["id"] == "inv-bob"
    command(
        game,
        "use_item",
        {
            "investigator_id": "inv-alice",
            "item_id": item_id,
            "quantity": 1,
            "operation": "销毁",
            "consume": True,
        },
    )
    restore_structured_save(context, "slot_008")
    restored = state_of(game)[0]
    assert restored["item_registry"]["items"][item_id]["quantity"] == 1
    assert restored["structured_acquisitions"]["seal"]["item_id"] == item_id
    assert acquire(game)["result"]["acquired_item_id"] == item_id
    assert state_of(branch_game)[0]["item_registry"]["items"][item_id]["holder"]["id"] == "inv-bob"


def test_clue_clock_cap_and_corruption_do_not_duplicate_or_silently_repair(game):
    context, _ = game
    with session_scope(context.database_url) as session:
        row = session.get(WorldState, context.world_id)
        state = copy.deepcopy(row.state)
        state["case_clocks"]["clue_clarity"] = 5
        row.state = state
    grant(game)
    assert state_of(game)[0]["case_clocks"]["clue_clarity"] == 5
    with session_scope(context.database_url) as session:
        row = session.get(WorldState, context.world_id)
        state = copy.deepcopy(row.state)
        state["case_clocks"]["clue_clarity"] = True
        row.state = state
    before = state_of(game)
    with pytest.raises(StructuredError, match="时钟记录无效"):
        grant(game, "document")
    assert state_of(game) == before


def test_original_choices_follow_acquisition_transfer_consumption_and_refresh(game):
    context, service = game
    result = acquire(game)
    item_id = result["result"]["acquired_item_id"]
    grant(game, recipient_investigator_ids=["inv-bob"])

    def choices(principal):
        snapshot = service.session_snapshot(world_id=context.world_id, principal=principal)
        return next(c for c in snapshot["clues"] if c["id"] == "seal")

    assert choices(ALICE)["allowed_physical_item_ids"] == [item_id]
    assert "original" in choices(ALICE)["presentation"]
    assert choices(BOB)["allowed_physical_item_ids"] == []
    moved = command(
        game,
        "transfer_item",
        {
            "item_id": item_id,
            "quantity": 1,
            "from": {"kind": "investigator", "id": "inv-alice"},
            "to": {"kind": "investigator", "id": "inv-bob"},
        },
    )
    updates = {
        e["payload"]["investigator_id"]: e
        for e in moved["events"]
        if e["type"] == "clue_updated" and e["payload"]["clue_id"] == "seal"
    }
    assert updates["inv-alice"]["payload"]["allowed_physical_item_ids"] == []
    assert updates["inv-bob"]["payload"]["allowed_physical_item_ids"] == [item_id]
    assert all(e["audience"]["kind"] == "investigators" for e in updates.values())
    for e in moved["events"]:
        validate_event(wire_envelope(e))
    from src.structured.memories import _derive_plan

    assert not any(
        p.get("topics") == ["clue"]
        for p in _derive_plan(
            state_of(game)[0],
            command_kind="transfer_item",
            command_payload={},
            events=moved["events"],
        )
    )
    assert choices(ALICE)["allowed_physical_item_ids"] == []
    assert choices(BOB)["allowed_physical_item_ids"] == [item_id]
    consumed = command(
        game,
        "use_item",
        {
            "investigator_id": "inv-bob",
            "item_id": item_id,
            "quantity": 1,
            "operation": "销毁",
            "consume": True,
        },
    )
    assert (
        next(e for e in consumed["events"] if e["type"] == "clue_updated")["payload"][
            "allowed_physical_item_ids"
        ]
        == []
    )
    assert "original" not in choices(BOB)["presentation"]


def test_original_request_uses_the_acquired_stable_id_without_transferring_it(game):
    item_id = acquire(game)["result"]["acquired_item_id"]
    context, service = game
    before = state_of(game)
    result = service.submit_action_request(
        world_id=context.world_id,
        principal=ALICE,
        request={
            "request_id": "real-original",
            "investigator_id": "inv-alice",
            "expected_revision": None,
            "action": {
                "kind": "present_clue",
                "clue_id": "seal",
                "presentation": "original",
                "physical_item_id": item_id,
                "target": {"kind": "investigator", "id": "inv-bob"},
            },
        },
    )
    assert result["status"] == "queued"
    assert state_of(game) == before


def test_held_but_unrelated_object_cannot_be_presented_as_authored_original(game):
    grant(game)
    item_id = next(
        i["item_id"]
        for i in state_of(game)[0]["item_registry"]["items"].values()
        if i["label"] == "绷带"
    )
    context, service = game
    with pytest.raises(StructuredError, match="不是此线索"):
        service.submit_action_request(
            world_id=context.world_id,
            principal=ALICE,
            request={
                "request_id": "false-original",
                "investigator_id": "inv-alice",
                "expected_revision": None,
                "action": {
                    "kind": "present_clue",
                    "clue_id": "seal",
                    "presentation": "original",
                    "physical_item_id": item_id,
                    "target": {"kind": "investigator", "id": "inv-bob"},
                },
            },
        )
