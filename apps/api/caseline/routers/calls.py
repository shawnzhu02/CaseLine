"""Call lifecycle events.

Guava SDK 0.45.0 has no HTTP webhooks or signed provider events. These events are posted by our own voice
agent (bearer-authenticated) from its SDK callbacks, and deduplicated on provider_event_id.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from caseline.auth import SERVICE
from caseline.deps import get_clock, get_session
from caseline.enums import CallState, TransferAttemptState
from caseline.models import CallSession, ProviderEvent, Referral, TransferAttempt
from caseline.schemas import CallEventIn

router = APIRouter(prefix="/v1", dependencies=[Depends(SERVICE)])


@router.post("/calls/events")
def call_event(ev: CallEventIn, session: Session = Depends(get_session),
               clock: Callable[[], datetime] = Depends(get_clock)):
    now = clock()
    try:
        with session.begin_nested():
            session.add(ProviderEvent(provider=ev.provider, provider_event_id=ev.provider_event_id,
                                      event_type=ev.event_type, received_at=now,
                                      payload_redacted={"termination_reason": ev.termination_reason}))
    except IntegrityError:
        session.rollback()
        return {"duplicate": True}

    call = session.scalar(select(CallSession).where(CallSession.provider_call_id == ev.provider_call_id))
    if call is None:
        call = CallSession(provider_call_id=ev.provider_call_id, started_at=now)
        session.add(call)
    if ev.event_type == "call_started" and ev.caller_id_number:
        call.caller_id_number = ev.caller_id_number  # informational only; never trusted as verified
    if ev.event_type == "session_ended":
        # Out-of-order safety: an end event is terminal and is never undone by a later start event.
        call.state, call.ended_at, call.termination_reason = CallState.ENDED, now, ev.termination_reason
        if ev.termination_reason == "bot-transfer" and call.case_id:
            # "bot-transfer" only means the agent left the call to transfer it. It is NOT evidence that the
            # destination answered, so the attempt stays REQUESTED and just records the signal.
            attempts = session.scalars(
                select(TransferAttempt).join(Referral).where(Referral.case_id == call.case_id,
                                                             TransferAttempt.state == TransferAttemptState.REQUESTED))
            for attempt in attempts:
                attempt.provider_signal = "guava:bot-transfer"
    session.commit()
    return {"duplicate": False}
