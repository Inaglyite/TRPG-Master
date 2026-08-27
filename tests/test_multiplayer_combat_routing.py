from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.ai.tools.registry import TOOL_RUNTIME
from src.app.config import PROJECT_ROOT
from src.app.runtime import RuntimeContext
from src.gameplay.combat import combat_action, start_combat
from src.gameplay.investigators import activate_investigator, sync_active_investigator
from src.multiplayer.messages import run_room_message_loop
from src.multiplayer.room_runtime import GameRoom, RoomEventHub
from src.storage.persistence import load_game, restore_snapshot, save_game


class ReplySocket:
    def __init__(self, payload: dict):
        self.payload = payload
        self.reads = 0
        self.messages: list[dict] = []

    async def receive_text(self) -> str:
        self.reads += 1
        if self.reads == 1:
            return json.dumps(self.payload)
        raise RuntimeError("test complete")

    async def send_json(self, payload: dict) -> None:
        self.messages.append(dict(payload))


class RecordingDriver:
    def __init__(self):
        self.messages: list[dict] = []

    async def submit(self, raw: str) -> None:
        self.messages.append(json.loads(raw))


class MemoryStore:
    def __init__(self, state: dict):
        self.state = copy.deepcopy(state)

    def load(self) -> dict:
        return copy.deepcopy(self.state)

    def update(self, mutator):
        mutator(self.state)
        return SimpleNamespace(state=self.load())


def test_combat_defender_can_reply_when_not_the_room_current_actor():
    room = GameRoom(
        "world-defense-reply",
        SimpleNamespace(),
        RoomEventHub("world-defense-reply"),
        "alice",
        current_actor_user_id="alice",
        status="playing",
    )
    driver = RecordingDriver()
    room.driver_transport = driver
    room.set_pending_reply("decision", "bob", request_id="defend-bob")
    socket = ReplySocket(
        {
            "type": "decision_reply",
            "decision_id": "defend-bob",
            "option_id": "dodge",
        }
    )
    controller = SimpleNamespace(
        deps=SimpleNamespace(database_url=lambda: "sqlite://")
    )

    async def scenario() -> None:
        with (
            patch("src.multiplayer.messages.websocket_user", return_value=object()),
            patch("src.multiplayer.messages.authorize_world", return_value="player"),
        ):
            with pytest.raises(RuntimeError, match="test complete"):
                await run_room_message_loop(
                    controller,
                    socket,
                    room,
                    SimpleNamespace(id="bob"),
                    room.world_id,
                    "bob-tab",
                    "player",
                )

    asyncio.run(scenario())

    assert room.pending_reply_user_id is None
    assert len(driver.messages) == 1
    assert driver.messages[0]["type"] == "decision_reply"
    assert driver.messages[0]["_room_user_id"] == "bob"
    assert socket.messages == []


def test_multiplayer_combat_stable_ids_survive_save_load_and_runtime_reopen(
    tmp_path: Path,
):
    context = RuntimeContext.create(
        "stable-combat-save",
        "mansion_of_madness",
        project_root=PROJECT_ROOT,
        runtime_root=tmp_path,
    )
    world = context.world_store.load()
    base_pc = world.get("pc", {})
    alice = {
        **copy.deepcopy(base_pc),
        "name": "Alice",
        "investigator_id": "inv-alice",
        "controller_user_id": "alice",
        "hp": 12,
        "max_hp": 12,
        "attributes": {"DEX": 80},
        "skills": {"fighting_brawl": 55, "dodge": 40},
        "conditions": [],
    }
    bob = {
        **copy.deepcopy(base_pc),
        "name": "Bob",
        "investigator_id": "inv-bob",
        "controller_user_id": "bob",
        "hp": 11,
        "max_hp": 11,
        "attributes": {"DEX": 70},
        "skills": {"fighting_brawl": 45, "dodge": 35},
        "conditions": [],
    }
    world.update(
        {
            "active_investigator_id": "inv-alice",
            "investigator_controllers": {
                "alice": "inv-alice",
                "bob": "inv-bob",
            },
            "investigators": {
                "inv-alice": alice,
                "inv-bob": bob,
            },
            "pc": copy.deepcopy(alice),
            "npcs": [
                {
                    "id": "cultist",
                    "name": "教徒",
                    "hp": 9,
                    "max_hp": 9,
                    "attributes": {"DEX": 90},
                    "skills": {"fighting_brawl": 65, "dodge": 35},
                    "disposition": "hostile",
                    "conditions": [],
                }
            ],
        }
    )
    start_combat(world, [{"id": "cultist"}], "伏击")
    pending = combat_action(
        world,
        actor_id="cultist",
        target_id="inv-bob",
        action_type="melee",
        description="教徒攻击 Bob",
    )
    context.world_store.restore(world)
    save_game(
        [{"role": "system", "content": "multiplayer combat"}],
        "slot_001",
        context=context,
    )

    _messages, snapshot = load_game("slot_001", context=context)
    assert restore_snapshot(snapshot, context=context)
    reopened = RuntimeContext.create(
        "stable-combat-save",
        "mansion_of_madness",
        project_root=PROJECT_ROOT,
        runtime_root=tmp_path,
    )
    restored = reopened.world_store.load()

    combat = restored["combat_state"]
    assert combat["turn_order"] == ["cultist", "inv-alice", "inv-bob"]
    assert combat["pending_decision"]["id"] == pending["decision"]["id"]
    assert (
        combat["pending_decision"]["responding_investigator_id"]
        == "inv-bob"
    )
    assert combat["pending_decision"]["action"]["target_id"] == "inv-bob"


def test_turn_tools_mutate_only_the_acting_multiplayer_investigator(tmp_path: Path):
    context = RuntimeContext.create(
        "stable-tool-routing",
        "mansion_of_madness",
        project_root=PROJECT_ROOT,
        runtime_root=tmp_path,
    )
    world = context.world_store.load()
    base_pc = world["pc"]
    alice = {
        **copy.deepcopy(base_pc),
        "name": "Alice",
        "investigator_id": "inv-alice",
        "controller_user_id": "alice",
        "hp": 12,
        "max_hp": 12,
        "san": 70,
        "skills": {**base_pc.get("skills", {}), "spot_hidden": 15},
        "inventory": ["手电筒"],
    }
    bob = {
        **copy.deepcopy(base_pc),
        "name": "Bob",
        "investigator_id": "inv-bob",
        "controller_user_id": "bob",
        "hp": 11,
        "max_hp": 11,
        "san": 62,
        "skills": {**base_pc.get("skills", {}), "spot_hidden": 88},
        "inventory": ["笔记本"],
    }
    world.update(
        {
            "active_investigator_id": "inv-alice",
            "investigator_controllers": {
                "alice": "inv-alice",
                "bob": "inv-bob",
            },
            "investigators": {
                "inv-alice": alice,
                "inv-bob": bob,
            },
            "pc": copy.deepcopy(alice),
        }
    )
    context.world_store.restore(world)

    activate_investigator(context, "inv-bob")
    check = json.loads(
        TOOL_RUNTIME.execute("skill_check", {"skill": "spot_hidden"}, context)
    )
    TOOL_RUNTIME.execute(
        "apply_damage",
        {"target": "pc", "amount": 2, "damage_type": "物理"},
        context,
    )
    TOOL_RUNTIME.execute("state_add_item", {"item": "黄铜钥匙"}, context)
    TOOL_RUNTIME.execute(
        "sanity_event",
        {"description": "验收用恐怖事件", "severity": "1/1"},
        context,
    )
    sync_active_investigator(context)

    routed = context.world_store.load()
    assert check["skill_value"] == 88
    assert routed["investigators"]["inv-bob"]["hp"] == 9
    assert routed["investigators"]["inv-bob"]["san"] == 61
    assert "黄铜钥匙" in routed["investigators"]["inv-bob"]["inventory"]
    assert routed["investigators"]["inv-alice"]["hp"] == 12
    assert routed["investigators"]["inv-alice"]["san"] == 70
    assert routed["investigators"]["inv-alice"]["inventory"] == ["手电筒"]

    activate_investigator(context, "inv-alice")
    assert context.world_store.load()["pc"]["name"] == "Alice"


def test_owner_skip_moves_room_and_combat_turn_to_online_bob_together():
    store = MemoryStore(
        {
            "active_investigator_id": "inv-alice",
            "pc": {
                "investigator_id": "inv-alice",
                "controller_user_id": "alice",
            },
            "investigator_controllers": {
                "alice": "inv-alice",
                "bob": "inv-bob",
            },
            "investigators": {
                "inv-alice": {"controller_user_id": "alice"},
                "inv-bob": {"controller_user_id": "bob"},
            },
            "combat_state": {
                "active": True,
                "round": 1,
                "phase": "awaiting_action",
                "participants": [
                    {
                        "id": "inv-alice",
                        "name": "Alice",
                        "kind": "pc",
                        "hp": 10,
                        "conditions": [],
                    },
                    {
                        "id": "inv-bob",
                        "name": "Bob",
                        "kind": "pc",
                        "hp": 10,
                        "conditions": [],
                    },
                    {
                        "id": "cultist",
                        "name": "教徒",
                        "kind": "npc",
                        "hp": 8,
                        "conditions": [],
                    },
                ],
                "turn_order": ["inv-alice", "inv-bob", "cultist"],
                "turn_index": 0,
                "current_actor": "inv-alice",
                "pending_decision": None,
                "defense_counts": {},
                "log": [],
            },
        }
    )
    engine = SimpleNamespace(
        context=SimpleNamespace(
            world_store=store,
        )
    )
    room = GameRoom(
        "world-owner-skip",
        engine,
        RoomEventHub("world-owner-skip"),
        "owner",
        current_actor_user_id="alice",
        status="playing",
    )
    room.member_connected("bob")
    socket = ReplySocket({"type": "actor_assign", "user_id": "bob"})
    persisted: list[str | None] = []
    state_broadcasts: list[str | None] = []

    async def broadcast_room_state(target_room: GameRoom) -> None:
        state_broadcasts.append(target_room.current_actor_user_id)

    controller = SimpleNamespace(
        deps=SimpleNamespace(database_url=lambda: "sqlite://"),
        room_control_change_blocked=lambda target_room: (
            target_room.action_active or target_room.pending_reply_kind is not None
        ),
        room_roster=lambda _world_id: (
            [
                {"user_id": "alice", "investigator_id": "inv-alice"},
                {"user_id": "bob", "investigator_id": "inv-bob"},
            ],
            {"alice", "bob"},
        ),
        persist_room_control=lambda target_room: persisted.append(
            target_room.current_actor_user_id
        ),
        broadcast_room_state=broadcast_room_state,
    )

    async def scenario() -> None:
        with (
            patch("src.multiplayer.messages.websocket_user", return_value=object()),
            patch("src.multiplayer.messages.authorize_world", return_value="owner"),
            patch(
                "src.multiplayer.messages.room_members",
                return_value={
                    "members": [
                        {"user_id": "owner", "role": "owner"},
                        {"user_id": "alice", "role": "player"},
                        {"user_id": "bob", "role": "player"},
                    ]
                },
            ),
        ):
            with pytest.raises(RuntimeError, match="test complete"):
                await run_room_message_loop(
                    controller,
                    socket,
                    room,
                    SimpleNamespace(id="owner"),
                    room.world_id,
                    "owner-tab",
                    "owner",
                )

    asyncio.run(scenario())

    combat = store.load()["combat_state"]
    assert room.current_actor_user_id == "bob"
    assert combat["current_actor"] == "inv-bob"
    assert combat["turn_index"] == 1
    assert combat["log"][-1]["text"].endswith("由 Bob 行动")
    assert persisted == ["bob"]
    assert state_broadcasts == ["bob"]
    assert socket.messages == []
