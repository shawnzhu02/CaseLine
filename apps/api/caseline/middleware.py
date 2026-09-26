"""Request correlation IDs and a simple per-client rate limiter.

The rate limiter is in-memory and per process: fine for one API instance (the Render blueprint runs one). Use a
shared store (or the platform's edge rate limiting) before scaling out horizontally.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
import uuid
from collections import deque

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

log = logging.getLogger("caseline.http")


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        request.state.request_id = rid[:64]
        started = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        # Path templates only (no IDs/PII in the message beyond the route); status + latency for observability.
        route = request.scope.get("route")
        log.info("request", extra={"correlation_id": request.state.request_id,
                                   "event": f"{request.method} {getattr(route, 'path', 'unmatched')} "
                                            f"{response.status_code} {int((time.perf_counter() - started) * 1000)}ms"})
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, per_minute: int, report_links_per_minute: int) -> None:
        super().__init__(app)
        self.per_minute = per_minute
        self.report_per_minute = report_links_per_minute
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def _key(self, request: Request) -> tuple[str, int]:
        client = request.client.host if request.client else "unknown"
        if request.url.path.startswith("/r/"):
            return f"report:{client}", self.report_per_minute
        auth = request.headers.get("authorization", "")
        ident = hashlib.sha256(auth.encode()).hexdigest()[:16] if auth else client
        return f"api:{ident}", self.per_minute

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.url.path.startswith("/health") or self.per_minute <= 0:
            return await call_next(request)
        key, limit = self._key(request)
        now = time.monotonic()
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and now - hits[0] > 60:
                hits.popleft()
            if len(hits) >= limit:
                retry = int(60 - (now - hits[0])) + 1
                return JSONResponse({"detail": {"reason": "rate_limited"}}, status_code=429,
                                    headers={"Retry-After": str(retry)})
            hits.append(now)
        return await call_next(request)
