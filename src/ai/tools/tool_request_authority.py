"""Server-side authority registry for one in-flight model tool request.

Provider function calls and DSML blocks are untrusted model output.  Their
attached ``ToolRequestSnapshot`` is useful evidence, but it is not a bearer
token: an old snapshot must not be replayed in another turn or world.  This
module keeps the exact catalog that the server issued for the *current*
request on the engine instance and validates it immediately before execution.

The registry is intentionally in-memory and turn-local.  Tool calls are
executed synchronously inside the same world/turn lock that owns the model
stream; durable history stores public calls/outcomes, never this capability.
"""

from __future__ import annotations

import copy
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from src.ai.tools.tool_policy import ToolPolicyError, ToolRequestSnapshot

_REGISTRY_ATTR = "_issued_model_tool_request"
_EXECUTION_ATTR = "_executing_tool_request_snapshot"


@dataclass(frozen=True)
class IssuedToolRequest:
    """Exact catalog and scope a server sent to one provider request."""

    snapshot: ToolRequestSnapshot
    world_id: str
    turn_id: str | None
    catalog: tuple[dict[str, Any], ...]

    def catalog_copy(self) -> list[dict[str, Any]]:
        return copy.deepcopy(list(self.catalog))


def _scope(host: Any) -> tuple[str, str | None]:
    context = getattr(host, "context", None)
    world_id = str(getattr(context, "world_id", "") or "")
    turn_id = getattr(host, "active_turn_id", None) or getattr(host, "_active_turn_id", None)
    return world_id, str(turn_id) if turn_id else None


def issue_model_request(
    host: Any,
    snapshot: ToolRequestSnapshot,
    catalog: list[dict],
) -> IssuedToolRequest:
    """Replace the active capability with the exact just-sent catalog.

    A turn invokes the provider serially.  Replacing instead of accumulating
    entries makes a response from an earlier planning step invalid as soon as
    the next request is issued, while all calls in one provider response still
    share the same authority.
    """
    if snapshot.caller != "model":
        raise ToolPolicyError("invalid_caller", "只有模型请求可以签发工具能力")
    world_id, turn_id = _scope(host)
    if snapshot.world_id != world_id or snapshot.turn_id != turn_id:
        raise ToolPolicyError("request_scope_mismatch", "工具请求不属于当前世界或回合")
    issued = IssuedToolRequest(
        snapshot=snapshot,
        world_id=world_id,
        turn_id=turn_id,
        catalog=tuple(copy.deepcopy(catalog)),
    )
    host.__dict__[_REGISTRY_ATTR] = issued
    return issued


def issued_model_request(host: Any, snapshot: ToolRequestSnapshot) -> IssuedToolRequest:
    """Return the active issued request or raise a non-sensitive policy error."""
    issued = host.__dict__.get(_REGISTRY_ATTR)
    if not isinstance(issued, IssuedToolRequest):
        raise ToolPolicyError("request_not_issued", "工具请求未由当前服务端签发")
    if issued.snapshot != snapshot:
        raise ToolPolicyError("request_snapshot_mismatch", "工具请求快照与已签发请求不一致")
    world_id, turn_id = _scope(host)
    if (
        issued.world_id != world_id
        or issued.turn_id != turn_id
        or snapshot.world_id != world_id
        or snapshot.turn_id != turn_id
    ):
        raise ToolPolicyError("request_scope_mismatch", "工具请求不属于当前世界或回合")
    return issued


@contextmanager
def execution_snapshot(host: Any, snapshot: ToolRequestSnapshot) -> Iterator[None]:
    """Expose validated request evidence to domain handlers for one call only."""
    previous = host.__dict__.get(_EXECUTION_ATTR)
    host.__dict__[_EXECUTION_ATTR] = snapshot
    try:
        yield
    finally:
        if previous is None:
            host.__dict__.pop(_EXECUTION_ATTR, None)
        else:
            host.__dict__[_EXECUTION_ATTR] = previous


def current_execution_snapshot(host: Any) -> ToolRequestSnapshot | None:
    """Return scoped validated evidence for a handler, never raw call metadata."""
    value = host.__dict__.get(_EXECUTION_ATTR)
    return value if isinstance(value, ToolRequestSnapshot) else None
