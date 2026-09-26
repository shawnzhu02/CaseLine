"""Stage A triage: persist validated fields, evaluate safety, eligibility and routing (spec §3, §19.1)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from caseline.config import Settings
from caseline.enums import CaseStatus, ConsentPurpose, FactProvenance, ReferralStatus, RoutingAction
from caseline.errors import conflict
from caseline.models import Caller, CallSession, Case, CaseFact, Firm, Referral
from caseline.schemas import FollowUpQuestion, SelectedFirm, TriageRequest, TriageResponse
from caseline.services import consent
from caseline.services.lifecycle import set_case_status
from caseline.services.matching import (
    DEFAULT_QUESTIONS,
    EXTENDED_INTAKE_QUESTIONS,
    RULE_VERSION,
    classify,
    select_firm,
)

ACTIVE_REFERRAL_STATES = (
    ReferralStatus.CREATED, ReferralStatus.TRANSFER_AUTHORIZED, ReferralStatus.TRANSFER_REQUESTED,
    ReferralStatus.PENDING_ASYNC, ReferralStatus.FIRM_NOTIFIED,
)

# Caller-facing wording. Must be reviewed by a qualified practitioner before real callers (spec §13).
EMERGENCY_PROMPT = (
    "It sounds like you may be in immediate danger. Please hang up and call 911 now, or your local "
    "emergency number if you are outside the United States. Your safety comes first."
)
HUMAN_REVIEW_PROMPT = (
    "Thank you. I'm not able to connect you to a firm right now, so I'm passing your information to a "
    "member of our team for review. No lawyer has been arranged yet."
)
DEADLINE_PROMPT = (
    "Because you mentioned a date or deadline, I'm flagging this for priority review by our team. "
    "I can't tell you how much time you have. No lawyer has been arranged yet."
)
NO_MATCH_PROMPT = (
    "I'm sorry, we don't currently have a participating firm for this kind of matter in that area, so no "
    "connection has been arranged. You may want to contact your local bar association's referral service."
)
CONSENT_PROMPT = (
    "I understand. Without your permission I can't take your details, so I won't record anything further. "
    "You're welcome to call back at any time."
)


def _extended_questions(practice_area: str | None) -> list[FollowUpQuestion]:
    qs = EXTENDED_INTAKE_QUESTIONS.get(practice_area or "", DEFAULT_QUESTIONS)
    return [FollowUpQuestion(key=k, question=q) for k, q in qs]


def _get_or_create_call(session: Session, provider_call_id: str) -> CallSession:
    call = session.scalar(select(CallSession).where(CallSession.provider_call_id == provider_call_id))
    if call is None:
        call = CallSession(provider_call_id=provider_call_id)
        session.add(call)
        session.flush()
    return call


def triage(session: Session, req: TriageRequest, settings: Settings, now: datetime) -> TriageResponse:
    call = _get_or_create_call(session, req.provider_call_id)
    if call.case_id is not None:
        # One triage per call; a different Idempotency-Key must not create a second case.
        raise conflict("call_already_triaged", "this call already has a case")

    case = Case(status=CaseStatus.NEW)
    session.add(case)
    session.flush()
    call.case_id = case.id

    if not req.consents.intake:
        consent.record(session, purpose=ConsentPurpose.INTAKE, allowed=False, case_id=case.id,
                       caller_id=None, call_session_id=call.id)
        set_case_status(session, case, CaseStatus.CONSENT_PENDING)
        set_case_status(session, case, CaseStatus.CLOSED)
        case.routing_action = RoutingAction.CONSENT_REQUIRED
        return TriageResponse(case_id=case.id, referral_id=None, action=RoutingAction.CONSENT_REQUIRED,
                              selected_firm=None, requires_transfer_consent=False, next_prompt=CONSENT_PROMPT,
                              permitted_next_steps=["end_call"])

    caller = Caller(
        name=req.caller.name, callback_number=req.caller.callback_number,
        callback_verified=req.caller.callback_confirmed, email=req.caller.email,
        preferred_language=req.caller.preferred_language, accessibility_needs=req.caller.accessibility_needs,
    )
    session.add(caller)
    session.flush()
    case.caller_id = caller.id

    for purpose, value in [
        (ConsentPurpose.INTAKE, req.consents.intake),
        (ConsentPurpose.RECORDING, req.consents.recording),
        (ConsentPurpose.SHARE_WITH_SELECTED_FIRM, req.consents.share_with_selected_firm),
        (ConsentPurpose.SMS, req.consents.sms),
        (ConsentPurpose.EMAIL, req.consents.email),
    ]:
        if value is not None:
            consent.record(session, purpose=purpose, allowed=value, case_id=case.id, caller_id=caller.id,
                           call_session_id=call.id)

    set_case_status(session, case, CaseStatus.TRIAGE_IN_PROGRESS)
    facts = req.facts
    for key, value in facts.model_dump(exclude_none=True).items():
        session.add(CaseFact(case_id=case.id, key=key, value=value, provenance=FactProvenance.CALLER_STATED))

    case.jurisdiction = facts.jurisdiction
    area, confidence = classify(facts.practice_area, facts.issue_summary)
    case.practice_area = area
    case.practice_area_confidence = confidence

    def finish(action: RoutingAction, status: CaseStatus, prompt: str, steps: list[str],
               **extra) -> TriageResponse:
        if status in {CaseStatus.HUMAN_REVIEW, CaseStatus.NO_ELIGIBLE_FIRM}:
            set_case_status(session, case, status)
        case.routing_action = action
        return TriageResponse(case_id=case.id, referral_id=extra.get("referral_id"), action=action,
                              selected_firm=extra.get("selected_firm"),
                              requires_transfer_consent=action == RoutingAction.TRANSFER,
                              availability_source=extra.get("availability_source"),
                              next_prompt=prompt, permitted_next_steps=steps,
                              extended_intake_questions=extra.get("questions", []))

    # Safety escalation precedes routine routing.
    if facts.immediate_danger:
        case.urgent, case.urgency_reason = True, "immediate_danger"
        return finish(RoutingAction.EMERGENCY_GUIDANCE, CaseStatus.HUMAN_REVIEW, EMERGENCY_PROMPT,
                      ["give_emergency_guidance", "end_call"])
    if facts.caller_reported_deadline:
        case.urgent, case.urgency_reason = True, "caller_reported_deadline"
        return finish(RoutingAction.HUMAN_REVIEW, CaseStatus.HUMAN_REVIEW, DEADLINE_PROMPT,
                      ["confirm_callback", "end_call"])
    if area is None or not facts.jurisdiction:
        return finish(RoutingAction.HUMAN_REVIEW, CaseStatus.HUMAN_REVIEW, HUMAN_REVIEW_PROMPT,
                      ["confirm_callback", "end_call"])

    firms = list(session.scalars(select(Firm)))
    counts = dict(session.execute(
        select(Referral.firm_id, func.count()).where(Referral.status.in_(ACTIVE_REFERRAL_STATES))
        .group_by(Referral.firm_id)).all())
    match = select_firm(jurisdiction=facts.jurisdiction, practice_area=area,
                        language=req.caller.preferred_language, firms=firms, open_referral_counts=counts,
                        now_utc=now, settings=settings)

    if match.kind == "none" or match.firm is None:
        set_case_status(session, case, CaseStatus.TRIAGE_READY)
        return finish(RoutingAction.NO_ELIGIBLE_FIRM, CaseStatus.NO_ELIGIBLE_FIRM, NO_MATCH_PROMPT,
                      ["offer_human_review", "end_call"])

    firm = match.firm
    set_case_status(session, case, CaseStatus.TRIAGE_READY)
    referral = Referral(case_id=case.id, firm_id=firm.id, status=ReferralStatus.CREATED,
                        selection_rule_version=RULE_VERSION, selection_rationale=match.rationale)
    session.add(referral)
    session.flush()
    selected = SelectedFirm(firm_id=firm.slug, display_name=firm.display_name, is_demo=firm.is_demo)
    label = f"{firm.display_name}, a demonstration participant" if firm.is_demo else firm.display_name

    if match.kind == "transfer":
        set_case_status(session, case, CaseStatus.TRANSFER_PENDING)
        return finish(RoutingAction.TRANSFER, CaseStatus.TRANSFER_PENDING,
                      f"I can connect you to {label}. They will decide independently whether they can help. "
                      "Would you like me to transfer you?",
                      ["ask_transfer_consent", "authorize_transfer", "extended_intake_if_declined"],
                      referral_id=referral.id, selected_firm=selected,
                      availability_source=match.availability_source, questions=_extended_questions(area))

    set_case_status(session, case, CaseStatus.EXTENDED_INTAKE)
    return finish(RoutingAction.EXTENDED_INTAKE, CaseStatus.EXTENDED_INTAKE,
                  f"{label} isn't available for a live call right now. I'd like to ask a few more questions so "
                  "they can review your matter. No lawyer has been arranged yet.",
                  ["extended_intake", "submit_extended_intake"],
                  referral_id=referral.id, selected_firm=selected,
                  availability_source=match.availability_source, questions=_extended_questions(area))
