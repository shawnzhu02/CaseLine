"""Deterministic, auditable firm selection (spec §4, §20). No LLM ranking."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from caseline.config import Settings
from caseline.models import Firm
from caseline.services.availability import compute_availability

RULE_VERSION = "match-v1"

# Controlled practice-area vocabulary. DEMO_AREA_* are fictional routing fixtures only and do
# NOT describe the real-world specialties of the people answering the demo numbers.
PRACTICE_AREAS: dict[str, str] = {
    "DEMO_AREA_A": "Fictional demo matter routed to Demo Partner Firm A",
    "DEMO_AREA_B": "Fictional demo matter routed to Demo Partner Firm B",
    "property_insurance": "Property damage / insurance dispute",
    "housing": "Housing / tenancy",
    "personal_injury": "Personal injury",
}

# Conservative keyword map for a *provisional* category when the agent could not pick one.
# Ambiguous (0 or 2+ matching categories) goes to human review.
_KEYWORDS: dict[str, tuple[str, ...]] = {
    "property_insurance": ("insurance", "insurer", "claim", "fire", "flood", "property damage"),
    "housing": ("evict", "eviction", "landlord", "tenant", "lease", "rent"),
}

EXTENDED_INTAKE_QUESTIONS: dict[str, list[tuple[str, str]]] = {
    "property_insurance": [
        ("event_date", "Roughly when did this happen?"),
        ("owner_or_tenant", "Are you the owner or a tenant of the property?"),
        ("anyone_unsafe", "Is anyone currently unsafe or without somewhere to stay?"),
        ("insurer_and_claim", "Who is your insurer, and have you filed a claim?"),
        ("adverse_decision", "Have you received a decision or letter from the insurer?"),
        ("notices_or_deadlines", "Have you received any legal notices or dates you were told about?"),
        ("emergency_services_involved", "Were police or fire services involved?"),
        ("contact_preferences", "What's the best time to reach you, and may we leave voicemail or text?"),
    ],
    "personal_injury": [
        ("event_date", "Roughly when did this happen?"),
        ("injuries_and_treatment", "What injuries were there, and where were you treated?"),
        ("responsible_party", "Who was responsible for the property or situation?"),
        ("prior_reports", "Had anyone reported the problem before it happened? When and how?"),
        ("notices_or_deadlines", "Have you received any legal notices or dates you were told about?"),
        ("contact_preferences", "What's the best time to reach you, and may we leave voicemail or text?"),
    ],
    "housing": [
        ("notice_received", "Have you received any written notice from your landlord or a court?"),
        ("notice_dates", "Are there any dates on that notice?"),
        ("contact_preferences", "What's the best time to reach you, and may we leave voicemail or text?"),
    ],
}
DEFAULT_QUESTIONS: list[tuple[str, str]] = [
    ("event_date", "Roughly when did this happen?"),
    ("notices_or_deadlines", "Have you been told about any dates or deadlines?"),
    ("contact_preferences", "What's the best time to reach you, and may we leave voicemail or text?"),
]


def classify(practice_area: str | None, issue_summary: str) -> tuple[str | None, str]:
    """Return (practice_area, confidence) where confidence is explicit|keyword|none|ambiguous."""
    if practice_area:
        return (practice_area, "explicit") if practice_area in PRACTICE_AREAS else (None, "none")
    text = issue_summary.lower()
    hits = [area for area, words in _KEYWORDS.items() if any(w in text for w in words)]
    if len(hits) == 1:
        return hits[0], "keyword"
    return None, "ambiguous" if hits else "none"


@dataclass
class MatchResult:
    kind: str  # transfer | async | none
    firm: Firm | None
    rationale: dict = field(default_factory=dict)
    availability_source: str | None = None


def select_firm(
    *,
    jurisdiction: str,
    practice_area: str,
    language: str | None,
    firms: list[Firm],
    open_referral_counts: dict,
    now_utc: datetime,
    settings: Settings,
) -> MatchResult:
    considered: list[dict] = []
    eligible: list[Firm] = []
    for firm in firms:
        reasons = []
        if firm.is_demo and not settings.demo_mode:
            reasons.append("demo_firm_outside_demo_mode")
        if not firm.is_demo and firm.verification_status != "verified":
            reasons.append("not_verified")
        if not firm.accepting_referrals:
            reasons.append("not_accepting_referrals")
        if jurisdiction not in firm.jurisdictions:
            reasons.append("jurisdiction")
        if practice_area not in firm.practice_areas:
            reasons.append("practice_area")
        if language and firm.languages and language not in firm.languages:
            reasons.append("language")
        if open_referral_counts.get(firm.id, 0) >= firm.max_open_referrals:
            reasons.append("capacity")
        considered.append({"firm": firm.slug, "eligible": not reasons, "excluded_for": reasons})
        if not reasons:
            eligible.append(firm)

    rationale: dict = {
        "rule_version": RULE_VERSION,
        "inputs": {"jurisdiction": jurisdiction, "practice_area": practice_area, "language": language},
        "considered": considered,
        "ordering": "routing_priority asc, slug asc (stable; round-robin pointer is a later iteration)",
    }
    if not eligible:
        return MatchResult("none", None, rationale)

    eligible.sort(key=lambda f: (f.routing_priority, f.slug))
    for firm in eligible:
        avail = compute_availability(firm, now_utc, settings)
        if avail.accepting_live_calls:
            rationale["selected"] = {"firm": firm.slug, "route": "transfer", "availability": avail.reason}
            return MatchResult("transfer", firm, rationale, avail.source)

    first = eligible[0]
    avail = compute_availability(first, now_utc, settings)
    rationale["selected"] = {"firm": first.slug, "route": "async", "availability": avail.reason}
    return MatchResult("async", first, rationale, avail.source)
