from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from src.app.engine import GameEngine
from src.auth.service import create_user
from src.gameplay.combat import combat_action, start_combat
from src.gameplay.investigators import reconcile_investigator_roster
from src.multiplayer.private_state import reconcile_world_investigator_roster
from src.multiplayer.service import release_investigator, remove_member, update_member_role
from src.storage.database import (
    Base,
    World,
    WorldInvestigator,
    WorldMember,
    get_engine,
    new_id,
    session_scope,
)
from src.storage.database_store import DatabaseWorldStore


class MemoryStore:
    def __init__(self, state: dict):
        self.state = copy.deepcopy(state)

    def load(self) -> dict:
        return copy.deepcopy(self.state)

    def update(self, mutator):
        mutator(self.state)
        return SimpleNamespace(state=self.load())


def _database_url(tmp_path) -> str:
    return f"sqlite:///{tmp_path / 'roster-reconcile.db'}"


def _old_multiplayer_state(owner_id: str, player_id: str) -> dict:
    owner = {
        "name": "Owner",
        "investigator_id": "inv-owner",
        "controller_user_id": owner_id,
        "hp": 5,
        "max_hp": 11,
        "san": 44,
        "inventory": [{"id": "owner-key"}],
    }
    player = {
        "name": "Player",
        "investigator_id": "inv-player",
        "controller_user_id": player_id,
        "hp": 7,
        "max_hp": 10,
        "san": 51,
        "inventory": [{"id": "player-book"}],
    }
    return {
        "active_investigator_id": "inv-player",
        "pc": copy.deepcopy(player),
        "investigator_controllers": {
            owner_id: "inv-owner",
            player_id: "inv-player",
        },
        "investigators": {
            "inv-owner": owner,
            "inv-player": player,
        },
        "combat_state": {
            "active": True,
            "round": 1,
            "phase": "awaiting_action",
            "participants": [
                {
                    "id": "inv-owner",
                    "name": "Owner",
                    "kind": "pc",
                    "hp": 5,
                    "conditions": [],
                },
                {
                    "id": "inv-player",
                    "name": "Player",
                    "kind": "pc",
                    "hp": 7,
                    "conditions": [],
                },
            ],
            "turn_order": ["inv-player", "inv-owner"],
            "turn_index": 0,
            "current_actor": "inv-player",
            "pending_decision": None,
            "defense_counts": {},
            "log": [],
        },
    }


def _seed_world(tmp_path):
    url = _database_url(tmp_path)
    Base.metadata.create_all(get_engine(url))
    owner = create_user(url, "reconcile_owner", "owner password 123")
    player = create_user(url, "reconcile_player", "player password 123")
    with session_scope(url) as session:
        session.add(
            World(
                id="world-reconcile",
                module_name="mansion_of_madness",
                created_by=owner.id,
                metadata_json={"room_status": "playing"},
            )
        )
        session.add_all(
            [
                WorldMember(
                    id=new_id("member"),
                    world_id="world-reconcile",
                    user_id=owner.id,
                    role="owner",
                ),
                WorldMember(
                    id=new_id("member"),
                    world_id="world-reconcile",
                    user_id=player.id,
                    role="player",
                ),
                WorldInvestigator(
                    id="inv-owner",
                    world_id="world-reconcile",
                    character_key="owner-character",
                    character_ref={},
                    controller_user_id=owner.id,
                    status="claimed",
                ),
                WorldInvestigator(
                    id="inv-player",
                    world_id="world-reconcile",
                    character_key="player-character",
                    character_ref={},
                    controller_user_id=player.id,
                    status="claimed",
                ),
            ]
        )
    store = DatabaseWorldStore(
        url,
        "world-reconcile",
        tmp_path / "worlds" / "world-reconcile",
    )
    snapshot = _old_multiplayer_state(owner.id, player.id)
    store.initialize(snapshot)
    context = SimpleNamespace(
        module_name="mansion_of_madness",
        world_store=store,
    )
    return url, owner, player, store, context, snapshot


@pytest.mark.parametrize("revocation", ["remove", "demote", "release"])
def test_old_save_cannot_restore_revoked_investigator_controller(tmp_path, revocation):
    url, owner, player, store, context, old_snapshot = _seed_world(tmp_path)

    if revocation == "remove":
        remove_member(url, "world-reconcile", player.id, owner.id)
    elif revocation == "demote":
        update_member_role(url, "world-reconcile", player.id, owner.id, "viewer")
    else:
        with session_scope(url) as session:
            world = session.get(World, "world-reconcile")
            world.metadata_json = {**world.metadata_json, "room_status": "lobby"}
        release_investigator(url, "world-reconcile", "inv-player", owner.id)
        with session_scope(url) as session:
            world = session.get(World, "world-reconcile")
            world.metadata_json = {**world.metadata_json, "room_status": "playing"}

    # Recreate the dangerous ordering: control-plane revocation commits first,
    # then an old save restores the former controller metadata into WorldState.
    store.restore(old_snapshot)
    controllers = reconcile_world_investigator_roster(
        url,
        context,
        "world-reconcile",
        preferred_user_id=player.id,
    )
    state = store.load()

    assert controllers == {owner.id: "inv-owner"}
    assert state["investigator_controllers"] == controllers
    assert state["investigators"]["inv-player"]["controller_user_id"] is None
    assert state["investigators"]["inv-player"]["hp"] == 7
    assert state["investigators"]["inv-player"]["inventory"] == [{"id": "player-book"}]
    assert state["investigators"]["inv-owner"]["controller_user_id"] == owner.id
    assert state["investigators"]["inv-owner"]["hp"] == 5
    assert state["active_investigator_id"] == "inv-owner"
    assert state["pc"]["investigator_id"] == "inv-owner"
    assert state["combat_state"]["current_actor"] == "inv-owner"


def test_old_save_keeps_current_valid_claim_and_investigator_state(tmp_path):
    url, owner, player, store, context, old_snapshot = _seed_world(tmp_path)
    store.restore(old_snapshot)

    controllers = reconcile_world_investigator_roster(
        url,
        context,
        "world-reconcile",
        preferred_user_id=player.id,
    )
    state = store.load()

    assert controllers == {
        owner.id: "inv-owner",
        player.id: "inv-player",
    }
    assert state["active_investigator_id"] == "inv-player"
    assert state["pc"]["hp"] == 7
    assert state["pc"]["san"] == 51
    assert state["pc"]["inventory"] == [{"id": "player-book"}]


def test_restored_decision_for_revoked_controller_uses_default_without_waiting():
    world = _old_multiplayer_state("owner", "former-player")
    world.pop("combat_state")
    world["npcs"] = [
        {
            "id": "cultist",
            "name": "Cultist",
            "hp": 8,
            "max_hp": 8,
            "attributes": {"DEX": 90},
            "skills": {"fighting_brawl": 60},
            "conditions": [],
            "disposition": "hostile",
        }
    ]
    world["investigators"]["inv-owner"]["attributes"] = {"DEX": 60}
    world["investigators"]["inv-owner"]["skills"] = {"dodge": 40}
    world["investigators"]["inv-owner"]["conditions"] = []
    world["investigators"]["inv-player"]["attributes"] = {"DEX": 50}
    world["investigators"]["inv-player"]["skills"] = {"dodge": 35}
    world["investigators"]["inv-player"]["conditions"] = []
    world["pc"] = copy.deepcopy(world["investigators"]["inv-player"])
    start_combat(world, [{"id": "cultist"}], "revoked defender")
    pending = combat_action(
        world,
        actor_id="cultist",
        target_id="inv-player",
        action_type="melee",
        description="attack former player",
    )
    assert pending["requires_decision"] is True

    context = SimpleNamespace(
        module_name="mansion_of_madness",
        world_store=MemoryStore(world),
    )
    reconcile_investigator_roster(
        context,
        [
            {
                "investigator_id": "inv-owner",
                "user_id": "owner",
                "character_ref": {},
            }
        ],
        preferred_investigator_id="inv-owner",
    )
    callback_calls: list[dict] = []
    fake_engine = SimpleNamespace(
        context=context,
        _multiplayer_roster_active=True,
        cb=SimpleNamespace(on_decision=lambda decision: callback_calls.append(decision)),
        messages=[],
        _combat_state=lambda: context.world_store.load()["combat_state"],
    )

    GameEngine._resume_pending_combat_decision(fake_engine)

    assert callback_calls == []
    assert context.world_store.load()["combat_state"]["pending_decision"] is None
    assert fake_engine.messages[-1]["content"].startswith("[恢复的战斗决定已结算]")
