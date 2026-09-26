"""Transactional email (NOT telephony). Resend is the intended provider; mock by default."""

from __future__ import annotations

from typing import Protocol

import httpx


class EmailGateway(Protocol):
    def send(self, *, to: str, subject: str, text: str, idempotency_key: str) -> str | None: ...


class ResendEmailGateway:
    def __init__(self, api_key: str, sender: str) -> None:
        self._client = httpx.Client(base_url="https://api.resend.com", timeout=httpx.Timeout(10.0, connect=3.0),
                                    headers={"Authorization": f"Bearer {api_key}"})
        self._sender = sender

    def send(self, *, to: str, subject: str, text: str, idempotency_key: str) -> str | None:
        r = self._client.post("/emails", json={"from": self._sender, "to": [to], "subject": subject, "text": text},
                              headers={"Idempotency-Key": idempotency_key})
        r.raise_for_status()
        return r.json().get("id")
