"""Outbox worker: DB-backed queue replacing Redis/RQ (RQ workers need os.fork; unusable on Windows).

Run: python -m caseline.workers.outbox [--once]
Each job reloads current data from the database, is idempotent on notifications.event_key, retries with
bounded exponential backoff, and ends in `failed` (operator queue) after max retries.
"""

from __future__ import annotations

import argparse
import logging
import time
from collections.abc import Callable
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from caseline.audit import audit
from caseline.config import APPROVED_DEMO_NUMBERS, Settings, get_settings
from caseline.enums import CaseStatus, ConsentPurpose, JobType, NotificationStatus, ReferralStatus
from caseline.models import Caller, Case, Notification, Referral, utcnow
from caseline.providers import Gateways, build_gateways
from caseline.services import consent
from caseline.services.lifecycle import set_case_status, set_referral_status
from caseline.services.notifications import render

log = logging.getLogger("caseline.outbox")


def _block(n: Notification, reason: str) -> None:
    n.status, n.status_reason = NotificationStatus.BLOCKED, reason


def _send_sms(session: Session, n: Notification, case: Case, referral: Referral | None, settings: Settings,
              gateways: Gateways) -> None:
    caller = session.get(Caller, case.caller_id) if case.caller_id else None
    if not settings.guava_sms_enabled:
        return _block(n, "guava_sms_disabled")
    if not consent.is_allowed(session, case.id, ConsentPurpose.SMS):
        return _block(n, "no_sms_consent")
    if caller is None or caller.sms_opted_out:
        return _block(n, "opted_out")
    if n.destination in APPROVED_DEMO_NUMBERS or n.destination in settings.transfer_allowlist:
        return _block(n, "destination_is_transfer_target")
    if not settings.guava_sms_from_number:
        return _block(n, "no_sender_number")
    _, body = render(n.template, case, referral)
    n.provider_response_id = gateways.sms.send_sms(from_number=settings.guava_sms_from_number,
                                                   to_number=n.destination, message=body,
                                                   idempotency_key=n.event_key)
    n.status, n.status_reason = NotificationStatus.SUBMITTED, "delivery_unknown"


def _send_email(session: Session, n: Notification, case: Case, referral: Referral | None, settings: Settings,
                gateways: Gateways) -> None:
    if referral is None or not consent.is_allowed(session, case.id, ConsentPurpose.SHARE_WITH_SELECTED_FIRM):
        return _block(n, "no_share_consent")
    subject, body = render(n.template, case, referral)
    n.provider_response_id = gateways.email.send(to=n.destination, subject=subject, text=body,
                                                 idempotency_key=n.event_key)
    n.status, n.status_reason = NotificationStatus.SUBMITTED, "delivery_unknown"
    if referral.status == ReferralStatus.PENDING_ASYNC:
        set_referral_status(session, referral, ReferralStatus.FIRM_NOTIFIED)
        if case.status == CaseStatus.REFERRAL_PENDING:
            set_case_status(session, case, CaseStatus.FIRM_NOTIFIED)


HANDLERS: dict[JobType, Callable[..., None]] = {
    JobType.SEND_GUAVA_SMS: _send_sms,
    JobType.SEND_FIRM_EMAIL: _send_email,
}


def process_due(session: Session, settings: Settings, gateways: Gateways, now: datetime | None = None,
                limit: int = 20) -> int:
    now = now or utcnow()
    q = (select(Notification)
         .where(Notification.status == NotificationStatus.PENDING, Notification.next_attempt_at <= now)
         .order_by(Notification.next_attempt_at).limit(limit))
    if session.get_bind().dialect.name == "postgresql":
        q = q.with_for_update(skip_locked=True)
    processed = 0
    for n in session.scalars(q).all():
        case = session.get(Case, n.case_id)
        referral = session.get(Referral, n.referral_id) if n.referral_id else None
        try:
            HANDLERS[n.job_type](session, n, case, referral, settings, gateways)
        except Exception as exc:  # transient provider/network error
            n.retry_count += 1
            n.last_error = type(exc).__name__
            if n.retry_count >= settings.notification_max_retries:
                n.status, n.status_reason = NotificationStatus.FAILED, "retries_exhausted"
            else:
                n.next_attempt_at = now + timedelta(
                    seconds=settings.notification_backoff_base_seconds * 2 ** (n.retry_count - 1))
        audit(session, operation=f"notification.{n.job_type}", target_type="notification", target_id=n.id,
              result=n.status, reason=n.status_reason, retry_count=n.retry_count)
        session.commit()
        processed += 1
    return processed


def main() -> None:
    from caseline.db import Database
    from caseline.logging import configure_logging

    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=float, default=5.0)
    args = parser.parse_args()
    configure_logging()
    settings = get_settings()
    db = Database(settings.database_url)
    gateways = build_gateways(settings)
    while True:
        with db.sessionmaker() as session:
            count = process_due(session, settings, gateways)
        log.info("outbox pass processed=%s", count)
        if args.once:
            return
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
