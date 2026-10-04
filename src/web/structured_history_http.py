"""Read authorized older narrative pages; no model, mutation or raw event API."""

from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import Query, Request
from fastapi.responses import JSONResponse

from src.auth.service import auth_required, local_request_trusted, request_user
from src.storage.database import World, WorldState, session_scope
from src.structured.gateway import StructuredGateway
from src.structured.message_history import visible_message_history


def read_message_history(deps, world_id, user_id, before_sequence, *, inherited=False):
    db_url = deps.database_url()
    gateway = StructuredGateway(db_url)
    if not gateway.is_structured(world_id):
        return None
    principal = gateway.connection_principal(world_id, user_id)
    if principal is None:
        return None
    with session_scope(db_url) as session:
        row = session.get(WorldState, world_id)
        if row is None:
            return None
        if inherited:
            world = session.get(World, world_id)
            branch = (world.metadata_json or {}).get("branch") if world else None
            if not isinstance(branch, dict) or branch.get("history_archive_version") != 1:
                return None
            from src.structured.history_archive import visible_archive_history

            return visible_archive_history(
                session, world_id, principal, before_sequence=before_sequence
            )
        return visible_message_history(
            session, world_id, principal, row.state, before_sequence=before_sequence
        )


def register_structured_history_routes(router, deps):
    @router.get("/api/worlds/{world_id}/narrative-history")
    async def history(
        world_id: str,
        request: Request,
        before_sequence: int = Query(gt=0, le=9007199254740991),
        scope: Literal["current", "inherited"] = "current",
    ):
        user = request_user(request, deps.database_url())
        if user is not None:
            user_id = user.id
        elif auth_required():
            return JSONResponse({"detail": "未登录或会话已过期"}, status_code=401)
        elif local_request_trusted(request.headers):
            user_id = None
        else:
            return JSONResponse({"detail": "本地请求不受信任"}, status_code=403)
        result = await asyncio.to_thread(
            read_message_history,
            deps,
            world_id,
            user_id,
            before_sequence,
            inherited=scope == "inherited",
        )
        response = JSONResponse(
            result if result is not None else {"detail": "无法读取叙事历史"},
            status_code=200 if result is not None else 404,
        )
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["Vary"] = "Cookie"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response
