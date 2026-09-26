"""Pydantic request/response contracts (spec §6, §19)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from caseline.config import is_e164
from caseline.enums import (
    CaseStatus,
    FactProvenance,
    NotificationStatus,
    ReferralStatus,
    RoutingAction,
    TransferAttemptState,
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CallerIn(StrictModel):
    name: str | None = Field(default=None, max_length=200)
    callback_number: str | None = Field(default=None, max_length=32)
    callback_confirmed: bool = False
    email: str | None = Field(default=None, max_length=320)
    preferred_language: str | None = Field(default="en", max_length=16)
    accessibility_needs: str | None = Field(default=None, max_length=500)

    @field_validator("callback_number")
    @classmethod
    def _e164(cls, v: str | None) -> str | None:
        if v is not None and not is_e164(v):
            raise ValueError("callback_number must be E.164")
        return v


class TriageFacts(StrictModel):
    jurisdiction: str | None = Field(default=None, max_length=64)
    practice_area: str | None = Field(default=None, max_length=64)
    issue_summary: str = Field(min_length=1, max_length=4000)
    desired_help: str | None = Field(default=None, max_length=1000)
    location: str | None = Field(default=None, max_length=200)
    immediate_danger: bool = False
    caller_reported_deadline: str | None = Field(default=None, max_length=200)
    has_existing_lawyer: bool | None = None


class TriageConsents(StrictModel):
    intake: bool
    recording: bool | None = None
    share_with_selected_firm: bool = False
    sms: bool = False
    email: bool = False


class TriageRequest(StrictModel):
    provider_call_id: str = Field(min_length=1, max_length=128)
    caller: CallerIn
    facts: TriageFacts
    consents: TriageConsents


class SelectedFirm(BaseModel):
    firm_id: str  # stable slug
    display_name: str
    is_demo: bool


class FollowUpQuestion(BaseModel):
    key: str
    question: str


class TriageResponse(BaseModel):
    case_id: uuid.UUID | None
    referral_id: uuid.UUID | None
    action: RoutingAction
    selected_firm: SelectedFirm | None
    requires_transfer_consent: bool
    # Always null from triage: the dial destination is only issued by authorize-transfer.
    transfer_authorization: None = None
    availability_source: str | None = None
    next_prompt: str
    permitted_next_steps: list[str]
    extended_intake_questions: list[FollowUpQuestion] = []


class AuthorizeTransferRequest(StrictModel):
    provider_call_id: str
    caller_consented: bool


class AuthorizeTransferResponse(BaseModel):
    authorization_id: uuid.UUID
    authorization_token: str
    destination_e164: str
    display_name: str
    expires_at: datetime


class TransferAttemptRequest(StrictModel):
    authorization_id: uuid.UUID
    authorization_token: str
    provider_call_id: str
    state: Literal["requested"] = "requested"


class TransferAttemptResponse(BaseModel):
    transfer_attempt_id: uuid.UUID
    referral_id: uuid.UUID
    state: TransferAttemptState
    destination_e164: str
    display_name: str
    # True only for the first recording of this attempt; the voice agent must dial only when true.
    dial: bool


class TransferOutcomeRequest(StrictModel):
    result: Literal["connected", "failed", "no_answer", "busy"]
    source: Literal["operator"]
    note: str | None = Field(default=None, max_length=500)


class FactIn(StrictModel):
    value: Any
    provenance: FactProvenance = FactProvenance.CALLER_STATED
    confirmed: bool = False


class FactsPatch(StrictModel):
    facts: dict[str, FactIn]


class ExtendedIntakeRequest(StrictModel):
    provider_call_id: str
    facts: dict[str, FactIn] = {}
    consents: TriageConsents | None = None
    reason: Literal["after_hours", "transfer_declined", "transfer_failed", "live_transfer_unavailable", "no_live"]


class ExtendedIntakeResponse(BaseModel):
    case_id: uuid.UUID
    referral_id: uuid.UUID
    case_status: CaseStatus
    referral_status: ReferralStatus
    notifications: list[NotificationOut]
    next_prompt: str


class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    event_key: str
    channel: str
    status: NotificationStatus
    status_reason: str | None


class ReferralStatusUpdate(StrictModel):
    # The actor is the authenticated principal; firm users may only update their own firm's referrals.
    status: Literal["accepted", "declined", "expired"]
    note: str | None = Field(default=None, max_length=500)


class CaseStatusOverride(StrictModel):
    status: Literal["human_review", "triage_ready", "closed"]
    note: str = Field(min_length=1, max_length=500)


class ReassignRequest(StrictModel):
    firm_id: str = Field(min_length=1, max_length=64)  # firm slug
    note: str = Field(min_length=1, max_length=500)


class OperatorConsent(StrictModel):
    """Consent captured by an operator on a recorded follow-up phone call."""

    purpose: Literal["share_with_selected_firm", "sms", "email"]
    allowed: bool
    firm_id: str | None = Field(default=None, max_length=64)  # required for share_with_selected_firm


class FirmPatch(StrictModel):
    accepting_referrals: bool | None = None
    accepting_live_calls: bool | None = None
    max_open_referrals: int | None = Field(default=None, ge=0, le=1000)


class FirmOut(BaseModel):
    firm_id: str
    display_name: str
    is_demo: bool
    is_fixture: bool
    verification_status: str
    jurisdictions: list[str]
    practice_areas: list[str]
    timezone: str
    accepting_referrals: bool
    accepting_live_calls: bool
    max_open_referrals: int
    open_now: bool
    availability_reason: str
    availability_updated_at: datetime
    has_transfer_number: bool


class CallEventIn(StrictModel):
    provider: Literal["guava"] = "guava"
    provider_event_id: str = Field(min_length=1, max_length=200)
    provider_call_id: str
    event_type: Literal["call_started", "session_ended", "transfer_command_sent"]
    termination_reason: str | None = Field(default=None, max_length=40)
    caller_id_number: str | None = Field(default=None, max_length=32)


class AvailabilityOut(BaseModel):
    firm_id: str
    open_now: bool
    accepting_referrals: bool
    accepting_live_calls: bool
    firm_local_time: str
    timezone: str
    source: Literal["configured_hours", "simulated_demo_availability"]
    source_updated_at: datetime
    reason: str


class AdminCaseRow(BaseModel):
    case_id: uuid.UUID
    status: CaseStatus
    jurisdiction: str | None
    practice_area: str | None
    urgent: bool
    caller_initials: str | None
    callback_masked: str | None
    latest_referral_status: ReferralStatus | None
    created_at: datetime


class AdminCasesPage(BaseModel):
    items: list[AdminCaseRow]
    total: int
    limit: int
    offset: int


ExtendedIntakeResponse.model_rebuild()
