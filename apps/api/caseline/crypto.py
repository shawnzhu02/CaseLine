"""Field-level encryption for caller PII and HMAC-signed report links.

Keys come from settings. Development/test fall back to fixed, clearly-non-secret keys; staging/demo/production must
set CASELINE_FIELD_ENCRYPTION_KEY and REPORT_LINK_SECRET (enforced in config.py).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import Text
from sqlalchemy.types import TypeDecorator

DEV_FIELD_KEY = base64.urlsafe_b64encode(hashlib.sha256(b"caseline-dev-only-field-key").digest()).decode()
DEV_LINK_SECRET = "caseline-dev-only-link-secret"

_fernet: Fernet | None = None


def configure(field_key: str | None) -> None:
    global _fernet
    _fernet = Fernet((field_key or DEV_FIELD_KEY).encode())


def _f() -> Fernet:
    if _fernet is None:
        configure(None)
    return _fernet  # type: ignore[return-value]


class EncryptedText(TypeDecorator[str]):
    """Stores Fernet ciphertext; application code sees plaintext. Not queryable by value (by design)."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: str | None, dialect) -> str | None:
        return None if value is None else _f().encrypt(value.encode()).decode()

    def process_result_value(self, value: str | None, dialect) -> str | None:
        if value is None:
            return None
        try:
            return _f().decrypt(value.encode()).decode()
        except InvalidToken:
            return "[undecryptable]"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sign_link(secret: str, payload: dict, ttl_seconds: int, now: float | None = None) -> str:
    body = {**payload, "exp": int((now or time.time()) + ttl_seconds)}
    raw = _b64(json.dumps(body, separators=(",", ":"), sort_keys=True).encode())
    sig = _b64(hmac.new(secret.encode(), raw.encode(), hashlib.sha256).digest())
    return f"{raw}.{sig}"


class LinkError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def verify_link(secret: str, token: str, now: float | None = None) -> dict:
    try:
        raw, sig = token.split(".", 1)
    except ValueError as exc:
        raise LinkError("malformed") from exc
    expected = _b64(hmac.new(secret.encode(), raw.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, expected):
        raise LinkError("bad_signature")
    body = json.loads(_unb64(raw))
    if (now or time.time()) >= body["exp"]:
        raise LinkError("expired")
    return body
