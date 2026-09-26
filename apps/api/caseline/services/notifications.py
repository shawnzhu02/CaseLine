"""Outbox enqueueing and minimum-necessary message templates (spec §7, §25)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from caseline.enums import JobType, NotificationChannel, NotificationStatus
from caseline.models import Case, Notification, Referral

TEMPLATES: dict[str, tuple[str, str]] = {
    # key -> (subject, body). Bodies carry a case reference only: no narrative, no transcript, no PII.
    "firm_referral_alert": (
        "CaseLine referral {ref} awaiting your review",
        "A caller has asked CaseLine to share an intake summary with {firm}.\n"
        "Reference: {ref}\n"
        "Access the report through the CaseLine firm portal (secure report links ship in Phase 3).\n"
        "Please run your own conflicts check; CaseLine has not established any representation.",
    ),
    "caller_referral_pending": (
        "",
        "CaseLine: we have received your request (ref {ref}). {firm} will review it and decide independently "
        "whether they can help. No lawyer has been engaged yet. Reply STOP to opt out.",
    ),
    "caller_referral_accepted": (
        "",
        "CaseLine: {firm} has indicated it can review your matter further (ref {ref}). They will contact you "
        "directly. Reply STOP to opt out.",
    ),
    "caller_referral_declined": (
        "",
        "CaseLine: {firm} is unable to take your matter (ref {ref}). Our team will follow up about other "
        "options. Reply STOP to opt out.",
    ),
}


def case_ref(case: Case) -> str:
    return str(case.id).split("-")[0].upper()


def render(template: str, case: Case, referral: Referral | None) -> tuple[str, str]:
    subject, body = TEMPLATES[template]
    values = {"ref": case_ref(case), "firm": referral.firm.display_name if referral else "a participating firm"}
    return subject.format(**values), body.format(**values)


def enqueue(session: Session, *, event_key: str, job_type: JobType, case: Case, referral: Referral | None,
            destination: str, template: str, now: datetime) -> Notification:
    """Insert exactly one outbox row per business event key (idempotent)."""
    existing = session.scalar(select(Notification).where(Notification.event_key == event_key))
    if existing is not None:
        return existing
    channel = NotificationChannel.SMS if job_type == JobType.SEND_GUAVA_SMS else NotificationChannel.EMAIL
    row = Notification(event_key=event_key, job_type=job_type, channel=channel, case_id=case.id,
                       referral_id=referral.id if referral else None, destination=destination,
                       template=template, status=NotificationStatus.PENDING, next_attempt_at=now)
    session.add(row)
    session.flush()
    return row
