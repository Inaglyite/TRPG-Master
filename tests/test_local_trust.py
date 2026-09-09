"""本地连接信任边界（安全初审 S03）：来源校验 + 每次启动的连接凭证。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.auth.service import (
    LOCAL_TOKEN_HEADER,
    local_launch_token,
    local_request_trusted,
    reset_local_token_for_tests,
    validate_websocket_origin,
)


@pytest.fixture(autouse=True)
def _fresh_token(monkeypatch, tmp_path):
    monkeypatch.delenv("TRPG_LOCAL_LAUNCH_TOKEN", raising=False)
    monkeypatch.delenv("TRPG_ALLOWED_ORIGINS", raising=False)
    monkeypatch.setenv("TRPG_RUNTIME_ROOT", str(tmp_path))
    reset_local_token_for_tests()
    yield
    reset_local_token_for_tests()


def headers(**overrides) -> dict:
    return {key.replace("_", "-"): value for key, value in overrides.items()}


# ---------------------------------------------------------------- 来源校验


@pytest.mark.parametrize(
    "raw",
    [
        headers(origin="http://127.0.0.1:8765"),
        headers(origin="http://localhost:5173"),
        headers(origin="https://127.0.0.1:8443"),
    ],
)
def test_loopback_origins_are_trusted(raw):
    assert local_request_trusted(raw) is True


@pytest.mark.parametrize(
    "raw",
    [
        headers(origin="https://evil.example"),
        headers(origin="http://127.0.0.1.evil.example:8765"),
        headers(origin="null"),
        headers(origin="file://"),
    ],
)
def test_remote_null_and_file_origins_are_rejected_without_token(raw):
    assert local_request_trusted(raw) is False


def test_missing_origin_browser_request_is_rejected():
    assert local_request_trusted(headers(sec_fetch_site="cross-site")) is False


def test_missing_origin_same_origin_navigation_is_trusted():
    assert local_request_trusted(headers(sec_fetch_site="same-origin")) is True
    assert local_request_trusted(headers(sec_fetch_site="none")) is True


def test_non_browser_client_without_origin_headers_is_trusted():
    assert local_request_trusted({}) is True


def test_allowlisted_origin_is_trusted(monkeypatch):
    monkeypatch.setenv("TRPG_ALLOWED_ORIGINS", "https://trpggame.xyz")
    assert local_request_trusted(headers(origin="https://trpggame.xyz")) is True
    assert local_request_trusted(headers(origin="https://other.example")) is False


# ---------------------------------------------------------------- 连接凭证


def test_valid_launch_token_is_trusted_even_for_file_origin():
    token = local_launch_token()
    assert local_request_trusted(headers(origin="file://", **{LOCAL_TOKEN_HEADER: token})) is True


def test_wrong_token_is_rejected():
    assert local_request_trusted(headers(origin="file://", **{LOCAL_TOKEN_HEADER: "stale"})) is False


def test_stale_token_from_previous_launch_is_rejected():
    previous = local_launch_token()
    reset_local_token_for_tests()
    current = local_launch_token()
    assert previous != current
    assert local_request_trusted(headers(origin="file://", **{LOCAL_TOKEN_HEADER: previous})) is False
    assert local_request_trusted(headers(origin="file://", **{LOCAL_TOKEN_HEADER: current})) is True


def test_injected_token_wins_and_is_not_persisted(monkeypatch, tmp_path):
    monkeypatch.setenv("TRPG_LOCAL_LAUNCH_TOKEN", "injected-launch-token")
    reset_local_token_for_tests()
    assert local_launch_token() == "injected-launch-token"
    assert not (tmp_path / "local_launch_token").exists()


def test_generated_token_is_persisted_for_desktop_reuse(tmp_path):
    token = local_launch_token()
    assert (tmp_path / "local_launch_token").read_text(encoding="utf-8").strip() == token


# ---------------------------------------------------------------- WebSocket


def websocket(**overrides) -> SimpleNamespace:
    return SimpleNamespace(headers=headers(**overrides))


def test_local_websocket_rejects_malicious_origin():
    with pytest.raises(HTTPException) as excinfo:
        validate_websocket_origin(websocket(origin="https://evil.example"))
    assert excinfo.value.status_code == 403


def test_local_websocket_accepts_same_origin_and_token():
    validate_websocket_origin(websocket(origin="http://127.0.0.1:8765"))
    validate_websocket_origin(websocket(origin="file://", **{LOCAL_TOKEN_HEADER: local_launch_token()}))


def test_cloud_websocket_ignores_local_token(monkeypatch):
    monkeypatch.setenv("TRPG_REQUIRE_AUTH", "1")
    monkeypatch.setenv("TRPG_ALLOWED_ORIGINS", "https://trpggame.xyz")
    with pytest.raises(HTTPException) as excinfo:
        validate_websocket_origin(websocket(origin="file://", **{LOCAL_TOKEN_HEADER: local_launch_token()}))
    assert excinfo.value.status_code == 403
    validate_websocket_origin(websocket(origin="https://trpggame.xyz"))


# ---------------------------------------------------------------- HTTP 入口


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    import server

    with TestClient(server.app) as test_client:
        yield test_client


def test_local_http_rejects_malicious_origin(client):
    response = client.get("/api/theme", headers={"Origin": "https://evil.example"})
    assert response.status_code == 403


def test_local_http_accepts_same_origin(client):
    response = client.get("/api/theme", headers={"Origin": "http://127.0.0.1:8765"})
    assert response.status_code == 200


def test_local_http_accepts_launch_token(client):
    response = client.get(
        "/api/theme",
        headers={"Origin": "file://", LOCAL_TOKEN_HEADER: local_launch_token()},
    )
    assert response.status_code == 200


def test_local_http_accepts_non_browser_client(client):
    assert client.get("/api/theme").status_code == 200
