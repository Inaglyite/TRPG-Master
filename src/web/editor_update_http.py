"""Local desktop-only browser-editor update transport."""

from __future__ import annotations

import asyncio
import os
import secrets

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from src.auth.service import auth_required, local_launch_token
from src.modules.editor_updates import EditorUpdateStore


def create_editor_update_router(store: EditorUpdateStore) -> APIRouter:
    router = APIRouter(prefix="/api/editor/updates", tags=["editor-updates"])

    def enabled(request: Request) -> bool:
        return (not auth_required() and os.environ.get("TRPG_ENABLE_EDITOR_UPDATES") == "1"
                and request.client is not None and request.client.host in {"127.0.0.1", "::1"})

    def trusted_install(request: Request) -> bool:
        # The system browser has no desktop token: require the exact same origin.
        origin = request.headers.get("origin", "")
        token = request.headers.get("X-TRPG-Local-Token", "")
        return origin == str(request.base_url).rstrip("/") or (
            bool(token) and secrets.compare_digest(token, local_launch_token()))

    @router.get("/check")
    async def check(request: Request, force: bool = False):
        if not enabled(request):
            return {"ok": True, "enabled": False, "current": store.current(), "update": None}
        try:
            return await asyncio.to_thread(store.check, force=force)
        except Exception:
            return JSONResponse({"ok": False, "error": "无法连接更新服务，请稍后重试；当前版本仍可使用"}, status_code=503)

    @router.post("/install")
    async def install(request: Request, data: dict):
        if not enabled(request) or not trusted_install(request):
            return JSONResponse({"ok": False, "error": "只允许本机客户端确认编辑器更新"}, status_code=403)
        try:
            return await asyncio.to_thread(store.install, data.get("version"), data.get("sha256"))
        except Exception:
            return JSONResponse({"ok": False, "error": "更新失败，已保留当前版本；请重新检查更新后重试"}, status_code=400)

    return router
