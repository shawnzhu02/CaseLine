"""Validated case/referral state changes. Invalid transitions -> HTTP 409 + audit row."""

from __future__ import annotations

from sqlalchemy.orm import Session

from caseline.audit import audit
from caseline.enums import CaseStatus, ReferralStatus
from caseline.errors import conflict
from caseline.models import Case, Referral
from caseline.state import can_transition_case, can_transition_referral


def _reject(session: Session, entity: str, target_id: object, current: str, target: str) -> None:
    # Persist the audit row independently: the caller's transaction will roll back on the 409.
    with Session(bind=session.get_bind()) as side:
        audit(side, operation=f"{entity}.transition", target_type=entity, target_id=target_id,
              result="rejected", current=current, target=target)
        side.commit()
    raise conflict("invalid_transition", f"{entity} cannot move from {current} to {target}")


def set_case_status(session: Session, case: Case, target: CaseStatus) -> None:
    if not can_transition_case(case.status, target):
        _reject(session, "case", case.id, case.status, target)
    if case.status != target:
        audit(session, operation="case.transition", target_type="case", target_id=case.id,
              result="ok", current=case.status, target=target)
        case.status = target


def set_referral_status(session: Session, referral: Referral, target: ReferralStatus) -> None:
    if not can_transition_referral(referral.status, target):
        _reject(session, "referral", referral.id, referral.status, target)
    if referral.status != target:
        audit(session, operation="referral.transition", target_type="referral", target_id=referral.id,
              result="ok", current=referral.status, target=target)
        referral.status = target
