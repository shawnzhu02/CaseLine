"""Firm-local office-hours computation using IANA time zones (spec §4 step 5)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from caseline.config import Settings
from caseline.models import Firm


@dataclass(frozen=True)
class Availability:
    open_now: bool
    accepting_live_calls: bool  # open_now AND firm has live calls enabled
    firm_local_time: datetime
    source: str  # configured_hours | simulated_demo_availability
    reason: str


def compute_availability(firm: Firm, now_utc: datetime, settings: Settings) -> Availability:
    if now_utc.tzinfo is None:
        raise ValueError("now_utc must be timezone-aware")
    local = now_utc.astimezone(ZoneInfo(firm.timezone))

    # Demo-only override. Visibly labeled, inert outside demo mode, and never bypasses the
    # allowlist, consent or authorization checks (those live in transfer_authorization).
    if firm.is_demo and settings.simulated_availability_active:
        return Availability(True, firm.accepting_live_calls, local, "simulated_demo_availability",
                            "SIMULATED AVAILABILITY (demo only)")

    if any(c.closed_on == local.date() for c in firm.closures):
        return Availability(False, False, local, "configured_hours", "closed: exception date")

    local_t = local.time().replace(tzinfo=None)
    open_now = any(h.weekday == local.weekday() and h.opens <= local_t < h.closes for h in firm.hours)
    if not open_now:
        return Availability(False, False, local, "configured_hours", "closed: outside office hours")
    if not firm.accepting_live_calls:
        return Availability(True, False, local, "configured_hours", "open: live calls disabled")
    return Availability(True, True, local, "configured_hours", "open: accepting live calls")
