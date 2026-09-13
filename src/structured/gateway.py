"""结构化协议的连接层网关：帧校验 → principal 解析 → 命令服务 → 事件投递。

传输无关：本地 /ws 与多人房间各自提供 deliver/broadcast 回调，网关负责
协议一致性（帧 schema、权限、幂等、错误信封）与按世界串行化（投递顺序
与提交顺序一致，前端 sequencer 只按 event_id 去重、不重排）。

调用方不发送 principal/controller_epoch：身份只从服务端会话与成员表解析；
帧里的 world_id 必须与连接所在世界一致（防一连接越权操作另一世界）。
"""

from __future__ import annotations

import asyncio
import copy
from collections.abc import Awaitable, Callable

from sqlalchemy import func, select

from src.storage.database import (
    EventOutbox,
    World,
    WorldState,
    session_scope,
    utcnow,
)
from src.storage.database_store import migrate_world_state

from .bootstrap import (
    LOCAL_OPERATOR_USER_ID,
    ensure_local_operator,
    local_player_investigator_ids,
)
from .errors import StructuredError
from .principal import Principal, resolve_keeper_principal, resolve_player_principal
from .service import StructuredPlayService, audience_visible, wire_envelope
from .validation import validate_frame

STRUCTURED_FRAME_TYPES = frozenset(
    {"action_request", "free_roll_request", "check_response", "command_request"}
)

Deliver = Callable[[dict], Awaitable[None]]
Broadcast = Callable[[dict], Awaitable[None]]


def world_modes(metadata: dict | None) -> tuple[str, str]:
    meta = metadata or {}
    return (
        str(meta.get("execution_profile") or "legacy"),
        str(meta.get("keeper_mode") or "human"),
    )


class StructuredGateway:
    """一个数据库一个网关；WS 连接共享它，内部按世界加锁串行执行。"""

    def __init__(self, database_url: str):
        self.database_url = database_url
        self.service = StructuredPlayService(database_url)
        self._locks: dict[str, asyncio.Lock] = {}

    # ------------------------------------------------------------------
    # 世界模式与 principal 解析
    # ------------------------------------------------------------------

    def is_structured(self, world_id: str) -> bool:
        with session_scope(self.database_url) as session:
            world = session.get(World, world_id)
            return world is not None and world_modes(world.metadata_json)[0] == "structured_v1"

    def _resolve_principal(
        self, session, world_id: str, user_id: str | None, frame_type: str
    ) -> Principal:
        if user_id is None:
            # 本地无账号模式：隐式 local 操作者，授权链路与云端同一套规则。
            ensure_local_operator(session, world_id)
            user_id = LOCAL_OPERATOR_USER_ID
        if frame_type == "command_request":
            return resolve_keeper_principal(session, world_id, user_id)
        principal = resolve_player_principal(session, world_id, user_id)
        if user_id == LOCAL_OPERATOR_USER_ID and not principal.investigator_ids:
            # 本地尚未走认领流程：退化为世界内全部调查员。
            row = session.get(WorldState, world_id)
            state, _ = migrate_world_state(copy.deepcopy(row.state)) if row else ({}, False)
            return Principal(
                kind="player",
                user_id=user_id,
                investigator_ids=local_player_investigator_ids(session, world_id, state),
            )
        return principal

    def connection_principal(self, world_id: str, user_id: str | None) -> Principal | None:
        """连接级 principal（事件过滤与快照投影用）。

        keeper 授权优先（keeper 按角色可见全部秘密）；本地无账号模式的隐式
        操作者同时戴主持与调查员两顶帽子（单机自己跑团），investigator_ids
        取世界内全部调查员，快照与发言授权沿用同一条链路。
        """
        with session_scope(self.database_url) as session:
            effective = user_id
            if effective is None:
                world = session.get(World, world_id)
                if world is None or world_modes(world.metadata_json)[0] != "structured_v1":
                    return None
                # 本地无账号：先确保隐式操作者存在，再走同一套授权解析。
                ensure_local_operator(session, world_id)
                row = session.get(WorldState, world_id)
                state, _ = migrate_world_state(copy.deepcopy(row.state)) if row else ({}, False)
                return Principal(
                    kind="keeper",
                    user_id=LOCAL_OPERATOR_USER_ID,
                    investigator_ids=local_player_investigator_ids(session, world_id, state),
                )
            try:
                return resolve_keeper_principal(session, world_id, effective)
            except StructuredError:
                pass
            try:
                return resolve_player_principal(session, world_id, effective)
            except StructuredError:
                return None

    # ------------------------------------------------------------------
    # 帧处理
    # ------------------------------------------------------------------

    def _world_lock(self, world_id: str) -> asyncio.Lock:
        lock = self._locks.get(world_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[world_id] = lock
        return lock

    def _execute(self, world_id: str, user_id: str | None, frame: dict) -> list[dict]:
        frame_type = str(frame.get("type") or "")
        validate_frame(frame_type, frame)
        frame_world = str(frame.get("world_id") or "")
        if frame_world != world_id:
            raise StructuredError(
                "not_authorized", "帧的 world_id 与当前连接世界不一致。", retryable=False
            )
        with session_scope(self.database_url) as session:
            principal = self._resolve_principal(session, world_id, user_id, frame_type)
        if frame_type == "action_request":
            self.service.submit_action_request(
                world_id=world_id, principal=principal, request=frame
            )
        elif frame_type == "free_roll_request":
            self.service.submit_free_roll(world_id=world_id, principal=principal, request=frame)
        elif frame_type == "check_response":
            self.service.submit_check_response(
                world_id=world_id, principal=principal, request=frame
            )
        else:
            self.service.execute_command(
                world_id=world_id,
                principal=principal,
                kind=str(frame.get("kind") or ""),
                payload=dict(frame.get("payload") or {}),
                command_id=str(frame.get("command_id") or ""),
                expected_revision=frame.get("expected_revision"),
                cause_id=str(frame.get("cause_id") or ""),
            )
        # 已提交事件从 outbox 重读（按 cause 归集）：发出的就是落库的；
        # 幂等重试也因此拿到原始 ack 事件（前端按 event_id 去重）。
        return self._committed_events(world_id, frame)

    @staticmethod
    def _cause_id(frame: dict) -> str:
        return str(frame.get("request_id") or frame.get("command_id") or "")

    def _committed_events(self, world_id: str, frame: dict) -> list[dict]:
        cause_ids = {self._cause_id(frame)}
        upstream = str(frame.get("cause_id") or "")
        if upstream:
            cause_ids.add(upstream)
        cause_ids.discard("")
        if not cause_ids:
            return []
        with session_scope(self.database_url) as session:
            rows = session.execute(
                select(EventOutbox)
                .where(
                    EventOutbox.world_id == world_id,
                    EventOutbox.cause_request_id.in_(sorted(cause_ids)),
                )
                .order_by(EventOutbox.sequence)
            ).scalars()
            return [
                {
                    "protocol_version": 1,
                    "event_id": int(row.id),
                    "world_id": world_id,
                    "sequence": int(row.sequence),
                    "revision": int(row.revision),
                    "type": row.event_type,
                    "cause_request_id": row.cause_request_id or None,
                    "payload": dict(row.payload),
                    "audience": dict(row.audience),
                }
                for row in rows
            ]

    async def handle_frame(
        self,
        *,
        world_id: str,
        user_id: str | None,
        frame: dict,
        deliver: Deliver,
        broadcast: Broadcast | None = None,
    ) -> None:
        """处理一帧：错误只回发起方；已提交事件按各连接 principal 过滤投递。"""
        async with self._world_lock(world_id):
            try:
                events = await asyncio.to_thread(self._execute, world_id, user_id, frame)
            except StructuredError as exc:
                error = await asyncio.to_thread(self._persist_request_error, world_id, frame, exc)
                await deliver(error)
                return
            except Exception:
                # 未预期异常：不能让客户端干等 ack，也不能泄露内部细节。
                error = await asyncio.to_thread(
                    self._persist_request_error,
                    world_id,
                    frame,
                    StructuredError("internal_error", "服务内部错误，请求未生效。", retryable=True),
                )
                await deliver(error)
                raise
            # 投递过滤用连接级身份（本地操作者同时是 keeper 与调查员；
            # 云端 keeper 也能看到自己权限内的 keeper 向事件），与执行授权分开。
            viewer = await asyncio.to_thread(self.connection_principal, world_id, user_id)
            for envelope in events:
                if viewer is not None and audience_visible(envelope["audience"], viewer):
                    await deliver(wire_envelope(envelope))
                if broadcast is not None:
                    # 内部信封（含 audience）交给传输层：按各连接 principal
                    # 过滤后剥离 audience 再上线。
                    await broadcast(envelope)

    # ------------------------------------------------------------------
    # request_error：拒绝也落 outbox（真实 event_id 让前端游标正常去重），
    # 但不推进世界 revision；audience 只给发起方（keeper 可见用于主持排障）。
    # ------------------------------------------------------------------

    def _persist_request_error(self, world_id: str, frame: dict, exc: StructuredError) -> dict:
        payload: dict = {
            "code": exc.code,
            "message": exc.message,
            "retryable": bool(exc.retryable),
        }
        request_id = str(frame.get("request_id") or "")
        command_id = str(frame.get("command_id") or "")
        if request_id:
            payload["request_id"] = request_id
        if command_id:
            payload["command_id"] = command_id
        cause_id = self._cause_id(frame) or f"error-{utcnow().timestamp()}"
        investigator_id = str(frame.get("investigator_id") or "")
        if str(frame.get("type") or "") == "command_request":
            audience = {"kind": "keeper"}
        elif investigator_id:
            audience = {"kind": "investigators", "investigator_ids": [investigator_id]}
        else:
            audience = {"kind": "keeper"}
        with session_scope(self.database_url) as session:
            row = session.get(WorldState, world_id)
            revision = int(row.revision) if row is not None else 0
            sequence = session.execute(
                select(func.max(EventOutbox.sequence)).where(EventOutbox.world_id == world_id)
            ).scalar_one()
            event = EventOutbox(
                world_id=world_id,
                sequence=int(sequence or 0) + 1,
                revision=revision,
                event_type="request_error",
                payload=payload,
                audience=audience,
                cause_request_id=cause_id,
            )
            session.add(event)
            session.flush()
            return {
                "protocol_version": 1,
                "event_id": int(event.id),
                "world_id": world_id,
                "sequence": int(event.sequence),
                "revision": revision,
                "type": "request_error",
                "cause_request_id": request_id or None,
                "payload": payload,
            }

    # ------------------------------------------------------------------
    # 快照（合成信封，不占 outbox；连接建立时全量重同步）
    # ------------------------------------------------------------------

    def snapshot_envelope(self, *, world_id: str, user_id: str | None) -> dict | None:
        principal = self.connection_principal(world_id, user_id)
        if principal is None:
            return None
        payload = self.service.session_snapshot(world_id=world_id, principal=principal)
        with session_scope(self.database_url) as session:
            last_event_id = session.execute(
                select(func.max(EventOutbox.id)).where(EventOutbox.world_id == world_id)
            ).scalar_one()
        return {
            "protocol_version": 1,
            # 快照不是新事件：沿用当前最大 event_id，已同步的连接按去重忽略，
            # 断线重连（游标落后）的连接正常接收并整体刷新。
            "event_id": int(last_event_id or 0),
            "world_id": world_id,
            "sequence": int(payload["cursor"]["sequence"]),
            "revision": int(payload["revision"]),
            "type": "session_snapshot",
            "cause_request_id": None,
            "payload": payload,
        }
