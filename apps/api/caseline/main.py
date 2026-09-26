"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from fastapi import FastAPI

from caseline.config import Settings, get_settings
from caseline.db import Database
from caseline.models import utcnow
from caseline.routers import admin, calls, health, intake, referrals


def create_app(settings: Settings | None = None, database: Database | None = None,
               clock: Callable[[], datetime] | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="CaseLine API", version="0.1.0",
                  description="Intake/referral backend. Guava is the only telephony/SMS provider.")
    app.state.settings = settings
    app.state.database = database or Database(settings.database_url)
    app.state.clock = clock or utcnow
    for r in (health.router, intake.router, referrals.router, calls.router, admin.router):
        app.include_router(r)
    return app


_app: FastAPI | None = None


def __getattr__(name: str) -> FastAPI:
    # Lazily build the default app so `uvicorn caseline.main:app` works while tests can import
    # create_app without reading the environment or configuring logging.
    global _app
    if name != "app":
        raise AttributeError(name)
    if _app is None:
        from caseline.logging import configure_logging

        configure_logging()
        _app = create_app()
    return _app
