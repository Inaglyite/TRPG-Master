"""Shared multiplayer room lifecycle, visibility-aware broadcast, and action policy."""

from __future__ import annotations

import asyncio
import copy
import os
import time
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Protocol

from src.gameplay.investigators import investigator_controller_user_id


class JsonConnection(Protocol):
    async def send_json(self, payload: dict[str, Any]) -> None: ...


@dataclass
class RoomConnection:
    connection_id: str
    user_id: str
    role: str
    socket: JsonConnection
    session_hash: str = ""
    authorization_check: Callable[[], bool] | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    transport_failure_callback: Callable[[], Awaitable[None]] | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    active: bool = True
    last_ack: int = 0
    send_tail: asyncio.Task[bool] | None = field(
        default=None,
        repr=False,
        compare=False,
    )


@dataclass(frozen=True)
class BufferedRoomEvent:
    event_id: int
    payload: dict[str, Any]
    visibility: str


class RoomEventHub:
    """One ordered public/private event boundary shared by all room clients."""

    def __init__(self, world_id: str, *, replay_limit: int = 2048):
        self.world_id = world_id
        self._connections: dict[str, RoomConnection] = {}
        self._events: deque[BufferedRoomEvent] = deque()
        self._replay_limit = max(16, replay_limit)
        self._pinned_after_event_id: int | None = None
        self._event_id = 0
        self._lock = asyncio.Lock()
        try:
            configured_timeout = float(os.environ.get("TRPG_ROOM_SEND_TIMEOUT", "5"))
        except ValueError:
            configured_timeout = 5.0
        self._send_timeout = max(0.05, min(30.0, configured_timeout))

    async def attach(self, connection: RoomConnection) -> None:
        async with self._lock:
            connection.active = True
            self._connections[connection.connection_id] = connection

    async def attach_pending(self, connection: RoomConnection) -> None:
        """Register a handshake before sending secrets, without receiving live events."""
        async with self._lock:
            connection.active = False
            self._connections[connection.connection_id] = connection

    async def attach_with_replay(
        self,
        connection: RoomConnection,
        after_event_id: int,
    ) -> dict:
        """Atomically enqueue missed events before activating future broadcasts."""
        async with self._lock:
            if not self._is_authorized(connection):
                return {
                    "gap": False,
                    "latest_event_id": self._event_id,
                    "delivered": False,
                }
            oldest = self._events[0].event_id if self._events else self._event_id + 1
            gap = after_event_id < oldest - 1 or after_event_id > self._event_id
            if gap:
                return {
                    "gap": True,
                    "latest_event_id": self._event_id,
                    "delivered": False,
                }
            connection.active = True
            self._connections[connection.connection_id] = connection
            deliveries = [
                self._queue_send_unlocked(connection, dict(event.payload))
                for event in self._events
                if event.event_id > after_event_id
                and self._visibility_allows(connection, event.visibility)
            ]
            latest_event_id = self._event_id
        delivered = all(await asyncio.gather(*deliveries)) if deliveries else True
        return {
            "gap": False,
            "latest_event_id": latest_event_id,
            "delivered": delivered,
        }

    async def activate_with_replay(
        self,
        connection_id: str,
        after_event_id: int,
    ) -> dict:
        """Atomically activate one pending handshake and enqueue its missed events."""
        async with self._lock:
            connection = self._connections.get(connection_id)
            if connection is None or not self._is_authorized(connection):
                return {
                    "gap": False,
                    "latest_event_id": self._event_id,
                    "delivered": False,
                }
            oldest = self._events[0].event_id if self._events else self._event_id + 1
            gap = after_event_id < oldest - 1 or after_event_id > self._event_id
            if gap:
                return {
                    "gap": True,
                    "latest_event_id": self._event_id,
                    "delivered": False,
                }
            deliveries = [
                self._queue_send_unlocked(connection, dict(event.payload))
                for event in self._events
                if event.event_id > after_event_id
                and self._visibility_allows(connection, event.visibility)
            ]
            connection.active = True
            latest_event_id = self._event_id
        delivered = all(await asyncio.gather(*deliveries)) if deliveries else True
        return {
            "gap": False,
            "latest_event_id": latest_event_id,
            "delivered": delivered,
        }

    async def detach(self, connection_id: str) -> RoomConnection | None:
        async with self._lock:
            return self._connections.pop(connection_id, None)

    async def update_user_role(self, user_id: str, role: str) -> None:
        async with self._lock:
            for connection in self._connections.values():
                if connection.user_id == user_id:
                    connection.role = role

    async def disconnect_user(
        self,
        user_id: str,
        *,
        code: int = 4403,
        reason: str = "房间成员权限已被移除",
    ) -> int:
        async with self._lock:
            removed = [
                connection
                for connection in self._connections.values()
                if connection.user_id == user_id
            ]
            for connection in removed:
                self._connections.pop(connection.connection_id, None)
        for connection in removed:
            close = getattr(connection.socket, "close", None)
            if close is not None:
                try:
                    await close(code=code, reason=reason)
                except Exception:
                    pass
        return len(removed)

    async def disconnect_session(self, session_hash: str, *, code: int = 4401) -> int:
        """Close every active or pending socket bound to one revoked login."""
        async with self._lock:
            removed = [
                connection
                for connection in self._connections.values()
                if connection.session_hash and connection.session_hash == session_hash
            ]
            for connection in removed:
                self._connections.pop(connection.connection_id, None)
        for connection in removed:
            close = getattr(connection.socket, "close", None)
            if close is not None:
                try:
                    await close(code=code, reason="登录会话已注销")
                except Exception:
                    pass
        return len(removed)

    async def disconnect_all(
        self,
        *,
        code: int = 1012,
        reason: str = "房间正在恢复，请重新连接",
    ) -> int:
        async with self._lock:
            removed = list(self._connections.values())
            self._connections.clear()
        for connection in removed:
            close = getattr(connection.socket, "close", None)
            if close is None:
                continue
            try:
                await close(code=code, reason=reason)
            except Exception:
                pass
        return len(removed)

    async def send_json(self, payload: dict[str, Any]) -> None:
        """Compatibility target for OrderedTurnEventStream; broadcasts publicly."""
        await self.broadcast(payload)

    async def send_direct(self, connection_id: str, payload: dict[str, Any]) -> bool:
        async with self._lock:
            connection = self._connections.get(connection_id)
            if connection is None or not self._is_authorized(connection):
                return False
            delivery = self._queue_send_unlocked(connection, dict(payload))
        return await delivery

    async def send_batch(
        self,
        connection_id: str,
        payload_factory: Callable[
            [int, tuple[BufferedRoomEvent, ...]],
            list[dict[str, Any]],
        ],
    ) -> bool:
        """Build and enqueue a snapshot batch at one room-event boundary."""
        async with self._lock:
            connection = self._connections.get(connection_id)
            if connection is None or not self._is_authorized(connection):
                return False
            deliveries = [
                self._queue_send_unlocked(connection, dict(payload))
                for payload in payload_factory(self._event_id, tuple(self._events))
            ]
        return all(await asyncio.gather(*deliveries)) if deliveries else True

    async def send_snapshot_with_replay(
        self,
        connection_id: str,
        payload_factory: Callable[
            [int, tuple[BufferedRoomEvent, ...]],
            list[dict[str, Any]],
        ],
    ) -> tuple[bool, int]:
        """Queue full state, its following events, and modal recovery atomically."""
        async with self._lock:
            connection = self._connections.get(connection_id)
            if connection is None or not self._is_authorized(connection):
                return False, self._event_id
            messages = payload_factory(self._event_id, tuple(self._events))
            if not messages:
                return True, self._event_id
            cursor = int(messages[0].get("latest_event_id") or 0)
            replay = [
                dict(event.payload)
                for event in self._events
                if event.event_id > cursor and self._visibility_allows(connection, event.visibility)
            ]
            recovered_tail = [
                payload
                for payload in messages[1:]
                if not any(
                    event.get("type") == payload.get("type")
                    and (payload.get("id") is None or event.get("id") == payload.get("id"))
                    for event in replay
                )
            ]
            ordered = [messages[0], *replay, *recovered_tail]
            deliveries = [
                self._queue_send_unlocked(connection, dict(payload)) for payload in ordered
            ]
            replay_cursor = self._event_id
        return all(await asyncio.gather(*deliveries)), replay_cursor

    async def build_at_boundary(
        self,
        payload_factory: Callable[
            [int, tuple[BufferedRoomEvent, ...]],
            Any,
        ],
    ) -> Any:
        """Build an unattached-client snapshot at one event-buffer boundary."""
        async with self._lock:
            return payload_factory(self._event_id, tuple(self._events))

    async def broadcast(
        self,
        payload: dict[str, Any],
        *,
        visibility: str = "public",
        on_enqueued: Callable[[], None] | None = None,
    ) -> int:
        if visibility == "server_only":
            return self._event_id
        async with self._lock:
            self._event_id += 1
            event_id = self._event_id
            wire = dict(payload)
            wire.setdefault("room_event_id", event_id)
            wire.setdefault("world_id", self.world_id)
            # Live recipients get the complete event. Replay storage drops
            # embedded base64 blobs, which otherwise multiply a single image
            # across hundreds of events and exhaust a small server's memory.
            buffered = self._without_embedded_assets(wire)
            event = BufferedRoomEvent(event_id, buffered, visibility)
            self._events.append(event)
            self._trim_events_unlocked()
            recipients = [
                connection
                for connection in self._connections.values()
                if self._can_receive(connection, visibility)
            ]
            deliveries = [
                self._queue_send_unlocked(connection, dict(wire)) for connection in recipients
            ]
            if on_enqueued is not None:
                on_enqueued()
        if deliveries:
            await asyncio.gather(*deliveries)
        return event_id

    def _queue_send_unlocked(
        self,
        connection: RoomConnection,
        payload: dict[str, Any],
    ) -> asyncio.Task[bool]:
        previous = connection.send_tail
        delivery = asyncio.create_task(self._deliver_after(connection, previous, payload))
        connection.send_tail = delivery
        return delivery

    async def _deliver_after(
        self,
        connection: RoomConnection,
        previous: asyncio.Task[bool] | None,
        payload: dict[str, Any],
    ) -> bool:
        if previous is not None:
            try:
                await previous
            except asyncio.CancelledError:
                return False
        async with self._lock:
            if self._connections.get(connection.connection_id) is not connection:
                return False
            if not self._is_authorized(connection):
                self._connections.pop(connection.connection_id, None)
                return False
        try:
            await asyncio.wait_for(
                connection.socket.send_json(payload),
                timeout=self._send_timeout,
            )
            return True
        except Exception:
            await self._drop_failed_connection(connection)
            return False

    async def _drop_failed_connection(self, connection: RoomConnection) -> None:
        removed = False
        async with self._lock:
            if self._connections.get(connection.connection_id) is connection:
                self._connections.pop(connection.connection_id, None)
                removed = True
        if removed:
            await self._close_failed_connection(connection)
            callback = connection.transport_failure_callback
            if callback is not None:
                try:
                    await callback()
                except Exception:
                    # Presence cleanup is best-effort here. The WebSocket
                    # receive loop still runs the same idempotent callback in
                    # its finally block when the close frame is observed.
                    pass

    async def _close_failed_connection(self, connection: RoomConnection) -> None:
        close = getattr(connection.socket, "close", None)
        if close is None:
            return
        try:
            await asyncio.wait_for(
                close(code=1011, reason="客户端接收超时，请重新连接"),
                timeout=min(1.0, self._send_timeout),
            )
        except Exception:
            pass

    @classmethod
    def _without_embedded_assets(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: cls._without_embedded_assets(item)
                for key, item in value.items()
                if key != "asset_data_uri"
            }
        if isinstance(value, list):
            return [cls._without_embedded_assets(item) for item in value]
        return copy.deepcopy(value)

    async def acknowledge(self, connection_id: str, event_id: int) -> bool:
        async with self._lock:
            connection = self._connections.get(connection_id)
            if (
                connection is None
                or not self._is_authorized(connection)
                or event_id < connection.last_ack
                or event_id > self._event_id
            ):
                return False
            connection.last_ack = event_id
            return True

    async def replay_after(self, connection_id: str, after_event_id: int) -> dict:
        async with self._lock:
            connection = self._connections.get(connection_id)
            if connection is None or not self._is_authorized(connection):
                return {"gap": True, "events": [], "latest_event_id": self._event_id}
            oldest = self._events[0].event_id if self._events else self._event_id + 1
            # A value ahead of the server is not "fully caught up": it means
            # the client crossed a process restart/event epoch and must replace
            # its old cursor from a personalized full-state image.
            gap = after_event_id < oldest - 1 or after_event_id > self._event_id
            events = (
                []
                if gap
                else [
                    dict(event.payload)
                    for event in self._events
                    if event.event_id > after_event_id
                    and self._can_receive(connection, event.visibility)
                ]
            )
            return {"gap": gap, "events": events, "latest_event_id": self._event_id}

    async def replay_to_connection(
        self,
        connection_id: str,
        after_event_id: int,
    ) -> dict:
        """Atomically enqueue replay events before any newer live event."""
        async with self._lock:
            connection = self._connections.get(connection_id)
            if connection is None or not self._is_authorized(connection):
                return {
                    "gap": True,
                    "latest_event_id": self._event_id,
                    "delivered": False,
                }
            oldest = self._events[0].event_id if self._events else self._event_id + 1
            gap = after_event_id < oldest - 1 or after_event_id > self._event_id
            if gap:
                return {
                    "gap": True,
                    "latest_event_id": self._event_id,
                    "delivered": False,
                }
            deliveries = [
                self._queue_send_unlocked(connection, dict(event.payload))
                for event in self._events
                if event.event_id > after_event_id
                and self._can_receive(connection, event.visibility)
            ]
            latest_event_id = self._event_id
        delivered = all(await asyncio.gather(*deliveries)) if deliveries else True
        return {
            "gap": False,
            "latest_event_id": latest_event_id,
            "delivered": delivered,
        }

    async def connection_snapshot(self) -> list[dict]:
        async with self._lock:
            return [
                {
                    "connection_id": item.connection_id,
                    "user_id": item.user_id,
                    "role": item.role,
                    "last_ack": item.last_ack,
                }
                for item in self._connections.values()
            ]

    async def latest_event_id(self) -> int:
        async with self._lock:
            return self._event_id

    async def pin_replay_boundary(self) -> int:
        """Keep every event after the returned action-start cursor."""
        async with self._lock:
            self._pinned_after_event_id = self._event_id
            self._trim_events_unlocked()
            return self._event_id

    def _trim_events_unlocked(self) -> None:
        while len(self._events) > self._replay_limit:
            if (
                self._pinned_after_event_id is not None
                and self._events[0].event_id > self._pinned_after_event_id
            ):
                break
            self._events.popleft()

    @staticmethod
    def _is_authorized(connection: RoomConnection) -> bool:
        check = connection.authorization_check
        if check is None:
            return True
        try:
            return bool(check())
        except Exception:
            return False

    @classmethod
    def _can_receive(cls, connection: RoomConnection, visibility: str) -> bool:
        return (
            connection.active
            and cls._is_authorized(connection)
            and cls._visibility_allows(connection, visibility)
        )

    @staticmethod
    def _visibility_allows(connection: RoomConnection, visibility: str) -> bool:
        if visibility == "public":
            return True
        if visibility == "owner":
            return connection.role == "owner"
        if visibility.startswith("user:"):
            return connection.user_id == visibility.removeprefix("user:")
        if visibility.startswith("player:"):
            return connection.role in {
                "owner",
                "player",
            } and connection.user_id == visibility.removeprefix("player:")
        return False


class RoomDriverTransport:
    """Virtual socket consumed by one shared run_ws_session room driver."""

    def __init__(self, room: GameRoom | None = None):
        self.room = room
        self._incoming: asyncio.Queue[str | None] = asyncio.Queue()
        self._closed = False

    async def receive_text(self) -> str:
        value = await self._incoming.get()
        if value is None:
            raise RuntimeError("room driver closed")
        return value

    async def submit(self, raw: str) -> None:
        if self._closed:
            raise RuntimeError("room driver closed")
        await self._incoming.put(raw)

    async def send_json(self, payload: dict[str, Any]) -> None:
        room = self.room
        if room is None:
            return
        wire = dict(payload)
        visibility = "public"
        if wire.get("type") == "private_event":
            target_user_id = str(wire.pop("target_user_id", ""))
            visibility = f"player:{target_user_id}" if target_user_id else "server_only"
        elif wire.get("type") == "character_state":
            # A character_state without a controller user must never fall back
            # to public visibility: it carries the full investigator payload
            # (hp/san/items/clues). Keep it server-only unless explicitly
            # targeted at the investigator's controller.
            target_user_id = str(wire.pop("target_user_id", ""))
            visibility = f"player:{target_user_id}" if target_user_id else "server_only"
        elif wire.get("type") in {
            "suggest_check",
            "decision_request",
            "decision_resolved",
        }:
            responding_investigator_id = str(
                wire.get("responding_investigator_id") or ""
            )
            actor_user_id = (
                self._investigator_controller(responding_investigator_id)
                if responding_investigator_id
                else room.current_actor_user_id
            )
            visibility = f"player:{actor_user_id}" if actor_user_id else "server_only"
            if wire.get("type") == "suggest_check":
                room.set_pending_reply("suggest", actor_user_id)
            elif wire.get("type") == "decision_request":
                room.set_pending_reply(
                    "decision",
                    actor_user_id,
                    request_id=str(wire.get("id") or ""),
                )
            elif wire.get("type") == "decision_resolved":
                room.clear_pending_reply()
        terminal_error = payload.get("type") == "error" and payload.get("terminal") is True
        if terminal_error:
            room.mark_action_failed()
        control_terminal = room.control_action_active and payload.get("type") in {
            "saved",
            "save_deleted",
            "save_renamed",
            "case_settled",
        }
        control_terminal = control_terminal or (room.control_action_active and terminal_error)
        turn_terminal = (
            payload.get("type")
            in {
                "done",
                "turn_rejected",
                "turn_rewritten",
                "turn_rewrite_failed",
            }
            or terminal_error
        )
        recovered_start_state: dict[str, Any] | None = None
        combat_actor_changed = False
        if control_terminal or turn_terminal:
            room.terminal_event_pending = True
            if payload.get("type") == "done":
                combat_actor_user_id = self.combat_actor_controller()
                if (
                    combat_actor_user_id
                    and combat_actor_user_id != room.current_actor_user_id
                ):
                    room.assign_actor(combat_actor_user_id)
                    if room.control_state_callback is not None:
                        try:
                            room.control_state_callback()
                        except Exception:
                            pass
                    combat_actor_changed = True
            if room.status == "starting":
                room.apply_status("playing" if payload.get("type") == "done" else "lobby")
                recovered_start_state = {
                    "type": "room_state",
                    "status": room.status,
                    "owner_user_id": room.owner_user_id,
                    "current_actor_user_id": room.current_actor_user_id,
                    "ready_user_ids": sorted(room.ready_users),
                    "online_user_ids": sorted(room.connected_users),
                }
            elif payload.get("type") == "case_settled":
                # Only a successful settlement (ok is True) ends the case and
                # returns the room to the lobby. A failed settlement (ok False,
                # e.g. "当前世界状态没有 pc") is not a terminal game over: the
                # room must stay "playing" so the case can still proceed.
                # Without the lobby return a finished room would deadlock (start
                # requires lobby, archive rejects starting/playing); clearing
                # ready_users forces every member to confirm ready again before
                # the next start instead of silently reopening via stale ready.
                # The next round opens with the current owner as first actor: a
                # stale actor from the finished case would otherwise survive
                # into the next start (start computes actor_id from
                # room.current_actor_user_id) and, once that member released
                # their investigator claim in the lobby, deadlock the opening
                # on investigator_required. assign_actor must run before
                # apply_status so the persistence callback snapshots the reset
                # actor together with the "lobby" status.
                if payload.get("ok") is True:
                    room.assign_actor(room.owner_user_id)
                    room.ready_users.clear()
                    room.apply_status("lobby")
                    recovered_start_state = {
                        "type": "room_state",
                        "status": room.status,
                        "owner_user_id": room.owner_user_id,
                        "current_actor_user_id": room.current_actor_user_id,
                        "ready_user_ids": sorted(room.ready_users),
                        "online_user_ids": sorted(room.connected_users),
                    }
            room.clear_pending_reply()
            failed_terminal = (
                payload.get("type")
                in {
                    "turn_rejected",
                    "turn_rewrite_failed",
                }
                or terminal_error
            )
            room.release_action(terminal_status="failed" if failed_terminal else "completed")
        if recovered_start_state is not None:
            await room.hub.broadcast(recovered_start_state)
        if control_terminal or turn_terminal:
            history: list[dict] | None = None
            if room.history_snapshot_callback is not None:
                try:
                    history = room.history_snapshot_callback()
                except Exception:
                    history = None

            def finalize_terminal() -> None:
                if history is not None:
                    room.recovery_history = copy.deepcopy(history)
                room.terminal_event_pending = False
                room.hub._pinned_after_event_id = None
                room.hub._trim_events_unlocked()

            await room.hub.broadcast(
                wire,
                visibility=visibility,
                on_enqueued=finalize_terminal,
            )
            if combat_actor_changed:
                await room.hub.broadcast(
                    {
                        "type": "actor_changed",
                        "user_id": room.current_actor_user_id,
                        "reason": "combat_turn",
                    }
                )
                await room.hub.broadcast(
                    {
                        "type": "room_state",
                        "status": room.status,
                        "owner_user_id": room.owner_user_id,
                        "current_actor_user_id": room.current_actor_user_id,
                        "ready_user_ids": sorted(room.ready_users),
                        "online_user_ids": sorted(room.connected_users),
                    }
                )
        else:
            await room.hub.broadcast(wire, visibility=visibility)

    def _investigator_controller(self, investigator_id: str) -> str | None:
        room = self.room
        if room is None:
            return None
        try:
            state = room.engine.context.world_store.load()
        except Exception:
            return None
        return investigator_controller_user_id(state, investigator_id)

    def combat_actor_controller(self) -> str | None:
        room = self.room
        if room is None:
            return None
        try:
            state = room.engine.context.world_store.load()
            combat = state.get("combat_state")
            if not isinstance(combat, dict) or not combat.get("active"):
                return None
            actor_id = str(combat.get("current_actor") or "")
            participant = next(
                (
                    item
                    for item in combat.get("participants", [])
                    if isinstance(item, dict) and str(item.get("id") or "") == actor_id
                ),
                None,
            )
            if not isinstance(participant, dict) or participant.get("kind") != "pc":
                return None
            return investigator_controller_user_id(state, actor_id)
        except Exception:
            return None

    def combat_participant_controllers(self) -> set[str]:
        room = self.room
        if room is None:
            return set()
        try:
            state = room.engine.context.world_store.load()
            combat = state.get("combat_state")
            if not isinstance(combat, dict) or not combat.get("active"):
                return set()
            return {
                controller
                for participant in combat.get("participants", [])
                if isinstance(participant, dict) and participant.get("kind") == "pc"
                if (
                    controller := investigator_controller_user_id(
                        state,
                        str(participant.get("id") or ""),
                    )
                )
            }
        except Exception:
            return set()

    async def close_input(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._incoming.put(None)


class ActionReservationError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class RoomCapacityError(RuntimeError):
    pass


@dataclass
class GameRoom:
    world_id: str
    engine: Any
    hub: RoomEventHub
    owner_user_id: str
    current_actor_user_id: str | None = None
    status: str = "lobby"
    play_mode: str = "multiplayer"
    ready_users: set[str] = field(default_factory=set)
    connected_users: dict[str, int] = field(default_factory=dict)
    _action_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    _action_ids: deque[str] = field(default_factory=lambda: deque(maxlen=2048), repr=False)
    _action_id_set: set[str] = field(default_factory=set, repr=False)
    last_empty_at: float | None = None
    driver_transport: RoomDriverTransport | None = field(default=None, repr=False)
    driver_task: asyncio.Task | None = field(default=None, repr=False)
    pending_reply_kind: str | None = None
    pending_reply_user_id: str | None = None
    pending_reply_request_id: str | None = None
    control_action_active: bool = False
    active_action_id: str | None = None
    active_action_failed: bool = False
    active_action_start_event_id: int = 0
    terminal_event_pending: bool = False
    recovery_history: list[dict] = field(default_factory=list, repr=False)
    history_snapshot_callback: Callable[[], list[dict]] | None = field(
        default=None,
        repr=False,
    )
    action_status_callback: Callable[[str, str, str], None] | None = field(
        default=None,
        repr=False,
    )
    status_change_callback: Callable[[str], None] | None = field(
        default=None,
        repr=False,
    )
    control_state_callback: Callable[[], None] | None = field(
        default=None,
        repr=False,
    )

    def member_connected(self, user_id: str) -> bool:
        first_connection = user_id not in self.connected_users
        self.connected_users[user_id] = self.connected_users.get(user_id, 0) + 1
        self.last_empty_at = None
        return first_connection

    def member_disconnected(self, user_id: str) -> bool:
        count = self.connected_users.get(user_id, 0)
        if count <= 1:
            self.connected_users.pop(user_id, None)
        else:
            self.connected_users[user_id] = count - 1
        if not self.connected_users:
            self.last_empty_at = time.monotonic()
        return user_id not in self.connected_users

    def set_ready(self, user_id: str, ready: bool) -> None:
        if ready:
            self.ready_users.add(user_id)
        else:
            self.ready_users.discard(user_id)

    def assign_actor(self, actor_user_id: str | None) -> None:
        self.current_actor_user_id = actor_user_id

    def protected_member_user_ids(self) -> set[str]:
        users = {
            user_id
            for user_id in (
                self.current_actor_user_id,
                self.pending_reply_user_id,
            )
            if user_id
        }
        if self.driver_transport is not None:
            users.update(self.driver_transport.combat_participant_controllers())
        return users

    def apply_status(self, status: str) -> None:
        if self.status_change_callback is not None:
            self.status_change_callback(status)
        else:
            self.status = status

    def set_pending_reply(
        self,
        kind: str,
        user_id: str | None,
        *,
        request_id: str | None = None,
    ) -> None:
        self.pending_reply_kind = kind
        self.pending_reply_user_id = user_id
        self.pending_reply_request_id = request_id

    def accept_pending_reply(
        self,
        kind: str,
        user_id: str,
        *,
        request_id: str | None = None,
    ) -> bool:
        if self.pending_reply_kind != kind or self.pending_reply_user_id != user_id:
            return False
        if kind == "decision" and self.pending_reply_request_id != str(request_id or ""):
            return False
        self.clear_pending_reply()
        return True

    def clear_pending_reply(self) -> None:
        self.pending_reply_kind = None
        self.pending_reply_user_id = None
        self.pending_reply_request_id = None

    async def reserve_action(
        self,
        user_id: str,
        action_id: str,
        *,
        require_current_actor: bool = True,
    ) -> None:
        action_id = str(action_id or "").strip()
        if not action_id or len(action_id) > 160:
            raise ActionReservationError("invalid_action_id", "行动 ID 无效")
        if require_current_actor and self.current_actor_user_id != user_id:
            raise ActionReservationError("not_current_actor", "现在还没有轮到你行动")
        if action_id in self._action_id_set:
            raise ActionReservationError("duplicate_action", "该行动已经提交")
        if self._action_lock.locked() or self.terminal_event_pending:
            raise ActionReservationError("room_turn_in_progress", "房间正在处理上一项行动")
        await self._action_lock.acquire()
        try:
            self.active_action_start_event_id = await self.hub.pin_replay_boundary()
        except BaseException:
            self._action_lock.release()
            raise
        self.active_action_id = action_id
        self.active_action_failed = False
        if len(self._action_ids) == self._action_ids.maxlen:
            expired = self._action_ids.popleft()
            self._action_id_set.discard(expired)
        self._action_ids.append(action_id)
        self._action_id_set.add(action_id)

    async def reserve_control(self, user_id: str, action_id: str) -> None:
        await self.reserve_action(
            user_id,
            action_id,
            require_current_actor=False,
        )
        self.control_action_active = True

    def mark_action_failed(self) -> None:
        if self.active_action_id is not None:
            self.active_action_failed = True

    def release_action(self, *, terminal_status: str | None = None) -> None:
        action_id = self.active_action_id
        status_persisted = True
        if terminal_status and action_id and self.action_status_callback is not None:
            status = (
                "unknown"
                if terminal_status == "unknown"
                else (
                    "failed"
                    if terminal_status == "failed" or self.active_action_failed
                    else "completed"
                )
            )
            try:
                self.action_status_callback(self.world_id, action_id, status)
            except Exception:
                # A status/audit write must never leave the in-memory room
                # permanently locked. A running DB row is recovered on reload.
                status_persisted = False
        if terminal_status == "failed" and action_id and status_persisted:
            # Failed actions are explicitly retryable with the same stable ID.
            # Remove the deque entry too, otherwise a later expiry of an older
            # duplicate would incorrectly evict the retried active ID.
            self._action_id_set.discard(action_id)
            try:
                self._action_ids.remove(action_id)
            except ValueError:
                pass
        self.control_action_active = False
        self.active_action_id = None
        self.active_action_failed = False
        if not self.terminal_event_pending:
            self.hub._pinned_after_event_id = None
            self.hub._trim_events_unlocked()
        if self._action_lock.locked():
            self._action_lock.release()

    @property
    def action_active(self) -> bool:
        return self._action_lock.locked()


RoomFactory = Callable[[], GameRoom | Awaitable[GameRoom]]


class RoomManager:
    """Atomically creates at most one active GameRoom for each world."""

    def __init__(self, *, max_rooms: int = 8):
        self._rooms: dict[str, GameRoom] = {}
        self._loading: dict[str, asyncio.Future[GameRoom]] = {}
        # Serializes room construction with destructive lifecycle operations
        # (archive/abandon) for one world.  The global map lock remains tiny;
        # expensive engine creation never holds it or blocks other worlds.
        self._lifecycle_locks: dict[str, asyncio.Lock] = {}
        self._lock = asyncio.Lock()
        self.max_rooms = max(1, int(max_rooms))

    async def _lifecycle_lock(self, world_id: str) -> asyncio.Lock:
        async with self._lock:
            lock = self._lifecycle_locks.get(world_id)
            if lock is None:
                lock = asyncio.Lock()
                self._lifecycle_locks[world_id] = lock
            return lock

    @asynccontextmanager
    async def world_lifecycle(self, world_id: str) -> AsyncIterator[None]:
        """Serialize one world's load and archive/abandon boundary.

        A world can be expensive to build, so this lock is deliberately scoped
        to a single ID instead of ``RoomManager._lock``.  It closes the gap in
        which an archive request used to miss ``_loading`` while a WebSocket
        room factory recovered durable action leases in parallel.
        """
        lock = await self._lifecycle_lock(world_id)
        async with lock:
            yield

    async def get_or_create(self, world_id: str, factory: RoomFactory) -> tuple[GameRoom, bool]:
        async with self.world_lifecycle(world_id):
            return await self._get_or_create(world_id, factory)

    async def _get_or_create(
        self,
        world_id: str,
        factory: RoomFactory,
    ) -> tuple[GameRoom, bool]:
        """Implement get/create while the caller owns ``world_lifecycle``."""
        creator = False
        async with self._lock:
            existing = self._rooms.get(world_id)
            if existing is not None:
                return existing, False
            pending = self._loading.get(world_id)
            if pending is None:
                if len(self._rooms) + len(self._loading) >= self.max_rooms:
                    raise RoomCapacityError("服务器活跃房间已达到上限，请稍后重试")
                pending = asyncio.get_running_loop().create_future()
                self._loading[world_id] = pending
                creator = True
        if not creator:
            return await pending, False
        try:
            created = factory()
            room = await created if isinstance(created, Awaitable) else created
            if room.world_id != world_id:
                raise ValueError("room factory returned the wrong world")
            async with self._lock:
                self._rooms[world_id] = room
                future = self._loading.pop(world_id)
                if not future.done():
                    future.set_result(room)
            return room, True
        except BaseException as exc:
            async with self._lock:
                future = self._loading.pop(world_id, None)
                if future is not None and not future.done():
                    future.set_exception(exc)
                    future.exception()
            raise

    async def get(self, world_id: str) -> GameRoom | None:
        async with self._lock:
            return self._rooms.get(world_id)

    async def remove(self, world_id: str, expected_room: GameRoom) -> bool:
        """Remove exactly one known room without racing a replacement."""
        async with self._lock:
            if self._rooms.get(world_id) is not expected_room:
                return False
            self._rooms.pop(world_id, None)
            return True

    async def remove_if_idle(self, world_id: str, *, idle_seconds: float = 30) -> bool:
        async with self._lock:
            room = self._rooms.get(world_id)
            if room is None or room.connected_users or room.action_active:
                return False
            if room.last_empty_at is None or time.monotonic() - room.last_empty_at < idle_seconds:
                return False
            self._rooms.pop(world_id, None)
            return True

    async def snapshot(self) -> list[dict]:
        async with self._lock:
            return [
                {
                    "world_id": room.world_id,
                    "status": room.status,
                    "owner_user_id": room.owner_user_id,
                    "current_actor_user_id": room.current_actor_user_id,
                    "connected_users": sorted(room.connected_users),
                    "action_active": room.action_active,
                }
                for room in self._rooms.values()
            ]

    async def disconnect_session(self, session_hash: str) -> int:
        """Disconnect one revoked login from every currently loaded room."""
        async with self._lock:
            rooms = list(self._rooms.values())
        disconnected = await asyncio.gather(
            *(room.hub.disconnect_session(session_hash) for room in rooms)
        )
        return sum(disconnected)
