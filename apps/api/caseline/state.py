"""Explicit state-transition tables for cases and referrals (spec §1, §18)."""

from __future__ import annotations

from caseline.enums import CaseStatus as C
from caseline.enums import ReferralStatus as R

CASE_TRANSITIONS: dict[C, frozenset[C]] = {
    C.NEW: frozenset({C.CONSENT_PENDING, C.TRIAGE_IN_PROGRESS, C.HUMAN_REVIEW, C.CLOSED}),
    C.CONSENT_PENDING: frozenset({C.TRIAGE_IN_PROGRESS, C.HUMAN_REVIEW, C.CLOSED}),
    C.TRIAGE_IN_PROGRESS: frozenset({C.TRIAGE_READY, C.HUMAN_REVIEW, C.NO_ELIGIBLE_FIRM, C.CLOSED}),
    C.TRIAGE_READY: frozenset({C.TRANSFER_PENDING, C.EXTENDED_INTAKE, C.HUMAN_REVIEW, C.NO_ELIGIBLE_FIRM}),
    C.TRANSFER_PENDING: frozenset({C.TRANSFER_CONNECTED, C.TRANSFER_FAILED, C.EXTENDED_INTAKE, C.HUMAN_REVIEW}),
    C.TRANSFER_CONNECTED: frozenset({C.FIRM_ACCEPTED, C.FIRM_DECLINED, C.CLOSED}),
    C.TRANSFER_FAILED: frozenset({C.EXTENDED_INTAKE, C.HUMAN_REVIEW}),
    C.EXTENDED_INTAKE: frozenset({C.REFERRAL_PENDING, C.HUMAN_REVIEW, C.CLOSED}),
    C.REFERRAL_PENDING: frozenset({C.FIRM_NOTIFIED, C.HUMAN_REVIEW, C.CLOSED}),
    C.FIRM_NOTIFIED: frozenset({C.FIRM_ACCEPTED, C.FIRM_DECLINED, C.HUMAN_REVIEW, C.CLOSED}),
    C.FIRM_ACCEPTED: frozenset({C.CLOSED}),
    C.FIRM_DECLINED: frozenset({C.HUMAN_REVIEW, C.CLOSED}),
    C.HUMAN_REVIEW: frozenset({C.TRIAGE_READY, C.CLOSED}),
    C.NO_ELIGIBLE_FIRM: frozenset({C.HUMAN_REVIEW, C.CLOSED}),
    C.CLOSED: frozenset(),
}

REFERRAL_TRANSITIONS: dict[R, frozenset[R]] = {
    R.CREATED: frozenset({R.TRANSFER_AUTHORIZED, R.PENDING_ASYNC, R.EXPIRED}),
    R.TRANSFER_AUTHORIZED: frozenset({R.TRANSFER_REQUESTED, R.PENDING_ASYNC, R.EXPIRED}),
    R.TRANSFER_REQUESTED: frozenset({R.TRANSFER_CONNECTED, R.TRANSFER_FAILED}),
    R.TRANSFER_CONNECTED: frozenset({R.ACCEPTED, R.DECLINED}),
    R.TRANSFER_FAILED: frozenset({R.PENDING_ASYNC, R.EXPIRED}),
    R.PENDING_ASYNC: frozenset({R.FIRM_NOTIFIED, R.EXPIRED}),
    R.FIRM_NOTIFIED: frozenset({R.ACCEPTED, R.DECLINED, R.EXPIRED}),
    R.ACCEPTED: frozenset(),
    R.DECLINED: frozenset(),
    R.EXPIRED: frozenset(),
}


class InvalidTransition(Exception):
    def __init__(self, entity: str, current: str, target: str) -> None:
        super().__init__(f"{entity} cannot move from {current} to {target}")
        self.entity = entity
        self.current = current
        self.target = target


def can_transition_case(current: C, target: C) -> bool:
    return current == target or target in CASE_TRANSITIONS[current]


def can_transition_referral(current: R, target: R) -> bool:
    return current == target or target in REFERRAL_TRANSITIONS[current]
