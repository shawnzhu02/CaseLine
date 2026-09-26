"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from caseline import crypto
from caseline.config import Settings, get_settings
from caseline.db import Database
from caseline.middleware import RateLimitMiddleware, RequestIdMiddleware
from caseline.models import utcnow
from caseline.routers import admin, calls, health, intake, live, operations, referrals


def build_extractor(settings: Settings):
    """Claude fact extractor for live assessment, or None (rules only)."""
    if not settings.assessment_llm_enabled:
        return None
    import logging

    try:
        from caseline.services.assessment import ClaudeExtractor

        key = settings.anthropic_api_key.get_secret_value() if settings.anthropic_api_key else None
        return ClaudeExtractor(settings.assessment_model, settings.assessment_timeout_seconds, api_key=key)
    except Exception as exc:  # e.g. SDK not installed or no credentials: fall back to rules, never fail startup
        logging.getLogger("caseline").warning("Claude extractor disabled: %s", type(exc).__name__)
        return None


def create_app(settings: Settings | None = None, database: Database | None = None,
               clock: Callable[[], datetime] | None = None, extractor=None) -> FastAPI:
    settings = settings or get_settings()
    crypto.configure(settings.caseline_field_encryption_key.get_secret_value()
                     if settings.caseline_field_encryption_key else None)
    app = FastAPI(title="CaseLine API", version="0.2.0",
                  description="Intake/referral backend. Guava is the only telephony/SMS provider.")
    app.state.settings = settings
    app.state.database = database or Database(settings.database_url)
    app.state.clock = clock or utcnow
    app.state.extractor = extractor if extractor is not None else build_extractor(settings)
    app.add_middleware(RateLimitMiddleware, per_minute=settings.rate_limit_per_minute,
                       report_links_per_minute=settings.report_link_rate_limit_per_minute)
    origins = [o.strip() for o in settings.cors_allow_origins.split(",") if o.strip()]
    if origins:
        app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST", "PATCH"],
                           allow_headers=["Authorization", "Content-Type", "Idempotency-Key"])
    app.add_middleware(RequestIdMiddleware)
    for r in (health.router, intake.router, referrals.router, calls.router, admin.router, operations.router,
              live.router, operations.public):
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
