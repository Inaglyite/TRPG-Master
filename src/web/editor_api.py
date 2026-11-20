"""HTTP adapter for persistent TRPG Mod Editor authoring sessions."""

from __future__ import annotations

import asyncio
import mimetypes
import re
import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.background import BackgroundTask

from src.modules.editor_projects import (
    EditorProjectConflict,
    EditorProjectError,
    EditorProjectNotFound,
    EditorProjectStore,
    export_project_package,
    read_project_package,
)
from src.modules.module_registry import MAX_SINGLE_FILE_BYTES, ModulePackageError
from src.web.module_http import _module_error_response, _receive_module_upload


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

    def with_files(record: dict) -> dict:
        return {**record, "files": store.list_files(record["session_id"])}

    @router.get("")
    async def list_projects():
        return {"ok": True, "projects": await asyncio.to_thread(store.list)}

    @router.post("")
    async def create_project(data: dict):
        try:
            record = await asyncio.to_thread(
                store.create,
                data.get("project"),
                source_session_id=data.get("source_session_id"),
            )
            payload = await asyncio.to_thread(with_files, record)
            return JSONResponse({"ok": True, **payload}, status_code=201)
        except EditorProjectError as exc:
            return _error_response(exc)

    @router.get("/{session_id}")
    async def get_project(session_id: str):
        try:
            record = await asyncio.to_thread(store.get, session_id)
            return {"ok": True, **await asyncio.to_thread(with_files, record)}
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
            return {"ok": True, **await asyncio.to_thread(with_files, record)}
        except EditorProjectError as exc:
            return _error_response(exc)

    @router.delete("/{session_id}")
    async def delete_project(session_id: str):
        try:
            await asyncio.to_thread(store.delete, session_id)
            return {"ok": True}
        except EditorProjectError as exc:
            return _error_response(exc)

    @router.post("/import")
    async def import_project(request: Request):
        try:
            package_path = await _receive_module_upload(request, store.root.parent)
        except ModulePackageError as exc:
            return _module_error_response(exc)
        try:
            project, files = await asyncio.to_thread(read_project_package, package_path)
            record = await asyncio.to_thread(store.create, project, files)
            payload = await asyncio.to_thread(with_files, record)
        except ModulePackageError as exc:
            return _module_error_response(exc)
        except EditorProjectError as exc:
            return _error_response(exc)
        finally:
            package_path.unlink(missing_ok=True)
        return JSONResponse({"ok": True, **payload}, status_code=201)

    @router.get("/{session_id}/files/{file_path:path}")
    async def get_project_file(session_id: str, file_path: str):
        try:
            paths = await asyncio.to_thread(store.file_paths, session_id)
            path = paths.get(file_path)
            if path is None:
                raise EditorProjectNotFound("工程文件不存在")
        except EditorProjectError as exc:
            return _error_response(exc)
        return FileResponse(
            path,
            media_type=mimetypes.guess_type(file_path)[0] or "application/octet-stream",
            headers={"Cache-Control": "no-cache"},
        )

    @router.put("/{session_id}/files/{file_path:path}")
    async def put_project_file(session_id: str, file_path: str, request: Request):
        content_length = request.headers.get("content-length")
        try:
            if content_length and int(content_length) > MAX_SINGLE_FILE_BYTES:
                raise ModulePackageError("file_too_large", "单个工程文件超过 32 MiB 上限")
        except ValueError:
            return _module_error_response(ModulePackageError("invalid_length", "Content-Length 无效"))
        except ModulePackageError as exc:
            return _module_error_response(exc)
        handle = tempfile.NamedTemporaryFile(mode="wb", prefix=".upload-", dir=store.root, delete=False)
        upload_path = Path(handle.name)
        total = 0
        try:
            async for chunk in request.stream():
                total += len(chunk)
                if total > MAX_SINGLE_FILE_BYTES:
                    raise ModulePackageError("file_too_large", "单个工程文件超过 32 MiB 上限")
                handle.write(chunk)
            handle.close()
            payload = await asyncio.to_thread(upload_path.read_bytes)
            file = await asyncio.to_thread(store.write_file, session_id, file_path, payload)
            files = await asyncio.to_thread(store.list_files, session_id)
            return {"ok": True, "file": file, "files": files}
        except ModulePackageError as exc:
            return _module_error_response(exc)
        except EditorProjectError as exc:
            return _error_response(exc)
        finally:
            handle.close()
            upload_path.unlink(missing_ok=True)

    @router.delete("/{session_id}/files/{file_path:path}")
    async def delete_project_file(session_id: str, file_path: str):
        try:
            await asyncio.to_thread(store.delete_file, session_id, file_path)
            return {"ok": True, "files": await asyncio.to_thread(store.list_files, session_id)}
        except EditorProjectError as exc:
            return _error_response(exc)

    @router.post("/{session_id}/export")
    async def export_project(session_id: str):
        try:
            record = await asyncio.to_thread(store.get, session_id)
            files = await asyncio.to_thread(store.file_paths, session_id)
        except EditorProjectError as exc:
            return _error_response(exc)
        work_dir = Path(tempfile.mkdtemp(prefix=".export-", dir=store.root))
        try:
            package_path, _inspection = await asyncio.to_thread(
                export_project_package, record["project"], work_dir, files
            )
        except ModulePackageError as exc:
            shutil.rmtree(work_dir, ignore_errors=True)
            return _module_error_response(exc)
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
