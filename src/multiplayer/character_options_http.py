"""Authenticated character choices for cloud worlds."""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from src.app.runtime import RuntimeContext
from src.auth.service import authorize_world, request_user
from src.gameplay.characters import list_character_options
from src.multiplayer.service import MultiplayerError
from src.storage.database import World, session_scope


def register_character_options_route(router: APIRouter, deps) -> None:
    db_url = deps.database_url

    @router.get("/api/worlds/{world_id}/investigators/options")
    async def get_world_investigator_options(world_id: str, request: Request):
        user = request_user(request, db_url())
        if user is None:
            return JSONResponse({"detail": "未登录"}, status_code=401)
        try:
            authorize_world(db_url(), user.id, world_id, "read")
            with session_scope(db_url()) as db_session:
                world = db_session.get(World, world_id)
                if world is None or world.status != "active":
                    raise MultiplayerError("world_not_found", "房间不存在", 404)
                module_name = world.module_name
            context = RuntimeContext.create(
                world_id,
                module_name,
                project_root=deps.project_root,
                runtime_root=deps.runtime_root,
            )
            return list_character_options(
                module_name,
                context=context,
                include_personal=False,
                # 云端单人房间按当前用户带角色库分组；多人房间不含私有角色。
                library_scope=(
                    user.id
                    if str((world.metadata_json or {}).get("play_mode") or "") == "solo"
                    else None
                ),
            )
        except MultiplayerError as exc:
            return JSONResponse({"detail": exc.message, "code": exc.code}, status_code=exc.status_code)
