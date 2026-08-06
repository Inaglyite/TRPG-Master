"""Logical room-archive route kept separate from the general room control plane."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from .auth import request_user
from .multiplayer import MultiplayerError, archive_world
from .room_runtime import RoomManager


def _archive_error(exc: MultiplayerError) -> JSONResponse:
    return JSONResponse(
        {"detail": exc.message, "code": exc.code},
        status_code=exc.status_code,
    )


def register_archive_world_route(
    router: APIRouter,
    *,
    database_url: Callable[[], str],
    room_manager: Callable[[], RoomManager],
) -> None:
    """Register the owner-only logical deletion endpoint for loaded or idle rooms."""

    @router.delete("/api/worlds/{world_id}", status_code=204)
    async def delete_world(world_id: str, request: Request):
        user = request_user(request, database_url())
        if user is None:
            return JSONResponse({"detail": "未登录"}, status_code=401)
        # The archive service performs the owner gate before activity checks, so
        # this runtime hint cannot reveal whether a room is busy to non-members.
        room = await room_manager().get(world_id)
        runtime_room_status = room.status if room is not None else None
        try:
            archive_world(
                database_url(),
                world_id,
                user.id,
                runtime_room_status=runtime_room_status,
            )
        except MultiplayerError as exc:
            return _archive_error(exc)

        # The committed archive is authoritative. Remove any loaded runtime so
        # connected and late WebSocket clients consistently receive the 4404 path.
        room = await room_manager().get(world_id)
        if room is not None:
            await room.hub.broadcast({"type": "room_deleted", "world_id": world_id})
            await room.hub.disconnect_all(code=4404, reason="房间已删除")
            await room_manager().remove(world_id, room)
            if room.driver_transport is not None:
                await room.driver_transport.close_input()
        return Response(status_code=204)
