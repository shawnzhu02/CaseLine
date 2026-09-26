from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

router = APIRouter()


@router.get("/health/live")
def live():
    return {"status": "ok"}


@router.get("/health/ready")
def ready(request: Request):
    try:
        with request.app.state.database.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        return JSONResponse({"status": "unavailable", "database": "down"}, status_code=503)
    s = request.app.state.settings
    return {"status": "ok", "database": "up", "guava_mode": s.guava_mode, "demo_mode": s.demo_mode,
            "demo_live_transfer_enabled": s.demo_live_transfer_enabled}
