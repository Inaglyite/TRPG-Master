"""Pure projection preparation; not a substitute for service/HTTP acceptance."""

from src.structured.received_materials import received_assets


def test_only_own_explicit_grants_are_projected_deduplicated_and_path_free():
    state = {
        "assets": {
            "photo": {"file": "private/scene/photo.PNG", "label": "公开标题"},
            "secret": {"file": "secret.webp", "label": "不可下放"},
            "document": {"file": "story.md"},
        },
        "asset_grants": [
            {"asset_id": "photo", "investigator_id": "alice"},
            {"asset_id": "photo", "investigator_id": "alice"},
            {"asset_id": "secret", "investigator_id": "bob"},
            {"asset_id": "document", "investigator_id": "alice"},
            {"asset_id": "nonexistent", "investigator_id": "alice"},
        ],
        "seen_assets": ["secret"],
    }
    assert received_assets(state, ("alice",)) == [{"id": "photo", "label": "公开标题"}]
    assert received_assets(state, ()) == []
    assert received_assets(state, ("nobody",)) == []
    assert received_assets(state, ("bob",)) == [{"id": "secret", "label": "不可下放"}]


def test_conflicting_registry_ids_are_not_projected():
    state = {
        "assets": {"photo": {"file": "a.png"}},
        "handout_assets": {"photo": {"file": "b.png"}},
        "asset_grants": [{"asset_id": "photo", "investigator_id": "alice"}],
    }
    assert received_assets(state, ("alice",)) == []


def test_malformed_grants_do_not_crash_or_gain_access():
    state = {
        "assets": {"photo": {"file": "a.png"}},
        "asset_grants": [
            None,
            "photo",
            {},
            {"asset_id": ["photo"], "investigator_id": "alice"},
            {"asset_id": "photo", "investigator_id": ["alice"]},
        ],
    }
    assert received_assets(state, ("alice",)) == []
    state["asset_grants"] = {"photo": "alice"}
    assert received_assets(state, ("alice",)) == []
