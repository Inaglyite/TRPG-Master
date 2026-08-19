"""HTTP endpoints for module packages, schemas, switching, and public assets."""

from __future__ import annotations

import asyncio
import mimetypes
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse

from src.lorebook import lorebook_json_schema
from src.module_compiler import compile_payload
from src.module_format import (
    manifest_json_schema,
    manifest_v2_json_schema,
    module_json_schema,
    module_v2_json_schema,
)
from src.module_registry import (
    MAX_PACKAGE_BYTES,
    ModulePackageError,
    ModuleRegistry,
    inspect_package,
)
from src.runtime import RuntimeContext


@dataclass(frozen=True)
class ModuleHttpDependencies:
    registry: Callable[[], ModuleRegistry]
    project_root: Path
    runtime_root: Callable[[], Path]
    active_context: Callable[[], RuntimeContext]
    set_active_context: Callable[[RuntimeContext], None]
    auth_required: Callable[[], bool]


def _module_error_response(exc: ModulePackageError) -> JSONResponse:
    status = {
        "version_conflict": 409,
        "package_too_large": 413,
        "expanded_too_large": 413,
        "file_too_large": 413,
        "too_many_files": 413,
    }.get(exc.code, 400)
    return JSONResponse(
        {
            "ok": False,
            "error_code": exc.code,
            "error": exc.message,
            "details": exc.details,
        },
        status_code=status,
    )


async def _receive_module_upload(request: Request, runtime_root: Path) -> Path:
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_PACKAGE_BYTES:
                raise ModulePackageError("package_too_large", "模组包超过 64 MiB 上限")
        except ValueError as exc:
            raise ModulePackageError("invalid_length", "Content-Length 无效") from exc

    import_dir = runtime_root / ".module-imports"
    import_dir.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="wb",
        prefix="upload-",
        suffix=".trpgmod",
        dir=import_dir,
        delete=False,
    )
    path = Path(handle.name)
    total = 0
    try:
        async for chunk in request.stream():
            total += len(chunk)
            if total > MAX_PACKAGE_BYTES:
                raise ModulePackageError("package_too_large", "模组包超过 64 MiB 上限")
            handle.write(chunk)
        handle.close()
        if total == 0:
            raise ModulePackageError("empty_upload", "没有收到模组包内容")
        return path
    except Exception:
        handle.close()
        path.unlink(missing_ok=True)
        raise


def serve_module_asset(
    registry: ModuleRegistry,
    *,
    hosted: bool,
    module_name: str,
    filename: str,
    versioned: bool = False,
) -> FileResponse | JSONResponse:
    """Serve a local-mode asset while preventing traversal and hosted spoilers."""
    if hosted:
        return JSONResponse({"error": "not found"}, status_code=404)
    try:
        record = registry.resolve(module_name)
    except FileNotFoundError:
        return JSONResponse({"error": "module not found"}, status_code=404)
    asset_path = (record.path / "assets" / filename).resolve()
    allowed = (record.path / "assets").resolve()
    if not asset_path.is_relative_to(allowed):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    if not asset_path.is_file():
        return JSONResponse({"error": "not found"}, status_code=404)
    mime, _ = mimetypes.guess_type(str(asset_path))
    # ?v=<模块版本> 只是缓存键：版本升级产生新 URL，因此带 v 的响应可以安全
    # 长期缓存；不带 v 的旧客户端回退到 ETag 条件请求。
    headers = (
        {"Cache-Control": "public, max-age=31536000, immutable"} if versioned else None
    )
    return FileResponse(asset_path, media_type=mime or "image/png", headers=headers)


def create_module_http_router(deps: ModuleHttpDependencies) -> APIRouter:
    router = APIRouter()

    @router.get("/api/modules")
    async def list_modules():
        context = deps.active_context()
        return {
            "modules": [record.to_dict() for record in deps.registry().list_modules()],
            "active": context.module_name,
        }

    @router.get("/api/modules/schema/manifest-v1")
    async def get_module_manifest_schema():
        return manifest_json_schema()

    @router.get("/api/modules/schema/module-v1")
    async def get_module_definition_schema():
        return module_json_schema()

    @router.get("/api/modules/schema/manifest-v2")
    async def get_module_manifest_v2_schema():
        return manifest_v2_json_schema()

    @router.get("/api/modules/schema/module-v2")
    async def get_module_definition_v2_schema():
        return module_v2_json_schema()

    @router.get("/api/modules/schema/lorebook-v3")
    async def get_lorebook_schema():
        return lorebook_json_schema()

    @router.post("/api/modules/compile")
    async def compile_module_preview(data: dict):
        preview = await asyncio.to_thread(
            compile_payload,
            data.get("manifest"),
            data.get("module"),
            data.get("keeper_document", ""),
            data.get("lorebook"),
        )
        return preview.to_dict()

    @router.post("/api/modules/inspect")
    async def inspect_module_upload(request: Request):
        try:
            path = await _receive_module_upload(request, deps.runtime_root())
            inspection = await asyncio.to_thread(inspect_package, path)
            return {"ok": True, "module": inspection.summary()}
        except ModulePackageError as exc:
            return _module_error_response(exc)
        finally:
            if "path" in locals():
                path.unlink(missing_ok=True)

    @router.post("/api/modules/import")
    async def import_module_upload(request: Request):
        try:
            path = await _receive_module_upload(request, deps.runtime_root())
            record, inspection, already_installed = await asyncio.to_thread(
                deps.registry().install, path
            )
            return JSONResponse(
                {
                    "ok": True,
                    "already_installed": already_installed,
                    "module": record.to_dict(),
                    "inspection": inspection.summary(),
                },
                status_code=200 if already_installed else 201,
            )
        except ModulePackageError as exc:
            return _module_error_response(exc)
        finally:
            if "path" in locals():
                path.unlink(missing_ok=True)

    @router.post("/api/modules/switch")
    async def switch_module(data: dict):
        if deps.auth_required():
            return JSONResponse(
                {"detail": "账号模式下请创建对应模组的新世界"},
                status_code=409,
            )
        active_context = deps.active_context()
        name = data.get("module", active_context.module_name)
        try:
            deps.registry().resolve(name)
        except FileNotFoundError:
            return {"ok": False, "error": f"模组'{name}'不存在"}
        context = RuntimeContext.local(
            name,
            project_root=deps.project_root,
            runtime_root=deps.runtime_root(),
        )
        deps.set_active_context(context)
        return {"ok": True, "module": name, "world_id": context.world_id}

    @router.get("/api/assets/{module_name}/{filename:path}")
    async def serve_asset(module_name: str, filename: str, v: str | None = None):
        return serve_module_asset(
            deps.registry(),
            hosted=deps.auth_required(),
            module_name=module_name,
            filename=filename,
            versioned=v is not None,
        )

    return router
