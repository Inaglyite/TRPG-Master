"""server.py 的结构化协议接线（本地 /ws 与房间驱动会话共用同一形态）。

server.py 的行数受架构门禁约束，这里承接全部接线逻辑，调用点保持：
创建并 register（1 行）、消息循环里经 dispatch 代理（替换原 dispatch 调用）、
初始化消息后 send_snapshot（1 行）。
"""

from __future__ import annotations

import asyncio
from typing import Any

from src.multiplayer.ws_router import DispatchResult

from .gateway import STRUCTURED_FRAME_TYPES, StructuredGateway

# structured_v1 世界关闭旧文字回合入口（效果所有权：行动效果只走命令服务，
# 防止新旧通路双结算；协议 §8）。
_LEGACY_TURN_MESSAGES = {
    "start": "该世界使用结构化协议：无 AI 开场回合，由守秘人直接主持。",
    "action": "该世界使用结构化协议：请使用出示/使用/前往等结构化入口，自由叙述会进入守秘人待办。",
    "continue": "该世界使用结构化协议：恢复进度请重连获取快照，不产生 AI 续写回合。",
    "save_load": "该世界使用结构化协议：恢复进度请重连获取快照，不产生 AI 续写回合。",
}


class StructuredLocalWire:
    """结构化帧处理 + 旧回合门禁 + 首连快照，绑定一条 WS 会话。"""

    def __init__(self, engine: Any, outbound: Any, user_id: str | None):
        self.engine = engine
        self.outbound = outbound
        self.user_id = user_id
        self.gateway = StructuredGateway(engine.context.database_url)

    def register(self, router: Any) -> StructuredLocalWire:
        """注册四种结构化帧；legacy 世界收到帧由网关回 request_error。"""

        async def _handle(data: dict) -> None:
            await self.gateway.handle_frame(
                world_id=self.engine.context.world_id,
                user_id=self.user_id,
                frame=data,
                deliver=self.outbound.send,
            )

        for frame_type in STRUCTURED_FRAME_TYPES:
            router.add(frame_type, _handle)
        return self

    async def dispatch(self, router: Any, data: dict) -> DispatchResult:
        """代理路由分发：结构化世界的旧回合帧在此拦截并给出明确指引。"""
        guidance = _LEGACY_TURN_MESSAGES.get(str(data.get("type") or ""))
        if guidance is not None and self.gateway.is_structured(self.engine.context.world_id):
            await self.outbound.send(
                {"type": "error", "code": "structured_required", "message": guidance}
            )
            return DispatchResult(True, str(data.get("type") or ""))
        return await router.dispatch(data)

    async def send_snapshot(self) -> None:
        """structured_v1 世界：下发权威快照（含 capabilities/游标）。"""
        if not self.gateway.is_structured(self.engine.context.world_id):
            return
        snapshot = await asyncio.to_thread(
            lambda: self.gateway.snapshot_envelope(
                world_id=self.engine.context.world_id, user_id=self.user_id
            )
        )
        if snapshot is not None:
            await self.outbound.send(snapshot)
