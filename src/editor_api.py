"""HTTP adapter for persistent TRPG Mod Editor authoring sessions."""

from __future__ import annotations

import asyncio
import re
import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse
from starlette.background import BackgroundTask

from .editor_projects import (
    EditorProjectConflict,
    EditorProjectError,
    EditorProjectNotFound,
    EditorProjectStore,
    export_project_package,
)
from .module_registry import ModulePackageError


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

    @router.post("/{session_id}/export")
    async def export_project(session_id: str):
        """把工程编译为 .trpgmod 下载；校验失败返回与打包器一致的受控错误。"""
        try:
            record = await asyncio.to_thread(store.get, session_id)
        except EditorProjectError as exc:
            return _error_response(exc)
        work_dir = Path(
            tempfile.mkdtemp(prefix=".export-", dir=store.root)
        )
        try:
            package_path, _inspection = await asyncio.to_thread(
                export_project_package, record["project"], work_dir
            )
        except ModulePackageError as exc:
            shutil.rmtree(work_dir, ignore_errors=True)
            return JSONResponse(
                {
                    "ok": False,
                    "error_code": exc.code,
                    "error": exc.message,
                    "details": exc.details,
                },
                status_code=400,
            )
        except EditorProjectError as exc:
            shutil.rmtree(work_dir, ignore_errors=True)
            return _error_response(exc)
        manifest = record["project"].get("manifest") or {}
        package_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(manifest.get("id") or "module"))
        version = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(manifest.get("version") or "0"))
        return FileResponse(
            package_path,
            filename=f"{package_id}-{version}.trpgmod",
            media_type="application/zip",
            background=BackgroundTask(shutil.rmtree, work_dir, True),
        )

    return router
