"""HTTP adapter for persistent TRPG Mod Editor authoring sessions."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from .editor_projects import (
    EditorProjectConflict,
    EditorProjectError,
    EditorProjectNotFound,
    EditorProjectStore,
)


def _error_response(exc: EditorProjectError) -> JSONResponse:
    if isinstance(exc, EditorProjectConflict):
        return JSONResponse({
            "ok": False,
            "error_code": "revision_conflict",
            "error": str(exc),
            "current": exc.current,
        }, status_code=409)
    status = 404 if isinstance(exc, EditorProjectNotFound) else 400
    return JSONResponse({
        "ok": False,
        "error_code": "project_not_found" if status == 404 else "invalid_project",
        "error": str(exc),
    }, status_code=status)


def create_editor_router(store: EditorProjectStore) -> APIRouter:
    router = APIRouter(prefix="/api/editor/projects", tags=["editor-projects"])

    @router.get("")
    async def list_projects():
        return {"ok": True, "projects": await asyncio.to_thread(store.list)}

    @router.post("")
    async def create_project(data: dict):
        try:
            record = await asyncio.to_thread(store.create, data.get("project"))
            return JSONResponse({"ok": True, **record}, status_code=201)
        except EditorProjectError as exc:
            return _error_response(exc)

    @router.get("/{session_id}")
    async def get_project(session_id: str):
        try:
            return {"ok": True, **await asyncio.to_thread(store.get, session_id)}
        except EditorProjectError as exc:
            return _error_response(exc)

    @router.patch("/{session_id}")
    async def update_project(session_id: str, data: dict):
        try:
            record = await asyncio.to_thread(
                store.update,
                session_id,
                data.get("expected_revision"),
                data.get("project"),
            )
            return {"ok": True, **record}
        except EditorProjectError as exc:
            return _error_response(exc)

    @router.delete("/{session_id}")
    async def delete_project(session_id: str):
        try:
            await asyncio.to_thread(store.delete, session_id)
            return {"ok": True}
        except EditorProjectError as exc:
            return _error_response(exc)

    return router
