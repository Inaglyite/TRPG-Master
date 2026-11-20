"""Authenticated profile and password management endpoints."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from src.ai.model.crypto_box import ConfigKeyError
from src.app import settings_service
from src.auth.service import (
    SESSION_COOKIE,
    audit,
    change_password,
    token_hash,
    update_display_name,
)


def user_payload(user) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "status": user.status,
    }


def create_account_router(
    database_url: Callable[[], str],
    disconnect_session: Callable[[str], Awaitable[int]] | None,
) -> APIRouter:
    router = APIRouter()

    def account_settings(request: Request, operation: Callable, data: dict):
        user = getattr(request.state, "user", None)
        if user is None:
            raise HTTPException(401, "请先登录")
        scope = settings_service.SettingsScope(
            mode="account", can_edit=True, user_id=user.id, owner_user_id=user.id,
            db_url=database_url(),
        )
        try:
            payload = operation(scope, data)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except (ConfigKeyError, OSError) as exc:
            raise HTTPException(503, "模型配置暂时不可用，请稍后重试") from exc
        return JSONResponse(payload, headers={"Cache-Control": "no-store"})

    @router.get("/api/account/model-settings")
    def get_model_settings(request: Request):
        return account_settings(request, settings_service.build_payload, {})

    @router.put("/api/account/model-settings")
    def save_model_settings(request: Request, data: dict):
        return account_settings(request, settings_service.apply_update, data)

    @router.post("/api/account/model-settings/restore")
    def restore_model_settings(request: Request, data: dict):
        return account_settings(request, settings_service.restore_default, data)

    @router.post("/api/account/model-settings/test")
    def test_model_settings(request: Request, data: dict):
        return account_settings(request, settings_service.run_test_connection, data)

    @router.patch("/api/auth/profile")
    def update_profile(data: dict, request: Request):
        user = getattr(request.state, "user", None)
        if user is None:
            return JSONResponse({"detail": "未登录或会话已过期"}, status_code=401)
        updated = update_display_name(database_url(), user.id, data.get("display_name"))
        audit(database_url(), "profile_update", user_id=user.id)
        return user_payload(updated)

    @router.post("/api/auth/password", status_code=204)
    async def update_password(data: dict, request: Request):
        user = getattr(request.state, "user", None)
        if user is None:
            return JSONResponse({"detail": "未登录或会话已过期"}, status_code=401)
        token = request.cookies.get(SESSION_COOKIE)
        revoked = change_password(
            database_url(),
            user.id,
            data.get("current_password"),
            data.get("new_password"),
            current_token_hash=token_hash(token) if token else "",
        )
        if disconnect_session is not None:
            for session_hash in revoked:
                await disconnect_session(session_hash)
        audit(database_url(), "password_change", user_id=user.id)

    return router
