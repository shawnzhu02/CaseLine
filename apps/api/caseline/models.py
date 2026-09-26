"""Persistence model (spec §5, §23). Call, case and referral state are kept separate."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from caseline.db import Base, UTCDateTime
from caseline.enums import (
    CallState,
    CaseStatus,
    ConsentPurpose,
    FactProvenance,
    JobType,
    NotificationChannel,
    NotificationStatus,
    ReferralStatus,
    TransferAttemptState,
)

JSONType = JSON().with_variant(JSONB(), "postgresql")


def utcnow() -> datetime:
    return datetime.now(UTC)


def _enum(e: type) -> Enum:
    # Store enum values as plain strings (no native PG enum) to keep migrations simple.
    return Enum(e, native_enum=False, length=40, values_callable=lambda x: [m.value for m in x])


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utcnow, onupdate=utcnow, nullable=False
    )


class Caller(TimestampMixin, Base):
    __tablename__ = "callers"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    # Contact details are PII. Encryption at rest is provided by the managed database in staging/prod;
    # application-level field encryption is a Phase 4 item (see docs/decisions/0001-architecture.md).
    name: Mapped[str | None] = mapped_column(String(200))
    callback_number: Mapped[str | None] = mapped_column(String(32))
    callback_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    email: Mapped[str | None] = mapped_column(String(320))
    preferred_language: Mapped[str | None] = mapped_column(String(16))
    accessibility_needs: Mapped[str | None] = mapped_column(Text)
    sms_opted_out: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class CallSession(TimestampMixin, Base):
    __tablename__ = "call_sessions"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(String(20), default="guava", nullable=False)
    provider_call_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    state: Mapped[CallState] = mapped_column(_enum(CallState), default=CallState.ACTIVE, nullable=False)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    termination_reason: Mapped[str | None] = mapped_column(String(40))
    caller_id_number: Mapped[str | None] = mapped_column(String(32))
    case_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("cases.id", ondelete="SET NULL"))


class Case(TimestampMixin, Base):
    __tablename__ = "cases"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    caller_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("callers.id", ondelete="RESTRICT"))
    status: Mapped[CaseStatus] = mapped_column(_enum(CaseStatus), default=CaseStatus.NEW, nullable=False)
    practice_area: Mapped[str | None] = mapped_column(String(64))
    practice_area_confidence: Mapped[str | None] = mapped_column(String(16))
    jurisdiction: Mapped[str | None] = mapped_column(String(64))
    urgent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    urgency_reason: Mapped[str | None] = mapped_column(String(64))
    routing_action: Mapped[str | None] = mapped_column(String(40))
    summary_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    assigned_operator: Mapped[str | None] = mapped_column(String(120))

    caller: Mapped[Caller | None] = relationship()
    facts: Mapped[list[CaseFact]] = relationship(back_populates="case", order_by="CaseFact.key")
    referrals: Mapped[list[Referral]] = relationship(back_populates="case", order_by="Referral.created_at")

    __table_args__ = (Index("ix_cases_status", "status"),)


class ConsentEvent(Base):
    __tablename__ = "consent_events"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    caller_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("callers.id", ondelete="RESTRICT"))
    case_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("cases.id", ondelete="RESTRICT"))
    call_session_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("call_sessions.id", ondelete="RESTRICT"))
    # For transfer/sharing consent: the firm the caller was told about, recorded by slug.
    subject_firm_slug: Mapped[str | None] = mapped_column(String(64))
    purpose: Mapped[ConsentPurpose] = mapped_column(_enum(ConsentPurpose), nullable=False)
    allowed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    policy_version: Mapped[str] = mapped_column(String(32), nullable=False)
    capture_method: Mapped[str] = mapped_column(String(32), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)

    __table_args__ = (Index("ix_consent_case_purpose", "case_id", "purpose"),)


class CaseFact(Base):
    __tablename__ = "case_facts"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id", ondelete="RESTRICT"), nullable=False)
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[Any | None] = mapped_column(JSONType, nullable=True)
    provenance: Mapped[FactProvenance] = mapped_column(_enum(FactProvenance), nullable=False)
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow, nullable=False)

    case: Mapped[Case] = relationship(back_populates="facts")
    __table_args__ = (UniqueConstraint("case_id", "key", name="uq_case_fact_key"),)


class Firm(TimestampMixin, Base):
    __tablename__ = "firms"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    legal_name: Mapped[str | None] = mapped_column(String(200))
    registration_id: Mapped[str | None] = mapped_column(String(64))
    verification_status: Mapped[str] = mapped_column(String(20), default="unverified", nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_fixture: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    jurisdictions: Mapped[list[str]] = mapped_column(JSONType, default=list, nullable=False)
    practice_areas: Mapped[list[str]] = mapped_column(JSONType, default=list, nullable=False)
    languages: Mapped[list[str]] = mapped_column(JSONType, default=list, nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    transfer_number: Mapped[str | None] = mapped_column(String(32))
    referral_email: Mapped[str | None] = mapped_column(String(320))
    accepting_referrals: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    accepting_live_calls: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    max_open_referrals: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    routing_priority: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    availability_updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)

    hours: Mapped[list[FirmHours]] = relationship(back_populates="firm", cascade="all, delete-orphan")
    closures: Mapped[list[FirmClosure]] = relationship(back_populates="firm", cascade="all, delete-orphan")


class FirmHours(Base):
    __tablename__ = "firm_hours"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firms.id", ondelete="CASCADE"), nullable=False)
    weekday: Mapped[int] = mapped_column(Integer, nullable=False)  # Monday=0 .. Sunday=6, firm-local
    opens: Mapped[time] = mapped_column(Time, nullable=False)
    closes: Mapped[time] = mapped_column(Time, nullable=False)

    firm: Mapped[Firm] = relationship(back_populates="hours")


class FirmClosure(Base):
    __tablename__ = "firm_closures"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firms.id", ondelete="CASCADE"), nullable=False)
    closed_on: Mapped[date] = mapped_column(Date, nullable=False)
    reason: Mapped[str | None] = mapped_column(String(120))

    firm: Mapped[Firm] = relationship(back_populates="closures")
    __table_args__ = (UniqueConstraint("firm_id", "closed_on", name="uq_firm_closure_day"),)


class Referral(TimestampMixin, Base):
    __tablename__ = "referrals"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id", ondelete="RESTRICT"), nullable=False)
    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firms.id", ondelete="RESTRICT"), nullable=False)
    status: Mapped[ReferralStatus] = mapped_column(
        _enum(ReferralStatus), default=ReferralStatus.CREATED, nullable=False
    )
    share_consent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("consent_events.id", ondelete="RESTRICT")
    )
    selection_rule_version: Mapped[str] = mapped_column(String(32), nullable=False)
    selection_rationale: Mapped[dict] = mapped_column(JSONType, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    case: Mapped[Case] = relationship(back_populates="referrals")
    firm: Mapped[Firm] = relationship()

    __table_args__ = (Index("ix_referrals_status", "status"), Index("ix_referrals_firm_id", "firm_id"))


class TransferAuthorization(Base):
    __tablename__ = "transfer_authorizations"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    referral_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("referrals.id", ondelete="RESTRICT"), nullable=False)
    call_session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("call_sessions.id", ondelete="RESTRICT"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    destination_e164: Mapped[str] = mapped_column(String(32), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)


class TransferAttempt(TimestampMixin, Base):
    __tablename__ = "transfer_attempts"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    referral_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("referrals.id", ondelete="RESTRICT"), nullable=False)
    authorization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("transfer_authorizations.id", ondelete="RESTRICT"), unique=True, nullable=False
    )
    provider_transfer_id: Mapped[str | None] = mapped_column(String(128))
    dial_target: Mapped[str] = mapped_column(String(32), nullable=False)
    state: Mapped[TransferAttemptState] = mapped_column(_enum(TransferAttemptState), nullable=False)
    attempted_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    connected_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    failed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    result_source: Mapped[str | None] = mapped_column(String(32))  # operator | provider_verified
    provider_signal: Mapped[str | None] = mapped_column(String(40))  # e.g. guava termination_reason


class Notification(TimestampMixin, Base):
    """Outbox row: every communication is persisted here before any send is attempted."""

    __tablename__ = "notifications"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    event_key: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    job_type: Mapped[JobType] = mapped_column(_enum(JobType), nullable=False)
    channel: Mapped[NotificationChannel] = mapped_column(_enum(NotificationChannel), nullable=False)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id", ondelete="RESTRICT"), nullable=False)
    referral_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("referrals.id", ondelete="RESTRICT"))
    destination: Mapped[str] = mapped_column(String(320), nullable=False)
    template: Mapped[str] = mapped_column(String(64), nullable=False)
    template_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[NotificationStatus] = mapped_column(
        _enum(NotificationStatus), default=NotificationStatus.PENDING, nullable=False
    )
    status_reason: Mapped[str | None] = mapped_column(String(64))
    provider_response_id: Mapped[str | None] = mapped_column(String(128))
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_attempt_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    last_error: Mapped[str | None] = mapped_column(String(200))

    __table_args__ = (Index("ix_notifications_status", "status"),)


class ProviderEvent(Base):
    __tablename__ = "provider_events"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    provider_event_id: Mapped[str] = mapped_column(String(200), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    received_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    payload_redacted: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)

    __table_args__ = (UniqueConstraint("provider", "provider_event_id", name="uq_provider_event"),)


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_keys"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    scope: Mapped[str] = mapped_column(String(64), nullable=False)
    key: Mapped[str] = mapped_column(String(200), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    response_body: Mapped[dict] = mapped_column(JSONType, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)

    __table_args__ = (UniqueConstraint("scope", "key", name="uq_idempotency_scope_key"),)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[str | None] = mapped_column(String(64))
    result: Mapped[str] = mapped_column(String(32), nullable=False)
    event_metadata: Mapped[dict] = mapped_column("metadata", JSONType, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
