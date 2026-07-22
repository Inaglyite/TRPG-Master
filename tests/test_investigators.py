from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

from src.investigators import (
    activate_investigator,
    initialize_investigator_roster,
    public_investigator_roster,
    sync_active_investigator,
    visible_clues_for_investigator,
)


class Store:
    def __init__(self, state):
        self.state = deepcopy(state)

    def update(self, mutator):
        mutator(self.state)
        return SimpleNamespace(state=deepcopy(self.state))

    def load(self):
        return deepcopy(self.state)


def _context(tmp_path: Path):
    module_dir = tmp_path / "mod"
    characters = module_dir / "characters"
    characters.mkdir(parents=True)
    (characters / "alice.json").write_text(
        '{"id":"alice","name":"Alice","occupation":"记者",'
        '"attributes":{"CON":50,"SIZ":50,"POW":60},"skills":{"spot_hidden":70},'
        '"derived":{"HP":10,"max_HP":10,"SAN":60,"max_SAN":60}}'
    )
    (characters / "bob.json").write_text(
        '{"id":"bob","name":"Bob","occupation":"医生",'
        '"attributes":{"CON":60,"SIZ":50,"POW":55},"skills":{"medicine":75},'
        '"derived":{"HP":11,"max_HP":11,"SAN":55,"max_SAN":55}}'
    )
    return SimpleNamespace(
        module_name="test-module",
        module_dir=module_dir,
        project_root=tmp_path,
        runtime_root=tmp_path / "runtime",
        custom_characters_dir=tmp_path / "custom",
        profiles_dir=tmp_path / "profiles",
        player_profile_file=tmp_path / "profiles" / "player_profile.json",
        default_characters_dir=tmp_path / "default",
        world_store=Store({"module_starting_inventory": [{"id": "torch"}], "pc": {}}),
    )


def test_roster_switches_legacy_pc_projection_without_losing_mutations(tmp_path: Path):
    context = _context(tmp_path)
    roster = [
        {
            "investigator_id": "inv-a",
            "user_id": "user-a",
            "character_ref": {"source": "module", "file": "alice.json", "module": "test-module"},
        },
        {
            "investigator_id": "inv-b",
            "user_id": "user-b",
            "character_ref": {"source": "module", "file": "bob.json", "module": "test-module"},
        },
    ]
    initialize_investigator_roster(context, roster, active_investigator_id="inv-a")
    assert context.world_store.state["pc"]["name"] == "Alice"
    assert len(context.world_store.state["investigators"]) == 2
    context.world_store.state["pc"]["hp"] = 7
    sync_active_investigator(context)

    activate_investigator(context, "inv-b")
    assert context.world_store.state["pc"]["name"] == "Bob"
    context.world_store.state["pc"]["san"] = 42
    activate_investigator(context, "inv-a")
    assert context.world_store.state["pc"]["hp"] == 7
    assert context.world_store.state["investigators"]["inv-b"]["san"] == 42
    public = public_investigator_roster(context.world_store.state)
    assert {item["name"] for item in public} == {"Alice", "Bob"}


def test_private_clues_are_visible_only_to_the_bound_investigator():
    clues = {
        "investigation": [
            {"id": "public", "text": "所有人可见", "visibility": "public"},
            {
                "id": "alice-only",
                "text": "Alice 的耳语",
                "visibility": "private",
                "owner_investigator_id": "inv-a",
            },
        ]
    }
    alice = visible_clues_for_investigator(clues, "inv-a")
    bob = visible_clues_for_investigator(clues, "inv-b")
    assert [item["id"] for item in alice["investigation"]] == [
        "public",
        "alice-only",
    ]
    assert [item["id"] for item in bob["investigation"]] == ["public"]
