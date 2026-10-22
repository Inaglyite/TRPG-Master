"""Scoped player tools: own skill sheet, visited map and public-prose note draft."""

from __future__ import annotations

import asyncio
import copy

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.ai.model.investigation_digest import extract_investigation_digest
from src.ai.model.route_service import LocalRouteResolver, resolve_routes
from src.auth.service import auth_required, authorize_world, local_request_trusted, request_user
from src.gameplay.investigators import investigator_entity
from src.gameplay.player_tools import player_skill_options, visited_scene_map
from src.storage.database import World, WorldInvestigator, WorldMember, WorldState, session_scope
from src.storage.model_config_store import room_route_resolver
from src.structured.bootstrap import LOCAL_OPERATOR_USER_ID, local_player_investigator_ids


class DigestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    source_id: str = Field(min_length=1, max_length=200)
    source_text: str = Field(min_length=4, max_length=12000)


def _identity(request, db_url):
    if auth_required():
        user = request_user(request, db_url)
        if user is None:
            raise HTTPException(401, "未登录或会话已过期")
        return user.id
    if not local_request_trusted(request.headers):
        raise HTTPException(403, "本地请求来源不受信任")
    return None


def read_player_tools(deps, world_id, user_id):
    db_url = deps.database_url()
    role = authorize_world(db_url, user_id, world_id, "read") if user_id else "owner"
    with session_scope(db_url) as session:
        world, row = session.get(World, world_id), session.get(WorldState, world_id)
        if world is None or row is None or world.status != "active":
            raise HTTPException(404, "世界不可用")
        if user_id is None:
            # Anonymous loopback mode must not read an account-owned world.
            members = session.query(WorldMember).filter_by(world_id=world_id).all()
            if any(member.user_id != LOCAL_OPERATOR_USER_ID for member in members):
                raise HTTPException(404, "世界不可用")
        state = copy.deepcopy(row.state or {})
        pc = {}
        structured = (world.metadata_json or {}).get("execution_profile") == "structured_v1"
        if user_id is None:
            ids = local_player_investigator_ids(session, world_id, state)
            pc = investigator_entity(state, ids[0] if structured and ids else "pc") or {}
        elif role in {"owner", "player"}:
            claim = session.query(WorldInvestigator).filter_by(
                world_id=world_id, controller_user_id=user_id, status="claimed",
            ).one_or_none()
            if claim:
                pc = investigator_entity(state, claim.character_key if structured else claim.id) or {}
        return {"world_id": world_id, "revision": int(row.revision),
                "character_name": str(pc.get("name") or ""),
                "skills": player_skill_options(pc) if pc else [],
                "map": visited_scene_map(state), "can_organize": role in {"owner", "player"}}


def _response(data):
    return JSONResponse(data, headers={"Cache-Control": "private, no-store", "Vary": "Cookie"})


def register_player_tools_routes(router, deps):
    @router.get("/api/worlds/{world_id}/player-tools")
    async def tools(world_id: str, request: Request):
        user_id = _identity(request, deps.database_url())
        return _response(await asyncio.to_thread(read_player_tools, deps, world_id, user_id))

    @router.post("/api/worlds/{world_id}/investigation-digest")
    async def digest(world_id: str, request: Request):
        user_id = _identity(request, deps.database_url())
        # Bound the actual body as well as validated text, regardless of Content-Length.
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 100_000:
                raise HTTPException(413, "本轮文字过长，请选取一段后再整理")
        try:
            data = DigestRequest.model_validate_json(raw)
        except ValidationError:
            raise HTTPException(400, "请选择一轮完整叙述后再整理") from None
        projection = await asyncio.to_thread(read_player_tools, deps, world_id, user_id)
        if not projection["can_organize"]:
            raise HTTPException(403, "旁观模式不能调用线索整理")

        def generate():
            resolver = (room_route_resolver(deps.database_url(), world_id) if user_id
                        else LocalRouteResolver(deps.runtime_root / "model_settings.local.json"))
            role = resolve_routes(resolver()).judgement
            return extract_investigation_digest(role, data.source_text,
                                                world_id=world_id, user_id=user_id or "local")

        try:
            result = await asyncio.to_thread(generate)
        except Exception:
            # No raw model/provider errors or credentials in the public response.
            raise HTTPException(503, "暂时无法自动整理，请检查模型设置后重试；本轮文字仍可手动摘录。") from None
        # Membership may have changed during the model request.
        current = await asyncio.to_thread(read_player_tools, deps, world_id, user_id)
        if not current["can_organize"]:
            raise HTTPException(403, "你已不再拥有线索整理权限")
        return _response({"world_id": world_id, "source_id": data.source_id, **result})
