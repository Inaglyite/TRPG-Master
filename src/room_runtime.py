"""Shared multiplayer room lifecycle, visibility-aware broadcast, and action policy."""

from __future__ import annotations

import asyncio
import copy
import os
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol


class JsonConnection(Protocol):
    async def send_json(self, payload: dict[str, Any]) -> None: ...


@dataclass
class RoomConnection:
    connection_id: str
    user_id: str
    role: str
    socket: JsonConnection
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

    def __init__(self, world_id: str, *, replay_limit: int = 256):
        self.world_id = world_id
        self._connections: dict[str, RoomConnection] = {}
        self._events: deque[BufferedRoomEvent] = deque(maxlen=max(16, replay_limit))
        self._event_id = 0
        self._lock = asyncio.Lock()
        try:
            configured_timeout = float(os.environ.get("TRPG_ROOM_SEND_TIMEOUT", "5"))
        except ValueError:
            configured_timeout = 5.0
        self._send_timeout = max(0.05, min(30.0, configured_timeout))

    async def attach(self, connection: RoomConnection) -> None:
        async with self._lock:
            self._connections[connection.connection_id] = connection

    async def attach_with_replay(
        self,
        connection: RoomConnection,
        after_event_id: int,
    ) -> dict:
        """Atomically enqueue missed events before activating future broadcasts."""
        async with self._lock:
            oldest = self._events[0].event_id if self._events else self._event_id + 1
            gap = after_event_id < oldest - 1 or after_event_id > self._event_id
            if gap:
                return {
                    "gap": True,
                    "latest_event_id": self._event_id,
                    "delivered": False,
                }
            self._connections[connection.connection_id] = connection
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

    async def detach(self, connection_id: str) -> RoomConnection | None:
        async with self._lock:
            return self._connections.pop(connection_id, None)

    async def update_user_role(self, user_id: str, role: str) -> None:
        async with self._lock:
            for connection in self._connections.values():
                if connection.user_id == user_id:
                    connection.role = role

    async def disconnect_user(self, user_id: str, *, code: int = 4403) -> int:
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
                    await close(code=code, reason="房间成员权限已被移除")
                except Exception:
                    pass
        return len(removed)

    async def send_json(self, payload: dict[str, Any]) -> None:
        """Compatibility target for OrderedTurnEventStream; broadcasts publicly."""
        await self.broadcast(payload)

    async def send_direct(self, connection_id: str, payload: dict[str, Any]) -> bool:
        async with self._lock:
            connection = self._connections.get(connection_id)
            if connection is None:
                return False
            delivery = self._queue_send_unlocked(connection, dict(payload))
        return await delivery

    async def send_batch(
        self,
        connection_id: str,
        payload_factory: Callable[[], list[dict[str, Any]]],
    ) -> bool:
        """Build and enqueue a snapshot batch at one room-event boundary."""
        async with self._lock:
            connection = self._connections.get(connection_id)
            if connection is None:
                return False
            deliveries = [
                self._queue_send_unlocked(connection, dict(payload))
                for payload in payload_factory()
            ]
        return all(await asyncio.gather(*deliveries)) if deliveries else True

    async def broadcast(self, payload: dict[str, Any], *, visibility: str = "public") -> int:
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
            recipients = [
                connection
                for connection in self._connections.values()
                if self._can_receive(connection, visibility)
            ]
            deliveries = [
                self._queue_send_unlocked(connection, dict(wire))
                for connection in recipients
            ]
        if deliveries:
            await asyncio.gather(*deliveries)
        return event_id

    def _queue_send_unlocked(
        self,
        connection: RoomConnection,
        payload: dict[str, Any],
    ) -> asyncio.Task[bool]:
        previous = connection.send_tail
        delivery = asyncio.create_task(
            self._deliver_after(connection, previous, payload)
        )
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
            if connection is None or event_id < connection.last_ack or event_id > self._event_id:
                return False
            connection.last_ack = event_id
            return True

    async def replay_after(self, connection_id: str, after_event_id: int) -> dict:
        async with self._lock:
            connection = self._connections.get(connection_id)
            if connection is None:
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

    @staticmethod
    def _can_receive(connection: RoomConnection, visibility: str) -> bool:
        if visibility == "public":
            return True
        if visibility == "owner":
            return connection.role == "owner"
        if visibility.startswith("player:"):
            return connection.user_id == visibility.removeprefix("player:")
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
        elif wire.get("type") == "character_state" and wire.get("target_user_id"):
            target_user_id = str(wire.pop("target_user_id"))
            visibility = f"player:{target_user_id}"
        elif wire.get("type") in {
            "suggest_check",
            "decision_request",
            "decision_resolved",
        }:
            actor_user_id = room.current_actor_user_id
            visibility = (
                f"player:{actor_user_id}" if actor_user_id else "server_only"
            )
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
        terminal_error = (
            payload.get("type") == "error"
            and payload.get("terminal") is True
        )
        if terminal_error:
            room.mark_action_failed()
        control_terminal = room.control_action_active and payload.get("type") in {
            "saved",
            "save_deleted",
            "save_renamed",
            "case_settled",
        }
        control_terminal = control_terminal or (
            room.control_action_active and terminal_error
        )
        turn_terminal = payload.get("type") in {
            "done",
            "turn_rejected",
            "turn_rewritten",
            "turn_rewrite_failed",
        } or terminal_error
        # Release the authoritative lease before publishing the terminal frame.
        # A fast client is then free to retry as soon as it observes that frame.
        if control_terminal or turn_terminal:
            room.clear_pending_reply()
            failed_terminal = payload.get("type") in {
                "turn_rejected",
                "turn_rewrite_failed",
            } or terminal_error
            room.release_action(
                terminal_status="failed" if failed_terminal else "completed"
            )
        await room.hub.broadcast(wire, visibility=visibility)

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
    action_status_callback: Callable[[str, str, str], None] | None = field(
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
        if self._action_lock.locked():
            raise ActionReservationError("room_turn_in_progress", "房间正在处理上一项行动")
        await self._action_lock.acquire()
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
        self._lock = asyncio.Lock()
        self.max_rooms = max(1, int(max_rooms))

    async def get_or_create(self, world_id: str, factory: RoomFactory) -> tuple[GameRoom, bool]:
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
