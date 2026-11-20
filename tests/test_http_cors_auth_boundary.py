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


@pytest.mark.parametrize("path", ["/api/auth/me", "/api/worlds", "/api/modules", "/api/account/model-settings"])
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


def test_account_nickname_can_be_set_updated_and_cleared(cloud_client):
    registered = cloud_client.post(
        "/api/auth/register",
        json={"username": "nickname_user", "password": "isolated password 123"},
        headers={"Origin": FRONTEND_ORIGIN},
    )
    assert registered.status_code == 201
    assert registered.json().get("display_name") == ""

    for nickname in ("调查员", "新的昵称", ""):
        response = cloud_client.patch(
            "/api/auth/profile",
            json={"display_name": nickname},
            headers={"Origin": FRONTEND_ORIGIN},
        )
        assert response.status_code == 200
        assert response.json()["display_name"] == nickname
        assert cloud_client.get("/api/auth/me").json()["display_name"] == nickname


def test_account_model_settings_before_world_creation(cloud_client):
    import server
    from src.storage.model_config_store import resolve_cloud_settings

    headers = {"Origin": FRONTEND_ORIGIN}
    url = "/api/account/model-settings"
    owner = cloud_client.post("/api/auth/register", headers=headers, json={
        "username": "model_owner", "password": "isolated password 123",
    }).json()
    initial = cloud_client.get(url)
    assert initial.headers["cache-control"] == "no-store"
    assert initial.json()["mode"] == "account"
    assert initial.json()["byok_required"] is True
    assert cloud_client.post(url + "/test", headers=headers, json={}).json()["ok"] is False

    binding = {"mode": "custom", "service": {
        "provider_kind": "openai_compatible", "api_format": "responses",
        "base_url": "https://1.1.1.1/v1", "model_id": "preview-model",
        "api_key": "unit-test-placeholder",
    }}
    update = {"narrative": binding, "judgement": binding, "confirm_data_sharing": True}
    saved = cloud_client.put(url, headers=headers, json=update)
    assert saved.status_code == 200 and saved.json()["saved"]
    assert "unit-test-placeholder" not in saved.text
    assert cloud_client.get(url).json()["narrative"]["service"]["api_format"] == "responses"
    effective = resolve_cloud_settings(server.DATABASE_URL, owner_user_id=owner["id"], world_id="new-world")
    assert effective.narrative.service.model_id == "preview-model"
    with TestClient(server.app, base_url=BACKEND_ORIGIN) as other:
        assert other.post("/api/auth/register", headers=headers, json={
            "username": "model_guest", "password": "isolated password 456",
        }).status_code == 201
        assert other.get(url).json()["narrative"]["mode"] == "default"
    refused = cloud_client.put(url, headers=headers, json={**update, "scope": "world"})
    assert refused.status_code == 400
    assert cloud_client.post(url + "/restore", headers=headers, json={}).json()["saved"]
    assert cloud_client.get(url).json()["narrative"]["mode"] == "default"


def test_password_change_verifies_current_password_and_revokes_other_sessions(
    cloud_client,
):
    import server

    password = "isolated password 123"
    assert cloud_client.post(
        "/api/auth/register",
        json={"username": "password_user", "password": password},
        headers={"Origin": FRONTEND_ORIGIN},
    ).status_code == 201
    with TestClient(server.app, base_url=BACKEND_ORIGIN) as other_client:
        assert other_client.post(
            "/api/auth/login",
            json={"username": "password_user", "password": password},
            headers={"Origin": FRONTEND_ORIGIN},
        ).status_code == 200

        wrong = cloud_client.post(
            "/api/auth/password",
            json={"current_password": "wrong password", "new_password": "new password 456"},
            headers={"Origin": FRONTEND_ORIGIN},
        )
        assert wrong.status_code == 403
        assert cloud_client.get("/api/auth/me").status_code == 200
        assert other_client.get("/api/auth/me").status_code == 200

        changed = cloud_client.post(
            "/api/auth/password",
            json={"current_password": password, "new_password": "new password 456"},
            headers={"Origin": FRONTEND_ORIGIN},
        )
        assert changed.status_code == 204
        assert cloud_client.get("/api/auth/me").status_code == 200
        assert other_client.get("/api/auth/me").status_code == 401
        assert other_client.post(
            "/api/auth/login",
            json={"username": "password_user", "password": password},
            headers={"Origin": FRONTEND_ORIGIN},
        ).status_code == 401
        assert other_client.post(
            "/api/auth/login",
            json={"username": "password_user", "password": "new password 456"},
            headers={"Origin": FRONTEND_ORIGIN},
        ).status_code == 200


def test_account_module_order_is_complete_and_persistent(cloud_client):
    assert cloud_client.post(
        "/api/auth/register",
        json={"username": "module_order_user", "password": "isolated password 123"},
        headers={"Origin": FRONTEND_ORIGIN},
    ).status_code == 201
    initial = cloud_client.get("/api/modules").json()["modules"]
    module_ids = [item["id"] for item in initial]
    assert len(module_ids) >= 2
    expected = list(reversed(module_ids))

    changed = cloud_client.put(
        "/api/me/modules/order",
        json={"module_ids": expected},
        headers={"Origin": FRONTEND_ORIGIN},
    )
    assert changed.status_code == 200
    assert [item["id"] for item in cloud_client.get("/api/modules").json()["modules"]] == expected

    rejected = cloud_client.put(
        "/api/me/modules/order",
        json={"module_ids": expected[:-1]},
        headers={"Origin": FRONTEND_ORIGIN},
    )
    assert rejected.status_code == 400
    assert [item["id"] for item in cloud_client.get("/api/modules").json()["modules"]] == expected
