"""In-memory email fake. Never sends anything."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class MockEmailGateway:
    sent: list[dict] = field(default_factory=list)
    fail_times: int = 0

    def send(self, *, to: str, subject: str, text: str, idempotency_key: str) -> str | None:
        if self.fail_times > 0:
            self.fail_times -= 1
            raise ConnectionError("mock transient failure")
        self.sent.append({"to": to, "subject": subject, "text": text, "key": idempotency_key})
        return f"mock-email-{len(self.sent)}"
