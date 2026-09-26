"""Operator dashboard endpoints, background sweeps and platform hardening."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import text

from caseline.models import AuditEvent, Notification, Referral
from caseline.services import sweeps
from caseline.workers.outbox import process_due
from tests.conftest import NIGHT


def test_review_queue_and_case_detail_are_audited(h):
    h.triage("c1", deadline="hearing next week")
    queue = h.get("/v1/admin/cases?queue=review").json()
    assert queue["total"] == 1 and queue["items"][0]["urgent"] is True
    detail = h.get(f"/v1/admin/cases/{queue['items'][0]['case_id']}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["caller"]["name"] == "Alex Demo"  # full detail is for operators only
    assert body["urgency_reason"] == "caller_reported_deadline"
    with h.db.sessionmaker() as s:
        assert s.query(AuditEvent).filter_by(operation="admin.read_case", actor="operator").count() == 1


def test_case_detail_never_shows_full_dial_target(live):
    tri = live.triage("c1").json()
    auth = live.authorize(tri["referral_id"], "c1").json()
    live.attempt(tri["referral_id"], "c1", auth)
    detail = live.get(f"/v1/admin/cases/{tri['case_id']}").json()
    assert detail["transfer_attempts"][0]["dial_target_masked"] == "***62"
    assert "+16173187562" not in str(detail)


def test_operator_toggles_firm_live_calls(h):
    r = h.patch("/v1/admin/firms/demo-firm-a", {"accepting_live_calls": False})
    assert r.status_code == 200 and r.json()["accepting_live_calls"] is False
    assert h.triage("c1").json()["action"] == "extended_intake"
    firms = h.get("/v1/admin/firms").json()
    assert {f["firm_id"] for f in firms} >= {"demo-firm-a", "demo-firm-b"}
    assert "+1267" not in str(firms) and "+1617" not in str(firms)


def test_manual_status_override_follows_state_machine(h):
    tri = h.triage("c1").json()
    ok = h.post(f"/v1/admin/cases/{tri['case_id']}/status", {"status": "human_review", "note": "caller called back"})
    assert ok.status_code == 200 and ok.json()["status"] == "human_review"
    closed = h.post(f"/v1/admin/cases/{tri['case_id']}/status", {"status": "closed", "note": "resolved"})
    assert closed.status_code == 200
    reopen = h.post(f"/v1/admin/cases/{tri['case_id']}/status", {"status": "triage_ready", "note": "x"})
    assert reopen.status_code == 409


def test_reassign_rejects_ineligible_firm(h):
    tri = h.triage("c1").json()
    r = h.post(f"/v1/admin/cases/{tri['case_id']}/reassign", {"firm_id": "fixture-open-firm", "note": "try"})
    assert r.status_code == 409 and r.json()["detail"]["reason"] == "firm_not_eligible"


def test_failures_view_and_retry(make_harness):
    h = make_harness(now=NIGHT, notification_max_retries=1)
    tri = h.triage("c1").json()
    h.extended(tri["referral_id"], "c1", share=True)
    h.gateways.email.fail_times = 5
    with h.db.sessionmaker() as s:
        process_due(s, h.settings, h.gateways, now=h.clock.now)
    failures = h.get("/v1/admin/failures").json()
    failed = [n for n in failures["notifications"] if n["status"] == "failed"]
    assert len(failed) == 1
    retry = h.post(f"/v1/admin/notifications/{failed[0]['id']}/retry", {})
    assert retry.status_code == 200 and retry.json()["status"] == "pending"
    h.gateways.email.fail_times = 0
    with h.db.sessionmaker() as s:
        process_due(s, h.settings, h.gateways, now=h.clock.now)
    assert len(h.gateways.email.sent) == 1


def test_referral_expiry_sweep(make_harness):
    h = make_harness(now=NIGHT)
    tri = h.triage("c1").json()
    h.extended(tri["referral_id"], "c1", share=False)
    with h.db.sessionmaker() as s:
        assert sweeps.run_all(s, h.settings, NIGHT)["expired_referrals"] == 0
        later = NIGHT + timedelta(hours=h.settings.referral_expiry_hours + 1)
        assert sweeps.run_all(s, h.settings, later)["expired_referrals"] == 1
        assert sweeps.run_all(s, h.settings, later)["expired_referrals"] == 0  # idempotent
        ref = s.get(Referral, __import__("uuid").UUID(tri["referral_id"]))
        assert ref.status == "expired" and ref.case.status == "human_review"


def test_stale_transfer_goes_to_human_review_without_inventing_result(live):
    tri = live.triage("c1").json()
    auth = live.authorize(tri["referral_id"], "c1").json()
    attempt = live.attempt(tri["referral_id"], "c1", auth).json()
    later = live.clock.now + timedelta(minutes=live.settings.stale_transfer_minutes + 1)
    with live.db.sessionmaker() as s:
        assert sweeps.run_all(s, live.settings, later)["stale_transfers"] == 1
    detail = live.get(f"/v1/admin/cases/{tri['case_id']}").json()
    assert detail["status"] == "human_review"
    assert detail["transfer_attempts"][0]["state"] == "requested"
    live.clock.now = later
    unresolved = live.get("/v1/admin/failures").json()["unresolved_transfers"]
    assert unresolved[0]["transfer_attempt_id"] == attempt["transfer_attempt_id"]


def test_caller_pii_encrypted_at_rest(h):
    h.triage("c1")
    with h.db.engine.connect() as conn:
        name, number = conn.execute(text("SELECT name, callback_number FROM callers")).one()
    assert "Alex" not in name and "+12125550100" not in number
    assert h.get("/v1/admin/cases").json()["items"][0]["caller_initials"] == "A.D."


def test_rate_limit_returns_429(make_harness):
    h = make_harness(rate_limit_per_minute=3)
    codes = [h.get("/v1/admin/firms").status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]
    assert h.client.get("/health/live").status_code == 200  # health is never limited


def test_request_id_echoed(h):
    r = h.client.get("/health/live", headers={"X-Request-ID": "abc123"})
    assert r.headers["X-Request-ID"] == "abc123"
    assert h.client.get("/health/live").headers["X-Request-ID"]


def test_notifications_table_untouched_by_failed_auth(h):
    h.client.post("/v1/admin/notifications/00000000-0000-0000-0000-000000000000/retry",
                  headers={"Authorization": "Bearer nope"})
    with h.db.sessionmaker() as s:
        assert s.query(Notification).count() == 0
