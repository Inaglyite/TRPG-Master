"""Realtime room notifications emitted by multiplayer HTTP mutations."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from src.multiplayer.room_runtime import GameRoom, RoomManager


async def broadcast_investigator_change(
    room_manager: RoomManager,
    broadcast_room_state: Callable[[GameRoom], Awaitable[None]],
    world_id: str,
    event_type: str,
    result: dict,
) -> None:
    room = await room_manager.get(world_id)
    if room is None:
        return
    user_id = str(result.get("user_id") or "")
    if user_id:
        room.set_ready(user_id, False)
    await room.hub.broadcast(
        {
            "type": event_type,
            "user_id": user_id or None,
            "investigator_id": result["id"],
            "character_key": result["character_key"],
        }
    )
    await broadcast_room_state(room)
