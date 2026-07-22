"""Password authentication, revocable server sessions, and world authorization."""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import UTC, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import HTTPException, Request, WebSocket, status

from .database import (
    AuditEvent,
    LoginSession,
    User,
    WorldMember,
    new_id,
    session_scope,
    utcnow,
)

_SESSION_COOKIE = os.environ.get("TRPG_SESSION_COOKIE", "trpg_session").strip()
SESSION_COOKIE = (
    _SESSION_COOKIE
    if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", _SESSION_COOKIE)
    else "trpg_session"
)
SESSION_DAYS = 30
USERNAME = re.compile(r"^[A-Za-z0-9_\-\u4e00-\u9fff]{3,40}$")
PASSWORD_HASHER = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)
ROLE_PERMISSIONS = {
    "owner": frozenset({"read", "play", "manage"}),
    "gm": frozenset({"read", "play", "manage"}),
    "player": frozenset({"read", "play"}),
    "viewer": frozenset({"read"}),
}


def auth_required() -> bool:
    return os.environ.get("TRPG_REQUIRE_AUTH", "0").lower() in {"1", "true", "yes", "on"}


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class LoginRateLimiter:
    def __init__(self, limit: int = 8, window_seconds: int = 300):
        self.limit = limit
        self.window = window_seconds
        self._attempts: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            attempts = self._attempts[key]
            while attempts and attempts[0] < now - self.window:
                attempts.popleft()
            if len(attempts) >= self.limit:
                raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "尝试次数过多，请稍后再试")
            attempts.append(now)

    def clear(self, key: str) -> None:
        with self._lock:
            self._attempts.pop(key, None)


LOGIN_LIMITER = LoginRateLimiter()


def normalize_username(value: object) -> str:
    username = str(value or "").strip().lower()
    if not USERNAME.fullmatch(username):
        raise HTTPException(400, "用户名须为 3-40 位汉字、字母、数字、下划线或连字符")
    return username


def validate_password(value: object) -> str:
    password = str(value or "")
    if len(password) < 10 or len(password) > 256:
        raise HTTPException(400, "密码长度须为 10-256 个字符")
    return password


def audit(
    db_url: str,
    event_type: str,
    *,
    user_id=None,
    world_id=None,
    success=True,
    ip_address="",
    details=None,
) -> None:
    with session_scope(db_url) as session:
        session.add(
            AuditEvent(
                id=new_id("audit"),
                user_id=user_id,
                event_type=event_type,
                world_id=world_id,
                success=success,
                ip_address=ip_address,
                details=details or {},
            )
        )


def create_user(db_url: str, username: object, password: object) -> User:
    normalized = normalize_username(username)
    valid_password = validate_password(password)
    with session_scope(db_url) as session:
        if session.query(User).filter_by(username=normalized).first():
            raise HTTPException(409, "用户名已存在")
        user = User(
            id=new_id("user"),
            username=normalized,
            password_hash=PASSWORD_HASHER.hash(valid_password),
        )
        session.add(user)
        session.flush()
        return user


def authenticate(db_url: str, username: object, password: object) -> User | None:
    normalized = normalize_username(username)
    with session_scope(db_url) as session:
        user = session.query(User).filter_by(username=normalized, status="active").one_or_none()
        if user is None:
            return None
        try:
            PASSWORD_HASHER.verify(user.password_hash, str(password or ""))
        except (VerifyMismatchError, InvalidHashError):
            return None
        if PASSWORD_HASHER.check_needs_rehash(user.password_hash):
            user.password_hash = PASSWORD_HASHER.hash(str(password))
            user.updated_at = utcnow()
        return user


def create_login_session(db_url: str, user: User, *, user_agent="", ip_address="") -> str:
    token = secrets.token_urlsafe(48)
    with session_scope(db_url) as session:
        session.add(
            LoginSession(
                id=new_id("session"),
                user_id=user.id,
                token_hash=token_hash(token),
                expires_at=utcnow() + timedelta(days=SESSION_DAYS),
                user_agent=str(user_agent)[:512],
                ip_address=str(ip_address)[:64],
            )
        )
    return token


def resolve_session(db_url: str, token: str | None) -> User | None:
    if not token:
        return None
    now = utcnow()
    with session_scope(db_url) as session:
        login = (
            session.query(LoginSession)
            .filter_by(token_hash=token_hash(token), revoked_at=None)
            .one_or_none()
        )
        expires_at = login.expires_at if login is not None else None
        if expires_at is not None and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if login is None or expires_at is None or expires_at <= now:
            return None
        user = session.get(User, login.user_id)
        if user is None or user.status != "active":
            return None
        login.last_seen_at = now
        return user


def revoke_session(db_url: str, token: str | None) -> None:
    if not token:
        return
    with session_scope(db_url) as session:
        login = session.query(LoginSession).filter_by(token_hash=token_hash(token)).one_or_none()
        if login and login.revoked_at is None:
            login.revoked_at = utcnow()


def authorize_world(db_url: str, user_id: str, world_id: str, permission: str) -> str:
    with session_scope(db_url) as session:
        member = (
            session.query(WorldMember).filter_by(world_id=world_id, user_id=user_id).one_or_none()
        )
        if member is None or permission not in ROLE_PERMISSIONS.get(member.role, frozenset()):
            raise HTTPException(403, "无权访问该世界")
        return member.role


def request_user(request: Request, db_url: str) -> User | None:
    return resolve_session(db_url, request.cookies.get(SESSION_COOKIE))


def websocket_user(websocket: WebSocket, db_url: str) -> User | None:
    return resolve_session(db_url, websocket.cookies.get(SESSION_COOKIE))


def validate_websocket_origin(websocket: WebSocket) -> None:
    allowed = {
        x.strip() for x in os.environ.get("TRPG_ALLOWED_ORIGINS", "").split(",") if x.strip()
    }
    origin = websocket.headers.get("origin")
    if auth_required() and not allowed:
        raise HTTPException(503, "服务端尚未配置 TRPG_ALLOWED_ORIGINS")
    if allowed and origin not in allowed:
        raise HTTPException(403, "WebSocket Origin 不受信任")
