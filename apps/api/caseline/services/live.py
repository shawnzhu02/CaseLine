"""Per-call live assessment state: process utterances, keep a change history, derive the dashboard view."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from caseline.config import Settings
from caseline.enums import CallState, JobType, ReferralStatus, TransferAttemptState
from caseline.models import CallAssessment, CallSession, Case, Firm, Notification, Referral, TransferAttempt
from caseline.services import assessment as A
from caseline.services.intake import ACTIVE_REFERRAL_STATES
from caseline.services.matching import select_firm
from caseline.services.notifications import TEMPLATES, case_ref


def _get_or_create(session: Session, provider_call_id: str, now: datetime) -> tuple[CallSession, CallAssessment]:
    call = session.scalar(select(CallSession).where(CallSession.provider_call_id == provider_call_id))
    if call is None:
        call = CallSession(provider_call_id=provider_call_id, started_at=now)
        session.add(call)
        session.flush()
    row = session.scalar(select(CallAssessment).where(CallAssessment.call_session_id == call.id))
    if row is None:
        row = CallAssessment(call_session_id=call.id, facts={}, view={}, history=[], version=0)
        session.add(row)
        session.flush()
    return call, row


def _match(session: Session, a: A.Assessment, settings: Settings, now: datetime) -> dict | None:
    if not a.ready or not a.jurisdiction or not a.category:
        return None
    firms = list(session.scalars(select(Firm)))
    counts = dict(session.execute(
        select(Referral.firm_id, func.count()).where(Referral.status.in_(ACTIVE_REFERRAL_STATES))
        .group_by(Referral.firm_id)).all())
    result = select_firm(jurisdiction=a.jurisdiction, practice_area=a.category, language="en", firms=firms,
                         open_referral_counts=counts, now_utc=now, settings=settings)
    if result.firm is None:
        return {"firm_id": None, "display_name": None, "route": "none"}
    return {"firm_id": result.firm.slug, "display_name": result.firm.display_name, "is_demo": result.firm.is_demo,
            "route": result.kind}


def _action(a: A.Assessment, match: dict | None) -> str | None:
    if a.urgency == "Emergency":
        return "Emergency guidance (911)"
    if match is None:
        return None
    return {"transfer": "Connect now", "async": "Send referral (firm unavailable for live calls)",
            "none": "Human review (no participating firm)"}[match["route"]]


def _derive_view(a: A.Assessment, match: dict | None) -> dict[str, Any]:
    return {
        "jurisdiction": a.jurisdiction, "jurisdiction_label": a.jurisdiction_label,
        "category": a.category, "category_label": A.CATEGORY_LABELS.get(a.category or ""),
        "matter": a.matter, "urgency": a.urgency, "key_factors": a.key_factors,
        "ready": a.ready, "match": match, "action": _action(a, match),
        "next_question": a.next_question, "reasons": a.reasons,
        "rule_version": A.ASSESSMENT_RULE_VERSION,
    }


TRACKED = (("jurisdiction_label", "Jurisdiction"), ("category_label", "Category"), ("matter", "Matter"),
           ("urgency", "Urgency"), ("match_name", "Match"), ("action", "Action"))


def _flat(view: dict) -> dict:
    return {**view, "match_name": (view.get("match") or {}).get("display_name")}


def process_utterance(session: Session, *, provider_call_id: str, speaker: str, text: str, settings: Settings,
                      extractor, now: datetime) -> dict:
    call, row = _get_or_create(session, provider_call_id, now)
    if speaker == "agent":
        q = A.question_for_text(text)
        if q:
            row.last_question = q
        return snapshot(session, call, row)

    delta_rules = A.rule_extract(text, row.last_question)
    delta_llm: dict = {}
    if extractor is not None:
        question_text = A.QUESTIONS.get(row.last_question or "", (None, []))[0]
        delta_llm = extractor.extract(text, question_text, row.facts)
    facts, changed = A.merge_facts(row.facts, delta_rules, delta_llm)
    row.utterances_processed += 1

    assessment = A.assess(facts)
    view = _derive_view(assessment, _match(session, assessment, settings, now))
    before, after = _flat(row.view), _flat(view)
    history = list(row.history)
    step = row.utterances_processed
    for key, label in TRACKED:
        if before.get(key) != after.get(key):
            history.append({"step": step, "at": now.isoformat(), "field": label, "from": before.get(key),
                            "to": after.get(key), "reason": assessment.reasons.get(key.replace("_label", ""))})
    if changed or history != row.history:
        row.facts, row.view, row.history = facts, view, history
        row.version += 1
    row.updated_at = now  # use the request clock (tests + consistent "last active" windows)
    return snapshot(session, call, row)


def mark_question_asked(row: CallAssessment, key: str | None) -> bool:
    """True when this question hasn't been sent to the agent yet (so the agent is steered once per question)."""
    if not key or row.asked_question == key:
        return False
    row.asked_question = key
    return True


def _email_draft(template: str, firm_name: str, ref: str) -> dict:
    subject, body = TEMPLATES[template]
    values = {"ref": ref, "firm": firm_name, "link": "[secure report link]", "link_expiry": "in 72 hours"}
    return {"subject": subject.format(**values), "body": body.format(**values)}


def outcome(session: Session, call: CallSession, row: CallAssessment) -> dict | None:
    """What CaseLine did for this call: a live transfer to the firm, or a referral email drafted to it."""
    sim = (row.view or {}).get("sim_outcome")
    if sim:
        return sim
    if not call.case_id:
        return None
    case = session.get(Case, call.case_id)
    referral = session.scalars(select(Referral).where(Referral.case_id == case.id)
                               .order_by(Referral.created_at.desc())).first()
    if referral is None:
        return None
    attempt = session.scalars(select(TransferAttempt).where(TransferAttempt.referral_id == referral.id)
                              .order_by(TransferAttempt.attempted_at.desc())).first()
    if attempt is not None:
        return {"type": "transfer", "firm": referral.firm.display_name, "state": attempt.state,
                "simulated": False,
                "note": "Live transfer requested through Guava. Connection is confirmed by an operator."}
    email = session.scalars(select(Notification).where(Notification.referral_id == referral.id,
                                                       Notification.job_type == JobType.SEND_FIRM_EMAIL)
                            .order_by(Notification.created_at.desc())).first()
    if email is not None:
        return {"type": "email", "firm": referral.firm.display_name, "status": email.status, "simulated": False,
                **_email_draft(email.template, referral.firm.display_name, case_ref(case))}
    return None


def simulate_outcome(row: CallAssessment, action: str) -> None:
    """Scripted demo only: record what CaseLine would do, without dialing or sending anything."""
    view = dict(row.view or {})
    firm = ((view.get("match") or {}).get("display_name")) or "the matched firm"
    if action == "connect":
        view["sim_outcome"] = {"type": "transfer", "firm": firm, "state": "requested", "simulated": True,
                               "note": "Simulated: on a live call Guava transfers the caller to the firm."}
    else:
        view["sim_outcome"] = {"type": "email", "firm": firm, "status": "drafted", "simulated": True,
                               **_email_draft("firm_referral_alert", firm, "DEMO-" + str(row.id)[:4].upper())}
    row.view = view
    row.version += 1


def _status(session: Session, call: CallSession, row: CallAssessment) -> str:
    sim = (row.view or {}).get("sim_outcome")
    if sim:
        return "CONNECTING..." if sim["type"] == "transfer" else "REFERRAL DRAFTED"
    if call.case_id:
        referrals = session.scalars(select(Referral).where(Referral.case_id == call.case_id)).all()
        ids = [r.id for r in referrals]
        attempts = session.scalars(select(TransferAttempt).where(TransferAttempt.referral_id.in_(ids))).all() \
            if ids else []
        if any(t.state == TransferAttemptState.CONNECTED for t in attempts):
            return "CONNECTED"
        if any(t.state == TransferAttemptState.REQUESTED for t in attempts):
            return "CONNECTING..."
        if any(t.state in (TransferAttemptState.FAILED, TransferAttemptState.NO_ANSWER, TransferAttemptState.BUSY)
               for t in attempts):
            return "TRANSFER NOT COMPLETED"
        if any(r.status in (ReferralStatus.PENDING_ASYNC, ReferralStatus.FIRM_NOTIFIED) for r in referrals):
            return "REFERRAL SENT"
    if call.state == CallState.ENDED:
        return "CALL ENDED"
    if row.view.get("urgency") == "Emergency":
        return "EMERGENCY GUIDANCE"
    if row.view.get("ready"):
        return "MATCH FOUND"
    if row.utterances_processed:
        return "LISTENING"
    return "Waiting for caller..."


def snapshot(session: Session, call: CallSession, row: CallAssessment) -> dict:
    view = row.view or {}
    return {
        "call_id": call.provider_call_id[-6:],  # short reference for display; never caller identity
        "version": row.version,
        "status": _status(session, call, row),
        "jurisdiction": view.get("jurisdiction_label"),
        "category": view.get("category_label"),
        "matter": view.get("matter"),
        "urgency": view.get("urgency"),
        "key_factors": view.get("key_factors", []),
        "match": view.get("match"),
        "action": view.get("action"),
        "ready": bool(view.get("ready")),
        "next_question": view.get("next_question"),
        "history": row.history,
        "outcome": outcome(session, call, row),
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def latest_real_since(session: Session, since) -> dict | None:
    """Most recent real (non-simulated) call updated since `since`. Snapshot fields are non-identifying."""
    rows = session.scalars(select(CallAssessment).where(CallAssessment.updated_at >= since)
                           .order_by(CallAssessment.updated_at.desc()).limit(20)).all()
    for row in rows:
        call = session.get(CallSession, row.call_session_id)
        if not call.provider_call_id.startswith("sim-"):
            return snapshot(session, call, row)
    return None


def latest(session: Session) -> dict | None:
    row = session.scalars(select(CallAssessment).order_by(CallAssessment.updated_at.desc())).first()
    if row is None:
        return None
    call = session.get(CallSession, row.call_session_id)
    return snapshot(session, call, row)


def assessed_routing(session: Session, call_session_id) -> tuple[str | None, str | None]:
    """(jurisdiction, practice_area) from a ready live assessment, for triage to use when the agent sent none."""
    row = session.scalar(select(CallAssessment).where(CallAssessment.call_session_id == call_session_id))
    if row is None or not (row.view or {}).get("ready"):
        return None, None
    return row.view.get("jurisdiction"), row.view.get("category")
