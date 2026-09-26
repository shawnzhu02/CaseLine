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
from caseline.errors import not_found
from caseline.models import Case
from caseline.schemas import FactsPatch, TriageRequest, TriageResponse
from caseline.services.intake import triage
from caseline.services.referrals import upsert_facts

router = APIRouter(prefix="/v1", dependencies=[Depends(require_internal_token)])


@router.post("/intake/triage", response_model=TriageResponse)
def post_triage(
    req: TriageRequest,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=200),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
    clock: Callable[[], datetime] = Depends(get_clock),
):
    body_hash = idempotency.request_hash(req)
    replay = idempotency.lookup(session, "triage", idempotency_key, body_hash)
    if replay is not None:
        return replay
    result = triage(session, req, settings, clock())
    idempotency.store(session, "triage", idempotency_key, body_hash, result)
    session.commit()
    return result


@router.patch("/cases/{case_id}/facts")
def patch_facts(case_id: uuid.UUID, patch: FactsPatch, session: Session = Depends(get_session)):
    case = session.get(Case, case_id)
    if case is None:
        raise not_found("case")
    upsert_facts(session, case, patch.facts)
    session.commit()
    return {"case_id": str(case.id), "summary_version": case.summary_version}
