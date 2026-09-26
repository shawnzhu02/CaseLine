"""Idempotency-Key replay for retried creates (spec §6)."""

from __future__ import annotations

import hashlib
import json

from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from sqlalchemy.orm import Session

from caseline.errors import ApiError
from caseline.models import IdempotencyRecord


def request_hash(body: object) -> str:
    return hashlib.sha256(json.dumps(jsonable_encoder(body), sort_keys=True).encode()).hexdigest()


def lookup(session: Session, scope: str, key: str, body_hash: str) -> dict | None:
    rec = session.scalar(select(IdempotencyRecord).where(IdempotencyRecord.scope == scope,
                                                          IdempotencyRecord.key == key))
    if rec is None:
        return None
    if rec.request_hash != body_hash:
        raise ApiError(422, "idempotency_key_reused_with_different_body")
    return rec.response_body


def store(session: Session, scope: str, key: str, body_hash: str, response: object, status_code: int = 200) -> None:
    session.add(IdempotencyRecord(scope=scope, key=key, request_hash=body_hash, status_code=status_code,
                                  response_body=jsonable_encoder(response)))
