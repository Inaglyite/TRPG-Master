from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.web.frontend_http import FrontendStaticFiles


def _frontend_client(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<title>TRPG Game</title>", encoding="utf-8")
    (tmp_path / "assets" / "index-deadbeef.js").write_text(
        "console.log('ok')",
        encoding="utf-8",
    )
    app = FastAPI()
    app.mount("/", FrontendStaticFiles(directory=tmp_path, html=True))
    return TestClient(app)


def test_html_shell_is_never_reused_across_releases(tmp_path):
    with _frontend_client(tmp_path) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache, no-store, must-revalidate"
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["expires"] == "0"


def test_fingerprinted_vite_assets_are_immutable(tmp_path):
    with _frontend_client(tmp_path) as client:
        response = client.get("/assets/index-deadbeef.js")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"
