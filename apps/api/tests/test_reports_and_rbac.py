"""Roles, firm isolation, versioned reports and signed links (spec §7, §8, §10 'wrong firm' row)."""

from __future__ import annotations

from datetime import timedelta

from caseline.models import AuditEvent, Notification, ReportVersion
from caseline.workers.outbox import process_due
from tests.conftest import NIGHT


def _shared_referral(h, area="DEMO_AREA_A", call="c1", share=True):
    tri = h.triage(call, practice_area=area).json()
    r = h.extended(tri["referral_id"], call, share=share,
                   facts={"event_date": {"value": "last week", "provenance": "caller_confirmed", "confirmed": True},
                          "insurer": {"value": None, "provenance": "declined"}})
    assert r.status_code == 200, r.text
    return tri


def test_service_token_cannot_use_operator_endpoints(h):
    assert h.get("/v1/admin/cases", as_="service").status_code == 403
    assert h.get("/v1/admin/firms", as_="firm_a").status_code == 403


def test_firm_user_cannot_use_voice_endpoints(h):
    tri = h.triage("c1").json()
    r = h.post(f"/v1/referrals/{tri['referral_id']}/authorize-transfer",
               {"provider_call_id": "c1", "caller_consented": True}, key="k", as_="firm_a")
    assert r.status_code == 403


def test_unknown_or_deactivated_token_rejected(h):
    r = h.client.get("/v1/admin/cases", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


def test_report_versioned_with_provenance_groups(make_harness):
    h = make_harness(now=NIGHT)
    tri = _shared_referral(h)
    report = h.get(f"/v1/referrals/{tri['referral_id']}/report", as_="firm_a")
    assert report.status_code == 200
    content = report.json()["content"]
    assert [f["key"] for f in content["facts"]["confirmed"]] == ["event_date"]
    assert "insurer" in [f["key"] for f in content["facts"]["unknown_or_declined"]]
    assert "issue_summary" in [f["key"] for f in content["facts"]["caller_stated"]]
    assert "not legal analysis" in content["disclaimer"]


def test_wrong_firm_report_access_denied_and_logged(make_harness):
    h = make_harness(now=NIGHT)
    tri = _shared_referral(h)  # Firm A's referral
    r = h.get(f"/v1/referrals/{tri['referral_id']}/report", as_="firm_b")
    assert r.status_code == 404  # indistinguishable from "does not exist"
    assert h.post(f"/v1/referrals/{tri['referral_id']}/status", {"status": "declined"}, as_="firm_b").status_code == 404
    assert h.get("/v1/firm/referrals", as_="firm_b").json()["items"] == []
    with h.db.sessionmaker() as s:
        assert s.query(AuditEvent).filter_by(result="denied_cross_firm").count() == 2


def test_no_report_without_share_consent(make_harness):
    h = make_harness(now=NIGHT)
    tri = _shared_referral(h, share=False)
    assert h.get(f"/v1/referrals/{tri['referral_id']}/report", as_="firm_a").status_code == 404
    with h.db.sessionmaker() as s:
        assert s.query(ReportVersion).count() == 0


def test_share_consent_is_firm_specific(make_harness):
    """Consent to share with Firm A must not allow sharing with Firm B after reassignment."""
    h = make_harness(now=NIGHT)
    tri = _shared_referral(h)
    with h.db.sessionmaker() as s:
        from caseline.models import Firm

        s.query(Firm).filter_by(slug="demo-firm-b").one().practice_areas = ["DEMO_AREA_A"]
        s.commit()
    re = h.post(f"/v1/admin/cases/{tri['case_id']}/reassign", {"firm_id": "demo-firm-b", "note": "Firm A declined"})
    assert re.status_code == 200, re.text
    new_ref = re.json()["referral_id"]
    blocked = h.post(f"/v1/admin/referrals/{new_ref}/send", {})
    assert blocked.status_code == 409 and blocked.json()["detail"]["reason"] == "share_consent_missing"
    ok = h.post(f"/v1/admin/cases/{tri['case_id']}/consents",
                {"purpose": "share_with_selected_firm", "allowed": True, "firm_id": "demo-firm-b"})
    assert ok.status_code == 200
    sent = h.post(f"/v1/admin/referrals/{new_ref}/send", {})
    assert sent.status_code == 200 and sent.json()["status"] == "pending_async"


def test_signed_link_opens_report_and_expires(make_harness):
    h = make_harness(now=NIGHT)
    tri = _shared_referral(h)
    link = h.post(f"/v1/referrals/{tri['referral_id']}/report-link", {}, as_="firm_a").json()
    path = link["url"].split("://", 1)[1].split("/", 1)[1]
    page = h.client.get("/" + path)
    assert page.status_code == 200
    assert "Referral" in page.text and "Alex Demo" in page.text
    assert page.headers["cache-control"] == "no-store"
    h.clock.now += timedelta(minutes=h.settings.report_link_ttl_minutes + 1)
    assert h.client.get("/" + path).status_code == 410
    tampered = "/" + path[:-3] + ("AAA" if not path.endswith("AAA") else "BBB")
    assert h.client.get(tampered).status_code == 410


def test_firm_email_contains_link_not_narrative(make_harness):
    h = make_harness(now=NIGHT)
    _shared_referral(h)
    with h.db.sessionmaker() as s:
        process_due(s, h.settings, h.gateways, now=h.clock.now)
    email = h.gateways.email.sent[0]
    assert "/r/" in email["text"] and "Fictional demo matter" not in email["text"]
    assert "Alex" not in email["text"]


def test_fact_edit_after_sharing_creates_new_version_and_one_update_alert(make_harness):
    h = make_harness(now=NIGHT)
    tri = _shared_referral(h)
    body = {"facts": {"insurer": {"value": "Example Mutual", "provenance": "caller_stated"}}}
    r1 = h.patch(f"/v1/admin/cases/{tri['case_id']}/facts", body)
    assert r1.status_code == 200 and r1.json()["firm_updates_queued"] == 1
    with h.db.sessionmaker() as s:
        versions = sorted(v.case_revision for v in s.query(ReportVersion))
        assert len(versions) == 2
        keys = sorted(n.event_key for n in s.query(Notification).filter_by(channel="email"))
        assert len(keys) == 2 and keys[0] != keys[1]
    report = h.get(f"/v1/referrals/{tri['referral_id']}/report", as_="firm_a").json()
    stated = {f["key"]: f["value"] for f in report["content"]["facts"]["caller_stated"]}
    assert stated["insurer"] == "Example Mutual"
    assert report["case_revision"] == max(versions)
