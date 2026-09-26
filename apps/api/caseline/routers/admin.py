"""Minimal operator endpoints. Full dashboard + RBAC are Phase 4."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from caseline.audit import audit
from caseline.auth import require_internal_token
from caseline.config import Settings
from caseline.deps import get_clock, get_session, get_settings_dep
from caseline.enums import CaseStatus
from caseline.errors import not_found
from caseline.models import Case, Firm
from caseline.schemas import AdminCaseRow, AdminCasesPage, AvailabilityOut
from caseline.services.availability import compute_availability

router = APIRouter(prefix="/v1", dependencies=[Depends(require_internal_token)])


def _mask(number: str | None) -> str | None:
    return None if not number else f"***{number[-2:]}"


def _initials(name: str | None) -> str | None:
    return None if not name else "".join(p[0].upper() + "." for p in name.split() if p)


@router.get("/admin/cases", response_model=AdminCasesPage)
def list_cases(
    status: CaseStatus | None = None,
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
):
    q = select(Case)
    if status:
        q = q.where(Case.status == status)
    total = session.scalar(select(func.count()).select_from(q.subquery()))
    rows = session.scalars(q.order_by(Case.created_at.desc()).limit(limit).offset(offset)).all()
    items = [
        AdminCaseRow(
            case_id=c.id, status=c.status, jurisdiction=c.jurisdiction, practice_area=c.practice_area,
            urgent=c.urgent, caller_initials=_initials(c.caller.name if c.caller else None),
            callback_masked=_mask(c.caller.callback_number if c.caller else None),
            latest_referral_status=c.referrals[-1].status if c.referrals else None, created_at=c.created_at,
        )
        for c in rows
    ]
    audit(session, operation="admin.list_cases", target_type="case", target_id=None, result="ok",
          count=len(items))
    session.commit()
    return AdminCasesPage(items=items, total=total or 0, limit=limit, offset=offset)


@router.get("/firms/{firm_slug}/availability", response_model=AvailabilityOut)
def firm_availability(firm_slug: str, session: Session = Depends(get_session),
                      settings: Settings = Depends(get_settings_dep),
                      clock: Callable[[], datetime] = Depends(get_clock)):
    firm = session.scalar(select(Firm).where(Firm.slug == firm_slug))
    if firm is None:
        raise not_found("firm")
    a = compute_availability(firm, clock(), settings)
    return AvailabilityOut(firm_id=firm.slug, open_now=a.open_now, accepting_referrals=firm.accepting_referrals,
                           accepting_live_calls=a.accepting_live_calls,
                           firm_local_time=a.firm_local_time.isoformat(), timezone=firm.timezone,
                           source=a.source, source_updated_at=firm.availability_updated_at, reason=a.reason)
