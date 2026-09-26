"""Periodic jobs run by the outbox worker (spec §25: expire_referral, reconcile_stale_transfer).

Both are idempotent: they only act on rows still in the triggering state.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from caseline.audit import audit
from caseline.config import Settings
from caseline.enums import CaseStatus, ReferralStatus, TransferAttemptState
from caseline.models import Referral, TransferAttempt
from caseline.services.lifecycle import set_case_status, set_referral_status
from caseline.state import can_transition_case

EXPIRABLE = (ReferralStatus.PENDING_ASYNC, ReferralStatus.FIRM_NOTIFIED)


def expire_referrals(session: Session, now: datetime) -> int:
    """Referrals past expires_at without a firm decision -> expired; case -> human review for follow-up."""
    rows = session.scalars(select(Referral).where(Referral.status.in_(EXPIRABLE), Referral.expires_at.is_not(None),
                                                  Referral.expires_at <= now)).all()
    for referral in rows:
        set_referral_status(session, referral, ReferralStatus.EXPIRED)
        if can_transition_case(referral.case.status, CaseStatus.HUMAN_REVIEW):
            set_case_status(session, referral.case, CaseStatus.HUMAN_REVIEW)
        audit(session, operation="referral.expired", target_type="referral", target_id=referral.id,
              result="expired", actor="worker", role="service")
    return len(rows)


def flag_stale_transfers(session: Session, settings: Settings, now: datetime) -> int:
    """Transfer attempts still 'requested' after N minutes need a human to record what happened.

    The attempt is NOT marked failed or connected (no evidence either way); the case goes to human review.
    """
    cutoff = now - timedelta(minutes=settings.stale_transfer_minutes)
    rows = session.scalars(select(TransferAttempt).where(TransferAttempt.state == TransferAttemptState.REQUESTED,
                                                         TransferAttempt.attempted_at <= cutoff)).all()
    flagged = 0
    for attempt in rows:
        case = session.get(Referral, attempt.referral_id).case
        if case.status == CaseStatus.TRANSFER_PENDING:
            set_case_status(session, case, CaseStatus.HUMAN_REVIEW)
            audit(session, operation="transfer.stale", target_type="transfer_attempt", target_id=attempt.id,
                  result="needs_operator_outcome", actor="worker", role="service")
            flagged += 1
    return flagged


def run_all(session: Session, settings: Settings, now: datetime) -> dict[str, int]:
    result = {"expired_referrals": expire_referrals(session, now),
              "stale_transfers": flag_stale_transfers(session, settings, now)}
    session.commit()
    return result
