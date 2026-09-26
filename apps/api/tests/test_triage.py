"""Triage branches (spec §10): open, after-hours, no-match, ambiguous, danger, deadline, consent denied."""

from __future__ import annotations

from caseline.models import Caller, CaseFact, ConsentEvent, Referral
from tests.conftest import NIGHT


def test_open_fixture_firm_offers_transfer(h):
    r = h.triage("c1", practice_area="property_insurance", jurisdiction="FIXTURE_JURISDICTION").json()
    assert r["action"] == "transfer"
    assert r["selected_firm"]["firm_id"] == "fixture-open-firm"
    assert r["requires_transfer_consent"] is True


def test_production_firm_is_not_dialable_in_this_build(live):
    r = live.triage("c1", practice_area="property_insurance", jurisdiction="FIXTURE_JURISDICTION").json()
    auth = live.authorize(r["referral_id"], "c1")
    assert auth.status_code == 409
    assert auth.json()["detail"]["reason"] == "production_transfer_not_configured"


def test_after_hours_routes_to_extended_intake(make_harness):
    h = make_harness(now=NIGHT)
    r = h.triage("c1", practice_area="DEMO_AREA_A").json()
    assert r["action"] == "extended_intake"
    assert r["selected_firm"]["firm_id"] == "demo-firm-a"
    assert r["referral_id"] is not None
    assert r["extended_intake_questions"]
    assert "No lawyer has been arranged" in r["next_prompt"]


def test_closed_live_calls_firm_routes_async(h):
    r = h.triage("c1", practice_area="housing", jurisdiction="FIXTURE_JURISDICTION").json()
    assert r["action"] == "extended_intake"
    assert r["selected_firm"]["firm_id"] == "fixture-closed-firm"


def test_no_match_routes_to_human_review_or_no_eligible(h):
    r = h.triage("c1", practice_area="DEMO_AREA_A", jurisdiction="NOWHERE").json()
    assert r["action"] == "no_eligible_firm"
    assert r["selected_firm"] is None and r["referral_id"] is None
    with h.db.sessionmaker() as s:
        assert s.query(Referral).count() == 0


def test_ambiguous_category_goes_to_human_review(h):
    r = h.triage("c1", practice_area=None, jurisdiction="FIXTURE_JURISDICTION",
                 issue_summary="My landlord's insurance claim after the fire").json()
    assert r["action"] == "human_review"
    assert r["selected_firm"] is None


def test_unknown_category_goes_to_human_review(h):
    r = h.triage("c1", practice_area="made_up_area").json()
    assert r["action"] == "human_review"


def test_keyword_classification_is_provisional_but_routes(h):
    r = h.triage("c1", practice_area=None, jurisdiction="FIXTURE_JURISDICTION",
                 issue_summary="House fire and the insurer denied my claim").json()
    assert r["action"] == "transfer"
    assert r["selected_firm"]["firm_id"] == "fixture-open-firm"


def test_immediate_danger_gives_emergency_guidance(h):
    r = h.triage("c1", immediate_danger=True).json()
    assert r["action"] == "emergency_guidance"
    assert "911" in r["next_prompt"]
    assert r["selected_firm"] is None


def test_imminent_deadline_escalates_without_calculating(h):
    r = h.triage("c1", deadline="court date tomorrow").json()
    assert r["action"] == "human_review"
    assert "can't tell you how much time" in r["next_prompt"]


def test_intake_consent_denied_stores_nothing(h):
    r = h.triage("c1", intake=False).json()
    assert r["action"] == "consent_required"
    with h.db.sessionmaker() as s:
        assert s.query(Caller).count() == 0
        assert s.query(CaseFact).count() == 0
        denials = s.query(ConsentEvent).all()
        assert [(d.purpose, d.allowed) for d in denials] == [("intake", False)]


def test_demo_firms_inert_outside_demo_mode(make_harness):
    h = make_harness(demo_mode=False)
    r = h.triage("c1", practice_area="DEMO_AREA_A").json()
    assert r["action"] == "no_eligible_firm"


def test_second_triage_for_same_call_rejected(h):
    h.triage("c1")
    from tests.conftest import triage_body

    r = h.post("/v1/intake/triage", triage_body("c1", "DEMO_AREA_B", "DEMO_JURISDICTION"), key="different")
    assert r.status_code == 409
