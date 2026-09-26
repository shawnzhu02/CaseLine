"""Service-to-service bearer authentication.

MVP: a single internal token shared by the Guava voice agent and operators. Role-based access
(admin/operator/firm_user) and firm isolation are Phase 4 (see docs/decisions/0001-architecture.md).
"""

from __future__ import annotations

import hmac

from fastapi import Header, HTTPException, Request, status


def require_internal_token(request: Request, authorization: str | None = Header(default=None)) -> str:
    expected = request.app.state.settings.caseline_internal_api_token.get_secret_value()
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token or not hmac.compare_digest(token, expected):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail={"reason": "unauthorized"})
    return "internal-service"
