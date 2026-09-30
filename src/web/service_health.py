"""Readiness checks independent of the server entry-point wiring."""

from fastapi.responses import JSONResponse
from sqlalchemy import text

from src.app.runtime import RuntimeContext
from src.storage.database import session_scope


def health_payload(context: RuntimeContext) -> dict:
    return {"ok": True, "module": context.module_name, "world_id": context.world_id}


def readiness_payload(database_url: str, context: RuntimeContext):
    """Check storage without exposing connection details on failure."""
    try:
        with session_scope(database_url) as session:
            session.execute(text("SELECT 1"))
    except Exception:
        return JSONResponse(
            {"ok": False, "detail": "database unavailable"}, status_code=503
        )
    return health_payload(context)
