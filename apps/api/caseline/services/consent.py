"""Consent capture and lookup. Each purpose is recorded separately with a policy version."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from caseline.enums import ConsentPurpose
from caseline.models import ConsentEvent

# Bump when the caller-facing notice/script wording changes (approved text lives in voice prompts).
POLICY_VERSION = "demo-notice-2026-09-26"


def record(
    session: Session,
    *,
    purpose: ConsentPurpose,
    allowed: bool,
    case_id: uuid.UUID | None,
    caller_id: uuid.UUID | None,
    call_session_id: uuid.UUID | None,
    subject_firm_slug: str | None = None,
    capture_method: str = "voice_agent",
) -> ConsentEvent:
    event = ConsentEvent(
        purpose=purpose, allowed=allowed, case_id=case_id, caller_id=caller_id,
        call_session_id=call_session_id, subject_firm_slug=subject_firm_slug,
        policy_version=POLICY_VERSION, capture_method=capture_method,
    )
    session.add(event)
    session.flush()
    return event


def latest(session: Session, case_id: uuid.UUID, purpose: ConsentPurpose,
           subject_firm_slug: str | None = None) -> ConsentEvent | None:
    stmt = select(ConsentEvent).where(ConsentEvent.case_id == case_id, ConsentEvent.purpose == purpose)
    if subject_firm_slug is not None:
        stmt = stmt.where(ConsentEvent.subject_firm_slug == subject_firm_slug)
    return session.scalars(stmt.order_by(ConsentEvent.captured_at.desc(), ConsentEvent.id)).first()


def is_allowed(session: Session, case_id: uuid.UUID, purpose: ConsentPurpose,
               subject_firm_slug: str | None = None) -> bool:
    event = latest(session, case_id, purpose, subject_firm_slug)
    return bool(event and event.allowed)
