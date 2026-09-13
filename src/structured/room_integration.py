"""结构化协议 × 多人房间：帧分派、按连接过滤的广播、无模型开场。

房间的每个成员连接在 run_room_message_loop 里各自收帧；结构化帧不进
共享引擎的旧回合管线（效果所有权），直接由命令服务落库，事件经
RoomEventHub.send_direct 按接收者 principal 过滤后逐连接投递。

human keeper_mode 的结构化房间：开局只翻转房间状态并下发快照，不建模型
会话、不做 BYOK 就绪检查、不跑开场回合。
"""

from __future__ import annotations

import logging

from src.storage.database import World, session_scope

from .bootstrap import KEEPER_MODES
from .gateway import STRUCTURED_FRAME_TYPES, StructuredGateway, world_modes
from .service import audience_visible, wire_envelope

logger = logging.getLogger("trpg.structured_room")

_gateways: dict[str, StructuredGateway] = {}


def gateway_for(database_url: str) -> StructuredGateway:
    gateway = _gateways.get(database_url)
    if gateway is None:
        gateway = StructuredGateway(database_url)
        _gateways[database_url] = gateway
    return gateway


def room_world_modes(database_url: str, world_id: str) -> tuple[str, str]:
    with session_scope(database_url) as session:
        world = session.get(World, world_id)
        if world is None:
            return ("legacy", "human")
        return world_modes(world.metadata_json)


async def handle_room_structured_frame(
    controller,
    room,
    ws,
    user,
    world_id: str,
    connection_id: str,
    frame: dict,
) -> None:
    """处理一条结构化帧：错误回发起方；事件按每个连接的 principal 过滤。"""
    gateway = gateway_for(controller.deps.database_url())
    # 本帧涉及连接的 principal 只解析一次（一帧产生的事件共享接收者集合）。
    principals: dict[str, object] = {}

    async def deliver(envelope: dict) -> None:
        await ws.send_json(envelope)

    async def broadcast(envelope: dict) -> None:
        audience = envelope.get("audience") or {"kind": "public"}
        wire = wire_envelope(envelope)
        for connection in await room.hub.connection_snapshot():
            other_id = connection["connection_id"]
            if other_id == connection_id:
                continue  # 发起方已由 deliver 按自身 principal 投递
            other_user = str(connection["user_id"])
            if other_user not in principals:
                principals[other_user] = gateway.connection_principal(world_id, other_user)
            principal = principals[other_user]
            if principal is None:
                continue
            if not audience_visible(audience, principal):
                continue
            await room.hub.send_direct(other_id, wire)

    await gateway.handle_frame(
        world_id=world_id,
        user_id=user.id,
        frame=frame,
        deliver=deliver,
        broadcast=broadcast,
    )


async def handle_structured_room_start(
    controller,
    room,
    ws,
    user,
    world_id: str,
) -> None:
    """结构化房间的无模型开场：翻转状态 + 全员快照，不启动引擎回合。"""
    if room.status != "lobby":
        await ws.send_json(
            {
                "type": "room_action_rejected",
                "code": "room_already_started",
                "message": "房间已经开始游戏",
            }
        )
        return
    gateway = gateway_for(controller.deps.database_url())
    principal = gateway.connection_principal(world_id, user.id)
    if principal is None or principal.kind != "keeper":
        await ws.send_json(
            {
                "type": "room_action_rejected",
                "code": "keeper_required",
                "message": "结构化房间由获授权的守秘人开局（keeper 与房主分别授权）",
            }
        )
        return
    controller.set_room_status(room, "playing")
    await controller.broadcast_room_state(room)
    # 全员按各自 principal 重同步（keeper 与玩家看到不同的快照投影）。
    for connection in await room.hub.connection_snapshot():
        envelope = gateway.snapshot_envelope(world_id=world_id, user_id=str(connection["user_id"]))
        if envelope is not None:
            await room.hub.send_direct(connection["connection_id"], envelope)


def structured_frame_gate_reason(database_url: str, world_id: str) -> str | None:
    """返回 None 表示非结构化世界；否则返回该世界结构化帧的 keeper_mode 提示。"""
    profile, keeper_mode = room_world_modes(database_url, world_id)
    if profile != "structured_v1":
        return None
    if keeper_mode not in KEEPER_MODES:  # 防御：metadata 损坏时按 human 处理
        logger.warning("world %s 的 keeper_mode 非法：%s", world_id, keeper_mode)
    return keeper_mode


__all__ = [
    "STRUCTURED_FRAME_TYPES",
    "gateway_for",
    "handle_room_structured_frame",
    "handle_structured_room_start",
    "room_world_modes",
    "structured_frame_gate_reason",
]
