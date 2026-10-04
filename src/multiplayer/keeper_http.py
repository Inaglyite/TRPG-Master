"""房主显式管理主持授权；房主移交不隐式授予秘密读取权。"""

from fastapi import Request
from fastapi.responses import JSONResponse

from src.auth.service import audit, request_user
from src.storage.database import KeeperControl, WorldMember, session_scope
from src.web.structured_asset_http import register_structured_asset_routes
from src.web.structured_guide_http import register_structured_guide_routes
from src.web.structured_history_http import register_structured_history_routes

from .service import MultiplayerError, _require_member, _require_owner, _require_world


def set_keeper_authorization(db_url, world_id, target_id, actor_id, enabled):
    if not isinstance(enabled, bool):
        raise MultiplayerError("invalid_keeper_authorization", "can_keeper 必须为布尔值")
    with session_scope(db_url) as session:
        world = _require_world(session, world_id)
        session.refresh(world, with_for_update=True)
        _require_owner(session, world_id, actor_id)
        if (world.metadata_json or {}).get("execution_profile") != "structured_v1":
            raise MultiplayerError("profile_mismatch", "仅结构化世界支持独立主持授权")
        member = _require_member(session, world_id, target_id)
        if not enabled and member.can_keeper:
            other = (
                session.query(WorldMember)
                .filter(
                    WorldMember.world_id == world_id,
                    WorldMember.user_id != target_id,
                    WorldMember.can_keeper.is_(True),
                )
                .first()
            )
            if other is None:
                raise MultiplayerError(
                    "keeper_required", "请先授权另一位主持，再撤销最后一位主持", 409
                )
        member.can_keeper = enabled
        control = session.get(KeeperControl, world_id, with_for_update=True)
        if (
            not enabled
            and control
            and control.controller_kind == "human"
            and control.controller_id == target_id
        ):
            control.controller_kind, control.controller_id = "none", ""
            control.epoch = int(control.epoch) + 1
        return {"user_id": target_id, "can_keeper": enabled}


def register_keeper_routes(router, deps):
    register_structured_asset_routes(router, deps)
    register_structured_guide_routes(router, deps)
    register_structured_history_routes(router, deps)

    @router.patch("/api/worlds/{world_id}/members/{target_id}/keeper")
    async def change_keeper(world_id: str, target_id: str, data: dict, request: Request):
        db_url = deps.database_url()
        user = request_user(request, db_url)
        if user is None:
            return JSONResponse({"detail": "未登录"}, status_code=401)
        try:
            result = set_keeper_authorization(
                db_url, world_id, target_id, user.id, data.get("can_keeper")
            )
        except MultiplayerError as exc:
            return JSONResponse(
                {"detail": exc.message, "code": exc.code}, status_code=exc.status_code
            )
        room = await deps.room_manager().get(world_id)
        if room is not None:
            await room.hub.disconnect_user(
                target_id, code=4409, reason="主持权限已更新，请重新连接"
            )
            await deps.broadcast_room_state(room)
        audit(
            db_url,
            "world_keeper_authorization_changed",
            user_id=user.id,
            world_id=world_id,
            details={"target_user_id": target_id, "can_keeper": result["can_keeper"]},
        )
        return result
