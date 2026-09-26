"""Typed views of CaseLine API responses the voice agent relies on (extra fields ignored)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

Action = Literal["transfer", "extended_intake", "human_review", "no_eligible_firm", "emergency_guidance",
                 "consent_required"]


class SelectedFirm(BaseModel):
    firm_id: str
    display_name: str
    is_demo: bool = False


class FollowUpQuestion(BaseModel):
    key: str
    question: str


class TriageResult(BaseModel):
    case_id: str | None
    referral_id: str | None
    action: Action
    selected_firm: SelectedFirm | None
    requires_transfer_consent: bool
    next_prompt: str
    extended_intake_questions: list[FollowUpQuestion] = []


class TransferAuthorization(BaseModel):
    authorization_id: str
    authorization_token: str
    destination_e164: str
    display_name: str


class TransferAttempt(BaseModel):
    transfer_attempt_id: str
    destination_e164: str
    display_name: str
    state: str
    dial: bool


class ExtendedIntakeResult(BaseModel):
    case_id: str
    referral_id: str
    next_prompt: str
