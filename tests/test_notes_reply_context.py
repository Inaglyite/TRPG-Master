import pytest

from src.storage.notes_reply_context import notes_reply_context


@pytest.mark.parametrize("request_id", [None, 3, {}, "", "x" * 97, "bad\nrequest", "用户"])
def test_malformed_correlation_is_not_echoed(request_id):
    assert notes_reply_context("actual-world", {"request_id": request_id}) == {
        "world_id": "actual-world"
    }


def test_legacy_request_and_valid_correlation_do_not_choose_the_world():
    assert notes_reply_context("actual-world", {}) == {"world_id": "actual-world"}
    assert notes_reply_context(
        "actual-world", {"world_id": "attacker-world", "request_id": "notes:request-1"}
    ) == {"world_id": "actual-world", "request_id": "notes:request-1"}
