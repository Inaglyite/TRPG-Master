"""Password authentication, revocable server sessions, and world authorization."""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import HTTPException, Request, WebSocket, status

from src.storage.database import (
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
    _SESSION_COOKIE if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", _SESSION_COOKIE) else "trpg_session"
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


# ---------------------------------------------------------------- 本地连接信任
#
# 本地模式（非账号鉴权）下服务只监听回环地址，但浏览器里的任意网页同样能
# 访问 127.0.0.1：没有来源校验时，恶意站点可以读配置、改目的地或触发带 Key
# 的模型调用（安全初审 S03）。这里把"受控来源"与"每次启动的连接凭证"一起
# 校验：
# - 桌面壳由 Electron 主进程在启动后端时注入一次性凭证，并在主进程的
#   webRequest 层把它加到发往本地后端的请求头（渲染进程拿不到，URL/日志/
#   前端存储里都没有）；页面无法为 WebSocket 或跨域请求自定义请求头。
# - 浏览器本地页面（含编辑器 dev）用 Origin 校验：只接受显式白名单与
#   环回来源；浏览器页面无法伪造 Origin。
# - 无 Origin 且无 Sec-Fetch-* 的调用方不是浏览器页面（curl/脚本/测试），
#   它们本就在本地信任边界内。
LOCAL_TOKEN_HEADER = "x-trpg-local-token"
LOCAL_TOKEN_ENV = "TRPG_LOCAL_LAUNCH_TOKEN"
LOCAL_TOKEN_FILENAME = "local_launch_token"
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
_local_token: str | None = None
_local_token_lock = threading.Lock()


def local_token_path():
    """本地凭证落盘位置（仅本地模式；桌面壳用它接管已在运行的后端）。"""
    from pathlib import Path

    from src.app.config import RUNTIME_ROOT

    root = os.environ.get("TRPG_RUNTIME_ROOT", "").strip()
    base = Path(root).resolve() if root else Path(RUNTIME_ROOT)
    return base / LOCAL_TOKEN_FILENAME


def local_launch_token() -> str:
    """本进程的本地连接凭证：优先启动注入，否则每次启动随机生成。

    非桌面场景（浏览器本地模式）不依赖它，生成后仅用于同进程校验；
    桌面壳注入时不落盘（避免多一份副本）。
    """
    global _local_token
    if _local_token is not None:
        return _local_token
    with _local_token_lock:
        if _local_token is None:
            injected = os.environ.get(LOCAL_TOKEN_ENV, "").strip()
            if injected:
                _local_token = injected
            else:
                _local_token = secrets.token_urlsafe(32)
                if not auth_required():
                    _persist_local_token(_local_token)
    return _local_token


def _persist_local_token(token: str) -> None:
    """把本次启动的凭证写入 0600 文件，供桌面壳接管已在运行的后端。"""
    path = local_token_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(token + "\n")
    except OSError:
        # 落盘失败不影响校验（同进程仍可用注入或随机凭证）。
        pass


def reset_local_token_for_tests() -> None:
    global _local_token
    with _local_token_lock:
        _local_token = None


def _configured_origins() -> set[str]:
    return {
        item.strip()
        for item in os.environ.get("TRPG_ALLOWED_ORIGINS", "").split(",")
        if item.strip()
    }


def _is_loopback_origin(origin: str) -> bool:
    from urllib.parse import urlsplit

    parts = urlsplit(origin)
    if parts.scheme not in {"http", "https"}:
        return False
    host = (parts.hostname or "").strip().lower()
    return host in _LOOPBACK_HOSTS


def local_request_trusted(headers) -> bool:
    """本地模式的来源/凭证联合校验；任一不满足即拒绝。"""
    token = str(headers.get(LOCAL_TOKEN_HEADER) or "")
    if token and secrets.compare_digest(token, local_launch_token()):
        return True
    origin = str(headers.get("origin") or "").strip()
    if origin:
        if origin in _configured_origins():
            return True
        return _is_loopback_origin(origin)
    # 无 Origin：同源导航由浏览器补 Sec-Fetch-Site；完全没有该头的不是浏览器页面。
    fetch_site = str(headers.get("sec-fetch-site") or "").strip().lower()
    if fetch_site:
        return fetch_site in {"same-origin", "none"}
    return True


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

    def reset(self) -> None:
        """Clear process-local counters (primarily for isolated test cases)."""
        with self._lock:
            self._attempts.clear()


LOGIN_LIMITER = LoginRateLimiter()
_REVOKED_SESSION_HASHES: dict[str, float] = {}
_REVOKED_SESSION_LOCK = threading.Lock()


@dataclass(frozen=True)
class AuthenticatedSession:
    """A verified login plus the immutable data needed by a live WebSocket."""

    user: User
    token_hash: str
    expires_at: datetime

    def locally_valid(self) -> bool:
        expires_at = self.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at <= utcnow():
            return False
        with _REVOKED_SESSION_LOCK:
            return self.token_hash not in _REVOKED_SESSION_HASHES


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


def resolve_session_identity(
    db_url: str,
    token: str | None,
) -> AuthenticatedSession | None:
    if not token:
        return None
    now = utcnow()
    digest = token_hash(token)
    with session_scope(db_url) as session:
        login = (
            session.query(LoginSession).filter_by(token_hash=digest, revoked_at=None).one_or_none()
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
        return AuthenticatedSession(
            user=user,
            token_hash=digest,
            expires_at=expires_at,
        )


def resolve_session(db_url: str, token: str | None) -> User | None:
    identity = resolve_session_identity(db_url, token)
    return identity.user if identity is not None else None


def revoke_session(db_url: str, token: str | None) -> None:
    if not token:
        return
    digest = token_hash(token)
    with session_scope(db_url) as session:
        login = session.query(LoginSession).filter_by(token_hash=digest).one_or_none()
        if login and login.revoked_at is None:
            login.revoked_at = utcnow()
    # Existing WebSockets cannot consult the database for every outbound chunk.
    # Keep an in-process revocation fence so a revoked session immediately stops
    # receiving broadcasts even before its socket close finishes.
    with _REVOKED_SESSION_LOCK:
        _REVOKED_SESSION_HASHES[digest] = time.time()
        if len(_REVOKED_SESSION_HASHES) > 100_000:
            oldest = next(iter(_REVOKED_SESSION_HASHES))
            _REVOKED_SESSION_HASHES.pop(oldest, None)


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


def websocket_session(
    websocket: WebSocket,
    db_url: str,
) -> AuthenticatedSession | None:
    return resolve_session_identity(db_url, websocket.cookies.get(SESSION_COOKIE))


def validate_websocket_origin(websocket: WebSocket) -> None:
    """云端严格白名单；本地模式走来源+启动凭证联合校验（S03）。"""
    allowed = _configured_origins()
    origin = websocket.headers.get("origin")
    if auth_required():
        if not allowed:
            raise HTTPException(503, "服务端尚未配置 TRPG_ALLOWED_ORIGINS")
        if origin not in allowed:
            raise HTTPException(403, "WebSocket Origin 不受信任")
        return
    if not local_request_trusted(websocket.headers):
        raise HTTPException(403, "本地 WebSocket 来源或连接凭证不受信任")
