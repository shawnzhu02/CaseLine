"""Operator endpoints backing the admin dashboard (spec §8). Every sensitive read and mutation is audited."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from caseline.audit import audit
from caseline.auth import OPERATOR, Principal, require_roles
from caseline.config import Settings
from caseline.deps import get_clock, get_session, get_settings_dep
from caseline.enums import CaseStatus, ConsentPurpose, NotificationStatus, ReferralStatus, TransferAttemptState
from caseline.errors import conflict, not_found
from caseline.models import (
    AuditEvent,
    CallSession,
    Case,
    ConsentEvent,
    Firm,
    Notification,
    Referral,
    TransferAttempt,
    utcnow,
)
from caseline.schemas import (
    AdminCaseRow,
    AdminCasesPage,
    AvailabilityOut,
    CaseStatusOverride,
    FactsPatch,
    FirmOut,
    FirmPatch,
    OperatorConsent,
    ReassignRequest,
)
from caseline.services import consent
from caseline.services.availability import compute_availability
from caseline.services.intake import ACTIVE_REFERRAL_STATES
from caseline.services.lifecycle import set_case_status, set_referral_status
from caseline.services.matching import RULE_VERSION
from caseline.services.referrals import dispatch_referral, share_allowed, update_facts

router = APIRouter(prefix="/v1")

REVIEW_STATES = (CaseStatus.HUMAN_REVIEW, CaseStatus.NO_ELIGIBLE_FIRM, CaseStatus.TRANSFER_FAILED,
                 CaseStatus.FIRM_DECLINED)


def _mask(number: str | None) -> str | None:
    return None if not number else f"***{number[-2:]}"


def _initials(name: str | None) -> str | None:
    return None if not name else "".join(p[0].upper() + "." for p in name.split() if p)


def _get_case(session: Session, case_id: uuid.UUID) -> Case:
    case = session.get(Case, case_id)
    if case is None:
        raise not_found("case")
    return case


@router.get("/admin/cases", response_model=AdminCasesPage)
def list_cases(
    status: CaseStatus | None = None,
    queue: str | None = Query(None, pattern="^(review|urgent|open)$"),
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    principal: Principal = Depends(OPERATOR),
    session: Session = Depends(get_session),
):
    q = select(Case)
    if status:
        q = q.where(Case.status == status)
    if queue == "review":
        q = q.where(Case.status.in_(REVIEW_STATES))
    elif queue == "urgent":
        q = q.where(Case.urgent.is_(True), Case.status != CaseStatus.CLOSED)
    elif queue == "open":
        q = q.where(Case.status != CaseStatus.CLOSED)
    total = session.scalar(select(func.count()).select_from(q.subquery()))
    rows = session.scalars(q.order_by(Case.urgent.desc(), Case.created_at.desc()).limit(limit).offset(offset)).all()
    items = [
        AdminCaseRow(
            case_id=c.id, status=c.status, jurisdiction=c.jurisdiction, practice_area=c.practice_area,
            urgent=c.urgent, caller_initials=_initials(c.caller.name if c.caller else None),
            callback_masked=_mask(c.caller.callback_number if c.caller else None),
            latest_referral_status=c.referrals[-1].status if c.referrals else None, created_at=c.created_at,
        )
        for c in rows
    ]
    audit(session, operation="admin.list_cases", target_type="case", target_id=None, result="ok",
          actor=principal.name, role=principal.role, count=len(items))
    session.commit()
    return AdminCasesPage(items=items, total=total or 0, limit=limit, offset=offset)


@router.get("/admin/cases/{case_id}")
def case_detail(case_id: uuid.UUID, principal: Principal = Depends(OPERATOR),
                session: Session = Depends(get_session)):
    """Full case view including caller contact details (audited sensitive read)."""
    case = _get_case(session, case_id)
    caller = case.caller
    consents = session.scalars(select(ConsentEvent).where(ConsentEvent.case_id == case.id)
                               .order_by(ConsentEvent.captured_at)).all()
    calls = session.scalars(select(CallSession).where(CallSession.case_id == case.id)).all()
    referral_ids = [r.id for r in case.referrals]
    attempts = session.scalars(select(TransferAttempt).where(TransferAttempt.referral_id.in_(referral_ids))
                               .order_by(TransferAttempt.attempted_at)).all() if referral_ids else []
    notes = session.scalars(select(Notification).where(Notification.case_id == case.id)
                            .order_by(Notification.created_at)).all()
    history = session.scalars(select(AuditEvent).where(AuditEvent.target_id.in_(
        [str(case.id), *map(str, referral_ids), *[str(a.id) for a in attempts]])).order_by(AuditEvent.created_at)
        .limit(200)).all()
    audit(session, operation="admin.read_case", target_type="case", target_id=case.id, result="ok",
          actor=principal.name, role=principal.role)
    session.commit()
    return {
        "case_id": str(case.id), "status": case.status, "jurisdiction": case.jurisdiction,
        "practice_area": case.practice_area, "practice_area_confidence": case.practice_area_confidence,
        "urgent": case.urgent, "urgency_reason": case.urgency_reason, "routing_action": case.routing_action,
        "summary_version": case.summary_version, "created_at": case.created_at.isoformat(),
        "caller": None if caller is None else {
            "name": caller.name, "callback_number": caller.callback_number,
            "callback_verified": caller.callback_verified, "preferred_language": caller.preferred_language,
            "sms_opted_out": caller.sms_opted_out},
        "facts": [{"key": f.key, "value": f.value, "provenance": f.provenance, "confirmed": f.confirmed}
                  for f in case.facts],
        "consents": [{"purpose": c.purpose, "allowed": c.allowed, "firm": c.subject_firm_slug,
                      "policy_version": c.policy_version, "captured_at": c.captured_at.isoformat()}
                     for c in consents],
        "calls": [{"provider_call_id": c.provider_call_id, "state": c.state,
                   "termination_reason": c.termination_reason, "started_at": c.started_at.isoformat(),
                   "ended_at": c.ended_at.isoformat() if c.ended_at else None} for c in calls],
        "referrals": [{"referral_id": str(r.id), "firm_id": r.firm.slug, "firm_name": r.firm.display_name,
                       "status": r.status, "rule_version": r.selection_rule_version,
                       "rationale": r.selection_rationale, "created_at": r.created_at.isoformat(),
                       "expires_at": r.expires_at.isoformat() if r.expires_at else None} for r in case.referrals],
        "transfer_attempts": [{"transfer_attempt_id": str(a.id), "referral_id": str(a.referral_id),
                               "state": a.state, "dial_target_masked": _mask(a.dial_target),
                               "provider_signal": a.provider_signal, "result_source": a.result_source,
                               "attempted_at": a.attempted_at.isoformat()} for a in attempts],
        "notifications": [{"id": str(n.id), "channel": n.channel, "template": n.template, "status": n.status,
                           "status_reason": n.status_reason, "retry_count": n.retry_count,
                           "created_at": n.created_at.isoformat()} for n in notes],
        "history": [{"at": h.created_at.isoformat(), "actor": h.actor, "operation": h.operation,
                     "result": h.result, "metadata": h.event_metadata} for h in history],
    }


@router.post("/admin/cases/{case_id}/status")
def override_case_status(case_id: uuid.UUID, req: CaseStatusOverride, principal: Principal = Depends(OPERATOR),
                         session: Session = Depends(get_session)):
    case = _get_case(session, case_id)
    set_case_status(session, case, CaseStatus(req.status))
    audit(session, operation="admin.case_status", target_type="case", target_id=case.id, result=req.status,
          actor=principal.name, role=principal.role, note_length=len(req.note))
    session.commit()
    return {"case_id": str(case.id), "status": case.status}


@router.patch("/admin/cases/{case_id}/facts")
def operator_facts(case_id: uuid.UUID, patch: FactsPatch, principal: Principal = Depends(OPERATOR),
                   session: Session = Depends(get_session), clock: Callable[[], datetime] = Depends(get_clock)):
    case = _get_case(session, case_id)
    alerts = update_facts(session, case, patch.facts, clock())
    audit(session, operation="admin.update_facts", target_type="case", target_id=case.id, result="ok",
          actor=principal.name, role=principal.role, keys=",".join(sorted(patch.facts)))
    session.commit()
    return {"case_id": str(case.id), "summary_version": case.summary_version, "firm_updates_queued": len(alerts)}


@router.post("/admin/cases/{case_id}/reassign")
def reassign(case_id: uuid.UUID, req: ReassignRequest, principal: Principal = Depends(OPERATOR),
             session: Session = Depends(get_session), settings: Settings = Depends(get_settings_dep)):
    """Manual referral to another eligible firm. The previous referral is expired, never duplicated."""
    case = _get_case(session, case_id)
    firm = session.scalar(select(Firm).where(Firm.slug == req.firm_id))
    if firm is None:
        raise not_found("firm")
    problems = []
    if not firm.accepting_referrals:
        problems.append("not_accepting_referrals")
    if case.jurisdiction not in firm.jurisdictions:
        problems.append("jurisdiction")
    if case.practice_area not in firm.practice_areas:
        problems.append("practice_area")
    if not firm.is_demo and firm.verification_status != "verified":
        problems.append("not_verified")
    if firm.is_demo and not settings.demo_mode:
        problems.append("demo_firm_outside_demo_mode")
    if problems:
        raise conflict("firm_not_eligible", ", ".join(problems))
    for old in case.referrals:
        if old.status in ACTIVE_REFERRAL_STATES:
            if old.firm_id == firm.id:
                raise conflict("already_referred", "case already has an active referral to this firm")
            set_referral_status(session, old, ReferralStatus.EXPIRED)
    new = Referral(case_id=case.id, firm_id=firm.id, status=ReferralStatus.CREATED, selection_rule_version="manual",
                   selection_rationale={"rule_version": RULE_VERSION, "manual_by": principal.name,
                                        "note": req.note})
    session.add(new)
    if case.status != CaseStatus.EXTENDED_INTAKE:
        if case.status != CaseStatus.TRIAGE_READY:
            set_case_status(session, case, CaseStatus.HUMAN_REVIEW)
            set_case_status(session, case, CaseStatus.TRIAGE_READY)
        set_case_status(session, case, CaseStatus.EXTENDED_INTAKE)
    session.flush()
    audit(session, operation="admin.reassign", target_type="referral", target_id=new.id, result="created",
          actor=principal.name, role=principal.role, firm=firm.slug)
    session.commit()
    return {"case_id": str(case.id), "referral_id": str(new.id), "firm_id": firm.slug, "case_status": case.status}


@router.post("/admin/cases/{case_id}/consents")
def operator_consent(case_id: uuid.UUID, req: OperatorConsent, principal: Principal = Depends(OPERATOR),
                     session: Session = Depends(get_session)):
    case = _get_case(session, case_id)
    if req.purpose == "share_with_selected_firm" and not req.firm_id:
        raise conflict("firm_required", "share consent must name the firm the caller agreed to")
    event = consent.record(session, purpose=ConsentPurpose(req.purpose), allowed=req.allowed, case_id=case.id,
                           caller_id=case.caller_id, call_session_id=None, subject_firm_slug=req.firm_id,
                           capture_method=f"operator_phone:{principal.name}")
    audit(session, operation="admin.consent", target_type="case", target_id=case.id, result=str(req.allowed),
          actor=principal.name, role=principal.role, purpose=req.purpose, firm=req.firm_id)
    session.commit()
    return {"consent_id": str(event.id), "purpose": req.purpose, "allowed": req.allowed}


@router.post("/admin/referrals/{referral_id}/send")
def send_referral(referral_id: uuid.UUID, principal: Principal = Depends(OPERATOR),
                  session: Session = Depends(get_session), settings: Settings = Depends(get_settings_dep),
                  clock: Callable[[], datetime] = Depends(get_clock)):
    """Dispatch a manually created referral (after reassignment). Requires share consent for THIS firm."""
    referral = session.get(Referral, referral_id)
    if referral is None:
        raise not_found("referral")
    case = referral.case
    if not share_allowed(session, case, referral):
        raise conflict("share_consent_missing", f"no share consent recorded for {referral.firm.slug}")
    if case.status != CaseStatus.EXTENDED_INTAKE:
        raise conflict("invalid_transition", f"case is {case.status}")
    notes, _ = dispatch_referral(session, case, referral, settings, clock())
    audit(session, operation="admin.send_referral", target_type="referral", target_id=referral.id,
          result="pending_async", actor=principal.name, role=principal.role)
    session.commit()
    return {"referral_id": str(referral.id), "status": referral.status, "notifications_queued": len(notes)}


def _firm_out(firm: Firm, now: datetime, settings: Settings) -> FirmOut:
    a = compute_availability(firm, now, settings)
    return FirmOut(firm_id=firm.slug, display_name=firm.display_name, is_demo=firm.is_demo,
                   is_fixture=firm.is_fixture, verification_status=firm.verification_status,
                   jurisdictions=firm.jurisdictions, practice_areas=firm.practice_areas, timezone=firm.timezone,
                   accepting_referrals=firm.accepting_referrals, accepting_live_calls=firm.accepting_live_calls,
                   max_open_referrals=firm.max_open_referrals, open_now=a.open_now, availability_reason=a.reason,
                   availability_updated_at=firm.availability_updated_at,
                   has_transfer_number=bool(firm.transfer_number))


@router.get("/admin/firms", response_model=list[FirmOut])
def list_firms(principal: Principal = Depends(OPERATOR), session: Session = Depends(get_session),
               settings: Settings = Depends(get_settings_dep), clock: Callable[[], datetime] = Depends(get_clock)):
    now = clock()
    return [_firm_out(f, now, settings) for f in session.scalars(select(Firm).order_by(Firm.slug))]


@router.patch("/admin/firms/{firm_slug}", response_model=FirmOut)
def patch_firm(firm_slug: str, patch: FirmPatch, principal: Principal = Depends(OPERATOR),
               session: Session = Depends(get_session), settings: Settings = Depends(get_settings_dep),
               clock: Callable[[], datetime] = Depends(get_clock)):
    firm = session.scalar(select(Firm).where(Firm.slug == firm_slug))
    if firm is None:
        raise not_found("firm")
    changes = patch.model_dump(exclude_none=True)
    for key, value in changes.items():
        setattr(firm, key, value)
    firm.availability_updated_at = clock()
    audit(session, operation="admin.firm_update", target_type="firm", target_id=firm.slug, result="ok",
          actor=principal.name, role=principal.role, **{k: str(v) for k, v in changes.items()})
    session.commit()
    return _firm_out(firm, clock(), settings)


@router.get("/admin/failures")
def failures(principal: Principal = Depends(OPERATOR), session: Session = Depends(get_session),
             settings: Settings = Depends(get_settings_dep), clock: Callable[[], datetime] = Depends(get_clock)):
    """Everything that needs a human: failed/blocked messages, unresolved transfers, stale data."""
    now = clock()
    stale_cut = now - timedelta(minutes=settings.stale_transfer_minutes)
    unack_cut = now - timedelta(hours=settings.unacknowledged_referral_hours)
    firm_cut = now - timedelta(days=settings.stale_firm_availability_days)
    notes = session.scalars(select(Notification).where(
        Notification.status.in_((NotificationStatus.FAILED, NotificationStatus.BLOCKED)))
        .order_by(Notification.updated_at.desc()).limit(100)).all()
    transfers = session.scalars(select(TransferAttempt).where(
        TransferAttempt.state == TransferAttemptState.REQUESTED, TransferAttempt.attempted_at <= stale_cut)).all()
    unacked = session.scalars(select(Referral).where(Referral.status == ReferralStatus.FIRM_NOTIFIED,
                                                     Referral.updated_at <= unack_cut)).all()
    stale_firms = session.scalars(select(Firm).where(Firm.availability_updated_at <= firm_cut)).all()
    dropped = session.scalars(select(CallSession).where(CallSession.case_id.is_(None),
                                                        CallSession.ended_at.is_not(None))
                              .order_by(CallSession.ended_at.desc()).limit(50)).all()
    return {
        "notifications": [{"id": str(n.id), "case_id": str(n.case_id), "channel": n.channel, "status": n.status,
                           "reason": n.status_reason, "retry_count": n.retry_count} for n in notes],
        "unresolved_transfers": [{"transfer_attempt_id": str(a.id), "referral_id": str(a.referral_id),
                                  "attempted_at": a.attempted_at.isoformat()} for a in transfers],
        "unacknowledged_referrals": [{"referral_id": str(r.id), "case_id": str(r.case_id), "firm_id": r.firm.slug,
                                      "notified_at": r.updated_at.isoformat()} for r in unacked],
        "stale_firm_availability": [{"firm_id": f.slug, "updated_at": f.availability_updated_at.isoformat()}
                                    for f in stale_firms],
        "calls_without_case": [{"provider_call_id": c.provider_call_id,
                                "termination_reason": c.termination_reason} for c in dropped],
    }


@router.post("/admin/notifications/{notification_id}/retry")
def retry_notification(notification_id: uuid.UUID, principal: Principal = Depends(OPERATOR),
                       session: Session = Depends(get_session)):
    n = session.get(Notification, notification_id)
    if n is None:
        raise not_found("notification")
    if n.status != NotificationStatus.FAILED:
        raise conflict("not_retryable", f"notification is {n.status}; only failed messages can be retried")
    n.status, n.status_reason, n.retry_count, n.next_attempt_at = NotificationStatus.PENDING, None, 0, utcnow()
    audit(session, operation="admin.notification_retry", target_type="notification", target_id=n.id,
          result="requeued", actor=principal.name, role=principal.role)
    session.commit()
    return {"id": str(n.id), "status": n.status}


@router.get("/firms/{firm_slug}/availability", response_model=AvailabilityOut)
def firm_availability(firm_slug: str, principal: Principal = Depends(require_roles("service", "operator")),
                      session: Session = Depends(get_session), settings: Settings = Depends(get_settings_dep),
                      clock: Callable[[], datetime] = Depends(get_clock)):
    firm = session.scalar(select(Firm).where(Firm.slug == firm_slug))
    if firm is None:
        raise not_found("firm")
    a = compute_availability(firm, clock(), settings)
    return AvailabilityOut(firm_id=firm.slug, open_now=a.open_now, accepting_referrals=firm.accepting_referrals,
                           accepting_live_calls=a.accepting_live_calls,
                           firm_local_time=a.firm_local_time.isoformat(), timezone=firm.timezone,
                           source=a.source, source_updated_at=firm.availability_updated_at, reason=a.reason)
