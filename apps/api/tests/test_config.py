"""Startup assertions (spec §17) and admin redaction."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from caseline.config import Settings


def S(**kw):
    return Settings(_env_file=None, **{"app_env": "test", **kw})


def test_defaults_are_safe():
    s = S()
    assert s.telecom_provider == "guava"
    assert s.guava_mode == "mock"
    assert s.demo_mode is False and s.demo_live_transfer_enabled is False
    assert s.guava_sms_enabled is False
    assert s.live_transfer_gate_open is False


@pytest.mark.parametrize("kwargs", [
    {"telecom_provider": "some_other_carrier"},
    {"demo_transfer_allowlist": "+12676804795,+15555550100", "demo_mode": True},
    {"demo_transfer_allowlist": "2676804795"},
    {"demo_firm_a_transfer_number": "+12025550100"},
    {"app_env": "production", "demo_mode": True, "caseline_internal_api_token": "x"},
    {"app_env": "staging"},
    {"guava_sms_enabled": True},
])
def test_unsafe_configs_rejected(kwargs):
    with pytest.raises(ValidationError):
        S(**kwargs)


def test_validation_errors_never_print_secrets():
    with pytest.raises(ValidationError) as exc:
        S(app_env="staging", guava_mode="live", guava_api_key="gva-super-secret-value")
    assert "gva-super-secret-value" not in str(exc.value)


def test_admin_list_is_redacted(h):
    h.triage("c1")
    body = h.get("/v1/admin/cases").json()
    row = body["items"][0]
    assert row["caller_initials"] == "A.D."
    assert row["callback_masked"] == "***00"
    assert "Alex" not in str(body) and "+12125550100" not in str(body)


def test_health(h):
    assert h.client.get("/health/live").json() == {"status": "ok"}
    ready = h.client.get("/health/ready").json()
    assert ready["status"] == "ok" and ready["demo_live_transfer_enabled"] is False


def test_firm_availability_endpoint(h):
    r = h.get("/v1/firms/demo-firm-a/availability").json()
    assert r["open_now"] is True and r["source"] == "configured_hours"
    assert "transfer_number" not in r and "+1267" not in str(r)
