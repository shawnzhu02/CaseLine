"""Live assessment endpoints: utterance ingest (voice agent) and the operator live view (poll + SSE)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import Field
from sqlalchemy.orm import Session

from caseline.auth import OPERATOR, SERVICE, Principal, require_roles
from caseline.config import Settings
from caseline.deps import get_clock, get_session, get_settings_dep
from caseline.schemas import StrictModel
from caseline.services import live

router = APIRouter(prefix="/v1")


class UtteranceIn(StrictModel):
    speaker: Literal["caller", "agent"]
    text: str = Field(min_length=1, max_length=2000)
    utterance_id: str | None = Field(default=None, max_length=128)


@router.post("/calls/{provider_call_id}/utterances")
def post_utterance(provider_call_id: str, body: UtteranceIn, request: Request,
                   principal: Principal = Depends(SERVICE), session: Session = Depends(get_session),
                   settings: Settings = Depends(get_settings_dep),
                   clock: Callable[[], datetime] = Depends(get_clock)):
    """Process one utterance. The text is used in memory only; only extracted facts are stored."""
    snap = live.process_utterance(session, provider_call_id=provider_call_id, speaker=body.speaker, text=body.text,
                                  settings=settings, extractor=request.app.state.extractor, now=clock())
    call, row = live._get_or_create(session, provider_call_id, clock())
    ask = None
    if body.speaker == "caller" and live.mark_question_asked(row, snap["next_question"]):
        from caseline.services.assessment import QUESTIONS

        ask = QUESTIONS[snap["next_question"]][0]
    row.updated_at = clock()
    session.commit()
    return {**snap, "ask": ask,
            "assessment_ready": snap["ready"] and snap["urgency"] != "Emergency"}


class SimulatedUtterance(StrictModel):
    call_id: str = Field(pattern=r"^[a-z0-9-]{4,40}$")
    speaker: Literal["caller", "agent"]
    text: str = Field(min_length=1, max_length=2000)
    # After the caller answers: what CaseLine does next (simulated; nothing is dialed or sent).
    outcome: Literal["connect", "refer"] | None = None


@router.post("/live/simulate")
def simulate(body: SimulatedUtterance, request: Request,
             principal: Principal = Depends(require_roles("operator", "demo")),
             session: Session = Depends(get_session), settings: Settings = Depends(get_settings_dep),
             clock: Callable[[], datetime] = Depends(get_clock)):
    """Rehearsal without a phone: an operator plays scripted lines through the same assessment path.
    Demo mode only; the call is prefixed `sim-` so it can never be mistaken for a real caller."""
    if not settings.demo_mode:
        from caseline.errors import conflict

        raise conflict("demo_mode_disabled", "simulation is only available in demo mode")
    snap = live.process_utterance(session, provider_call_id=f"sim-{body.call_id}", speaker=body.speaker,
                                  text=body.text, settings=settings, extractor=request.app.state.extractor,
                                  now=clock())
    if body.outcome:
        call, row = live._get_or_create(session, f"sim-{body.call_id}", clock())
        live.simulate_outcome(row, body.outcome)
        row.updated_at = clock()
        session.flush()
        snap = live.snapshot(session, call, row)
    session.commit()
    return snap


@router.get("/live/sim/{call_id}")
def simulated_call(call_id: str, principal: Principal = Depends(require_roles("operator", "demo")),
                   session: Session = Depends(get_session)):
    """Snapshot of one simulated call only. Never returns real calls, so it is safe behind a public page."""
    from sqlalchemy import select

    from caseline.models import CallAssessment, CallSession

    call = session.scalar(select(CallSession).where(CallSession.provider_call_id == f"sim-{call_id}"))
    row = session.scalar(select(CallAssessment).where(CallAssessment.call_session_id == call.id)) if call else None
    if call is None or row is None:
        return {"status": "Waiting for caller...", "version": 0}
    return live.snapshot(session, call, row)


@router.get("/live/public-latest")
def public_latest(principal: Principal = Depends(require_roles("operator", "demo")),
                  session: Session = Depends(get_session), settings: Settings = Depends(get_settings_dep),
                  clock: Callable[[], datetime] = Depends(get_clock)):
    """Public judge page: the latest REAL call's assessment (no names, numbers or spoken words), only when
    PUBLIC_DEMO_SHOW_LIVE_CALLS is on, and only for calls active in the last N minutes."""
    from datetime import timedelta

    if not (settings.demo_mode and settings.public_demo_show_live_calls):
        return {"status": "Live call view is off", "version": 0}
    snap = live.latest_real_since(session, clock() - timedelta(minutes=settings.public_demo_window_minutes))
    return snap or {"status": "Waiting for caller...", "version": 0}


@router.get("/live/current")
def current(principal: Principal = Depends(OPERATOR), session: Session = Depends(get_session)):
    return live.latest(session) or {"status": "Waiting for caller...", "version": 0}


@router.get("/live/stream")
async def stream(request: Request, principal: Principal = Depends(OPERATOR)):
    """Server-sent events. Polls the database (works across API processes); emits on any change."""
    db = request.app.state.database

    def read() -> dict:
        with db.sessionmaker() as s:
            return live.latest(s) or {"status": "Waiting for caller...", "version": 0}

    async def events():
        last = None
        idle = 0.0
        while not await request.is_disconnected():
            snap = await asyncio.to_thread(read)
            key = json.dumps([snap.get("call_id"), snap.get("version"), snap.get("status")])
            if key != last:
                last, idle = key, 0.0
                yield f"data: {json.dumps(snap)}\n\n"
            else:
                idle += 0.5
                if idle >= 15:
                    idle = 0.0
                    yield ": keep-alive\n\n"
            await asyncio.sleep(0.5)

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})
