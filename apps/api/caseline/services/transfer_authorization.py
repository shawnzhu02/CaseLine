"""Short-lived, single-use transfer authorizations (spec §6 security design, §19.2, §19.3).

The dial destination is resolved server-side from the firm record and is only released after every
check below passes. The caller and the language model can never supply or override it.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from caseline.audit import audit
from caseline.config import Settings, is_e164
from caseline.enums import CaseStatus, ConsentPurpose, ReferralStatus, TransferAttemptState
from caseline.errors import ApiError, conflict, not_found
from caseline.models import CallSession, Case, Referral, TransferAttempt, TransferAuthorization
from caseline.schemas import AuthorizeTransferResponse, TransferAttemptResponse
from caseline.services import consent
from caseline.services.availability import compute_availability
from caseline.services.lifecycle import set_case_status, set_referral_status


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _load(session: Session, referral_id, provider_call_id: str) -> tuple[Referral, Case, CallSession]:
    referral = session.get(Referral, referral_id)
    if referral is None:
        raise not_found("referral")
    call = session.scalar(select(CallSession).where(CallSession.provider_call_id == provider_call_id))
    if call is None or call.case_id != referral.case_id:
        raise conflict("call_mismatch", "referral does not belong to this call")
    return referral, referral.case, call


def authorize(session: Session, *, referral_id, provider_call_id: str, caller_consented: bool,
              settings: Settings, now: datetime) -> AuthorizeTransferResponse:
    referral, case, call = _load(session, referral_id, provider_call_id)
    firm = referral.firm

    def deny(reason: str, message: str) -> ApiError:
        audit(session, operation="transfer.authorize", target_type="referral", target_id=referral.id,
              result="denied", reason=reason, firm=firm.slug)
        session.commit()  # keep the denial + any consent record even though we return 409
        return conflict(reason, message)

    if referral.status not in (ReferralStatus.CREATED, ReferralStatus.TRANSFER_AUTHORIZED):
        raise deny("referral_not_transferable", f"referral is {referral.status}")

    consent.record(session, purpose=ConsentPurpose.TRANSFER, allowed=caller_consented, case_id=case.id,
                   caller_id=case.caller_id, call_session_id=call.id, subject_firm_slug=firm.slug)
    if not caller_consented:
        set_case_status(session, case, CaseStatus.EXTENDED_INTAKE)
        raise deny("caller_declined", "caller did not consent to the transfer")

    avail = compute_availability(firm, now, settings)
    if not (firm.accepting_referrals and avail.accepting_live_calls):
        raise deny("firm_unavailable", avail.reason)
    if firm.is_demo and not settings.demo_mode:
        raise deny("demo_mode_disabled", "demo firms are inert outside demo mode")
    if not firm.is_demo:
        # Production partner destinations need a verified-partner allowlist; not part of this MVP.
        raise deny("production_transfer_not_configured", "only demo destinations are dialable in this build")
    if not settings.live_transfer_gate_open:
        raise deny("live_transfer_disabled", "DEMO_LIVE_TRANSFER_ENABLED is off")
    destination = firm.transfer_number or ""
    if not is_e164(destination) or destination not in settings.transfer_allowlist:
        raise deny("destination_not_allowlisted", "firm destination is not on the transfer allowlist")

    token = secrets.token_urlsafe(32)
    auth = TransferAuthorization(
        referral_id=referral.id, call_session_id=call.id, token_hash=_hash(token), destination_e164=destination,
        expires_at=now + timedelta(seconds=settings.transfer_authorization_ttl_seconds),
        created_by="authorize-transfer", created_at=now,
    )
    session.add(auth)
    set_referral_status(session, referral, ReferralStatus.TRANSFER_AUTHORIZED)
    session.flush()
    audit(session, operation="transfer.authorize", target_type="referral", target_id=referral.id,
          result="authorized", authorization_id=auth.id, firm=firm.slug)
    return AuthorizeTransferResponse(authorization_id=auth.id, authorization_token=token,
                                     destination_e164=destination, display_name=firm.display_name,
                                     expires_at=auth.expires_at)


def record_attempt(session: Session, *, referral_id, authorization_id, authorization_token: str,
                   provider_call_id: str, now: datetime) -> TransferAttemptResponse:
    referral, case, call = _load(session, referral_id, provider_call_id)
    q = select(TransferAuthorization).where(TransferAuthorization.id == authorization_id)
    if session.get_bind().dialect.name == "postgresql":
        q = q.with_for_update()
    auth = session.scalar(q)
    if auth is None or auth.referral_id != referral.id or auth.call_session_id != call.id:
        raise not_found("transfer_authorization")
    if auth.token_hash != _hash(authorization_token):
        raise ApiError(403, "authorization_token_invalid")
    if auth.used_at is not None:
        raise conflict("authorization_already_used", "authorization is single-use")
    if now >= auth.expires_at:
        raise conflict("authorization_expired", "authorization expired; re-confirm with the caller")

    auth.used_at = now
    attempt = TransferAttempt(referral_id=referral.id, authorization_id=auth.id, dial_target=auth.destination_e164,
                              state=TransferAttemptState.REQUESTED, attempted_at=now)
    session.add(attempt)
    set_referral_status(session, referral, ReferralStatus.TRANSFER_REQUESTED)
    session.flush()
    audit(session, operation="transfer.requested", target_type="transfer_attempt", target_id=attempt.id,
          result="requested", referral_id=referral.id)
    return TransferAttemptResponse(transfer_attempt_id=attempt.id, referral_id=referral.id,
                                   state=attempt.state, destination_e164=attempt.dial_target,
                                   display_name=referral.firm.display_name, dial=True)


def record_outcome(session: Session, *, attempt_id, result: str, source: str, actor: str,
                   now: datetime) -> TransferAttempt:
    attempt = session.get(TransferAttempt, attempt_id)
    if attempt is None:
        raise not_found("transfer_attempt")
    if attempt.state != TransferAttemptState.REQUESTED:
        raise conflict("outcome_already_recorded", f"attempt is {attempt.state}")
    referral = session.get(Referral, attempt.referral_id)
    case = referral.case
    attempt.result_source = source
    if result == "connected":
        attempt.state, attempt.connected_at = TransferAttemptState.CONNECTED, now
        set_referral_status(session, referral, ReferralStatus.TRANSFER_CONNECTED)
        set_case_status(session, case, CaseStatus.TRANSFER_CONNECTED)
    else:
        attempt.state, attempt.failed_at = TransferAttemptState(result), now
        set_referral_status(session, referral, ReferralStatus.TRANSFER_FAILED)
        set_case_status(session, case, CaseStatus.TRANSFER_FAILED)
    audit(session, operation="transfer.outcome", target_type="transfer_attempt", target_id=attempt.id,
          result=result, actor=actor, role="operator", source=source)
    return attempt
