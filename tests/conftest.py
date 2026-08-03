from __future__ import annotations

import pytest

from src.auth import LOGIN_LIMITER


@pytest.fixture(autouse=True)
def _reset_process_local_login_limiter():
    """A test's synthetic client IP must not rate-limit unrelated test cases."""
    LOGIN_LIMITER.reset()
    yield
    LOGIN_LIMITER.reset()
