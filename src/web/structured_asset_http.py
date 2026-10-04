"""On-demand structured handout preview, behind world and recipient permissions.

Images never enter snapshots, model context, or persisted outbox payloads.
Player access is established by a committed grant, not possession of an asset ID.
"""

from __future__ import annotations

import asyncio
import base64

from fastapi import Request
from fastapi.responses import JSONResponse

from src.auth.service import auth_required, local_request_trusted, request_user
from src.storage.database import World, WorldState, session_scope
from src.structured.gateway import StructuredGateway
from src.structured.materials import asset_entries, asset_is_granted

MAX_PREVIEW_BYTES = 4 * 1024 * 1024
IMAGE_MIMES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".avif": "image/avif",
}


def read_world_asset(deps, world_id: str, asset_id: str, user_id: str | None) -> dict | None:
    """Use the same server-derived world identity as WS; no RuntimeContext writes."""
    db_url = deps.database_url()
    gateway = StructuredGateway(db_url)
    if not gateway.is_structured(world_id):
        return None
    principal = gateway.connection_principal(world_id, user_id)
    if principal is None:
        return None
    with session_scope(db_url) as session:
        world = session.get(World, world_id)
        row = session.get(WorldState, world_id)
        if world is None or row is None:
            return None
        state = row.state or {}
        if principal.kind not in {"keeper", "agent"} and not asset_is_granted(
            state, asset_id, principal.investigator_ids
        ):
            return None
        entry = asset_entries(state).get(asset_id)
        if entry is None:
            return None
        module_name = world.module_name
    try:
        record = deps.module_registry.resolve(module_name)
        root = (record.path / "assets").resolve()
        path = (root / entry["file"]).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            return None
        mime = IMAGE_MIMES.get(path.suffix.lower())
        if mime is None:
            return None
        with path.open("rb") as stream:
            data = stream.read(MAX_PREVIEW_BYTES + 1)
    except (OSError, ValueError, FileNotFoundError):
        return None
    if not data or len(data) > MAX_PREVIEW_BYTES:
        return None
    return {
        "asset_id": asset_id,
        "label": str(entry.get("label") or asset_id),
        "asset_data_uri": f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}",
    }


def register_structured_asset_routes(router, deps) -> None:
    @router.get("/api/worlds/{world_id}/handouts/{asset_id}")
    async def preview_handout(world_id: str, asset_id: str, request: Request):
        user = request_user(request, deps.database_url())
        if user is not None:
            user_id = user.id
        elif auth_required():
            return JSONResponse({"detail": "未登录或会话已过期"}, status_code=401)
        else:
            if not local_request_trusted(request.headers):
                return JSONResponse({"detail": "本地请求不受信任"}, status_code=403)
            user_id = None
        payload = await asyncio.to_thread(read_world_asset, deps, world_id, asset_id, user_id)
        # Do not reveal whether a forbidden world, ID, or file exists.
        return JSONResponse(
            payload or {"detail": "图片不可用，或你尚未获准查看", "code": "asset_unavailable"},
            status_code=200 if payload else 404,
            headers={
                "Cache-Control": "private, no-store",
                "Vary": "Cookie",
                "X-Content-Type-Options": "nosniff",
            },
        )
