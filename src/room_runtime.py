"""Shared multiplayer room lifecycle, visibility-aware broadcast, and action policy."""

from __future__ import annotations

import asyncio
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


@dataclass(frozen=True)
class BufferedRoomEvent:
    event_id: int
    payload: dict[str, Any]
    visibility: str


class RoomEventHub:
    """One ordered public/private event boundary shared by all room clients."""

    def __init__(self, world_id: str, *, replay_limit: int = 1024):
        self.world_id = world_id
        self._connections: dict[str, RoomConnection] = {}
        self._events: deque[BufferedRoomEvent] = deque(maxlen=max(16, replay_limit))
        self._event_id = 0
        self._lock = asyncio.Lock()

    async def attach(self, connection: RoomConnection) -> None:
        async with self._lock:
            self._connections[connection.connection_id] = connection

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
        await connection.socket.send_json(dict(payload))
        return True

    async def broadcast(self, payload: dict[str, Any], *, visibility: str = "public") -> int:
        if visibility == "server_only":
            return self._event_id
        async with self._lock:
            self._event_id += 1
            event_id = self._event_id
            wire = dict(payload)
            wire.setdefault("room_event_id", event_id)
            wire.setdefault("world_id", self.world_id)
            event = BufferedRoomEvent(event_id, wire, visibility)
            self._events.append(event)
            recipients = [
                connection
                for connection in self._connections.values()
                if self._can_receive(connection, visibility)
            ]
        failed: list[str] = []
        for connection in recipients:
            try:
                await connection.socket.send_json(dict(wire))
            except Exception:
                failed.append(connection.connection_id)
        if failed:
            async with self._lock:
                for connection_id in failed:
                    self._connections.pop(connection_id, None)
        return event_id

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
            gap = after_event_id < oldest - 1
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
        visibility = "public"
        if payload.get("type") in {
            "suggest_check",
            "decision_request",
            "decision_resolved",
        }:
            actor_user_id = room.current_actor_user_id
            visibility = (
                f"player:{actor_user_id}" if actor_user_id else "server_only"
            )
        await room.hub.broadcast(payload, visibility=visibility)
        if payload.get("type") in {
            "done",
            "turn_rejected",
            "turn_rewrite_failed",
        }:
            room.release_action()

    async def close_input(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._incoming.put(None)


class ActionReservationError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


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

    def member_connected(self, user_id: str) -> None:
        self.connected_users[user_id] = self.connected_users.get(user_id, 0) + 1
        self.last_empty_at = None

    def member_disconnected(self, user_id: str) -> None:
        count = self.connected_users.get(user_id, 0)
        if count <= 1:
            self.connected_users.pop(user_id, None)
        else:
            self.connected_users[user_id] = count - 1
        if not self.connected_users:
            self.last_empty_at = time.monotonic()

    def set_ready(self, user_id: str, ready: bool) -> None:
        if ready:
            self.ready_users.add(user_id)
        else:
            self.ready_users.discard(user_id)

    def assign_actor(self, actor_user_id: str | None) -> None:
        self.current_actor_user_id = actor_user_id

    async def reserve_action(self, user_id: str, action_id: str) -> None:
        action_id = str(action_id or "").strip()
        if not action_id or len(action_id) > 160:
            raise ActionReservationError("invalid_action_id", "行动 ID 无效")
        if self.current_actor_user_id != user_id:
            raise ActionReservationError("not_current_actor", "现在还没有轮到你行动")
        if action_id in self._action_id_set:
            raise ActionReservationError("duplicate_action", "该行动已经提交")
        if self._action_lock.locked():
            raise ActionReservationError("room_turn_in_progress", "房间正在处理上一项行动")
        await self._action_lock.acquire()
        if len(self._action_ids) == self._action_ids.maxlen:
            expired = self._action_ids.popleft()
            self._action_id_set.discard(expired)
        self._action_ids.append(action_id)
        self._action_id_set.add(action_id)

    def release_action(self) -> None:
        if self._action_lock.locked():
            self._action_lock.release()

    @property
    def action_active(self) -> bool:
        return self._action_lock.locked()


RoomFactory = Callable[[], GameRoom | Awaitable[GameRoom]]


class RoomManager:
    """Atomically creates at most one active GameRoom for each world."""

    def __init__(self):
        self._rooms: dict[str, GameRoom] = {}
        self._loading: dict[str, asyncio.Future[GameRoom]] = {}
        self._lock = asyncio.Lock()

    async def get_or_create(self, world_id: str, factory: RoomFactory) -> tuple[GameRoom, bool]:
        creator = False
        async with self._lock:
            existing = self._rooms.get(world_id)
            if existing is not None:
                return existing, False
            pending = self._loading.get(world_id)
            if pending is None:
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
