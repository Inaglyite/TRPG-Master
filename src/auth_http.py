"""HTTP account/session endpoints for local and hosted deployments."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from .auth import (
    LOGIN_LIMITER,
    SESSION_COOKIE,
    audit,
    auth_required,
    authenticate,
    create_login_session,
    create_user,
    request_user,
    revoke_session,
)


@dataclass(frozen=True)
class AuthHttpDependencies:
    database_url: Callable[[], str]


def create_auth_router(deps: AuthHttpDependencies) -> APIRouter:
    router = APIRouter()

    def db_url() -> str:
        return deps.database_url()

    @router.post("/api/auth/register", status_code=201)
    async def register(data: dict, request: Request, response: Response):
        if os.environ.get("TRPG_ALLOW_REGISTRATION", "1").lower() not in {
            "1",
            "true",
            "yes",
            "on",
        }:
            return JSONResponse({"detail": "注册已关闭"}, status_code=403)
        ip = request.client.host if request.client else ""
        LOGIN_LIMITER.check(f"register:{ip}")
        user = create_user(db_url(), data.get("username"), data.get("password"))
        token = create_login_session(
            db_url(),
            user,
            user_agent=request.headers.get("user-agent", ""),
            ip_address=ip,
        )
        response.set_cookie(
            SESSION_COOKIE,
            token,
            httponly=True,
            secure=auth_required(),
            samesite="lax",
            max_age=30 * 86400,
            path="/",
        )
        audit(db_url(), "register", user_id=user.id, ip_address=ip)
        return {"id": user.id, "username": user.username}

    @router.post("/api/auth/login")
    async def login(data: dict, request: Request, response: Response):
        ip = request.client.host if request.client else ""
        key = f"login:{ip}:{str(data.get('username') or '').lower()}"
        LOGIN_LIMITER.check(key)
        user = authenticate(db_url(), data.get("username"), data.get("password"))
        if user is None:
            audit(db_url(), "login", success=False, ip_address=ip)
            return JSONResponse({"detail": "用户名或密码错误"}, status_code=401)
        LOGIN_LIMITER.clear(key)
        token = create_login_session(
            db_url(),
            user,
            user_agent=request.headers.get("user-agent", ""),
            ip_address=ip,
        )
        response.set_cookie(
            SESSION_COOKIE,
            token,
            httponly=True,
            secure=auth_required(),
            samesite="lax",
            max_age=30 * 86400,
            path="/",
        )
        audit(db_url(), "login", user_id=user.id, ip_address=ip)
        return {"id": user.id, "username": user.username}

    @router.post("/api/auth/logout", status_code=204)
    async def logout(request: Request, response: Response):
        user = request_user(request, db_url())
        revoke_session(db_url(), request.cookies.get(SESSION_COOKIE))
        response.delete_cookie(SESSION_COOKIE, path="/")
        if user:
            audit(db_url(), "logout", user_id=user.id)

    @router.get("/api/auth/me")
    async def current_user(request: Request):
        user = request_user(request, db_url())
        if user is None:
            return JSONResponse({"detail": "未登录"}, status_code=401)
        return {"id": user.id, "username": user.username, "status": user.status}

    return router
