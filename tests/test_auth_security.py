"""Regression coverage for account ban semantics and the login rate limiter.

These are application-layer checks: a non-active user must be unable to log
in and every previously issued session token must be rejected on its next
database-backed resolution.  The process-local ``LoginRateLimiter`` must also
enforce its window/threshold/clear contract. Nginx-level rate limiting cannot
substitute for these.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from src.auth import (
    LoginRateLimiter,
    authenticate,
    create_login_session,
    create_user,
    resolve_session,
    resolve_session_identity,
)
from src.database import (
    Base,
    User,
    get_engine,
    session_scope,
)


def sqlite_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'auth-security.db'}"


def test_inactive_user_cannot_login_and_existing_session_is_rejected(
    tmp_path: Path,
) -> None:
    url = sqlite_url(tmp_path)
    Base.metadata.create_all(get_engine(url))
    user = create_user(url, "banned_user", "a sufficiently long password")
    token = create_login_session(url, user)
    assert resolve_session_identity(url, token) is not None
    assert authenticate(url, "banned_user", "a sufficiently long password") is not None

    with session_scope(url) as session:
        stored = session.get(User, user.id)
        assert stored is not None
        stored.status = "inactive"

    # 封禁账号：既不能重新登录，已发 Session token 下次查库解析时也被拒绝。
    assert authenticate(url, "banned_user", "a sufficiently long password") is None
    assert resolve_session_identity(url, token) is None
    assert resolve_session(url, token) is None


def test_login_rate_limiter_window_threshold_and_clear() -> None:
    limiter = LoginRateLimiter(limit=3, window_seconds=300)
    for _ in range(3):
        limiter.check("alice")
    with pytest.raises(HTTPException) as exc:
        limiter.check("alice")
    assert exc.value.status_code == 429

    # 成功登录后清理计数，同一用户可继续尝试；其他用户不受影响。
    limiter.clear("alice")
    limiter.check("alice")
    limiter.check("bob")


def test_login_rate_limiter_expires_attempts_after_window() -> None:
    limiter = LoginRateLimiter(limit=2, window_seconds=60)
    limiter.check("carol")
    limiter.check("carol")
    with pytest.raises(HTTPException):
        limiter.check("carol")

    # 把最早一次尝试时间戳推到窗口之外，重新获得容量。
    with limiter._lock:
        attempts = limiter._attempts["carol"]
        earliest = attempts.popleft()
        attempts.appendleft(earliest - 61.0)
    limiter.check("carol")
