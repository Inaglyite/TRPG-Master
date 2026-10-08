"""Real transactions: transfer cannot invent custodians or lose provenance."""

import copy
import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import func, select
from test_structured_commands import make_structured_world

from src.storage.database import EventOutbox, GameCommand, WorldState, session_scope
from src.structured.errors import StructuredError
from src.structured.principal import Principal
from src.structured.registries import ensure_item_registry
from src.structured.service import StructuredPlayService

KEEPER = Principal(kind="keeper", user_id="u-keeper")


@pytest.fixture
def game(tmp_path):
    context = make_structured_world(tmp_path)
    with session_scope(context.database_url) as session:
        row = session.get(WorldState, context.world_id)
        state = copy.deepcopy(row.state)
        state["npcs"][0]["inventory"] = ["NPC私有钥匙"]
        registry = ensure_item_registry(state)
        for item in registry["items"].values():
            if item["label"] == "绷带":
                item["source_clue_id"] = "documented-bandages"
                item["custom_metadata"] = {"origin": "不可丢失"}
        row.state = state
    return context, StructuredPlayService(context.database_url)


def saved(game):
    context, _ = game
    with session_scope(context.database_url) as session:
        row = session.get(WorldState, context.world_id)
        return copy.deepcopy(row.state), row.revision


def transfer(game, source, target, *, label="绷带", quantity=1, command_id=None, principal=KEEPER):
    context, service = game
    state, revision = saved(game)
    item = next(i for i in state["item_registry"]["items"].values() if i["label"] == label)
    return service.execute_command(
        world_id=context.world_id,
        principal=principal,
        kind="transfer_item",
        payload={"item_id": item["item_id"], "quantity": quantity, "from": source, "to": target},
        command_id=command_id or uuid.uuid4().hex,
        expected_revision=revision,
    )


@pytest.mark.parametrize("kind", ["investigator", "npc", "scene"])
def test_unknown_target_rejected_without_any_write(game, kind):
    before = saved(game)
    with pytest.raises(StructuredError, match="持有者不存在") as caught:
        transfer(game, {"kind": "investigator", "id": "inv-alice"}, {"kind": kind, "id": "missing"})
    assert caught.value.code == "unknown_target"
    assert saved(game) == before
    with session_scope(game[0].database_url) as session:
        assert session.scalar(select(func.count()).select_from(GameCommand)) == 0
        assert session.scalar(select(func.count()).select_from(EventOutbox)) == 0


@pytest.mark.parametrize(
    "target",
    [
        {"kind": "npc", "id": "keeper_npc"},
        {"kind": "scene", "id": "library"},
        {"kind": "investigator", "id": "inv-bob"},
    ],
)
def test_real_remote_custodian_allowed_and_split_keeps_metadata(game, target):
    result = transfer(game, {"kind": "investigator", "id": "inv-alice"}, target)
    state, _ = saved(game)
    moved = state["item_registry"]["items"][result["result"]["item_id"]]
    assert moved["holder"] == target
    assert moved["quantity"] == 1
    assert moved["source_clue_id"] == "documented-bandages"
    assert moved["custom_metadata"] == {"origin": "不可丢失"}
    assert (
        sum(i["quantity"] for i in state["item_registry"]["items"].values() if i["label"] == "绷带")
        == 2
    )
    inventory = [e for e in result["events"] if e["type"] == "inventory_changed"]
    assert inventory
    for event in inventory:
        recipient = event["payload"]["investigator_id"]
        assert event["audience"] in [
            {"kind": "keeper"},
            {"kind": "investigators", "investigator_ids": [recipient]},
        ]


def test_npc_gives_actual_item_without_granting_its_whole_inventory(game):
    result = transfer(
        game,
        {"kind": "npc", "id": "keeper_npc"},
        {"kind": "investigator", "id": "inv-alice"},
        label="NPC私有钥匙",
    )
    assert result["status"] == "committed"
    state, _ = saved(game)
    item = state["item_registry"]["items"][result["result"]["item_id"]]
    assert item["holder"] == {"kind": "investigator", "id": "inv-alice"}
    assert all(
        e["audience"].get("kind") != "public"
        for e in result["events"]
        if e["type"] == "inventory_changed"
    )


def test_outbox_failure_rolls_back_split_and_holder_changes(game):
    before = saved(game)
    with patch.object(game[1], "_append_events", side_effect=RuntimeError("outbox unavailable")):
        with pytest.raises(RuntimeError):
            transfer(
                game,
                {"kind": "investigator", "id": "inv-alice"},
                {"kind": "npc", "id": "keeper_npc"},
            )
    assert saved(game) == before


def test_incorrect_source_cannot_take_other_custodians_item(game):
    before = saved(game)
    with pytest.raises(StructuredError) as caught:
        transfer(
            game, {"kind": "investigator", "id": "inv-bob"}, {"kind": "scene", "id": "library"}
        )
    assert caught.value.code == "object_not_held"
    assert saved(game) == before


def test_committed_transfer_replay_does_not_repeat_or_split_again(game):
    source = {"kind": "npc", "id": "keeper_npc"}
    target = {"kind": "investigator", "id": "inv-alice"}
    first = transfer(game, source, target, label="NPC私有钥匙", command_id="npc-gives-key")
    after = saved(game)
    replay = transfer(game, source, target, label="NPC私有钥匙", command_id="npc-gives-key")
    assert replay["deduplicated"] is True
    assert replay["result"] == first["result"]
    assert saved(game) == after


def test_player_cannot_use_keeper_transfer_to_take_npc_item(game):
    before = saved(game)
    with pytest.raises(StructuredError):
        transfer(
            game,
            {"kind": "npc", "id": "keeper_npc"},
            {"kind": "investigator", "id": "inv-alice"},
            label="NPC私有钥匙",
            principal=Principal(kind="player", user_id="u-alice", investigator_ids=("inv-alice",)),
        )
    assert saved(game) == before
