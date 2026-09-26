from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime

from fastapi import APIRouter, Depends, Header
from sqlalchemy.orm import Session

from caseline import idempotency
from caseline.auth import require_internal_token
from caseline.config import Settings
from caseline.deps import get_clock, get_session, get_settings_dep
from caseline.schemas import (
    AuthorizeTransferRequest,
    AuthorizeTransferResponse,
    ExtendedIntakeRequest,
    ExtendedIntakeResponse,
    ReferralStatusUpdate,
    TransferAttemptRequest,
    TransferAttemptResponse,
    TransferOutcomeRequest,
)
from caseline.services import transfer_authorization as ta
from caseline.services.referrals import submit_extended_intake, update_referral_status

router = APIRouter(prefix="/v1", dependencies=[Depends(require_internal_token)])

IdemKey = Header(alias="Idempotency-Key", min_length=1, max_length=200)


@router.post("/referrals/{referral_id}/authorize-transfer", response_model=AuthorizeTransferResponse)
def authorize_transfer(
    referral_id: uuid.UUID,
    req: AuthorizeTransferRequest,
    idempotency_key: str = IdemKey,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
    clock: Callable[[], datetime] = Depends(get_clock),
):
    # Replays of a successful authorization are NOT served from cache: the one-time token must not be
    # re-issued. A retry after a lost response simply requests a fresh authorization.
    result = ta.authorize(session, referral_id=referral_id, provider_call_id=req.provider_call_id,
                          caller_consented=req.caller_consented, settings=settings, now=clock())
    session.commit()
    return result


@router.post("/referrals/{referral_id}/transfer-attempts", response_model=TransferAttemptResponse)
def create_transfer_attempt(
    referral_id: uuid.UUID,
    req: TransferAttemptRequest,
    idempotency_key: str = IdemKey,
    session: Session = Depends(get_session),
    clock: Callable[[], datetime] = Depends(get_clock),
):
    body_hash = idempotency.request_hash(req)
    replay = idempotency.lookup(session, f"transfer-attempt:{referral_id}", idempotency_key, body_hash)
    if replay is not None:
        # Same attempt, but never instruct the agent to dial twice.
        return {**replay, "dial": False}
    result = ta.record_attempt(session, referral_id=referral_id, authorization_id=req.authorization_id,
                               authorization_token=req.authorization_token,
                               provider_call_id=req.provider_call_id, now=clock())
    idempotency.store(session, f"transfer-attempt:{referral_id}", idempotency_key, body_hash, result)
    session.commit()
    return result


@router.post("/transfer-attempts/{attempt_id}/outcome")
def transfer_outcome(
    attempt_id: uuid.UUID,
    req: TransferOutcomeRequest,
    session: Session = Depends(get_session),
    clock: Callable[[], datetime] = Depends(get_clock),
):
    attempt = ta.record_outcome(session, attempt_id=attempt_id, result=req.result, source=req.source,
                                actor="operator", now=clock())
    session.commit()
    return {"transfer_attempt_id": str(attempt.id), "state": attempt.state, "result_source": attempt.result_source}


@router.post("/referrals/{referral_id}/extended-intake", response_model=ExtendedIntakeResponse)
def extended_intake(
    referral_id: uuid.UUID,
    req: ExtendedIntakeRequest,
    idempotency_key: str = IdemKey,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
    clock: Callable[[], datetime] = Depends(get_clock),
):
    body_hash = idempotency.request_hash(req)
    replay = idempotency.lookup(session, f"extended-intake:{referral_id}", idempotency_key, body_hash)
    if replay is not None:
        return replay
    result = submit_extended_intake(session, referral_id=referral_id, req=req, settings=settings, now=clock())
    idempotency.store(session, f"extended-intake:{referral_id}", idempotency_key, body_hash, result)
    session.commit()
    return result


@router.post("/referrals/{referral_id}/status")
def referral_status(
    referral_id: uuid.UUID,
    req: ReferralStatusUpdate,
    session: Session = Depends(get_session),
    clock: Callable[[], datetime] = Depends(get_clock),
):
    referral = update_referral_status(session, referral_id=referral_id, status=req.status, actor=req.actor,
                                      now=clock())
    session.commit()
    return {"referral_id": str(referral.id), "status": referral.status, "case_status": referral.case.status}
