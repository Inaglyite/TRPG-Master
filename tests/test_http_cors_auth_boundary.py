"""Browser-readable authentication failures without weakening auth/CSRF gates."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.storage.database import Base, get_engine

FRONTEND_ORIGIN = "https://127.0.0.1:8788"
BACKEND_ORIGIN = "https://127.0.0.1:8789"


@pytest.fixture
def cloud_client(monkeypatch, tmp_path):
    import server

    url = f"sqlite:///{tmp_path / 'cors-auth.db'}"
    Base.metadata.create_all(get_engine(url))
    monkeypatch.setenv("TRPG_REQUIRE_AUTH", "1")
    monkeypatch.setenv("TRPG_ALLOW_REGISTRATION", "1")
    monkeypatch.setenv("TRPG_ALLOWED_ORIGINS", FRONTEND_ORIGIN)
    monkeypatch.setattr(server, "DATABASE_URL", url)
    with TestClient(server.app, base_url=BACKEND_ORIGIN) as client:
        yield client


@pytest.mark.parametrize("path", ["/api/auth/me", "/api/worlds", "/api/modules"])
def test_anonymous_401_is_readable_from_trusted_browser_origin(cloud_client, path):
    response = cloud_client.get(path, headers={"Origin": FRONTEND_ORIGIN})
    assert response.status_code == 401
    assert response.json() == {"detail": "未登录或会话已过期"}
    assert response.headers.get("access-control-allow-origin") == FRONTEND_ORIGIN
    assert response.headers.get("access-control-allow-credentials") == "true"
    assert "origin" in response.headers.get("vary", "").lower()


def test_untrusted_origin_never_receives_cors_permission(cloud_client):
    response = cloud_client.get("/api/auth/me", headers={"Origin": "https://untrusted.example"})
    assert response.status_code == 401
    assert "access-control-allow-origin" not in response.headers


def test_valid_preflight_does_not_require_a_session_or_authorize_the_mutation(cloud_client):
    response = cloud_client.options(
        "/api/worlds",
        headers={
            "Origin": FRONTEND_ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == FRONTEND_ORIGIN
    refused = cloud_client.post("/api/worlds", json={}, headers={"Origin": FRONTEND_ORIGIN})
    assert refused.status_code == 401


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "https://untrusted.example", "Access-Control-Request-Method": "POST"},
        {"Origin": FRONTEND_ORIGIN, "Access-Control-Request-Method": "TRACE"},
        {
            "Origin": FRONTEND_ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "x-unapproved-header",
        },
    ],
)
def test_untrusted_preflights_stay_rejected(cloud_client, headers):
    assert cloud_client.options("/api/worlds", headers=headers).status_code == 400


def test_authenticated_csrf_rejection_stays_403_and_is_browser_readable(cloud_client):
    registered = cloud_client.post(
        "/api/auth/register",
        json={"username": "cors_test_user", "password": "isolated password 123"},
        headers={"Origin": FRONTEND_ORIGIN},
    )
    assert registered.status_code == 201
    assert cloud_client.get("/api/auth/me").status_code == 200
    # Loopback is CORS-readable but this specific origin is NOT in the
    # mutation allowlist. CORS visibility must never substitute for CSRF.
    other_loopback = "https://localhost:8790"
    refused = cloud_client.post("/api/worlds", json={}, headers={"Origin": other_loopback})
    assert refused.status_code == 403
    assert refused.json()["detail"] == "请求 Origin 不受信任"
    assert refused.headers.get("access-control-allow-origin") == other_loopback


def test_local_trust_rejection_is_readable_but_still_denied(monkeypatch, cloud_client):
    monkeypatch.setenv("TRPG_REQUIRE_AUTH", "0")
    refused = cloud_client.get("/api/theme", headers={"Origin": "null"})
    assert refused.status_code == 403
    assert refused.json()["detail"] == "本地请求来源或连接凭证不受信任"
    assert refused.headers.get("access-control-allow-origin") == "null"
