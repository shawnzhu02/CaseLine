"""Deterministic matching + timezone-aware availability (spec §4, §20)."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time

from caseline.config import Settings
from caseline.models import Firm, FirmClosure, FirmHours
from caseline.services.availability import compute_availability
from caseline.services.matching import RULE_VERSION, classify, select_firm


def _settings(**kw) -> Settings:
    return Settings(_env_file=None, app_env="test", demo_mode=True, **kw)


def _firm(slug, tz="America/New_York", areas=("X",), live=True, priority=100, hours=((0, 9, 17),), **kw):
    f = Firm(id=uuid.uuid4(), slug=slug, display_name=slug, timezone=tz, jurisdictions=["J"],
             practice_areas=list(areas),
             languages=["en"], accepting_referrals=True, accepting_live_calls=live, routing_priority=priority,
             verification_status="verified", is_demo=False, max_open_referrals=10)
    f.hours = [FirmHours(weekday=d, opens=time(o), closes=time(c)) for d, o, c in hours]
    f.closures = []
    for k, v in kw.items():
        setattr(f, k, v)
    return f


MONDAY_10_NY = datetime(2026, 9, 28, 14, 0, tzinfo=UTC)


def test_selection_is_deterministic_and_explained():
    firms = [_firm("b-firm"), _firm("a-firm")]
    r1 = select_firm(jurisdiction="J", practice_area="X", language="en", firms=firms, open_referral_counts={},
                     now_utc=MONDAY_10_NY, settings=_settings())
    r2 = select_firm(jurisdiction="J", practice_area="X", language="en", firms=list(reversed(firms)),
                     open_referral_counts={}, now_utc=MONDAY_10_NY, settings=_settings())
    assert r1.firm.slug == r2.firm.slug == "a-firm"
    assert r1.rationale["rule_version"] == RULE_VERSION
    assert {c["firm"] for c in r1.rationale["considered"]} == {"a-firm", "b-firm"}


def test_unverified_and_capacity_excluded():
    full = _firm("full")
    unverified = _firm("unv", verification_status="unverified")
    r = select_firm(jurisdiction="J", practice_area="X", language="en", firms=[full, unverified],
                    open_referral_counts={full.id: 10}, now_utc=MONDAY_10_NY, settings=_settings())
    assert r.kind == "none"
    reasons = {c["firm"]: c["excluded_for"] for c in r.rationale["considered"]}
    assert "capacity" in reasons["full"] and "not_verified" in reasons["unv"]


def test_open_firm_preferred_over_closed_for_transfer():
    closed = _firm("a-closed", live=False, priority=1)
    open_ = _firm("b-open", priority=2)
    r = select_firm(jurisdiction="J", practice_area="X", language="en", firms=[closed, open_],
                    open_referral_counts={}, now_utc=MONDAY_10_NY, settings=_settings())
    assert (r.kind, r.firm.slug) == ("transfer", "b-open")


def test_holiday_closure_blocks_live_transfer():
    f = _firm("f")
    f.closures = [FirmClosure(closed_on=date(2026, 9, 28), reason="fictional holiday")]
    a = compute_availability(f, MONDAY_10_NY, _settings())
    assert not a.open_now and "exception" in a.reason


def test_dst_boundary_uses_firm_timezone():
    # US DST ends Sunday 2026-11-01. Firm open Monday 09:00-17:00 New York time.
    f = _firm("f", hours=((0, 9, 17),))
    before = datetime(2026, 10, 26, 13, 30, tzinfo=UTC)  # 09:30 EDT (UTC-4) -> open
    after = datetime(2026, 11, 2, 13, 30, tzinfo=UTC)  # 08:30 EST (UTC-5) -> closed
    assert compute_availability(f, before, _settings()).open_now is True
    assert compute_availability(f, after, _settings()).open_now is False
    assert compute_availability(f, datetime(2026, 11, 2, 14, 30, tzinfo=UTC), _settings()).open_now is True


def test_simulated_availability_is_demo_only_and_labeled():
    f = _firm("demo", is_demo=True, hours=())
    night = datetime(2026, 9, 30, 3, 0, tzinfo=UTC)
    assert compute_availability(f, night, _settings()).open_now is False
    sim = compute_availability(f, night, _settings(demo_simulate_firm_availability=True))
    assert sim.open_now and sim.source == "simulated_demo_availability" and "SIMULATED" in sim.reason
    off = Settings(_env_file=None, app_env="test", demo_mode=False, demo_simulate_firm_availability=True)
    assert compute_availability(f, night, off).open_now is False


def test_classifier():
    assert classify("DEMO_AREA_A", "x") == ("DEMO_AREA_A", "explicit")
    assert classify(None, "the insurer denied my fire claim") == ("property_insurance", "keyword")
    assert classify(None, "landlord insurance") == (None, "ambiguous")
    assert classify(None, "something else") == (None, "none")
