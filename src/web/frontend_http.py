"""HTTP cache policy for the built browser frontend."""

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.responses import RedirectResponse, Response
from starlette.types import Scope

from src.modules.editor_updates import EditorUpdateStore
from src.web.editor_update_http import create_editor_update_router


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


class EditorStaticFiles(FrontendStaticFiles):
    def __init__(self, store: EditorUpdateStore):
        super().__init__(directory=str(store.bundled), html=True)
        self.store = store

    async def get_response(self, path: str, scope: Scope) -> Response:
        if path in {"", ".", "index.html"}:
            current = self.store.current()
            if current["source"] == "downloaded":
                return RedirectResponse(
                    f'/editor/updates/{current["bundle_id"]}/index.html',
                    headers={"Cache-Control": "no-store"},
                )
        if path.startswith("updates/"):
            parts = path.split("/", 2)
            if len(parts) == 3:
                bundle_id, resource = parts[1:]
                directory = self.store.bundle_directory(bundle_id)
                if directory is not None:
                    return await FrontendStaticFiles(directory=str(directory), html=True).get_response(resource, scope)
        return await super().get_response(path, scope)


def mount_editor_bundle(app: FastAPI, project_root: Path, runtime_root: Path | None = None) -> None:
    """Mount the vendored TRPG Mod Editor browser build at /editor, if present.

    同源托管让编辑器复用本后端的 editor_api 会话契约；云端模式下编辑器 API
    本身已有管理员门禁，静态壳不携带任何特权数据。必须在 "/" 前端挂载之前
    注册，否则会被前端 SPA 兜底吞掉。
    """

    editor_dir = Path(project_root) / "editor" / "dist"
    if editor_dir.exists():
        store = EditorUpdateStore(editor_dir, runtime_root or project_root,
                                  test_tag=os.environ.get("TRPG_EDITOR_UPDATE_TEST_TAG", ""))
        app.include_router(create_editor_update_router(store))
        app.mount(
            "/editor",
            EditorStaticFiles(store),
            name="mod-editor",
        )
