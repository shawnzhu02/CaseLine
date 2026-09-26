"""Operator and firm-user endpoints: transfer outcomes, firm decisions and case reports."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime, timedelta
from html import escape

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from caseline.audit import audit
from caseline.auth import FIRM_OR_OPERATOR, OPERATOR, Principal, get_principal
from caseline.config import Settings
from caseline.crypto import LinkError
from caseline.deps import get_clock, get_session, get_settings_dep
from caseline.errors import ApiError, not_found
from caseline.models import Referral, ReportVersion
from caseline.schemas import ReferralStatusUpdate, TransferOutcomeRequest
from caseline.services import reports
from caseline.services import transfer_authorization as ta
from caseline.services.referrals import update_referral_status

router = APIRouter(prefix="/v1")
public = APIRouter()


@router.get("/me")
def whoami(principal: Principal = Depends(get_principal)):
    return {"name": principal.name, "role": principal.role,
            "firm_id": str(principal.firm_id) if principal.firm_id else None}


@router.post("/transfer-attempts/{attempt_id}/outcome")
def transfer_outcome(attempt_id: uuid.UUID, req: TransferOutcomeRequest,
                     principal: Principal = Depends(OPERATOR), session: Session = Depends(get_session),
                     clock: Callable[[], datetime] = Depends(get_clock)):
    attempt = ta.record_outcome(session, attempt_id=attempt_id, result=req.result, source=req.source,
                                actor=principal.name, now=clock())
    session.commit()
    return {"transfer_attempt_id": str(attempt.id), "state": attempt.state, "result_source": attempt.result_source}


@router.post("/referrals/{referral_id}/status")
def referral_status(referral_id: uuid.UUID, req: ReferralStatusUpdate,
                    principal: Principal = Depends(FIRM_OR_OPERATOR), session: Session = Depends(get_session),
                    clock: Callable[[], datetime] = Depends(get_clock)):
    referral = update_referral_status(session, referral_id=referral_id, status=req.status, actor=principal.name,
                                      role=principal.role, actor_firm_id=principal.firm_id, now=clock())
    session.commit()
    return {"referral_id": str(referral.id), "status": referral.status, "case_status": referral.case.status}


def _authorized_referral(session: Session, principal: Principal, referral_id: uuid.UUID) -> Referral:
    referral = session.get(Referral, referral_id)
    if referral is None:
        raise not_found("referral")
    if principal.role == "firm_user" and referral.firm_id != principal.firm_id:
        audit(session, operation="report.read", target_type="referral", target_id=referral_id,
              result="denied_cross_firm", actor=principal.name, role=principal.role)
        session.commit()
        raise not_found("referral")
    return referral


@router.get("/firm/referrals")
def firm_referrals(principal: Principal = Depends(FIRM_OR_OPERATOR), session: Session = Depends(get_session)):
    """A firm user's own referrals that have a shared report (operators see all)."""
    q = select(Referral).join(ReportVersion, ReportVersion.referral_id == Referral.id).distinct()
    if principal.role == "firm_user":
        q = q.where(Referral.firm_id == principal.firm_id)
    rows = session.scalars(q.order_by(Referral.created_at.desc()).limit(100)).all()
    return {"items": [{"referral_id": str(r.id), "status": r.status, "firm_id": r.firm.slug,
                       "created_at": r.created_at.isoformat()} for r in rows]}


@router.get("/referrals/{referral_id}/report")
def get_report(referral_id: uuid.UUID, principal: Principal = Depends(FIRM_OR_OPERATOR),
               session: Session = Depends(get_session)):
    referral = _authorized_referral(session, principal, referral_id)
    rv = reports.latest_version(session, referral.id)
    if rv is None:
        raise ApiError(404, "report_not_shared")
    audit(session, operation="report.read", target_type="report_version", target_id=rv.id, result="ok",
          actor=principal.name, role=principal.role)
    session.commit()
    return {"report_version_id": str(rv.id), "case_revision": rv.case_revision, "content": rv.content}


@router.post("/referrals/{referral_id}/report-link")
def report_link(referral_id: uuid.UUID, principal: Principal = Depends(FIRM_OR_OPERATOR),
                session: Session = Depends(get_session), settings: Settings = Depends(get_settings_dep),
                clock: Callable[[], datetime] = Depends(get_clock)):
    referral = _authorized_referral(session, principal, referral_id)
    rv = reports.latest_version(session, referral.id)
    if rv is None:
        raise ApiError(404, "report_not_shared")
    now = clock()
    ttl = timedelta(minutes=settings.report_link_ttl_minutes)
    url = reports.make_link(settings, rv, int(ttl.total_seconds()), now)
    audit(session, operation="report.link_issued", target_type="report_version", target_id=rv.id, result="ok",
          actor=principal.name, role=principal.role)
    session.commit()
    return {"url": url, "expires_at": (now + ttl).isoformat()}


def _render_report(content: dict, superseded: bool) -> str:
    def rows(items: list[dict]) -> str:
        if not items:
            return "<p class=muted>None</p>"
        return "<table>" + "".join(
            f"<tr><th>{escape(i['key'].replace('_', ' '))}</th><td>{escape(str(i['value'] or '—'))}</td></tr>"
            for i in items) + "</table>"

    caller = content["caller"]
    banner = ("<p class=warn>A newer version of this report exists. Ask CaseLine for the updated link.</p>"
              if superseded else "")
    return f"""<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><meta name=robots content=noindex>
<title>CaseLine referral {escape(content['case_reference'])}</title>
<style>body{{font:15px/1.5 system-ui,sans-serif;max-width:760px;margin:24px auto;padding:0 16px;color:#1a1a1a}}
table{{border-collapse:collapse;width:100%;margin:8px 0 16px}}th,td{{text-align:left;padding:6px 8px;
border-bottom:1px solid #ddd;vertical-align:top}}th{{width:35%;color:#555;font-weight:600}}.muted{{color:#777}}
.warn{{background:#fff4d6;padding:8px 12px;border-radius:6px}}.disc{{font-size:13px;color:#555;
border-top:1px solid #ddd;padding-top:12px}}</style></head><body>
<h1>Referral {escape(content['case_reference'])}</h1>{banner}
<p><b>For:</b> {escape(content['firm'])} · <b>Matter (provisional):</b> {escape(content['practice_area_label'])}
· <b>Jurisdiction:</b> {escape(str(content['jurisdiction']))} · <b>Revision:</b> {content['case_revision']}</p>
<p><b>Urgent:</b> {'Yes — ' + escape(str(content['urgency_reason'])) if content['urgent'] else 'No'}</p>
<h2>Caller</h2><table>
<tr><th>Name</th><td>{escape(str(caller['name'] or '—'))}</td></tr>
<tr><th>Callback</th><td>{escape(str(caller['callback_number'] or '—'))}
{' (confirmed on the call)' if caller['callback_confirmed'] else ''}</td></tr>
<tr><th>Texts permitted</th><td>{'Yes' if caller['sms_permitted'] else 'No'}</td></tr></table>
<h2>Confirmed by caller</h2>{rows(content['facts']['confirmed'])}
<h2>Caller's account (unverified)</h2>{rows(content['facts']['caller_stated'])}
<h2>Unknown or declined</h2>{rows(content['facts']['unknown_or_declined'])}
<p class=disc>{escape(content['disclaimer'])}<br>Generated {escape(content['generated_at'])}.</p>
</body></html>"""


@public.get("/r/{token}", response_class=HTMLResponse, include_in_schema=False)
def open_report_link(token: str, request: Request, session: Session = Depends(get_session),
                     settings: Settings = Depends(get_settings_dep),
                     clock: Callable[[], datetime] = Depends(get_clock)):
    """Signed, expiring report link (sent in firm alert emails). Every open is audited."""
    try:
        rv_id = uuid.UUID(reports.resolve_link(settings, token, clock()))
    except (LinkError, ValueError, KeyError) as exc:
        audit(session, operation="report.link_open", target_type="report_version", target_id=None,
              result=f"denied:{getattr(exc, 'reason', 'invalid')}", actor="link", role="anonymous")
        session.commit()
        return HTMLResponse("<h1>This link is invalid or has expired.</h1>", status_code=410)
    rv = session.get(ReportVersion, rv_id)
    if rv is None:
        return HTMLResponse("<h1>This link is invalid or has expired.</h1>", status_code=410)
    latest = reports.latest_version(session, rv.referral_id)
    audit(session, operation="report.link_open", target_type="report_version", target_id=rv.id, result="ok",
          actor="link", role="anonymous")
    session.commit()
    return HTMLResponse(_render_report(rv.content, superseded=latest is not None and latest.id != rv.id),
                        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})
