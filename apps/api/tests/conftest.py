"""Test harness: in-memory SQLite, fixed clock, mock Guava/email gateways. Never dials, texts or emails."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from caseline.config import Settings
from caseline.db import Base, Database
from caseline.main import create_app
from caseline.providers import Gateways
from caseline.providers.email_mock import MockEmailGateway
from caseline.providers.guava_mock import MockGuavaSmsGateway
from caseline.seed import seed

# Tuesday 2026-09-29 13:00Z = 09:00 New York (demo firms open 08-20) = 14:00 London (fixture open 09-17).
DAYTIME = datetime(2026, 9, 29, 13, 0, tzinfo=UTC)
# Wednesday 03:00Z = 23:00 New York / 04:00 London: everyone closed.
NIGHT = datetime(2026, 9, 30, 3, 0, tzinfo=UTC)

TOKEN = "test-only-token"

# Hard guarantee: tests never talk to Guava for real.
os.environ.pop("GUAVA_API_KEY", None)


@pytest.fixture(autouse=True)
def _isolate_settings_env(monkeypatch):
    """Tests pass settings explicitly; ignore CI/developer env vars (e.g. DEMO_MODE) so defaults are testable."""
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@dataclass
class Harness:
    client: TestClient
    settings: Settings
    db: Database
    clock: Clock
    gateways: Gateways

    def post(self, path: str, body: dict, key: str | None = None, **kw):
        headers = {"Authorization": f"Bearer {TOKEN}"}
        if key:
            headers["Idempotency-Key"] = key
        return self.client.post(path, json=body, headers=headers, **kw)

    def get(self, path: str, **kw):
        return self.client.get(path, headers={"Authorization": f"Bearer {TOKEN}"}, **kw)

    def triage(self, call_id: str, practice_area: str | None = "DEMO_AREA_A",
               jurisdiction: str | None = "DEMO_JURISDICTION", **overrides):
        body = triage_body(call_id, practice_area, jurisdiction, **overrides)
        return self.post("/v1/intake/triage", body, key=f"{call_id}:triage:v1")

    def authorize(self, referral_id: str, call_id: str, consented: bool = True):
        return self.post(f"/v1/referrals/{referral_id}/authorize-transfer",
                         {"provider_call_id": call_id, "caller_consented": consented},
                         key=f"{call_id}:transfer-consent:v1")

    def attempt(self, referral_id: str, call_id: str, auth: dict, key: str | None = None):
        return self.post(f"/v1/referrals/{referral_id}/transfer-attempts",
                         {"authorization_id": auth["authorization_id"],
                          "authorization_token": auth["authorization_token"],
                          "provider_call_id": call_id, "state": "requested"},
                         key=key or f"{call_id}:transfer-attempt:v1")


def triage_body(call_id: str, practice_area: str | None, jurisdiction: str | None, *,
                issue_summary: str = "Fictional demo matter", immediate_danger: bool = False,
                deadline: str | None = None, intake: bool = True, share: bool = True, sms: bool = False,
                callback: str = "+12125550100") -> dict:
    return {
        "provider_call_id": call_id,
        "caller": {"name": "Alex Demo", "callback_number": callback, "callback_confirmed": True,
                   "preferred_language": "en"},
        "facts": {"jurisdiction": jurisdiction, "practice_area": practice_area, "issue_summary": issue_summary,
                  "immediate_danger": immediate_danger, "caller_reported_deadline": deadline},
        "consents": {"intake": intake, "share_with_selected_firm": share, "sms": sms},
    }


@pytest.fixture
def make_harness() -> Callable[..., Harness]:
    def _make(now: datetime = DAYTIME, **overrides) -> Harness:
        values = {"app_env": "test", "database_url": "sqlite://", "caseline_internal_api_token": TOKEN,
                  "guava_mode": "mock", "demo_mode": True, "demo_live_transfer_enabled": False}
        values.update(overrides)
        settings = Settings(_env_file=None, **values)
        db = Database(settings.database_url)
        Base.metadata.create_all(db.engine)
        with db.sessionmaker() as s:
            seed(s, settings)
        clock = Clock(now)
        app = create_app(settings, db, clock)
        return Harness(TestClient(app), settings, db, clock,
                       Gateways(sms=MockGuavaSmsGateway(), email=MockEmailGateway()))

    return _make


@pytest.fixture
def h(make_harness) -> Harness:
    """Demo mode on, live transfer OFF (the safe default, same as CI)."""
    return make_harness()


@pytest.fixture
def live(make_harness) -> Harness:
    """Demo mode with the live-transfer gate open, but GUAVA_MODE=mock: authorizations are issued and
    recorded, yet nothing can dial because no Guava client exists in the API and tests never run the agent."""
    return make_harness(demo_live_transfer_enabled=True)
