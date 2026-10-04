"""Bounded request correlation, never a client-controlled world or access grant."""

import re
from collections.abc import Mapping

_REQUEST_ID = re.compile(r"[A-Za-z0-9._:-]{1,96}\Z")


def notes_reply_context(world_id: str, request: Mapping[str, object]) -> dict[str, str]:
    result = {"world_id": world_id}
    request_id = request.get("request_id")
    if isinstance(request_id, str) and _REQUEST_ID.fullmatch(request_id):
        result["request_id"] = request_id
    return result
