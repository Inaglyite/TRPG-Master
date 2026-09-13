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
# load/save_load 不在此列：它们走 restore_structured_save（CAS 回滚 +
# 结构化表 reconcile），见 dispatch。
_LEGACY_TURN_MESSAGES = {
    "start": "该世界使用结构化协议：无 AI 开场回合，由守秘人直接主持。",
    "action": "该世界使用结构化协议：请使用出示/使用/前往等结构化入口，自由叙述会进入守秘人待办。",
    "continue": "该世界使用结构化协议：恢复进度请重连获取快照，不产生 AI 续写回合。",
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
        msg_type = str(data.get("type") or "")
        if msg_type in {"load", "save_load"} and self.gateway.is_structured(
            self.engine.context.world_id
        ):
            await self._handle_structured_restore(data)
            return DispatchResult(True, msg_type)
        guidance = _LEGACY_TURN_MESSAGES.get(msg_type)
        if guidance is not None and self.gateway.is_structured(self.engine.context.world_id):
            await self.outbound.send(
                {"type": "error", "code": "structured_required", "message": guidance}
            )
            return DispatchResult(True, msg_type)
        return await router.dispatch(data)

    async def _handle_structured_restore(self, data: dict) -> None:
        """结构化世界读档：CAS 回滚 + reconcile（绝不走 engine.load 静默回滚）。

        恢复成功后下发 loaded 回执与全新 session_snapshot（世界 revision 已
        回退，客户端必须以快照重同步，不能沿用旧游标）。
        """
        from src.app.game_application import SaveNotFoundError
        from src.storage.database_store import StaleRevisionError

        from .branch import restore_structured_save

        slot_id = str(data.get("slot_id") or "") or None
        try:
            result = await asyncio.to_thread(restore_structured_save, self.engine.context, slot_id)
        except SaveNotFoundError:
            await self.outbound.send({"type": "error", "message": "未找到存档。", "terminal": True})
            return
        except StaleRevisionError as exc:
            await self.outbound.send({"type": "error", "message": str(exc), "terminal": True})
            return
        await self.outbound.send(
            {
                "type": "loaded",
                "ok": True,
                "slot_id": result["slot_id"],
                "count": 0,  # 结构化世界无消息历史；权威内容在快照里
            }
        )
        await self.send_snapshot()

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
