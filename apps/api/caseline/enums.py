"""Persistent state enums (spec §18). Never store free-form status strings."""

from enum import StrEnum


class CaseStatus(StrEnum):
    NEW = "new"
    CONSENT_PENDING = "consent_pending"
    TRIAGE_IN_PROGRESS = "triage_in_progress"
    TRIAGE_READY = "triage_ready"
    TRANSFER_PENDING = "transfer_pending"
    TRANSFER_CONNECTED = "transfer_connected"
    TRANSFER_FAILED = "transfer_failed"
    EXTENDED_INTAKE = "extended_intake"
    REFERRAL_PENDING = "referral_pending"
    FIRM_NOTIFIED = "firm_notified"
    FIRM_ACCEPTED = "firm_accepted"
    FIRM_DECLINED = "firm_declined"
    HUMAN_REVIEW = "human_review"
    NO_ELIGIBLE_FIRM = "no_eligible_firm"
    CLOSED = "closed"


class ReferralStatus(StrEnum):
    CREATED = "created"
    TRANSFER_AUTHORIZED = "transfer_authorized"
    TRANSFER_REQUESTED = "transfer_requested"
    TRANSFER_CONNECTED = "transfer_connected"
    TRANSFER_FAILED = "transfer_failed"
    PENDING_ASYNC = "pending_async"
    FIRM_NOTIFIED = "firm_notified"
    ACCEPTED = "accepted"
    DECLINED = "declined"
    EXPIRED = "expired"


class RoutingAction(StrEnum):
    TRANSFER = "transfer"
    EXTENDED_INTAKE = "extended_intake"
    HUMAN_REVIEW = "human_review"
    NO_ELIGIBLE_FIRM = "no_eligible_firm"
    EMERGENCY_GUIDANCE = "emergency_guidance"
    # Deviation from spec §18: caller refused intake consent, so nothing beyond the refusal is stored.
    CONSENT_REQUIRED = "consent_required"


class CallState(StrEnum):
    ACTIVE = "active"
    ENDED = "ended"


class TransferAttemptState(StrEnum):
    # Guava SDK 0.45 exposes no transfer answer/connect/fail events, so an attempt stays
    # REQUESTED until an operator (or a future verified provider signal) records the result.
    REQUESTED = "requested"
    CONNECTED = "connected"
    FAILED = "failed"
    NO_ANSWER = "no_answer"
    BUSY = "busy"
    UNKNOWN = "unknown"


class ConsentPurpose(StrEnum):
    INTAKE = "intake"
    RECORDING = "recording"
    SHARE_WITH_SELECTED_FIRM = "share_with_selected_firm"
    TRANSFER = "transfer"
    SMS = "sms"
    EMAIL = "email"


class FactProvenance(StrEnum):
    CALLER_STATED = "caller_stated"
    CALLER_CONFIRMED = "caller_confirmed"
    INFERRED = "inferred"
    UNKNOWN = "unknown"
    DECLINED = "declined"


class NotificationChannel(StrEnum):
    SMS = "sms"
    EMAIL = "email"


class NotificationStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    # Provider accepted the request; delivery is unknown until evidence arrives.
    SUBMITTED = "submitted"
    # Not sent by policy (SMS disabled, opted out, prohibited destination). Needs manual follow-up.
    BLOCKED = "blocked"
    FAILED = "failed"


class JobType(StrEnum):
    SEND_GUAVA_SMS = "send_guava_sms"
    SEND_FIRM_EMAIL = "send_firm_email"
