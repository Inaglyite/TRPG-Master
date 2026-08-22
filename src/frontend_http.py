"""HTTP cache policy for the built browser frontend."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response
from starlette.types import Scope


class FrontendStaticFiles(StaticFiles):
    """Serve the Vite build without letting an old HTML shell survive releases."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        content_type = response.headers.get("content-type", "").lower()

        if content_type.startswith("text/html"):
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        elif path.startswith("assets/"):
            # Vite fingerprints files below assets/, so they are safe to cache forever.
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"

        return response


def mount_editor_bundle(app: FastAPI, project_root: Path) -> None:
    """Mount the vendored TRPG Mod Editor browser build at /editor, if present.

    同源托管让编辑器复用本后端的 editor_api 会话契约；云端模式下编辑器 API
    本身已有管理员门禁，静态壳不携带任何特权数据。必须在 "/" 前端挂载之前
    注册，否则会被前端 SPA 兜底吞掉。
    """

    editor_dir = Path(project_root) / "editor" / "dist"
    if editor_dir.exists():
        app.mount(
            "/editor",
            FrontendStaticFiles(directory=str(editor_dir), html=True),
            name="mod-editor",
        )
