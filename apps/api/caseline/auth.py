"""Bearer authentication with roles (spec §8: admin, operator, firm_user; plus service for the voice agent).

- The voice agent authenticates with CASELINE_INTERNAL_API_TOKEN and gets role `service`.
- People authenticate with personal tokens created by `python -m caseline.cli create-principal`; only a SHA-256
  hash is stored. `firm_user` principals are bound to one firm and can only see that firm's referrals.
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from caseline.deps import get_session
from caseline.models import ApiPrincipal

ROLES = ("admin", "operator", "firm_user", "service")


@dataclass(frozen=True)
class Principal:
    name: str
    role: str
    firm_id: uuid.UUID | None = None


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _unauthorized() -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, detail={"reason": "unauthorized"})


def get_principal(request: Request, authorization: str | None = Header(default=None),
                  session: Session = Depends(get_session)) -> Principal:
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise _unauthorized()
    service_token = request.app.state.settings.caseline_internal_api_token.get_secret_value()
    if hmac.compare_digest(token, service_token):
        principal = Principal(name="voice-agent", role="service")
    else:
        row = session.scalar(select(ApiPrincipal).where(ApiPrincipal.token_hash == hash_token(token),
                                                        ApiPrincipal.active.is_(True)))
        if row is None:
            raise _unauthorized()
        principal = Principal(name=row.name, role=row.role, firm_id=row.firm_id)
    request.state.principal = principal
    return principal


def require_roles(*roles: str) -> Callable[..., Principal]:
    allowed = set(roles) | {"admin"}

    def dep(principal: Principal = Depends(get_principal)) -> Principal:
        if principal.role not in allowed:
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"reason": "forbidden_for_role"})
        return principal

    return dep


# Common role sets
SERVICE = require_roles("service")
OPERATOR = require_roles("operator")
FIRM_OR_OPERATOR = require_roles("operator", "firm_user")


def require_internal_token(principal: Principal = Depends(SERVICE)) -> Principal:
    """Backwards-compatible alias used by voice-agent endpoints."""
    return principal
