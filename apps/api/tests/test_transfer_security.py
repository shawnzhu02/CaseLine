"""Transfer authorization gates (spec §19.2, §4A acceptance checks)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from pydantic import ValidationError

from caseline.config import Settings
from caseline.models import Firm, TransferAttempt, TransferAuthorization


def _triage(h, call_id="call-1", area="DEMO_AREA_A"):
    r = h.triage(call_id, practice_area=area)
    assert r.status_code == 200, r.text
    return r.json()


def test_transfer_blocked_when_demo_live_disabled(h):
    result = _triage(h)
    r = h.authorize(result["referral_id"], "call-1")
    assert r.status_code == 409
    assert r.json()["detail"]["reason"] == "live_transfer_disabled"
    assert "destination_e164" not in r.text and "+1267" not in r.text


def test_transfer_blocked_for_number_not_in_allowlist(live):
    result = _triage(live)
    with live.db.sessionmaker() as s:
        firm = s.query(Firm).filter_by(slug="demo-firm-a").one()
        firm.transfer_number = "+12676804796"  # e.g. a transcription mistake / tampered record
        s.commit()
    r = live.authorize(result["referral_id"], "call-1")
    assert r.status_code == 409
    assert r.json()["detail"]["reason"] == "destination_not_allowlisted"


def test_transfer_blocked_when_caller_declines(live):
    result = _triage(live)
    r = live.authorize(result["referral_id"], "call-1", consented=False)
    assert r.status_code == 409
    assert r.json()["detail"]["reason"] == "caller_declined"
    with live.db.sessionmaker() as s:
        assert s.query(TransferAuthorization).count() == 0


def test_transfer_blocked_for_wrong_call(live):
    result = _triage(live)
    _triage(live, call_id="other-call", area="DEMO_AREA_B")
    r = live.authorize(result["referral_id"], "other-call")
    assert r.status_code == 409
    assert r.json()["detail"]["reason"] == "call_mismatch"


def test_transfer_blocked_when_firm_closed_even_if_live_enabled(live):
    result = _triage(live)
    with live.db.sessionmaker() as s:
        s.query(Firm).filter_by(slug="demo-firm-a").one().accepting_live_calls = False
        s.commit()
    r = live.authorize(result["referral_id"], "call-1")
    assert r.status_code == 409
    assert r.json()["detail"]["reason"] == "firm_unavailable"


def test_request_cannot_supply_destination(live):
    result = _triage(live)
    r = live.post(f"/v1/referrals/{result['referral_id']}/authorize-transfer",
                  {"provider_call_id": "call-1", "caller_consented": True, "destination_e164": "+15555550199"},
                  key="k")
    assert r.status_code == 422  # extra fields are forbidden


def test_transfer_authorization_is_single_use(live):
    result = _triage(live)
    auth = live.authorize(result["referral_id"], "call-1").json()
    first = live.attempt(result["referral_id"], "call-1", auth, key="k1")
    assert first.status_code == 200
    second = live.attempt(result["referral_id"], "call-1", auth, key="k2")
    assert second.status_code == 409
    assert second.json()["detail"]["reason"] == "authorization_already_used"


def test_transfer_authorization_expires(live):
    result = _triage(live)
    auth = live.authorize(result["referral_id"], "call-1").json()
    live.clock.now += timedelta(seconds=live.settings.transfer_authorization_ttl_seconds + 1)
    r = live.attempt(result["referral_id"], "call-1", auth)
    assert r.status_code == 409
    assert r.json()["detail"]["reason"] == "authorization_expired"


def test_transfer_attempt_requires_valid_token(live):
    result = _triage(live)
    auth = live.authorize(result["referral_id"], "call-1").json()
    r = live.attempt(result["referral_id"], "call-1", {**auth, "authorization_token": "forged"})
    assert r.status_code == 403


def test_duplicate_transfer_attempt_does_not_redial(live):
    result = _triage(live)
    auth = live.authorize(result["referral_id"], "call-1").json()
    first = live.attempt(result["referral_id"], "call-1", auth).json()
    again = live.attempt(result["referral_id"], "call-1", auth).json()  # same Idempotency-Key
    assert first["dial"] is True
    assert again["dial"] is False
    assert again["transfer_attempt_id"] == first["transfer_attempt_id"]
    with live.db.sessionmaker() as s:
        assert s.query(TransferAttempt).count() == 1


def test_attempt_is_not_connected_on_bot_transfer_signal(live):
    result = _triage(live)
    auth = live.authorize(result["referral_id"], "call-1").json()
    attempt = live.attempt(result["referral_id"], "call-1", auth).json()
    ev = live.post("/v1/calls/events", {"provider_event_id": "call-1:end", "provider_call_id": "call-1",
                                        "event_type": "session_ended", "termination_reason": "bot-transfer"})
    assert ev.status_code == 200
    with live.db.sessionmaker() as s:
        row = s.get(TransferAttempt, __import__("uuid").UUID(attempt["transfer_attempt_id"]))
        assert row.state == "requested"
        assert row.provider_signal == "guava:bot-transfer"
        assert row.connected_at is None


def test_operator_confirmed_connection(live):
    result = _triage(live)
    auth = live.authorize(result["referral_id"], "call-1").json()
    attempt = live.attempt(result["referral_id"], "call-1", auth).json()
    r = live.post(f"/v1/transfer-attempts/{attempt['transfer_attempt_id']}/outcome",
                  {"result": "connected", "source": "operator"})
    assert r.status_code == 200 and r.json()["state"] == "connected"
    dup = live.post(f"/v1/transfer-attempts/{attempt['transfer_attempt_id']}/outcome",
                    {"result": "failed", "source": "operator"})
    assert dup.status_code == 409


def test_ci_configuration_cannot_enable_live_transfer():
    # Live transfer without demo mode is rejected at startup.
    with pytest.raises(ValidationError):
        Settings(_env_file=None, app_env="test", demo_mode=False, demo_live_transfer_enabled=True)
    # The test environment can never be pointed at live Guava.
    with pytest.raises(ValidationError):
        Settings(_env_file=None, app_env="test", guava_mode="live", guava_api_key="x",
                 guava_agent_number="+12025550100")
    # CI's env block (see .github/workflows/ci.yml) leaves the gate closed.
    ci = Settings(_env_file=None, app_env="test", demo_mode=True, demo_live_transfer_enabled=False, guava_mode="mock")
    assert ci.live_transfer_gate_open is False


def test_requires_bearer_token(h):
    r = h.client.post("/v1/intake/triage", json={})
    assert r.status_code == 401
    r = h.client.post("/v1/intake/triage", json={}, headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401
