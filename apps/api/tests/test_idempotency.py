"""Duplicate / reordered requests and events (spec §10, §32)."""

from __future__ import annotations

from caseline.models import CallSession, Case, Notification, ProviderEvent, Referral
from tests.conftest import NIGHT, triage_body


def test_duplicate_triage_idempotency_returns_same_case(h):
    first = h.triage("c1").json()
    second = h.triage("c1").json()
    assert first["case_id"] == second["case_id"]
    assert first["referral_id"] == second["referral_id"]
    with h.db.sessionmaker() as s:
        assert s.query(Case).count() == 1
        assert s.query(Referral).count() == 1


def test_idempotency_key_reuse_with_different_body_rejected(h):
    h.triage("c1")
    r = h.post("/v1/intake/triage", triage_body("c2", "DEMO_AREA_B", "DEMO_JURISDICTION"), key="c1:triage:v1")
    assert r.status_code == 422


def test_duplicate_call_events_deduplicated(h):
    ev = {"provider_event_id": "c1:end", "provider_call_id": "c1", "event_type": "session_ended",
          "termination_reason": "user-hangup"}
    assert h.post("/v1/calls/events", ev).json() == {"duplicate": False}
    assert h.post("/v1/calls/events", ev).json() == {"duplicate": True}
    with h.db.sessionmaker() as s:
        assert s.query(ProviderEvent).count() == 1


def test_reordered_events_keep_call_ended(h):
    h.post("/v1/calls/events", {"provider_event_id": "c1:end", "provider_call_id": "c1",
                                "event_type": "session_ended", "termination_reason": "user-hangup"})
    h.post("/v1/calls/events", {"provider_event_id": "c1:start", "provider_call_id": "c1",
                                "event_type": "call_started"})
    with h.db.sessionmaker() as s:
        assert s.query(CallSession).one().state == "ended"


def test_duplicate_extended_intake_creates_one_referral_and_one_notification(make_harness):
    h = make_harness(now=NIGHT)
    tri = h.triage("c1", sms=True).json()
    body = {"provider_call_id": "c1", "reason": "after_hours",
            "facts": {"event_date": {"value": "last week"}}}
    a = h.post(f"/v1/referrals/{tri['referral_id']}/extended-intake", body, key="c1:extended:v1")
    b = h.post(f"/v1/referrals/{tri['referral_id']}/extended-intake", body, key="c1:extended:v1")
    assert a.status_code == b.status_code == 200
    assert a.json() == b.json()
    with h.db.sessionmaker() as s:
        assert s.query(Referral).count() == 1
        assert s.query(Notification).filter_by(channel="email").count() == 1
        assert s.query(Notification).filter_by(channel="sms").count() == 1


def test_caller_disconnect_mid_intake_keeps_consented_partial_case(make_harness):
    h = make_harness(now=NIGHT)
    tri = h.triage("c1").json()
    h.post("/v1/calls/events", {"provider_event_id": "c1:end", "provider_call_id": "c1",
                                "event_type": "session_ended", "termination_reason": "user-hangup"})
    with h.db.sessionmaker() as s:
        case = s.query(Case).one()
        assert str(case.id) == tri["case_id"]
        assert case.status == "extended_intake"  # stays open for follow-up; no referral sent
        assert s.query(Notification).count() == 0


def test_invalid_transition_returns_409_and_is_audited(h):
    tri = h.triage("c1").json()
    r = h.post(f"/v1/referrals/{tri['referral_id']}/status", {"status": "accepted", "actor": "op1"})
    assert r.status_code == 409
    from caseline.models import AuditEvent

    with h.db.sessionmaker() as s:
        assert s.query(AuditEvent).filter_by(result="rejected").count() == 1
