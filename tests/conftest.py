from __future__ import annotations

import os
import tempfile
from pathlib import Path

# This module is imported by pytest before test modules import ``src.app.config``
# or ``server``.  Keep the default writable runtime away from the checkout:
# many WebSocket tests intentionally use ``RuntimeContext.local()`` and a
# plain ``TestClient(server.app)``.  Without these two early settings they
# inherit the developer's project-root SQLite database and ``worlds/`` tree.
#
# Individual tests can still patch either variable (or ``server.DATABASE_URL``)
# for an explicit temporary database.  The session default merely makes the
# implicit path safe, including when a developer happens to have a production
# database URL exported in their shell.
TEST_RUNTIME_ROOT = Path(tempfile.mkdtemp(prefix="trpg-pytest-runtime-")).resolve()
os.environ["TRPG_RUNTIME_ROOT"] = str(TEST_RUNTIME_ROOT)
# ``database_url(runtime_root)`` deliberately gives an explicit database URL
# precedence over a runtime root.  Do not pin one here: plenty of tests build
# several isolated RuntimeContexts with their own temporary roots.  A global
# test URL would make those contexts (which often reuse a world id) share one
# database and leak state across test cases.  Clearing an inherited URL still
# protects a developer's local/production database; the forced runtime root
# below is then used to derive the safe default database path.
os.environ.pop("TRPG_DATABASE_URL", None)

import pytest

from src.auth.service import LOGIN_LIMITER


@pytest.fixture(autouse=True)
def _reset_process_local_login_limiter():
    """A test's synthetic client IP must not rate-limit unrelated test cases."""
    LOGIN_LIMITER.reset()
    yield
    LOGIN_LIMITER.reset()


@pytest.fixture(scope="session")
def isolated_test_runtime_root() -> Path:
    """Expose the forced default runtime root to isolation regression tests."""
    return TEST_RUNTIME_ROOT
