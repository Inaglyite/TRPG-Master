"""Consistent multiplayer recovery images and ordered direct socket writes."""

from __future__ import annotations

import copy
from collections.abc import Callable, Sequence
from typing import Any

from .asset_payload import asset_payload, enrich_pc_for_frontend
from .investigators import (
    investigator_entity,
    public_investigator_roster,
    visible_clues_for_investigator,
)
from .narrative_history import enrich_public_history, enrich_public_history_record
from .player_notes import PlayerNotesStore
from .room_runtime import BufferedRoomEvent, GameRoom, RoomEventHub

_STATE_CONTROLLER = object()


def turn_recovery_payload(engine: Any, requested_turn_id: str | None = None) -> dict:
    """Return the public turn-journal recovery contract for a room member.

    Room recovery images and turn-journal recovery serve different purposes:
    the former restores roster/private state, while this payload answers
    whether an interrupted model turn was committed and, when completed,
    provides its replayable public events. Keep the same speaker/asset
    enrichment as the single-player WebSocket without exposing prompts or
    tool arguments.
    """
    requested = requested_turn_id if isinstance(requested_turn_id, str) else None
    payload = engine.turn_recovery_status(requested)
    for key in ("requested", "active", "latest_completed"):
        record = payload.get(key)
        if not isinstance(record, dict):
            continue
        public = enrich_public_history_record(record, engine)
        for event in public.get("events") or []:
            if not isinstance(event, dict) or event.get("type") != "handout":
                continue
            filename = event.get("file")
            if isinstance(filename, str) and filename:
                event.update(asset_payload(filename, engine.context))
        payload[key] = public
    return {"type": "turn_recovery", **payload}


def public_history_snapshot(room: GameRoom) -> list[dict]:
    """Return the committed, public narrative history for one room."""
    try:
        return enrich_public_history(
            room.engine.turn_journal.public_history(),
            room.engine,
        )
    except Exception:
        return []


def private_recovery_payload(
    room: GameRoom,
    user_id: str,
    enrich_clues: Callable[[dict, dict | None, Any | None], dict],
    *,
    investigator_id: str | None | object = _STATE_CONTROLLER,
) -> dict:
    """Build recovery data visible only to one authenticated room member."""
    try:
        world_state = room.engine.context.world_store.load()
        controllers = world_state.get("investigator_controllers", {})
        if investigator_id is _STATE_CONTROLLER:
            investigator_id = controllers.get(user_id) if isinstance(controllers, dict) else None
        own_pc = (
            investigator_entity(world_state, investigator_id) if investigator_id else {}
        )
        pc_data = enrich_pc_for_frontend(
            own_pc if isinstance(own_pc, dict) else {},
            room.engine.context,
        )
        clues_data = enrich_clues(
            visible_clues_for_investigator(
                world_state.get("clues_found", {}),
                investigator_id,
            ),
            world_state,
            room.engine.context,
        )
    except Exception:
        investigator_id, pc_data, clues_data = None, {}, {}
    try:
        notes = PlayerNotesStore(
            room.engine.context.world_dir,
            user_id=user_id,
        ).load()
    except (OSError, TypeError, ValueError, RuntimeError):
        notes = {"text": "", "revision": 0}
    return {
        "investigator_id": investigator_id,
        "pc": pc_data,
        "clues": clues_data,
        "player_notes": notes,
    }


def pending_reply_payload(
    room: GameRoom,
    user_id: str,
    events: Sequence[BufferedRoomEvent],
) -> dict | None:
    """Recover the active actor's modal request from the private replay buffer."""
    kind = room.pending_reply_kind
    if kind not in {"suggest", "decision"} or room.pending_reply_user_id != user_id:
        return None
    event_type = "suggest_check" if kind == "suggest" else "decision_request"
    visibility = f"player:{user_id}"
    for event in reversed(events):
        if event.visibility != visibility or event.payload.get("type") != event_type:
            continue
        if (
            kind == "decision"
            and str(event.payload.get("id") or "") != room.pending_reply_request_id
        ):
            continue
        payload = dict(event.payload)
        payload["recovered"] = True
        return payload
    return None


def recovery_messages(
    room: GameRoom,
    user_id: str,
    enrich_clues: Callable[[dict, dict | None, Any | None], dict],
    latest_event_id: int,
    events: Sequence[BufferedRoomEvent],
    *,
    investigator_id: str | None | object = _STATE_CONTROLLER,
) -> list[dict]:
    """Build a full image and optional modal at one hub event boundary."""
    action_in_flight = room.action_active or room.terminal_event_pending
    history = (
        copy.deepcopy(room.recovery_history) if action_in_flight else public_history_snapshot(room)
    )
    recovery_event_id = room.active_action_start_event_id if action_in_flight else latest_event_id
    try:
        state = room.engine.context.world_store.load()
        investigators = public_investigator_roster(state)
        active_investigator_id = state.get("active_investigator_id")
    except Exception:
        investigators = []
        active_investigator_id = None
    pending = pending_reply_payload(room, user_id, events)
    full = {
        "type": "room_full_state",
        "status": room.status,
        "owner_user_id": room.owner_user_id,
        "current_actor_user_id": room.current_actor_user_id,
        "ready_user_ids": sorted(room.ready_users),
        "online_user_ids": sorted(room.connected_users),
        "latest_event_id": recovery_event_id,
        "history": history,
        "investigators": investigators,
        "active_investigator_id": active_investigator_id,
        "private_state": private_recovery_payload(
            room,
            user_id,
            enrich_clues,
            investigator_id=investigator_id,
        ),
        "pending_reply": pending,
    }
    messages = [full]
    if isinstance(pending, dict):
        recovered = dict(pending)
        recovered.pop("room_event_id", None)
        messages.append(recovered)
    return messages


class OrderedRoomSocket:
    """Delegate direct controller replies through a room's ordered send chain."""

    def __init__(
        self,
        socket: Any,
        hub: RoomEventHub,
        connection_id: str,
    ):
        self._socket = socket
        self._hub = hub
        self._connection_id = connection_id

    async def send_json(self, payload: dict[str, Any]) -> None:
        if not await self._hub.send_direct(self._connection_id, payload):
            raise RuntimeError("room connection is no longer active")

    def __getattr__(self, name: str) -> Any:
        return getattr(self._socket, name)
