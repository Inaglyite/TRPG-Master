"""角色库 HTTP 路由：列表/详情/新建/编辑/复制/删除/导入预览/导出。

本地与云端共用同一组端点（authentication_gate 已保证云端必须登录、
本地必须来源可信）：归属键 ``owner_id`` 云端取登录用户 id，本地为空串。
所有读写都按 owner 过滤，跨用户访问一律 404（不泄露存在性）。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import quote

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from src.auth.service import auth_required, request_user
from src.gameplay.character_library import (
    MAX_CARD_BYTES,
    CharacterLibraryError,
    create_entry,
    delete_entry,
    duplicate_entry,
    get_entry,
    get_export_entry,
    inspect_payload,
    list_entries,
    update_entry,
)


@dataclass(frozen=True)
class CharacterLibraryHttpDependencies:
    database_url: Callable[[], str]


def _error_response(exc: CharacterLibraryError) -> JSONResponse:
    return JSONResponse(
        {
            "ok": False,
            "error_code": exc.code,
            "error": exc.message,
            "details": exc.details,
        },
        status_code=exc.status,
    )


def _owner_id(request: Request, database_url: str) -> str:
    """云端 = 登录用户 id；本地 = 空串。云端未登录在 gate 已拦截，这里兜底。"""
    if not auth_required():
        return ""
    user = getattr(request.state, "user", None) or request_user(request, database_url)
    if user is None:
        raise CharacterLibraryError("unauthorized", "未登录或会话已过期", 401)
    return user.id


def _check_body_size(request: Request) -> None:
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_CARD_BYTES:
                raise CharacterLibraryError(
                    "card_too_large",
                    f"角色卡文件超过 {MAX_CARD_BYTES // 1024} KB 上限",
                    413,
                )
        except ValueError as exc:
            raise CharacterLibraryError("invalid_length", "Content-Length 无效") from exc


def _export_filename(name: str) -> str:
    safe = "".join(ch for ch in name if ch.isalnum() or ch in "_- ").strip()
    return f"character-{safe or 'card'}.json"


def create_character_library_router(
    deps: CharacterLibraryHttpDependencies,
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/character-library")
    async def list_library(request: Request):
        try:
            owner_id = _owner_id(request, deps.database_url())
            return {"ok": True, "entries": list_entries(deps.database_url(), owner_id)}
        except CharacterLibraryError as exc:
            return _error_response(exc)

    @router.post("/api/character-library/inspect")
    async def inspect_library_card(request: Request):
        try:
            _check_body_size(request)
            owner_id = _owner_id(request, deps.database_url())
            payload = await request.json()
            return inspect_payload(deps.database_url(), owner_id, payload)
        except CharacterLibraryError as exc:
            return _error_response(exc)
        except (json.JSONDecodeError, ValueError):
            return JSONResponse(
                {
                    "ok": False,
                    "errors": [{"field": "(root)", "message": "文件不是合法的 JSON"}],
                    "warnings": [],
                    "preview": None,
                },
                status_code=200,
            )

    @router.post("/api/character-library", status_code=201)
    async def create_library_entry(request: Request):
        try:
            _check_body_size(request)
            owner_id = _owner_id(request, deps.database_url())
            payload = await request.json()
            result = create_entry(deps.database_url(), owner_id, payload)
            return JSONResponse({"ok": True, **result}, status_code=201)
        except CharacterLibraryError as exc:
            return _error_response(exc)
        except (json.JSONDecodeError, ValueError):
            return _error_response(
                CharacterLibraryError("invalid_json", "文件不是合法的 JSON", 400)
            )

    @router.get("/api/character-library/{entry_id}")
    async def get_library_entry(entry_id: str, request: Request):
        try:
            owner_id = _owner_id(request, deps.database_url())
            return {"ok": True, **get_entry(deps.database_url(), owner_id, entry_id)}
        except CharacterLibraryError as exc:
            return _error_response(exc)

    @router.put("/api/character-library/{entry_id}")
    async def put_library_entry(entry_id: str, request: Request):
        try:
            _check_body_size(request)
            owner_id = _owner_id(request, deps.database_url())
            payload = await request.json()
            return {"ok": True, **update_entry(deps.database_url(), owner_id, entry_id, payload)}
        except CharacterLibraryError as exc:
            return _error_response(exc)
        except (json.JSONDecodeError, ValueError):
            return _error_response(
                CharacterLibraryError("invalid_json", "请求体不是合法的 JSON", 400)
            )

    @router.post("/api/character-library/{entry_id}/duplicate", status_code=201)
    async def duplicate_library_entry(entry_id: str, request: Request):
        try:
            owner_id = _owner_id(request, deps.database_url())
            result = duplicate_entry(deps.database_url(), owner_id, entry_id)
            return JSONResponse({"ok": True, **result}, status_code=201)
        except CharacterLibraryError as exc:
            return _error_response(exc)

    @router.delete("/api/character-library/{entry_id}", status_code=204)
    async def delete_library_entry(entry_id: str, request: Request):
        try:
            owner_id = _owner_id(request, deps.database_url())
            delete_entry(deps.database_url(), owner_id, entry_id)
            return Response(status_code=204)
        except CharacterLibraryError as exc:
            return _error_response(exc)

    @router.get("/api/character-library/{entry_id}/export")
    async def export_library_entry(entry_id: str, request: Request):
        try:
            owner_id = _owner_id(request, deps.database_url())
            envelope = get_export_entry(deps.database_url(), owner_id, entry_id)
            name = str((envelope.get("card") or {}).get("name") or "card")
            filename = _export_filename(name)
            return JSONResponse(
                envelope,
                headers={
                    "Content-Disposition": (
                        f"attachment; filename=\"character.json\"; "
                        f"filename*=UTF-8''{quote(filename)}"
                    )
                },
            )
        except CharacterLibraryError as exc:
            return _error_response(exc)

    return router
