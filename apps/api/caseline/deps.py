"""Request-scoped dependencies resolved from app.state (easy to override in tests)."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import datetime

from fastapi import Request
from sqlalchemy.orm import Session

from caseline.config import Settings


def get_session(request: Request) -> Iterator[Session]:
    yield from request.app.state.database.session()


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


def get_clock(request: Request) -> Callable[[], datetime]:
    return request.app.state.clock
