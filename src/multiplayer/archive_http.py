"""Logical room-archive route kept separate from the general room control plane."""

from __future__ import annotations

import asyncio
import logging
import secrets
from collections.abc import Callable

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from src.auth.service import request_user
from src.multiplayer.room_runtime import ActionReservationError, GameRoom, RoomManager
from src.multiplayer.service import (
    MultiplayerError,
    abandon_solo_world,
    archive_world,
    check_solo_abandon_access,
    finish_room_action,
    reserve_room_action,
)
from src.storage.database import RoomAction, session_scope

logger = logging.getLogger(__name__)


def _archive_error(exc: MultiplayerError) -> JSONResponse:
    return JSONResponse(
        {"detail": exc.message, "code": exc.code},
        status_code=exc.status_code,
    )


def _world_has_running_action(db_url: str, world_id: str) -> bool:
    """Durable busy check for a world whose runtime is not loaded here."""
    with session_scope(db_url) as session:
        return (
            session.query(RoomAction.id).filter_by(world_id=world_id, status="running").first()
            is not None
        )


async def _teardown_archived_room(
    room_manager: RoomManager,
    world_id: str,
    *,
    room: GameRoom | None = None,
) -> None:
    """Close the loaded runtime after a committed logical archive.

    The database commit is the authority.  Closing sockets afterwards makes
    every already-connected client converge on the same ``room_deleted`` /
    4404 path without letting the old driver keep an orphaned input loop.
    ``room`` is optional because a connection may finish building while the
    archive transaction is in progress.
    """
    target = room or await room_manager.get(world_id)
    if target is None:
        return
    try:
        await target.hub.broadcast({"type": "room_deleted", "world_id": world_id})
    except Exception:
        logger.exception("归档后广播 room_deleted 失败 world_id=%s", world_id)
    try:
        await target.hub.disconnect_all(code=4404, reason="房间已删除")
    except Exception:
        logger.exception("归档后断开房间连接失败 world_id=%s", world_id)
    try:
        await room_manager.remove(world_id, target)
    except Exception:
        logger.exception("归档后移除房间运行时失败 world_id=%s", world_id)
    if target.driver_transport is not None:
        try:
            await target.driver_transport.close_input()
        except Exception:
            logger.exception("归档后关闭房间输入失败 world_id=%s", world_id)


def register_archive_world_route(
    router: APIRouter,
    *,
    database_url: Callable[[], str],
    room_manager: Callable[[], RoomManager],
) -> None:
    """Register the owner-only logical deletion endpoint for loaded or idle rooms."""

    @router.post("/api/worlds/{world_id}/abandon", status_code=204)
    async def abandon_active_solo_world(world_id: str, request: Request):
        """Let a solo owner give up an adventure without settling it first.

        Normal DELETE remains deliberately strict for every room type.  This
        explicit action exists only for a private solo world.  The player is
        not required to end the session: when a turn is streaming or a
        decision is pending, the archive transaction finalizes the orphaned
        durable leases as ``unknown`` and the runtime teardown below makes
        the room driver's ``finally`` cancel model streaming and wake the
        pending handshake with its safe default.  The whole branch tree is
        archived together, so no orphaned timeline worlds survive.
        """
        user = request_user(request, database_url())
        if user is None:
            return JSONResponse({"detail": "未登录"}, status_code=401)
        manager = room_manager()
        # Serialize against this world's get_or_create path.  Without this
        # boundary an HTTP abandon could miss RoomManager._loading while a WS
        # factory recovered its newly-created durable lease as ``unknown``.
        async with manager.world_lifecycle(world_id):
            try:
                access = check_solo_abandon_access(database_url(), world_id, user.id)
            except MultiplayerError as exc:
                return _archive_error(exc)
            if access["already_archived"]:
                # A prior request may have committed before its socket teardown
                # completed. Retrying must finish that cleanup, not merely 204.
                await _teardown_archived_room(manager, world_id)
                return Response(status_code=204)

            room = await manager.get(world_id)
            room_busy = room is not None and (
                room.action_active
                or room.pending_reply_kind is not None
                or room.terminal_event_pending
            )
            if room_busy or (
                room is None
                and await asyncio.to_thread(_world_has_running_action, database_url(), world_id)
            ):
                # 强制路径：回合/确认仍在进行，本地行动锁与持久租约都被它
                # 占着，无法再登记放弃租约。归档事务直接把树内所有进行中
                # 租约标 unknown，随后的拆除会取消流式生成。
                try:
                    result = abandon_solo_world(
                        database_url(),
                        world_id,
                        user.id,
                        runtime_room_status=room.status if room is not None else None,
                    )
                except MultiplayerError as exc:
                    # A concurrent request may have archived the world after
                    # the preflight. Preserve owner-only idempotency.
                    if exc.code == "world_not_found":
                        try:
                            access = check_solo_abandon_access(database_url(), world_id, user.id)
                        except MultiplayerError as access_exc:
                            return _archive_error(access_exc)
                        if access["already_archived"]:
                            await _teardown_archived_room(manager, world_id)
                            return Response(status_code=204)
                    return _archive_error(exc)
                except Exception:
                    logger.exception("放弃云端单人冒险失败 world_id=%s", world_id)
                    return JSONResponse(
                        {
                            "detail": "放弃冒险时发生服务器错误，请稍后重试",
                            "code": "abandon_unavailable",
                        },
                        status_code=503,
                    )
                for tree_world_id in result.get("tree_world_ids", [world_id]):
                    try:
                        await _teardown_archived_room(
                            manager,
                            tree_world_id,
                            room=room if tree_world_id == world_id else None,
                        )
                    except Exception:
                        # The archive itself already committed.  Do not report
                        # a false failure; a later idempotent request retries
                        # cleanup of any surviving runtime.
                        logger.exception("放弃后清理房间运行时失败 world_id=%s", tree_world_id)
                return Response(status_code=204)

            # 空闲路径：本地行动锁 + 持久租约双重序列化，防止放弃与即将
            # 开始的回合擦肩而过。整树归档仍会带走其他时间线的残留租约。
            action_id = f"solo-abandon:{secrets.token_urlsafe(18)}"
            local_reservation = False
            reservation_attempted = False
            archive_committed = False
            tree_world_ids: list[str] = [world_id]
            try:
                if room is not None:
                    # The owner/solo preflight above prevents a viewer from
                    # learning or briefly locking a live room by guessing an id.
                    try:
                        await room.reserve_control(user.id, action_id)
                    except ActionReservationError as exc:
                        raise MultiplayerError(exc.code, str(exc), 409) from exc
                    local_reservation = True
                    # reserve_control serializes input, then recheck the prompt
                    # edge in case a driver event arrived at that await point.
                    if room.pending_reply_kind is not None:
                        raise MultiplayerError(
                            "room_turn_in_progress",
                            "当前回合或确认请求尚未结束，请稍后再放弃冒险",
                            409,
                        )

                reservation_attempted = True
                reserve_room_action(
                    database_url(),
                    world_id,
                    action_id,
                    user.id,
                    "solo_abandon",
                    required_permission="manage",
                )
                result = abandon_solo_world(
                    database_url(),
                    world_id,
                    user.id,
                    reservation_action_id=action_id,
                    runtime_room_status=room.status if room is not None else None,
                )
                tree_world_ids = list(result.get("tree_world_ids", [world_id]))
                archive_committed = True
                # Keep the live lease until the archive becomes visible to
                # every client; the durable lease was completed atomically.
                for tree_world_id in tree_world_ids:
                    try:
                        await _teardown_archived_room(
                            manager,
                            tree_world_id,
                            room=room if tree_world_id == world_id else None,
                        )
                    except Exception:
                        # The archive itself already committed.  Do not report a
                        # false failure to the caller; a later idempotent request
                        # will retry cleanup of any surviving runtime.
                        logger.exception("放弃后清理房间运行时失败 world_id=%s", tree_world_id)
                return Response(status_code=204)
            except MultiplayerError as exc:
                # A different process may have archived the world after the
                # preflight. Preserve owner-only idempotency and clean leftovers.
                if exc.code == "world_not_found":
                    try:
                        access = check_solo_abandon_access(database_url(), world_id, user.id)
                    except MultiplayerError as access_exc:
                        return _archive_error(access_exc)
                    if access["already_archived"]:
                        await _teardown_archived_room(manager, world_id)
                        return Response(status_code=204)
                return _archive_error(exc)
            except Exception:
                logger.exception("放弃云端单人冒险失败 world_id=%s", world_id)
                return JSONResponse(
                    {
                        "detail": "放弃冒险时发生服务器错误，请稍后重试",
                        "code": "abandon_unavailable",
                    },
                    status_code=503,
                )
            finally:
                # Every unsuccessful path must free both leases.  The action is
                # only a serialization fence and never reaches the engine, so
                # marking it failed is safe even after an uncertain DB failure.
                if not archive_committed and reservation_attempted:
                    try:
                        finish_room_action(database_url(), world_id, action_id, "failed")
                    except Exception:
                        logger.exception("放弃失败后清理持久行动租约失败 world_id=%s", world_id)
                if local_reservation:
                    room.release_action(
                        terminal_status="completed" if archive_committed else "failed"
                    )

    @router.delete("/api/worlds/{world_id}", status_code=204)
    async def delete_world(world_id: str, request: Request):
        user = request_user(request, database_url())
        if user is None:
            return JSONResponse({"detail": "未登录"}, status_code=401)
        manager = room_manager()
        async with manager.world_lifecycle(world_id):
            # The archive service performs the owner gate before activity checks,
            # so this runtime hint cannot reveal whether a room is busy to a
            # non-member.
            room = await manager.get(world_id)
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

            # The committed archive is authoritative. Remove any loaded runtime
            # so connected and late clients consistently receive the 4404 path.
            await _teardown_archived_room(manager, world_id, room=room)
            return Response(status_code=204)
