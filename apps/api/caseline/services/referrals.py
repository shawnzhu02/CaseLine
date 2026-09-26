"""Async referral path: extended intake, firm/caller notifications, firm decisions (spec §1 step 6-8)."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from caseline.audit import audit
from caseline.config import Settings
from caseline.enums import CaseStatus, ConsentPurpose, JobType, ReferralStatus
from caseline.errors import conflict, not_found
from caseline.models import CallSession, Case, CaseFact, Referral
from caseline.schemas import (
    ExtendedIntakeRequest,
    ExtendedIntakeResponse,
    FactIn,
    NotificationOut,
    TriageConsents,
)
from caseline.services import consent
from caseline.services.lifecycle import set_case_status, set_referral_status
from caseline.services.notifications import enqueue

PENDING_PROMPT = (
    "Thank you. I've passed your information to {firm}. They will review it and decide independently whether "
    "they can help; no lawyer has been engaged yet. {contact}"
)


def upsert_facts(session: Session, case: Case, facts: dict[str, FactIn]) -> None:
    existing = {f.key: f for f in session.scalars(select(CaseFact).where(CaseFact.case_id == case.id))}
    for key, fact in facts.items():
        row = existing.get(key)
        if row is None:
            session.add(CaseFact(case_id=case.id, key=key, value=fact.value, provenance=fact.provenance,
                                 confirmed=fact.confirmed))
        else:
            row.value, row.provenance, row.confirmed = fact.value, fact.provenance, fact.confirmed
    case.summary_version += 1


def _record_consents(session: Session, case: Case, call: CallSession | None, c: TriageConsents) -> None:
    for purpose, value in [(ConsentPurpose.SHARE_WITH_SELECTED_FIRM, c.share_with_selected_firm),
                           (ConsentPurpose.SMS, c.sms), (ConsentPurpose.EMAIL, c.email)]:
        consent.record(session, purpose=purpose, allowed=value, case_id=case.id, caller_id=case.caller_id,
                       call_session_id=call.id if call else None)


def submit_extended_intake(session: Session, *, referral_id, req: ExtendedIntakeRequest, settings: Settings,
                           now: datetime) -> ExtendedIntakeResponse:
    referral = session.get(Referral, referral_id)
    if referral is None:
        raise not_found("referral")
    case = referral.case
    call = session.scalar(select(CallSession).where(CallSession.provider_call_id == req.provider_call_id))
    if call is None or call.case_id != case.id:
        raise conflict("call_mismatch", "referral does not belong to this call")

    if req.consents is not None:
        _record_consents(session, case, call, req.consents)
    upsert_facts(session, case, req.facts)

    if case.status in (CaseStatus.TRANSFER_PENDING, CaseStatus.TRANSFER_FAILED):
        set_case_status(session, case, CaseStatus.EXTENDED_INTAKE)
    set_case_status(session, case, CaseStatus.REFERRAL_PENDING)
    set_referral_status(session, referral, ReferralStatus.PENDING_ASYNC)
    referral.expires_at = now + timedelta(hours=settings.referral_expiry_hours)

    notes = []
    firm = referral.firm
    share_ok = consent.is_allowed(session, case.id, ConsentPurpose.SHARE_WITH_SELECTED_FIRM)
    if share_ok:
        referral.share_consent_id = consent.latest(session, case.id, ConsentPurpose.SHARE_WITH_SELECTED_FIRM).id
        # Firm alerts are mocked for demo firms: the demo numbers' owners never receive real case email.
        dest = firm.referral_email or f"mock+{firm.slug}@caseline.invalid"
        notes.append(enqueue(session, event_key=f"referral:{referral.id}:firm_alert:v{case.summary_version}",
                             job_type=JobType.SEND_FIRM_EMAIL, case=case, referral=referral, destination=dest,
                             template="firm_referral_alert", now=now))
    caller = case.caller
    if caller and caller.callback_number and consent.is_allowed(session, case.id, ConsentPurpose.SMS):
        notes.append(enqueue(session, event_key=f"referral:{referral.id}:caller_pending_sms",
                             job_type=JobType.SEND_GUAVA_SMS, case=case, referral=referral,
                             destination=caller.callback_number, template="caller_referral_pending", now=now))
    audit(session, operation="referral.extended_intake", target_type="referral", target_id=referral.id,
          result="pending_async", reason=req.reason, share_consent=share_ok)

    # Only mention a text if one can actually be submitted (SMS enabled + consent + a queued row).
    will_text = settings.guava_sms_enabled and any(n.job_type == JobType.SEND_GUAVA_SMS for n in notes)
    contact = ("We'll send you a text confirmation." if will_text
               else "Our team will follow up using the contact details you gave.")
    if not share_ok:
        contact = ("Because you didn't give permission to share your details, the firm hasn't been sent them. "
                   "Our team will follow up.")
    return ExtendedIntakeResponse(
        case_id=case.id, referral_id=referral.id, case_status=case.status, referral_status=referral.status,
        notifications=[NotificationOut.model_validate(n) for n in notes],
        next_prompt=PENDING_PROMPT.format(firm=firm.display_name, contact=contact),
    )


def update_referral_status(session: Session, *, referral_id, status: str, actor: str, now: datetime) -> Referral:
    referral = session.get(Referral, referral_id)
    if referral is None:
        raise not_found("referral")
    case = referral.case
    target = {"accepted": ReferralStatus.ACCEPTED, "declined": ReferralStatus.DECLINED,
              "expired": ReferralStatus.EXPIRED}[status]
    set_referral_status(session, referral, target)
    if target == ReferralStatus.ACCEPTED:
        set_case_status(session, case, CaseStatus.FIRM_ACCEPTED)
    elif target == ReferralStatus.DECLINED:
        set_case_status(session, case, CaseStatus.FIRM_DECLINED)
    else:
        set_case_status(session, case, CaseStatus.HUMAN_REVIEW)
    audit(session, operation="referral.firm_decision", target_type="referral", target_id=referral.id,
          result=status, actor=actor, role="operator")
    caller = case.caller
    if target != ReferralStatus.EXPIRED and caller and caller.callback_number and consent.is_allowed(
            session, case.id, ConsentPurpose.SMS):
        enqueue(session, event_key=f"referral:{referral.id}:caller_{status}_sms", job_type=JobType.SEND_GUAVA_SMS,
                case=case, referral=referral, destination=caller.callback_number,
                template=f"caller_referral_{status}", now=now)
    return referral
