"""Starting inventory merges once; consumption and transfer never resurrect it."""

import copy

from src.structured.registries import ensure_item_registry, register_investigator_inventory


def test_preexisting_npc_id_and_same_label_other_holder_remain_unchanged():
    state = {"npcs": [{"id": "doctor", "inventory": ["绷带"]}]}
    registry = ensure_item_registry(state)
    original = copy.deepcopy(registry["items"])
    register_investigator_inventory(
        state, {"alice": {"inventory": ["绷带", "绷带"]}, "bob": {"inventory": ["绷带"]}}
    )
    assert all(registry["items"][key] == value for key, value in original.items())
    own = {e["holder"]["id"]: e["quantity"] for e in registry["items"].values()}
    assert own == {"doctor": 1, "alice": 2, "bob": 1}


def test_repeat_after_consumption_and_full_transfer_does_not_reseed():
    state = {}
    roster = {"alice": {"inventory": ["绷带", "钥匙"]}, "bob": {"inventory": []}}
    register_investigator_inventory(state, roster)
    registry = state["item_registry"]
    for entry in registry["items"].values():
        if entry["label"] == "绷带":
            entry["quantity"] = 0
        else:
            entry["holder"] = {"kind": "investigator", "id": "bob"}
    settled = copy.deepcopy(registry)
    register_investigator_inventory(state, roster)
    assert registry == settled


def test_existing_registry_without_marker_uses_original_stack_owner_not_current_holder():
    state = {"investigators": {"alice": {"inventory": ["钥匙"]}}}
    registry = ensure_item_registry(state)
    entry = next(iter(registry["items"].values()))
    item_id = entry["item_id"]
    entry["holder"] = {"kind": "investigator", "id": "bob"}
    register_investigator_inventory(state, state["investigators"])
    assert list(registry["items"]) == [item_id]
    assert entry["holder"]["id"] == "bob"


def test_empty_inventory_is_initialized_once_not_inferred_as_missing():
    state = {}
    register_investigator_inventory(state, {"alice": {"inventory": []}})
    register_investigator_inventory(state, {"alice": {"inventory": ["不应凭空出现"]}})
    assert state["item_registry"]["items"] == {}
