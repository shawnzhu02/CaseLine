"""In-memory fake of the Guava SMS gateway for tests, CI and local development. Never sends anything."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class MockGuavaSmsGateway:
    sent: list[dict] = field(default_factory=list)
    fail_times: int = 0  # simulate transient failures

    def send_sms(self, *, from_number: str, to_number: str, message: str, idempotency_key: str) -> str | None:
        if self.fail_times > 0:
            self.fail_times -= 1
            raise ConnectionError("mock transient failure")
        self.sent.append({"from": from_number, "to": to_number, "message": message, "key": idempotency_key})
        return f"mock-sms-{len(self.sent)}"
