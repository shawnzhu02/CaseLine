"""Guava SMS gateway (the ONLY SMS provider). Call handling lives in apps/voice.

Verified against guava-sdk 0.45.0: `guava.Client(api_key=...).send_sms(from_number, to_number, message)`
returns None (no message id) and the SDK exposes no delivery-status or STOP/opt-out events. Therefore a
successful call is recorded as `submitted` (delivery unknown), never as delivered.
"""

from __future__ import annotations

from typing import Protocol


class GuavaSmsGateway(Protocol):
    def send_sms(self, *, from_number: str, to_number: str, message: str, idempotency_key: str) -> str | None: ...


class LiveGuavaSmsGateway:
    def __init__(self, api_key: str) -> None:
        import guava  # optional dependency: pip install -e ".[guava]"

        self._client = guava.Client(api_key=api_key)

    def send_sms(self, *, from_number: str, to_number: str, message: str, idempotency_key: str) -> str | None:
        # The SDK takes no idempotency key; the outbox guarantees at-most-once submission per event_key.
        self._client.send_sms(from_number=from_number, to_number=to_number, message=message)
        return None
