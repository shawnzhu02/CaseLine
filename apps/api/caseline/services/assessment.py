"""Live assessment: turn each caller utterance into structured facts, then recompute a routing view.

Hybrid by design (ADR 0003):
- *Extraction* (what did the caller say?) uses deterministic rules, optionally enriched by Claude with a fixed
  JSON schema. Claude never sees firm data and never chooses a firm.
- *Assessment and routing* (category, matter, urgency, match) are deterministic rules over the extracted facts,
  followed by the same auditable `select_firm` used by triage.

Only extracted facts are stored; utterance text is processed in memory and discarded.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from caseline.services.matching import PRACTICE_AREAS

log = logging.getLogger("caseline.assessment")

ASSESSMENT_RULE_VERSION = "assess-v1"

US_STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA", "colorado": "CO",
    "connecticut": "CT", "delaware": "DE", "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS", "kentucky": "KY", "louisiana": "LA",
    "maine": "ME", "maryland": "MD", "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR", "pennsylvania": "PA",
    "rhode island": "RI", "south carolina": "SC", "south dakota": "SD", "tennessee": "TN", "texas": "TX",
    "utah": "UT", "vermont": "VT", "virginia": "VA", "washington": "WA", "west virginia": "WV",
    "wisconsin": "WI", "wyoming": "WY", "district of columbia": "DC",
}
STATE_NAMES = {v: k.title() for k, v in US_STATES.items()}
# A few well-known cities so "Cambridge" alone is not guessed; a city only counts when paired with a state,
# except these unambiguous ones.
CITY_TO_STATE = {"boston": "MA", "philadelphia": "PA", "chicago": "IL", "houston": "TX"}

INCIDENTS = ("residential_fire", "vehicle_accident", "eviction", "insurance_denial", "workplace_injury",
             "slip_and_fall", "other")
RESPONSIBLE = ("landlord", "property_owner", "employer", "driver", "business", "government", "other")

# Fact keys, types and plain-English labels (for key factors).
FACT_FIELDS: dict[str, str] = {
    "state": "string",  # 2-letter US state code
    "city": "string",
    "incident": "enum",
    "property_damage": "boolean",
    "total_loss": "boolean",
    "injury": "boolean",
    "medical_treatment": "boolean",
    "prior_hazard_reported": "boolean",
    "responsible_party": "enum",
    "responsible_party_notified": "boolean",
    "tenant": "boolean",
    "immediate_danger": "boolean",
    "deadline_mentioned": "boolean",
    "insurance_involved": "boolean",
}

# Question keys the agent may be asked to put to the caller. Yes/no answers are interpreted against the last one.
QUESTIONS: dict[str, tuple[str, list[str]]] = {
    "location": ("Where did this happen? Which city and state?", []),
    "medical": ("Did you or anyone else need medical treatment after this?", ["injury", "medical_treatment"]),
    "prior_hazard": ("Were there any known problems with the property before this happened, and had anyone "
                     "reported them?", ["prior_hazard_reported"]),
    "tenant": ("Do you rent or own the home?", ["tenant"]),
    "danger": ("Is anyone in danger right now?", ["immediate_danger"]),
    "deadline": ("Have you been given any court dates or deadlines?", ["deadline_mentioned"]),
}

MATTER_LABELS = {
    "premises_liability": "Potential Premises Liability",
    "residential_fire": "Residential Fire",
    "vehicle_accident": "Vehicle Accident",
    "eviction": "Eviction",
    "insurance_denial": "Insurance Claim Dispute",
    "workplace_injury": "Workplace Injury",
    "slip_and_fall": "Slip and Fall",
}
CATEGORY_LABELS = {"personal_injury": "Personal Injury", "property_insurance": "Property / Insurance",
                   "housing": "Housing / Tenancy"}


# ---------------------------------------------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------------------------------------------

_YES = re.compile(r"^\s*(yes|yeah|yep|yup|correct|i did|we did|they did|right|uh-huh|sure)\b", re.I)
_NO = re.compile(r"^\s*(no|nope|nah|not really|we didn't|i didn't|none)\b", re.I)


def _has(text: str, *words: str) -> bool:
    """Whole words/phrases only ("fire" must not match "fired")."""
    return any(re.search(rf"\b{re.escape(w)}\b", text) for w in words)


def _stem(text: str, *stems: str) -> bool:
    """Word prefixes, for stems like "injur" (injury, injured) or "evict" (eviction)."""
    return any(re.search(rf"\b{re.escape(w)}", text) for w in stems)


# Longest names first so "west virginia" wins over "virginia".
_STATES_BY_LENGTH = sorted(US_STATES.items(), key=lambda kv: -len(kv[0]))
_EXTRA_STATE_PATTERNS = {"washington dc": "DC", "washington, dc": "DC", "d.c.": "DC"}


def rule_extract(utterance: str, last_question: str | None) -> dict[str, Any]:
    """Deterministic keyword/pattern extraction. Returns only the facts this utterance supports."""
    t = utterance.lower()
    out: dict[str, Any] = {}

    for pattern, code in _EXTRA_STATE_PATTERNS.items():
        if pattern in t:
            out["state"] = code
            break
    else:
        for name, code in _STATES_BY_LENGTH:
            if re.search(rf"\b{name}\b", t):
                out["state"] = code
                break
    m = re.search(r"\b([a-z][a-z .'-]{1,30}),\s*(?:[a-z .]+)$", t.strip(" ."))
    if m and "state" in out:
        out["city"] = m.group(1).strip().title()
    for city, code in CITY_TO_STATE.items():
        if _has(t, city):
            out.setdefault("state", code)
            out.setdefault("city", city.title())

    if _has(t, "fire", "fires", "on fire", "caught fire", "burned", "burnt", "burning", "burned down",
            "smoke damage", "went up in flames"):
        out["incident"] = "residential_fire"
        out["property_damage"] = True
    if _has(t, "everything is gone", "lost everything", "burned down", "total loss", "destroyed"):
        out["property_damage"] = True
        out["total_loss"] = True
    if _has(t, "car accident", "crash", "crashed", "rear-ended", "hit by a car", "collision"):
        out["incident"] = "vehicle_accident"
    if _stem(t, "evict") or _has(t, "notice to quit", "kicked out"):
        out["incident"] = "eviction"
        out["tenant"] = True
    if _has(t, "insurance", "insurer", "claim", "claims"):
        out["insurance_involved"] = True
        if _has(t, "denied", "refused", "won't pay", "rejected"):
            out.setdefault("incident", "insurance_denial")
    if _has(t, "hospital", "emergency room", "er", "ambulance", "doctor", "treated", "treatment", "urgent care"):
        out["medical_treatment"] = True
        out["injury"] = True
    if _stem(t, "injur") or _has(t, "hurt", "burns", "smoke inhalation", "broke my", "broken bone"):
        out["injury"] = True
    if _has(t, "landlord", "landlords", "property manager"):
        out["responsible_party"] = "landlord"
        out["tenant"] = True
    if _has(t, "sparking", "sparked", "faulty", "wiring", "outlet", "outlets", "leak", "leaking", "broken stair",
            "hazard", "smoke detector", "smoke detectors", "problem with", "problems with"):
        if _has(t, "told", "reported", "complained", "notified", "already", "warned", "asked them to fix"):
            out["prior_hazard_reported"] = True
            if "responsible_party" in out:
                out["responsible_party_notified"] = True
    if _has(t, "we rent", "i rent", "renting", "tenant", "tenants", "my lease"):
        out["tenant"] = True
    if _has(t, "we own", "i own", "homeowner", "my own house"):
        out["tenant"] = False
    if _has(t, "court date", "hearing", "deadline", "served with", "summons"):
        out["deadline_mentioned"] = True
    if _has(t, "danger right now", "still on fire", "trapped", "being attacked", "not safe right now"):
        out["immediate_danger"] = True

    # Bare yes/no answers apply only to the question the agent asked immediately before.
    if last_question in QUESTIONS:
        targets = QUESTIONS[last_question][1]
        if _YES.search(t):
            for key in targets:
                out.setdefault(key, True)
        elif _NO.search(t):
            for key in targets:
                out.setdefault(key, False)
    return out


class ClaudeExtractor:
    """Structured fact extraction with Claude (fixed JSON schema). Any failure returns {} and rules carry on."""

    SYSTEM = (
        "You extract facts from one utterance of a caller speaking to CaseLine, a legal intake and referral "
        "line. Return only facts the caller actually stated or clearly implied in THIS utterance, using the "
        "previous question for context when they answer yes or no. Use null for anything not addressed. Do not "
        "assess legal merit, name a type of lawyer, or guess. Record allegations as what the caller said. "
        "state is the two-letter US state code only when the caller named or clearly implied the state."
    )

    def __init__(self, model: str, timeout_seconds: float, api_key: str | None = None) -> None:
        import anthropic  # optional at import time so tests/CI never need it

        self._anthropic = anthropic
        # No retries: the voice agent's request budget is short, and rules already cover the utterance.
        self._client = anthropic.Anthropic(api_key=api_key, timeout=timeout_seconds, max_retries=0)
        self.model = model

    @staticmethod
    def schema() -> dict:
        props: dict[str, Any] = {}
        for key, kind in FACT_FIELDS.items():
            if kind == "boolean":
                props[key] = {"type": ["boolean", "null"]}
            elif key == "incident":
                props[key] = {"type": ["string", "null"], "enum": [*INCIDENTS, None]}
            elif key == "responsible_party":
                props[key] = {"type": ["string", "null"], "enum": [*RESPONSIBLE, None]}
            else:
                props[key] = {"type": ["string", "null"]}
        return {"type": "object", "properties": props, "required": list(FACT_FIELDS), "additionalProperties": False}

    def extract(self, utterance: str, last_question_text: str | None, known: dict[str, Any]) -> dict[str, Any]:
        prompt = (
            f"Previous question from the agent: {last_question_text or '(none)'}\n"
            f"Facts already known: {json.dumps(known, sort_keys=True)}\n"
            f"Caller utterance: {utterance}"
        )
        try:
            response = self._client.messages.create(
                model=self.model,
                max_tokens=1024,
                system=[{"type": "text", "text": self.SYSTEM, "cache_control": {"type": "ephemeral"}}],
                output_config={"format": {"type": "json_schema", "schema": self.schema()}},
                messages=[{"role": "user", "content": prompt}],
            )
        except self._anthropic.APIConnectionError as exc:  # includes timeouts
            log.warning("assessment extractor unavailable: %s", type(exc).__name__)
            return {}
        except self._anthropic.RateLimitError:
            log.warning("assessment extractor rate limited")
            return {}
        except self._anthropic.APIStatusError as exc:
            log.warning("assessment extractor error status=%s", exc.status_code)
            return {}
        if response.stop_reason in ("refusal", "max_tokens"):
            log.warning("assessment extractor stop_reason=%s", response.stop_reason)
            return {}
        text = next((b.text for b in response.content if b.type == "text"), "")
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return {}
        if not isinstance(data, dict):
            return {}
        clean: dict[str, Any] = {}
        for key, value in data.items():
            if key not in FACT_FIELDS or value is None:
                continue
            if key == "state":
                value = str(value).upper()
                if value not in STATE_NAMES:
                    continue
            clean[key] = value
        return clean


# ---------------------------------------------------------------------------------------------------------------
# Assessment
# ---------------------------------------------------------------------------------------------------------------


@dataclass
class Assessment:
    jurisdiction: str | None = None  # e.g. US-MA
    jurisdiction_label: str | None = None
    category: str | None = None
    matter: str | None = None
    urgency: str | None = None
    key_factors: list[str] = field(default_factory=list)
    routing_area: str | None = None  # practice area used for matching (may be narrower than category)
    ready: bool = False
    next_question: str | None = None  # QUESTIONS key
    reasons: dict[str, str] = field(default_factory=dict)


def assess(facts: dict[str, Any]) -> Assessment:
    a = Assessment()
    if facts.get("state"):
        a.jurisdiction = f"US-{facts['state']}"
        a.jurisdiction_label = STATE_NAMES.get(facts["state"], facts["state"])
        a.reasons["jurisdiction"] = "caller named the location"

    injury = facts.get("injury") or facts.get("medical_treatment")
    if injury:
        a.category = "personal_injury"
        a.reasons["category"] = "caller reported an injury / medical treatment"
    elif facts.get("incident") == "eviction":
        a.category = "housing"
        a.reasons["category"] = "caller described an eviction"
    elif facts.get("property_damage") or facts.get("insurance_involved") or facts.get("incident") in (
            "residential_fire", "insurance_denial"):
        a.category = "property_insurance"
        a.reasons["category"] = "caller described property loss"

    a.routing_area = a.category
    if a.category == "personal_injury" and facts.get("prior_hazard_reported") and facts.get("responsible_party") in (
            "landlord", "property_owner", "business"):
        a.matter = MATTER_LABELS["premises_liability"]
        a.routing_area = "premises_liability"
        a.reasons["matter"] = "hazard reported to the responsible party before the incident"
    elif facts.get("incident") in MATTER_LABELS:
        a.matter = MATTER_LABELS[facts["incident"]]

    if facts.get("immediate_danger"):
        a.urgency = "Emergency"
    elif facts.get("medical_treatment") or facts.get("total_loss") or facts.get("deadline_mentioned"):
        a.urgency = "High"
    elif facts:
        a.urgency = "Standard"

    labels = [
        ("incident", "residential_fire", "Residential fire"),
        ("property_damage", True, "Property loss"),
        ("medical_treatment", True, "Hospital / medical treatment"),
        ("injury", True, "Injury reported"),
        ("prior_hazard_reported", True, "Hazard previously reported"),
        ("responsible_party_notified", True, None),
        ("tenant", True, "Caller is a tenant"),
        ("deadline_mentioned", True, "Caller mentioned a date or deadline"),
        ("immediate_danger", True, "Immediate danger"),
    ]
    for key, value, label in labels:
        if facts.get(key) == value:
            if key == "responsible_party_notified":
                party = str(facts.get("responsible_party") or "responsible party").replace("_", " ")
                a.key_factors.append(f"{party.capitalize()} previously notified")
            elif not (key == "injury" and facts.get("medical_treatment")):
                a.key_factors.append(label)

    # What is still missing, in order. The backend decides the question; the model only phrases it.
    if not a.jurisdiction:
        a.next_question = "location"
    elif a.category == "property_insurance" and facts.get("injury") is None and facts.get(
            "medical_treatment") is None:
        a.next_question = "medical"
    elif a.category == "personal_injury" and facts.get("incident") in ("residential_fire", "slip_and_fall") and \
            facts.get("prior_hazard_reported") is None:
        a.next_question = "prior_hazard"

    a.ready = bool(a.jurisdiction and a.category in PRACTICE_AREAS and a.next_question is None
                   and a.urgency != "Emergency")
    return a


def merge_facts(current: dict[str, Any], *deltas: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Apply deltas in order (later wins). Returns the new facts and the list of changed keys."""
    merged = dict(current)
    changed: list[str] = []
    for delta in deltas:
        for key, value in delta.items():
            if key in FACT_FIELDS and value is not None and merged.get(key) != value:
                merged[key] = value
                if key not in changed:
                    changed.append(key)
    return merged, changed


def question_for_text(agent_text: str) -> str | None:
    """Map what our agent just said to a known question key (for yes/no interpretation). None = unrelated."""
    t = agent_text.lower()
    if _has(t, "in danger", "are you safe", "is everyone safe"):
        return "danger"
    if _has(t, "court date", "court dates", "deadline", "deadlines", "hearing"):
        return "deadline"
    if _has(t, "where did this happen", "where did it happen", "which city", "what city", "what state"):
        return "location"
    if _has(t, "known problems", "problems with the property", "reported them", "before the fire", "before this"):
        return "prior_hazard"
    if _has(t, "rent or own"):
        return "tenant"
    if _has(t, "medical", "hospital", "treatment", "treated", "doctor") or _stem(t, "injur"):
        return "medical"
    return None
