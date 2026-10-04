"""Frozen history keeps recipient restrictions; ownership is not Keeper power."""

import pytest

from src.structured.history_archive_capture import archive_entry_visible
from src.structured.principal import Principal


def entry(audience, *, declaration=False, author=None):
    return {
        "message": {
            "speaker": {"kind": "investigator", "id": "inv-alice", "name": "爱丽丝"},
            "text": "申报尚未执行",
            **({"entry_kind": "action_request"} if declaration else {}),
        },
        "audience": audience,
        "submitted_by": author,
    }


@pytest.mark.parametrize("kind", ["player", "viewer"])
def test_room_members_and_revoked_keeper_cannot_read_keeper_archive(kind):
    assert not archive_entry_visible(
        entry({"kind": "keeper"}), Principal(kind=kind, user_id="room-owner")
    )


def test_viewer_with_stale_claims_cannot_read_old_private_messages_or_declarations():
    viewer = Principal(kind="viewer", user_id="u-alice", investigator_ids=("inv-alice",))
    assert archive_entry_visible(entry({"kind": "public"}), viewer)
    assert not archive_entry_visible(
        entry({"kind": "investigators", "investigator_ids": ["inv-alice"]}), viewer
    )
    assert not archive_entry_visible(
        entry({"kind": "keeper"}, declaration=True, author="u-alice"), viewer
    )


def test_new_character_controller_does_not_inherit_old_users_private_declaration():
    declaration = entry({"kind": "keeper"}, declaration=True, author="u-alice")
    assert archive_entry_visible(declaration, Principal(kind="player", user_id="u-alice"))
    assert not archive_entry_visible(
        declaration, Principal(kind="player", user_id="u-bob", investigator_ids=("inv-alice",))
    )
    assert archive_entry_visible(declaration, Principal(kind="keeper", user_id="u-keeper"))


def test_local_declaration_requires_current_claim_not_empty_user_id_equality():
    declaration = entry({"kind": "keeper"}, declaration=True)
    assert not archive_entry_visible(declaration, Principal(kind="player"))
    assert archive_entry_visible(
        declaration, Principal(kind="player", investigator_ids=("inv-alice",))
    )


@pytest.mark.parametrize(
    "malformed",
    [
        {"message": {}, "audience": None},
        entry({"kind": "unknown"}),
        entry({"kind": "unknown"}, declaration=True, author="u-alice"),
        entry({"kind": "public"}, declaration=True, author="u-alice"),
        {
            "message": {"entry_kind": "action_request", "speaker": {"kind": "npc"}},
            "audience": {"kind": "keeper"},
        },
    ],
)
def test_malformed_archive_cannot_be_promoted_to_keeper_visible(malformed):
    assert not archive_entry_visible(malformed, Principal(kind="keeper"))
