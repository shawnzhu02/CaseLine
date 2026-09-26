"""Outbox worker: Guava-only SMS, mock email, idempotency, retries, firm decisions (spec §7, §25)."""

from __future__ import annotations

from datetime import timedelta

from caseline.models import Case, Notification, Referral
from caseline.workers.outbox import process_due
from tests.conftest import NIGHT


def _after_hours_referral(h, *, sms=True, share=True, callback="+12125550100"):
    tri = h.triage("c1", callback=callback).json()
    r = h.extended(tri["referral_id"], "c1", share=share, sms=sms)
    assert r.status_code == 200, r.text
    return tri, r.json()


def _run(h):
    with h.db.sessionmaker() as s:
        return process_due(s, h.settings, h.gateways, now=h.clock.now)


def test_after_hours_sends_mock_notifications_without_calling(make_harness):
    h = make_harness(now=NIGHT, guava_sms_enabled=True, guava_sms_from_number="+12025550199")
    tri, ext = _after_hours_referral(h)
    assert ext["referral_status"] == "pending_async"
    assert "No lawyer has been engaged" in ext["next_prompt"] or "no lawyer has been engaged" in ext["next_prompt"]
    assert _run(h) == 2
    assert len(h.gateways.email.sent) == 1
    assert len(h.gateways.sms.sent) == 1
    sms = h.gateways.sms.sent[0]
    assert sms["to"] == "+12125550100" and "STOP" in sms["message"]
    assert "+12676804795" not in str(h.gateways.sms.sent) and "+16173187562" not in str(h.gateways.sms.sent)
    email = h.gateways.email.sent[0]
    assert email["to"].endswith(".invalid")  # demo firm alerts are mocked, never real inboxes
    assert "Fictional demo matter" not in email["text"]  # minimum necessary: no narrative
    with h.db.sessionmaker() as s:
        assert s.get(Referral, __import__("uuid").UUID(tri["referral_id"])).status == "firm_notified"
        statuses = {n.channel: n.status for n in s.query(Notification)}
        assert statuses == {"sms": "submitted", "email": "submitted"}  # never "delivered"


def test_sms_disabled_does_not_call_guava_client(make_harness):
    h = make_harness(now=NIGHT)  # GUAVA_SMS_ENABLED defaults to false
    _, ext = _after_hours_referral(h)
    assert "text" not in ext["next_prompt"]  # never promise an SMS that cannot be sent
    _run(h)
    assert h.gateways.sms.sent == []
    with h.db.sessionmaker() as s:
        n = s.query(Notification).filter_by(channel="sms").one()
        assert (n.status, n.status_reason) == ("blocked", "guava_sms_disabled")


def test_sms_not_sent_without_consent(make_harness):
    h = make_harness(now=NIGHT, guava_sms_enabled=True, guava_sms_from_number="+12025550199")
    _after_hours_referral(h, sms=False)
    _run(h)
    assert h.gateways.sms.sent == []
    with h.db.sessionmaker() as s:
        assert s.query(Notification).filter_by(channel="sms").count() == 0


def test_sms_never_sent_to_demo_lawyer_numbers(make_harness):
    h = make_harness(now=NIGHT, guava_sms_enabled=True, guava_sms_from_number="+12025550199")
    _after_hours_referral(h, callback="+12676804795")
    _run(h)
    assert h.gateways.sms.sent == []
    with h.db.sessionmaker() as s:
        assert s.query(Notification).filter_by(channel="sms").one().status_reason == "destination_is_transfer_target"


def test_share_declined_means_no_firm_email(make_harness):
    h = make_harness(now=NIGHT)
    _, ext = _after_hours_referral(h, share=False)
    _run(h)
    assert h.gateways.email.sent == []
    assert "hasn't been sent them" in ext["next_prompt"]


def test_worker_restart_mid_notification_is_retryable_and_idempotent(make_harness):
    h = make_harness(now=NIGHT)
    _after_hours_referral(h, sms=False)
    h.gateways.email.fail_times = 1
    _run(h)
    assert h.gateways.email.sent == []
    with h.db.sessionmaker() as s:
        n = s.query(Notification).one()
        assert (n.status, n.retry_count) == ("pending", 1)
    assert _run(h) == 0  # backoff not yet elapsed
    h.clock.now += timedelta(seconds=h.settings.notification_backoff_base_seconds)
    assert _run(h) == 1
    assert _run(h) == 0  # already submitted: never re-sent
    assert len(h.gateways.email.sent) == 1


def test_retry_exhaustion_marks_failed(make_harness):
    h = make_harness(now=NIGHT, notification_max_retries=2, notification_backoff_base_seconds=1)
    _after_hours_referral(h, sms=False)
    h.gateways.email.fail_times = 10
    for _ in range(3):
        _run(h)
        h.clock.now += timedelta(seconds=10)
    with h.db.sessionmaker() as s:
        n = s.query(Notification).one()
        assert (n.status, n.status_reason) == ("failed", "retries_exhausted")


def test_firm_accept_and_decline_are_distinct(make_harness):
    h = make_harness(now=NIGHT)
    tri, _ = _after_hours_referral(h, sms=False)
    _run(h)
    r = h.post(f"/v1/referrals/{tri['referral_id']}/status", {"status": "accepted"})
    assert r.status_code == 200
    assert r.json() == {"referral_id": tri["referral_id"], "status": "accepted", "case_status": "firm_accepted"}
    again = h.post(f"/v1/referrals/{tri['referral_id']}/status", {"status": "declined"})
    assert again.status_code == 409
    with h.db.sessionmaker() as s:
        assert s.query(Case).one().status == "firm_accepted"
