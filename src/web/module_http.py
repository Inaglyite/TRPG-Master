"""HTTP endpoints for module packages, schemas, switching, and public assets."""

from __future__ import annotations

import asyncio
import json
import mimetypes
import tempfile
from collections.abc import Awaitable, Callable
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from fastapi import APIRouter, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.exc import IntegrityError

from src.ai.context.lorebook import lorebook_json_schema
from src.app.runtime import RuntimeContext
from src.modules.editor_projects import editor_skill_json_schema
from src.modules.module_compiler import compile_payload
from src.modules.module_deletion import (
    account_module_usage,
    delete_account_module,
    delete_module,
    module_usage,
)
from src.modules.module_format import (
    manifest_json_schema,
    manifest_v2_json_schema,
    module_json_schema,
    module_v2_json_schema,
)
from src.modules.module_registry import (
    MAX_PACKAGE_BYTES,
    ModulePackageError,
    ModuleRecord,
    ModuleRegistry,
    inspect_package,
)
from src.storage.database import User, session_scope, utcnow


@contextmanager
def track_module_session(engine, sessions: set):
    """Keep module removal blocked until a local websocket session has ended."""
    sessions.add(engine)
    try:
        yield
    finally:
        sessions.discard(engine)


@dataclass(frozen=True)
class ModuleHttpDependencies:
    registry: Callable[[], ModuleRegistry]
    project_root: Path
    runtime_root: Callable[[], Path]
    active_context: Callable[[], RuntimeContext]
    set_active_context: Callable[[RuntimeContext], None]
    auth_required: Callable[[], bool]
    modules_in_use: Callable[[], set[str]] = lambda: set()
    database_url: Callable[[], str] | None = None
    prepare_module_removal: Callable[[str], Awaitable[None]] | None = None


def _module_error_response(exc: ModulePackageError) -> JSONResponse:
    status = {
        "version_conflict": 409,
        "multiplayer_world_in_use": 409,
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


def _menu_theme(module_dir: Path) -> dict:
    try:
        theme = json.loads((module_dir / "theme.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return theme if isinstance(theme, dict) else {}


def _menu_cover(module_dir: Path, theme: dict) -> Path | None:
    name = theme.get("backgroundImage")
    if not isinstance(name, str) or not 0 < len(name) <= 200:
        return None
    relative = Path(name)
    if relative.is_absolute() or ".." in relative.parts or "\x00" in name:
        return None
    assets = module_dir / "assets"
    try:
        cover = (assets / relative).resolve()
        if (
            cover.is_relative_to(assets)
            and cover.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".avif", ".gif"}
            and cover.is_file()
        ):
            return cover
    except (OSError, RuntimeError):
        pass
    return None


def _menu_entry(record: ModuleRecord) -> dict:
    theme = _menu_theme(record.path)
    entry = record.to_dict()
    for key, field, length in (
        ("title", "menu_title", 120),
        ("subtitle", "subtitle", 200),
        ("description", "menu_description", 600),
        ("startButtonText", "start_button_text", 40),
    ):
        value = theme.get(key)
        if isinstance(value, str) and value:
            entry[field] = value[:length]
    for field, allowed, limit in (
        (
            "colors",
            (
                "bg",
                "bg2",
                "bg3",
                "bg4",
                "text",
                "textDim",
                "textFaint",
                "gold",
                "goldDim",
                "goldBright",
                "red",
                "redDim",
                "redBright",
                "crimson",
                "crimsonBright",
                "green",
                "blue",
                "blueBright",
                "purple",
                "ink",
                "border",
                "borderBright",
            ),
            80,
        ),
        ("fonts", ("body", "mono", "heading"), 240),
    ):
        values = theme.get(field)
        if isinstance(values, dict):
            entry[field] = {
                k: v for k in allowed if isinstance(v := values.get(k), str) and len(v) <= limit
            }
    return {**entry, "has_cover": _menu_cover(record.path, theme) is not None}


def _apply_module_order(records: list[ModuleRecord], order: object) -> list[ModuleRecord]:
    if not isinstance(order, list):
        return records
    by_key = {record.key: record for record in records}
    ordered: list[ModuleRecord] = []
    seen: set[str] = set()
    for key in order:
        if isinstance(key, str) and key in by_key and key not in seen:
            ordered.append(by_key[key])
            seen.add(key)
    ordered.extend(record for record in records if record.key not in seen)
    return ordered


def create_module_http_router(deps: ModuleHttpDependencies) -> APIRouter:
    router = APIRouter()

    @router.get("/api/modules")
    async def list_modules(request: Request, response: Response):
        context = deps.active_context()
        registry = deps.registry()
        records = registry.list_modules()
        user = getattr(request.state, "user", None)
        if user is not None:
            records.extend(registry.account_modules(user.id))
            records = _apply_module_order(records, user.module_order)
            response.headers.update({"Cache-Control": "private, no-store", "Vary": "Cookie"})
        entries = []
        for record in records:
            entry = _menu_entry(record)
            if record.source == "account" and deps.database_url is not None:
                entry.update(account_module_usage(deps.database_url(), record.key))
            entries.append(entry)
        return {
            "modules": entries,
            "active": context.module_name,
        }

    @router.get("/api/modules/{module_key}/cover")
    async def module_cover(module_key: str, request: Request):
        user = getattr(request.state, "user", None)
        registry = deps.registry()
        try:
            record = (registry.resolve_for_account(module_key, user.id)
                      if user is not None else registry.resolve(module_key))
        except FileNotFoundError:
            return JSONResponse({"error": "module not found"}, status_code=404)
        cover = _menu_cover(record.path, _menu_theme(record.path))
        if cover is None:
            return JSONResponse({"error": "cover not found"}, status_code=404)
        return FileResponse(
            cover,
            headers={"Cache-Control": "private, no-cache", "X-Content-Type-Options": "nosniff"},
        )

    def removal_block(module_key: str) -> str:
        if module_key == deps.active_context().module_name or module_key in deps.modules_in_use():
            return "此模组正在使用，请先在开始页切换到其他模组，并关闭仍使用它的其他窗口。"
        return ""

    @router.get("/api/modules/manage")
    async def manage_modules():
        if deps.auth_required():
            return JSONResponse({"error": "模组管理仅适用于本地模式"}, status_code=403)
        registry = deps.registry()
        return {"modules": [
            {**_menu_entry(record), **module_usage(registry, record.key),
             "delete_block": "内置模组不能删除" if record.source != "user" else removal_block(record.key)}
            for record in registry.list_modules()
        ]}

    @router.delete("/api/modules/{module_key}")
    async def remove_module(module_key: str):
        if deps.auth_required():
            return JSONResponse({"error": "模组管理仅适用于本地模式"}, status_code=403)
        if reason := removal_block(module_key):
            return JSONResponse({"error": reason}, status_code=409)
        try:
            # No await between checking live sessions and removing their data. Local
            # websocket switches run on this same event loop; active engines are blocked.
            removed = delete_module(deps.registry(), module_key)
            return {"ok": True, **removed}
        except FileNotFoundError:
            return JSONResponse({"error": "模组已不存在，请刷新列表"}, status_code=404)
        except ModulePackageError as exc:
            return _module_error_response(exc)
        except IntegrityError:
            return JSONResponse({"error": "存档仍被其他世界引用，删除已撤销"}, status_code=409)
        except OSError:
            return JSONResponse({"error": "模组文件无法删除，请检查文件权限或占用情况"}, status_code=500)

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

    @router.get("/api/modules/schema/editor-skill-v1")
    async def get_editor_skill_schema():
        # TRPG Mod Editor 的 Skill 草稿表单按此 schema 锁定（H3.1 作者契约）。
        return editor_skill_json_schema()

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

    @router.put("/api/me/modules/order")
    async def reorder_account_modules(data: dict, request: Request):
        user = getattr(request.state, "user", None)
        if user is None:
            return JSONResponse({"detail": "请先登录"}, status_code=401)
        registry = deps.registry()
        records = registry.list_modules() + registry.account_modules(user.id)
        module_ids = data.get("module_ids")
        expected = {record.key for record in records}
        if (
            not isinstance(module_ids, list)
            or not all(isinstance(key, str) for key in module_ids)
            or len(module_ids) != len(records)
            or set(module_ids) != expected
        ):
            return JSONResponse(
                {"detail": "模组顺序与当前列表不一致，请刷新后重试"}, status_code=400
            )
        if deps.database_url is None:
            return JSONResponse({"detail": "模组排序存储未配置"}, status_code=503)
        with session_scope(deps.database_url()) as session:
            stored = session.get(User, user.id)
            if stored is None or stored.status != "active":
                return JSONResponse({"detail": "未登录或会话已过期"}, status_code=401)
            stored.module_order = list(module_ids)
            stored.updated_at = utcnow()
        return {"ok": True}

    @router.post("/api/me/modules/inspect")
    async def inspect_account_upload(request: Request):
        if getattr(request.state, "user", None) is None:
            return JSONResponse({"detail": "请先登录"}, status_code=401)
        return await inspect_module_upload(request)

    @router.post("/api/me/modules/import")
    async def import_account_upload(request: Request):
        user = getattr(request.state, "user", None)
        if user is None:
            return JSONResponse({"detail": "请先登录"}, status_code=401)
        try:
            path = await _receive_module_upload(request, deps.runtime_root())
            try:
                record, inspection, existing = await asyncio.to_thread(
                    deps.registry().install_for_account, user.id, path
                )
                return JSONResponse({"ok": True, "already_installed": existing,
                                     "module": _menu_entry(record), "inspection": inspection.summary()},
                                    status_code=200 if existing else 201)
            finally:
                path.unlink(missing_ok=True)
        except ModulePackageError as exc:
            return _module_error_response(exc)

    @router.delete("/api/me/modules/{module_key}")
    async def remove_account_upload(module_key: str, request: Request):
        user = getattr(request.state, "user", None)
        if user is None:
            return JSONResponse({"detail": "请先登录"}, status_code=401)
        try:
            record = await asyncio.to_thread(deps.registry().resolve_for_account, module_key, user.id)
            if record.source != "account":
                raise FileNotFoundError(module_key)
        except FileNotFoundError:
            return JSONResponse({"detail": "个人模组不存在"}, status_code=404)
        if deps.prepare_module_removal is not None:
            await deps.prepare_module_removal(module_key)
        if reason := removal_block(module_key):
            return JSONResponse({"error": reason}, status_code=409)
        if deps.database_url is None:
            return JSONResponse({"detail": "模组删除存储未配置"}, status_code=503)
        try:
            removed = await asyncio.to_thread(
                delete_account_module, deps.registry(), module_key, user.id, deps.database_url()
            )
            return {"ok": True, **removed}
        except FileNotFoundError:
            return JSONResponse({"detail": "个人模组不存在"}, status_code=404)
        except ModulePackageError as exc:
            return _module_error_response(exc)
        except IntegrityError:
            return JSONResponse({"error": "存档仍被其他世界引用，删除已撤销"}, status_code=409)
        except OSError:
            return JSONResponse({"error": "模组文件无法删除，请检查文件权限或占用情况"}, status_code=500)

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
