"""Idempotent seed data: two demo firms sharing one destination (spec §4A, §24) + fictional fixture firms (Phase 1).

Nothing here describes the real people who answer the demo numbers: names are placeholders, categories
are fictional routing fixtures, and hours are invented demo hours.
"""

from __future__ import annotations

from datetime import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from caseline.config import Settings
from caseline.models import Firm, FirmClosure, FirmHours, utcnow

WEEKDAYS = range(0, 5)
ALL_DAYS = range(0, 7)


def demo_firms(settings: Settings) -> list[dict]:
    common = {
        "jurisdictions": ["DEMO_JURISDICTION"],
        "languages": ["en"],
        "accepting_referrals": True,
        "accepting_live_calls": True,
        "is_demo": True,
        "verification_status": "demo_only",
        "timezone": "America/New_York",
        "notes": "DEMO PARTICIPANT - placeholder label; not a verified law firm record.",
        "hours": [(d, time(8, 0), time(20, 0)) for d in ALL_DAYS],  # fictional demo hours
    }
    return [
        {**common, "slug": "demo-firm-a", "display_name": settings.demo_firm_a_name,
         "transfer_number": settings.demo_firm_a_transfer_number, "practice_areas": ["DEMO_AREA_A"],
         "routing_priority": 10},
        {**common, "slug": "demo-firm-b", "display_name": settings.demo_firm_b_name,
         "transfer_number": settings.demo_firm_b_transfer_number, "practice_areas": ["DEMO_AREA_B"],
         "routing_priority": 10},
    ]


# Fictional firms. 555-01XX numbers are reserved for fiction and are never dialable here anyway,
# because authorize-transfer only releases demo destinations on the allowlist.
FIXTURE_FIRMS: list[dict] = [
    {"slug": "fixture-open-firm", "display_name": "Fictional Open Firm (fixture)",
     "jurisdictions": ["FIXTURE_JURISDICTION"], "practice_areas": ["property_insurance"], "languages": ["en"],
     "timezone": "Europe/London", "transfer_number": "+12025550101",
     "referral_email": "intake@fixture-open-firm.invalid", "accepting_referrals": True,
     "accepting_live_calls": True, "verification_status": "verified", "is_fixture": True, "routing_priority": 20,
     "hours": [(d, time(9, 0), time(17, 0)) for d in WEEKDAYS], "closures": []},
    {"slug": "fixture-closed-firm", "display_name": "Fictional Closed Firm (fixture)",
     "jurisdictions": ["FIXTURE_JURISDICTION"], "practice_areas": ["housing"], "languages": ["en"],
     "timezone": "America/Chicago", "transfer_number": "+12025550102",
     "referral_email": "intake@fixture-closed-firm.invalid", "accepting_referrals": True,
     "accepting_live_calls": False, "verification_status": "verified", "is_fixture": True, "routing_priority": 20,
     "hours": [(d, time(9, 0), time(17, 0)) for d in WEEKDAYS], "closures": []},
    {"slug": "fixture-ineligible-firm", "display_name": "Fictional Unverified Firm (fixture)",
     "jurisdictions": ["FIXTURE_JURISDICTION"], "practice_areas": ["property_insurance", "housing"],
     "languages": ["en"], "timezone": "America/New_York", "transfer_number": "+12025550103",
     "accepting_referrals": True, "accepting_live_calls": True, "verification_status": "unverified",
     "is_fixture": True, "routing_priority": 1,
     "hours": [(d, time(0, 0), time(23, 59)) for d in ALL_DAYS], "closures": []},
]


def upsert_firm(session: Session, spec: dict) -> Firm:
    spec = dict(spec)
    hours = spec.pop("hours", [])
    closures = spec.pop("closures", [])
    firm = session.scalar(select(Firm).where(Firm.slug == spec["slug"]))
    if firm is None:
        firm = Firm(slug=spec["slug"])
        session.add(firm)
    for key, value in spec.items():
        setattr(firm, key, value)
    firm.availability_updated_at = utcnow()
    firm.hours = [FirmHours(weekday=d, opens=o, closes=c) for d, o, c in hours]
    firm.closures = [FirmClosure(closed_on=day, reason=reason) for day, reason in closures]
    return firm


def seed(session: Session, settings: Settings, include_fixtures: bool = True) -> list[Firm]:
    specs = demo_firms(settings) + (FIXTURE_FIRMS if include_fixtures else [])
    firms = [upsert_firm(session, s) for s in specs]
    session.commit()
    return firms
