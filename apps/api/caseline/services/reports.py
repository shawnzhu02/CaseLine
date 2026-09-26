"""Versioned referral reports (spec §7) and signed, expiring access links.

A report is built only from structured facts (never a transcript), labels confirmed / caller-stated / unknown
information separately, and is shared only after the caller's share-with-firm consent.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from caseline.config import Settings
from caseline.crypto import DEV_LINK_SECRET, sign_link, verify_link
from caseline.enums import ConsentPurpose, FactProvenance
from caseline.models import Case, CaseFact, Referral, ReportVersion
from caseline.services import consent
from caseline.services.matching import PRACTICE_AREAS
from caseline.services.notifications import case_ref

DISCLAIMER = (
    "Caller-provided information collected by an AI intake assistant. It has not been verified and is not legal "
    "analysis. CaseLine has not established any attorney-client relationship. Please run your own conflicts check "
    "and decide independently whether to accept this matter."
)

_UNKNOWN = {FactProvenance.UNKNOWN, FactProvenance.DECLINED}


def link_secret(settings: Settings) -> str:
    return settings.report_link_secret.get_secret_value() if settings.report_link_secret else DEV_LINK_SECRET


def build_content(session: Session, case: Case, referral: Referral, now: datetime) -> dict:
    facts = list(session.scalars(select(CaseFact).where(CaseFact.case_id == case.id).order_by(CaseFact.key)))
    caller = case.caller
    grouped: dict[str, list[dict]] = {"confirmed": [], "caller_stated": [], "unknown_or_declined": []}
    for f in facts:
        item = {"key": f.key, "value": f.value, "provenance": str(f.provenance)}
        if f.provenance in _UNKNOWN or f.value in (None, ""):
            grouped["unknown_or_declined"].append(item)
        elif f.confirmed or f.provenance == FactProvenance.CALLER_CONFIRMED:
            grouped["confirmed"].append(item)
        else:
            grouped["caller_stated"].append(item)
    return {
        "case_reference": case_ref(case),
        "referral_id": str(referral.id),
        "generated_at": now.isoformat(),
        "case_revision": case.summary_version,
        "firm": referral.firm.display_name,
        "practice_area": case.practice_area,
        "practice_area_label": PRACTICE_AREAS.get(case.practice_area or "", "Unclassified"),
        "practice_area_confidence": case.practice_area_confidence,
        "jurisdiction": case.jurisdiction,
        "urgent": case.urgent,
        "urgency_reason": case.urgency_reason,
        "caller": {
            "name": caller.name if caller else None,
            "callback_number": caller.callback_number if caller else None,
            "callback_confirmed": bool(caller and caller.callback_verified),
            "preferred_language": caller.preferred_language if caller else None,
            "sms_permitted": consent.is_allowed(session, case.id, ConsentPurpose.SMS),
        },
        "facts": grouped,
        "referral_status": str(referral.status),
        "disclaimer": DISCLAIMER,
    }


def snapshot(session: Session, case: Case, referral: Referral, now: datetime) -> ReportVersion | None:
    """Create (or reuse) the report version for the case's current revision. None without share consent."""
    if not consent.is_allowed(session, case.id, ConsentPurpose.SHARE_WITH_SELECTED_FIRM, referral.firm.slug):
        return None
    existing = session.scalar(select(ReportVersion).where(ReportVersion.referral_id == referral.id,
                                                          ReportVersion.case_revision == case.summary_version))
    if existing is not None:
        return existing
    rv = ReportVersion(referral_id=referral.id, case_revision=case.summary_version,
                       content=build_content(session, case, referral, now), created_at=now)
    session.add(rv)
    session.flush()
    return rv


def latest_version(session: Session, referral_id) -> ReportVersion | None:
    return session.scalars(select(ReportVersion).where(ReportVersion.referral_id == referral_id)
                           .order_by(ReportVersion.case_revision.desc())).first()


def make_link(settings: Settings, rv: ReportVersion, ttl_seconds: int, now: datetime) -> str:
    token = sign_link(link_secret(settings), {"rv": str(rv.id)}, ttl_seconds, now=now.timestamp())
    return f"{settings.api_public_base_url.rstrip('/')}/r/{token}"


def resolve_link(settings: Settings, token: str, now: datetime) -> str:
    """Return the report_version id encoded in a valid, unexpired token (raises crypto.LinkError)."""
    return verify_link(link_secret(settings), token, now=now.timestamp())["rv"]
