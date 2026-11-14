"""Transient reasoning delivery. The normal turn journal must never receive it."""

from __future__ import annotations

import asyncio

from sqlalchemy import select


def private_event_sink(outbound, emit):
    def receive(info):
        if info.get("type") == "model_reasoning":
            outbound.emit(info)
        else:
            emit({"type": "private_event", **info})

    return receive


def _allowed_users(room):
    from src.storage.database import WorldMember, session_scope

    context = room.engine.context
    with session_scope(context.database_url) as session:
        query = select(WorldMember.user_id).where(WorldMember.world_id == room.world_id)
        if room.play_mode == "solo":
            query = query.where(
                WorldMember.user_id == room.owner_user_id, WorldMember.role == "owner"
            )
        else:
            query = query.where(WorldMember.can_keeper.is_(True))
        return set(session.scalars(query))


async def deliver_room_reasoning(room, payload):
    if payload.get("world_id") not in (None, room.world_id):
        return
    allowed = await asyncio.to_thread(_allowed_users, room)
    wire = {**payload, "world_id": room.world_id}
    # No RoomEventHub.broadcast: raw thinking is absent from replay/public history.
    for connection in await room.hub.connection_snapshot():
        if connection["user_id"] in allowed:
            await room.hub.send_direct(connection["connection_id"], wire)
