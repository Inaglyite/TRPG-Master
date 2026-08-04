"""HTTP cache policy for the built browser frontend."""

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
