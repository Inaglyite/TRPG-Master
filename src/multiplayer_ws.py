"""Authoritative multiplayer WebSocket adapter and recovery boundary."""

from __future__ import annotations

import asyncio
import os
import secrets
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from .auth import authorize_world, validate_websocket_origin, websocket_session
from .characters import list_character_options
from .database import Turn, World, WorldInvestigator, WorldMember, session_scope, utcnow
from .model_settings import ModelSettings
from .multiplayer import (
    finish_room_action,
    recover_room_actions,
    world_play_mode,
)
from .multiplayer_guards import USER_TURN_GUARD
from .multiplayer_messages import (
    owner_turn_required as owner_turn_required,
)
from .multiplayer_messages import (
    run_room_message_loop,
)
from .multiplayer_private_state import reconcile_world_investigator_roster
from .multiplayer_recovery import (
    OrderedRoomSocket,
    private_recovery_payload,
    public_history_snapshot,
    recovery_messages,
)
from .room_runtime import (
    GameRoom,
    RoomConnection,
    RoomDriverTransport,
    RoomEventHub,
    RoomManager,
)
from .runtime import RuntimeContext
from .solo_timeline_ws import (
    SOLO_SWITCH_CLOSE_CODE,
    resolve_solo_current_world_id,
)


@dataclass(frozen=True)
class MultiplayerWsDependencies:
    database_url: Callable[[], str]
    room_manager: Callable[[], RoomManager]
    active_model_settings: Callable[[], ModelSettings]
    engine_factory: Callable[[RuntimeContext], Any]
    run_ws_session: Callable[..., Awaitable[None]]
    list_modules: Callable[[], list[dict]]
    load_theme: Callable[[RuntimeContext], dict]
    model_settings_payload: Callable[[ModelSettings], dict]
    enrich_clues: Callable[[dict, dict | None, RuntimeContext | None], dict]
    project_root: Path
    runtime_root: Path


class MultiplayerWsController:
    def __init__(self, deps: MultiplayerWsDependencies):
        self.deps = deps

    def router(self) -> APIRouter:
        router = APIRouter()
        router.add_api_websocket_route("/ws/room", self.websocket)
        return router

    async def room_bootstrap(self, ws: WebSocket, room: GameRoom) -> None:
        engine = room.engine
        await ws.send_json(
            {
                "type": "module_list",
                "modules": self.deps.list_modules(),
                "active": engine.context.module_name,
                "world_id": engine.context.world_id,
                "module_name": engine.context.module_name,
            }
        )
        await ws.send_json(
            {
                "type": "character_list",
                **list_character_options(
                    engine.context.module_name,
                    context=engine.context,
                    include_personal=False,
                ),
            }
        )
        await ws.send_json({"type": "theme", "theme": self.deps.load_theme(engine.context)})
        await ws.send_json(
            self.deps.model_settings_payload(
                ModelSettings.validated(engine.narrative_model, engine.judgement_model)
            )
        )
        await ws.send_json({"type": "save_list", "saves": engine.list_saves()})

    def authoritative_investigator_id(
        self,
        world_id: str,
        user_id: str,
        role: str,
    ) -> str | None:
        """Resolve private-state ownership from the control plane, not stale JSON."""
        if role not in {"owner", "player"}:
            return None
        with session_scope(self.deps.database_url()) as db_session:
            member = (
                db_session.query(WorldMember)
                .filter_by(world_id=world_id, user_id=user_id)
                .one_or_none()
            )
            if member is None or member.role not in {"owner", "player"}:
                return None
            claim = (
                db_session.query(WorldInvestigator)
                .filter_by(
                    world_id=world_id,
                    controller_user_id=user_id,
                    status="claimed",
                )
                .one_or_none()
            )
            return claim.id if claim is not None else None

    def room_private_recovery_payload(
        self,
        room: GameRoom,
        user_id: str,
        *,
        role: str | None = None,
    ) -> dict:
        """Build recovery data visible only to one authenticated room member."""
        if role is None:
            return private_recovery_payload(
                room,
                user_id,
                self.deps.enrich_clues,
            )
        return private_recovery_payload(
            room,
            user_id,
            self.deps.enrich_clues,
            investigator_id=self.authoritative_investigator_id(
                room.world_id,
                user_id,
                role,
            ),
        )

    def _recovery_messages_factory(
        self,
        room: GameRoom,
        user_id: str,
        role: str | None = None,
    ) -> Callable[[int, tuple], list[dict]]:
        if role is None:
            return lambda latest_event_id, events: recovery_messages(
                room,
                user_id,
                self.deps.enrich_clues,
                latest_event_id,
                events,
            )
        investigator_id = self.authoritative_investigator_id(
            room.world_id,
            user_id,
            role,
        )
        return lambda latest_event_id, events: recovery_messages(
            room,
            user_id,
            self.deps.enrich_clues,
            latest_event_id,
            events,
            investigator_id=investigator_id,
        )

    async def room_full_recovery_payload(
        self,
        room: GameRoom,
        user_id: str,
        *,
        role: str | None = None,
    ) -> dict:
        """Build a public recovery image plus the requesting member's private state."""
        messages = await room.hub.build_at_boundary(
            self._recovery_messages_factory(room, user_id, role)
        )
        return messages[0]

    async def pending_reply_recovery_payload(
        self,
        room: GameRoom,
        user_id: str,
        *,
        role: str | None = None,
    ) -> dict | None:
        """Recover the active actor's modal request without replay cursor races."""
        payload = await self.room_full_recovery_payload(room, user_id, role=role)
        pending = payload.get("pending_reply")
        return pending if isinstance(pending, dict) else None

    async def send_room_full_recovery(
        self,
        ws: WebSocket,
        room: GameRoom,
        user_id: str,
        *,
        role: str | None = None,
        connection_id: str | None = None,
        include_pending_reemit: bool = False,
    ) -> int:
        """Send a full image, then re-emit a pending modal outside event dedupe."""
        factory = self._recovery_messages_factory(room, user_id, role)
        if connection_id is not None:
            delivered, replay_cursor = await room.hub.send_snapshot_with_replay(
                connection_id,
                factory,
            )
            if not delivered:
                raise RuntimeError("room connection is no longer active")
            return replay_cursor
        messages = await room.hub.build_at_boundary(factory)
        if not include_pending_reemit:
            messages = messages[:1]
        for payload in messages:
            await ws.send_json(payload)
        return int(messages[0].get("latest_event_id") or 0)

    @staticmethod
    def room_control_change_blocked(room: GameRoom) -> bool:
        return room.action_active or room.pending_reply_kind is not None

    @staticmethod
    def room_state_payload(room: GameRoom) -> dict:
        return {
            "type": "room_state",
            "status": room.status,
            "owner_user_id": room.owner_user_id,
            "current_actor_user_id": room.current_actor_user_id,
            "ready_user_ids": sorted(room.ready_users),
            "online_user_ids": sorted(tuple(room.connected_users)),
            # 前端据此推导 timelineCapabilities（solo + 房主），不再按
            # mode !== "local" 硬编码门禁；服务端仍逐消息独立校验。
            "play_mode": room.play_mode,
        }

    def set_room_status(self, room: GameRoom, status: str) -> None:
        room.status = status
        self.persist_room_control(room)

    async def broadcast_room_state(self, room: GameRoom) -> None:
        await room.hub.broadcast(self.room_state_payload(room))

    def persist_room_control(self, room: GameRoom) -> None:
        with session_scope(self.deps.database_url()) as db_session:
            world = db_session.get(World, room.world_id)
            if world is None:
                return
            metadata = dict(world.metadata_json or {})
            metadata["room_status"] = room.status
            metadata["current_actor_user_id"] = room.current_actor_user_id
            world.metadata_json = metadata
            world.updated_at = utcnow()

    def refresh_room_control(self, room: GameRoom) -> None:
        """Reconcile a newly built runtime with the current database control plane.

        Engine construction runs in a worker thread and can take long enough for
        an owner transfer, role downgrade, or member removal to commit meanwhile.
        While a room is still in ``RoomManager._loading``, those HTTP handlers
        cannot update the not-yet-published ``GameRoom``. Re-reading immediately
        after publication closes that gap; the synchronous read and assignments
        contain no event-loop yield, so a concurrent handler either runs wholly
        before this reconciliation or updates the published room afterwards.
        """
        with session_scope(self.deps.database_url()) as db_session:
            world = db_session.get(World, room.world_id)
            if world is None or world.status != "active":
                raise RuntimeError("房间不存在")
            owner = (
                db_session.query(WorldMember)
                .filter_by(world_id=room.world_id, role="owner")
                .one_or_none()
            )
            if owner is None:
                raise RuntimeError("房间没有有效房主")
            owner_user_id = owner.user_id
            playable_members = {
                member.user_id
                for member in db_session.query(WorldMember)
                .filter(WorldMember.world_id == room.world_id)
                .all()
                if member.role in {"owner", "player"}
            }
            metadata = dict(world.metadata_json or {})
            actor_user_id = (
                str(metadata.get("current_actor_user_id") or "") or owner_user_id
            )
            if actor_user_id not in playable_members:
                actor_user_id = owner_user_id
                metadata["current_actor_user_id"] = actor_user_id
                world.metadata_json = metadata
                world.updated_at = utcnow()
            room_status = str(metadata.get("room_status") or "lobby")
            play_mode = world_play_mode(metadata)

        room.owner_user_id = owner_user_id
        room.current_actor_user_id = actor_user_id
        room.status = room_status
        room.play_mode = play_mode
        room.ready_users.intersection_update(playable_members)

    def room_roster(self, world_id: str) -> tuple[list[dict], set[str]]:
        """Return claimed investigators and every member required to be ready."""
        with session_scope(self.deps.database_url()) as db_session:
            playable_members = {
                member.user_id
                for member in db_session.query(WorldMember)
                .filter(WorldMember.world_id == world_id)
                .all()
                if member.role in {"owner", "player"}
            }
            claims = (
                db_session.query(WorldInvestigator)
                .filter_by(world_id=world_id, status="claimed")
                .all()
            )
            roster = [
                {
                    "investigator_id": claim.id,
                    "user_id": claim.controller_user_id,
                    "character_ref": dict(claim.character_ref or {}),
                }
                for claim in claims
                if claim.controller_user_id in playable_members
            ]
        return roster, playable_members

    async def retire_room_after_grace(
        self, world_id: str, room: GameRoom, idle_seconds: float
    ) -> None:
        await asyncio.sleep(idle_seconds)
        while not room.connected_users:
            if await self.deps.room_manager().remove_if_idle(world_id, idle_seconds=idle_seconds):
                if room.driver_transport is not None:
                    await room.driver_transport.close_input()
                if room.driver_task is not None:
                    try:
                        await room.driver_task
                    except (RuntimeError, asyncio.CancelledError):
                        pass
                return
            if not room.action_active:
                return
            # A turn keeps running when its last viewer disconnects. Retire as soon
            # as the authoritative commit releases the room action lock.
            await asyncio.sleep(0.5)

    async def report_room_driver_exit(self, room: GameRoom, task: asyncio.Task) -> None:
        # The driver may have exited after the world/turn transaction committed
        # but before its independent RoomAction status write. Never make that
        # uncertain action automatically retryable.
        room.release_action(terminal_status="unknown")
        if room.status == "starting":
            self.set_room_status(room, "lobby")
            await self.broadcast_room_state(room)
        if task.cancelled():
            return
        try:
            task.exception()
        except (asyncio.CancelledError, RuntimeError):
            return
        if not await self.deps.room_manager().remove(room.world_id, room):
            return
        await room.hub.broadcast(
            {
                "type": "room_error",
                "code": "room_driver_stopped",
                "message": "房间运行时意外停止，请重新进入房间",
            }
        )
        await room.hub.disconnect_all(
            code=1012,
            reason="房间运行时已重启，请重新连接",
        )

    async def websocket(self, ws: WebSocket):
        """Join one authoritative shared engine using the authenticated user session."""
        room: GameRoom | None = None
        user: Any | None = None
        world_id = ""
        connection_id = ""
        connection: RoomConnection | None = None
        attached = False
        presence_registered = False
        created = False
        try:
            validate_websocket_origin(ws)
            identity = websocket_session(ws, self.deps.database_url())
            if identity is None:
                await ws.close(code=4401, reason="未登录或会话已过期")
                return
            user = identity.user
            world_id = str(ws.query_params.get("world_id") or "")
            if not world_id:
                await ws.close(code=4400, reason="缺少房间 ID")
                return
            role = authorize_world(self.deps.database_url(), user.id, world_id, "read")
            with session_scope(self.deps.database_url()) as db_session:
                world = db_session.get(World, world_id)
                if world is None or world.status != "active":
                    await ws.close(code=4404, reason="房间不存在")
                    return
                module_name = world.module_name
                owner_member = (
                    db_session.query(WorldMember)
                    .filter_by(world_id=world_id, role="owner")
                    .one_or_none()
                )
                if owner_member is None:
                    await ws.close(code=4403, reason="房间没有有效房主")
                    return
                owner_user_id = owner_member.user_id
                room_metadata = dict(world.metadata_json or {})
                room_status = str(room_metadata.get("room_status") or "lobby")
                play_mode = world_play_mode(room_metadata)
                # "starting" is a crash-recovery marker, never a durable game
                # state. Recover a committed opening as playing; otherwise make
                # the uncommitted attempt retryable.
                if room_status == "starting":
                    latest_opening = (
                        db_session.query(Turn.status)
                        .filter(
                            Turn.world_id == world_id,
                            Turn.kind == "opening",
                        )
                        .order_by(Turn.created_at.desc(), Turn.pk.desc())
                        .first()
                    )
                    opening_committed = bool(latest_opening and latest_opening[0] == "completed")
                    room_status = "playing" if opening_committed else "lobby"
                    room_metadata["room_status"] = room_status
                    world.metadata_json = room_metadata
                    world.updated_at = utcnow()
                stored_actor_user_id = (
                    str(room_metadata.get("current_actor_user_id") or "") or owner_user_id
                )
                actor_member = (
                    db_session.query(WorldMember)
                    .filter(
                        WorldMember.world_id == world_id,
                        WorldMember.user_id == stored_actor_user_id,
                        WorldMember.role.in_(("owner", "player")),
                    )
                    .one_or_none()
                )
                if actor_member is None:
                    # A member may have left while no room process was alive.
                    # Never revive a stale/non-playing actor from metadata.
                    stored_actor_user_id = owner_user_id
                    room_metadata["current_actor_user_id"] = owner_user_id
                    world.metadata_json = room_metadata
                    world.updated_at = utcnow()

            if play_mode == "solo":
                # solo 世界树的“当前时间线”由树根指针决定。客户端拿着旧分支
                # 的 world_id 来连（过期标签页、旧大厅列表）时，不能就地建房：
                # claim 已随指针迁走，非当前世界建房会在 roster 核对时缺少
                # 有效调查员绑定。改为接受后立即告知重定向目标并关闭，客户端
                # 走与 solo_world_switched 完全相同的重连路径。
                redirect_world_id = await asyncio.to_thread(
                    resolve_solo_current_world_id,
                    self.deps.database_url(),
                    world_id,
                )
                if redirect_world_id != world_id:
                    await ws.accept()
                    await ws.send_json(
                        {
                            "type": "solo_world_switched",
                            "world_id": redirect_world_id,
                            "label": "",
                            "reason": "redirect",
                        }
                    )
                    await ws.close(
                        code=SOLO_SWITCH_CLOSE_CODE,
                        reason="已重定向到当前时间线",
                    )
                    return

            def build_engine() -> Any:
                context = RuntimeContext.create(
                    world_id,
                    module_name,
                    project_root=self.deps.project_root,
                    runtime_root=self.deps.runtime_root,
                )
                engine = self.deps.engine_factory(context)
                engine.configure_models(
                    self.deps.active_model_settings().narrative_model,
                    self.deps.active_model_settings().judgement_model,
                )
                engine.prepare_session()
                if room_status == "playing":
                    engine.restore_latest_committed_history()
                    engine._multiplayer_roster_active = True
                return engine

            async def create_room() -> GameRoom:
                await asyncio.to_thread(
                    recover_room_actions,
                    self.deps.database_url(),
                    world_id,
                )
                engine = await asyncio.to_thread(build_engine)
                initial_actor_user_id = stored_actor_user_id
                if room_status == "playing":
                    controllers = reconcile_world_investigator_roster(
                        self.deps.database_url(),
                        engine.context,
                        world_id,
                        preferred_user_id=stored_actor_user_id,
                    )
                    if controllers and initial_actor_user_id not in controllers:
                        initial_actor_user_id = (
                            owner_user_id
                            if owner_user_id in controllers
                            else next(iter(controllers))
                        )
                room = GameRoom(
                    world_id,
                    engine,
                    RoomEventHub(world_id),
                    owner_user_id,
                    current_actor_user_id=initial_actor_user_id,
                    status=room_status,
                    play_mode=play_mode,
                )
                def record_action_status(
                    target_world_id: str,
                    action_id: str,
                    status: str,
                ) -> None:
                    try:
                        finish_room_action(
                            self.deps.database_url(),
                            target_world_id,
                            action_id,
                            status,
                        )
                    finally:
                        # A terminal room action frees the account's single
                        # in-flight generation slot, even if the durable
                        # status write itself fails.
                        USER_TURN_GUARD.release_action(target_world_id, action_id)

                room.action_status_callback = record_action_status
                room.recovery_history = public_history_snapshot(room)
                room.history_snapshot_callback = lambda: public_history_snapshot(room)
                room.status_change_callback = lambda status: self.set_room_status(
                    room,
                    status,
                )
                room.control_state_callback = lambda: self.persist_room_control(room)
                transport = RoomDriverTransport(room)
                room.driver_transport = transport
                if initial_actor_user_id != stored_actor_user_id:
                    self.persist_room_control(room)
                recovered_actor_user_id = transport.combat_actor_controller()
                if (
                    recovered_actor_user_id
                    and recovered_actor_user_id != room.current_actor_user_id
                ):
                    room.assign_actor(recovered_actor_user_id)
                    self.persist_room_control(room)
                return room

            room, created = await self.deps.room_manager().get_or_create(world_id, create_room)
            # Publishing a room is the first point at which concurrent HTTP
            # membership handlers can see and mutate it. Reconcile the database
            # once here so changes committed during the slower engine build are
            # not lost.
            self.refresh_room_control(room)
            await ws.accept()
            connection_id = f"connection_{secrets.token_hex(12)}"
            connection = RoomConnection(
                connection_id,
                user.id,
                role,
                ws,
                session_hash=identity.token_hash,
                authorization_check=identity.locally_valid,
                active=False,
            )
            await room.hub.attach_pending(connection)
            # Room creation can include database recovery and model initialization.
            # Revalidate after that await and before any room metadata or private
            # recovery image is sent. Pending registration lets member removal or
            # logout find this handshake during the remaining bootstrap window.
            refreshed_identity = websocket_session(ws, self.deps.database_url())
            if (
                refreshed_identity is None
                or refreshed_identity.user.id != user.id
                or refreshed_identity.token_hash != identity.token_hash
            ):
                await ws.close(code=4401, reason="未登录或会话已过期")
                return
            role = authorize_world(self.deps.database_url(), user.id, world_id, "read")
            await room.hub.update_user_role(user.id, role)
            ordered_ws = OrderedRoomSocket(ws, room.hub, connection_id)
            if not created:
                await self.room_bootstrap(ordered_ws, room)
            recovery_cursor = await self.send_room_full_recovery(
                ordered_ws,
                room,
                user.id,
                role=role,
                connection_id=connection_id,
                include_pending_reemit=False,
            )
            # A role change is allowed to race bootstrap, but the control-plane
            # disconnects downgraded pending sockets. This final check also covers
            # direct session revocation before activation.
            refreshed_identity = websocket_session(ws, self.deps.database_url())
            if (
                refreshed_identity is None
                or refreshed_identity.user.id != user.id
                or refreshed_identity.token_hash != identity.token_hash
            ):
                await ws.close(code=4401, reason="未登录或会话已过期")
                return
            role = authorize_world(self.deps.database_url(), user.id, world_id, "read")
            await room.hub.update_user_role(user.id, role)
            replay = await room.hub.activate_with_replay(
                connection_id,
                recovery_cursor,
            )
            if replay["gap"] or not replay["delivered"]:
                await room.hub.detach(connection_id)
                await ws.close(
                    code=1012,
                    reason="房间事件已更新，请重新连接",
                )
                return
            attached = True
            first_user_connection = room.member_connected(user.id)
            presence_registered = True

            async def release_presence() -> None:
                nonlocal presence_registered
                if not presence_registered:
                    return
                # Flip the guard before awaiting broadcasts: a close frame can
                # make the WebSocket finally block race this send-timeout path.
                presence_registered = False
                last_user_connection = room.member_disconnected(user.id)
                if last_user_connection:
                    await room.hub.broadcast(
                        {"type": "member_left", "user_id": user.id}
                    )
                await self.broadcast_room_state(room)
                if not room.connected_users:
                    idle_seconds = max(
                        0.0,
                        float(os.environ.get("TRPG_ROOM_IDLE_SECONDS", "30")),
                    )
                    asyncio.create_task(
                        self.retire_room_after_grace(
                            world_id,
                            room,
                            idle_seconds,
                        )
                    )

            connection.transport_failure_callback = release_presence
            if first_user_connection:
                await room.hub.broadcast(
                    {
                        "type": "member_joined",
                        "user_id": user.id,
                        "role": role,
                    }
                )
            await self.broadcast_room_state(room)
            if created:
                room.driver_task = asyncio.create_task(
                    self.deps.run_ws_session(
                        room.driver_transport,
                        room.engine,
                        user_id=room.owner_user_id,
                    )
                )
                room.driver_task.add_done_callback(
                    lambda task: asyncio.create_task(self.report_room_driver_exit(room, task))
                )
            await run_room_message_loop(
                self,
                ordered_ws,
                room,
                user,
                world_id,
                connection_id,
                role,
            )
        except WebSocketDisconnect:
            pass
        except HTTPException as exc:
            print(
                f"[room] WebSocket 请求被拒绝: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            denied = exc.status_code == 403
            state = getattr(ws, "client_state", None)
            if getattr(state, "name", "") != "DISCONNECTED":
                try:
                    await ws.close(
                        code=4403 if denied else 1011,
                        reason="房间连接被拒绝" if denied else "房间连接暂时不可用",
                    )
                except Exception:
                    pass
        except Exception as exc:
            print(
                f"[room] WebSocket 内部错误: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            state = getattr(ws, "client_state", None)
            if getattr(state, "name", "") != "DISCONNECTED":
                try:
                    await ws.close(
                        code=1011,
                        reason="房间连接发生内部错误",
                    )
                except Exception:
                    pass
        finally:
            if room is not None and connection_id:
                await room.hub.detach(connection_id)
            if room is not None and user is not None and presence_registered:
                callback = (
                    connection.transport_failure_callback
                    if connection is not None
                    else None
                )
                if callback is not None:
                    await callback()
            elif room is not None and created and not attached:
                if await self.deps.room_manager().remove(world_id, room):
                    if room.driver_transport is not None:
                        await room.driver_transport.close_input()
