"""Call flow (spec §21). SDK-free: drives any CallGateway, so it runs against the mock in tests and demos.

The backend decides everything that matters (routing, firm, destination). This module only collects fields,
relays backend prompts, asks for consent and dials the exact backend-authorized, allowlisted destination.
"""

from __future__ import annotations

import logging
from typing import Any

import phonenumbers

from caseline_voice import prompts
from caseline_voice.backend_client import BackendConflict, BackendError, CaseLineBackend
from caseline_voice.schemas import FollowUpQuestion, TriageResult
from caseline_voice.telecom import CallGateway, FieldSpec, SaySpec

log = logging.getLogger("caseline_voice.flow")

YES_NO = ["yes", "no"]


def _yes(value: Any) -> bool:
    return isinstance(value, str) and value.strip().lower() in {"yes", "y", "true"}


def normalize_phone(raw: Any, region: str) -> str | None:
    if not raw:
        return None
    try:
        parsed = phonenumbers.parse(str(raw), region)
    except phonenumbers.NumberParseException:
        return None
    if not phonenumbers.is_valid_number(parsed):
        return None
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


class CallFlow:
    def __init__(self, backend: CaseLineBackend, transfer_allowlist: frozenset[str], phone_region: str = "US"):
        self.backend = backend
        self.allowlist = transfer_allowlist
        self.region = phone_region

    # ---- lifecycle -------------------------------------------------------------------------------------------
    @staticmethod
    def provider_call_id(gw: CallGateway) -> str:
        return f"guava-{gw.call_id}"

    def on_call_start(self, gw: CallGateway) -> None:
        self._event(gw, "call_started", caller_id_number=normalize_phone(gw.caller_id_number, self.region))
        gw.start_task("consent", prompts.TRIAGE_OBJECTIVE, [
            SaySpec(prompts.NOTICE),
            "If the caller says anyone is in immediate danger, tell them to hang up and call 911 right away.",
            FieldSpec("intake_consent", "Whether the caller agrees to share information.", "multiple_choice",
                      question=prompts.CONSENT_QUESTION, choices=YES_NO),
            FieldSpec("recording_ok", "Whether the caller is okay with the call being recorded and transcribed.",
                      "multiple_choice", question="Is that okay with you?", choices=YES_NO),
        ])

    def on_task_complete(self, gw: CallGateway, task_id: str) -> None:
        handler = {"consent": self._after_consent, "triage": self._after_triage,
                   "transfer_consent": self._after_transfer_consent,
                   "extended_intake": self._after_extended_intake}.get(task_id)
        if handler is None:
            log.warning("unknown task completed: %s", task_id)
            return
        try:
            handler(gw)
        except BackendError as exc:
            # Never guess a firm or number: human-review fallback.
            log.error("backend failure during %s: %s", task_id, type(exc).__name__)
            gw.end_call(prompts.BACKEND_FALLBACK)

    def on_session_end(self, gw: CallGateway, termination_reason: str | None) -> None:
        self._event(gw, "session_ended", termination_reason=termination_reason)

    # ---- stages ----------------------------------------------------------------------------------------------
    def _after_consent(self, gw: CallGateway) -> None:
        if not _yes(gw.get_field("intake_consent")):
            gw.end_call(prompts.CONSENT_DECLINED)
            return
        gw.set_variable("recording_ok", _yes(gw.get_field("recording_ok")))
        gw.start_task("triage", prompts.TRIAGE_OBJECTIVE, [
            FieldSpec("caller_name", "The caller's name.", question="What's your name?"),
            FieldSpec("issue_summary", "Let the caller explain what happened in their own words. Summarize it "
                      "faithfully as the caller's account, without legal conclusions."),
            FieldSpec("immediate_danger", "Whether anyone is in immediate physical danger right now.",
                      "multiple_choice", question="Is anyone in immediate danger right now?", choices=YES_NO),
            FieldSpec("jurisdiction", "Where the issue happened / where the caller needs help. Choose the closest "
                      "option; if unsure pick 'Other or not sure'.", "multiple_choice",
                      choices=list(prompts.JURISDICTION_CHOICES)),
            FieldSpec("practice_area", "Provisional category of the caller's issue. If it doesn't clearly fit, pick "
                      "'Something else or not sure'. Never tell the caller this is a legal classification.",
                      "multiple_choice", choices=list(prompts.PRACTICE_AREA_CHOICES)),
            FieldSpec("deadline", "Any court date, hearing or deadline the caller has been told about, in their "
                      "words. Do not calculate or estimate deadlines.", required=False,
                      question="Have you been told about any court dates or deadlines?"),
            FieldSpec("has_lawyer", "Whether the caller already has a lawyer for this.", "multiple_choice",
                      required=False, choices=["yes", "no", "not sure"]),
            FieldSpec("callback_number", "The best phone number to reach the caller, confirmed digit by digit.",
                      question="What's the best number to reach you?"),
            prompts.TRIAGE_READ_BACK,
        ])

    def _triage_payload(self, gw: CallGateway) -> dict:
        danger = _yes(gw.get_field("immediate_danger"))
        return {
            "provider_call_id": self.provider_call_id(gw),
            "caller": {
                "name": gw.get_field("caller_name"),
                "callback_number": normalize_phone(gw.get_field("callback_number"), self.region),
                "callback_confirmed": True,  # read back per TRIAGE_READ_BACK
                "preferred_language": "en",
            },
            "facts": {
                "jurisdiction": prompts.JURISDICTION_CHOICES.get(gw.get_field("jurisdiction") or ""),
                "practice_area": prompts.PRACTICE_AREA_CHOICES.get(gw.get_field("practice_area") or ""),
                "issue_summary": (gw.get_field("issue_summary") or "not provided")[:4000],
                "immediate_danger": danger,
                "caller_reported_deadline": gw.get_field("deadline") or None,
                "has_existing_lawyer": {"yes": True, "no": False}.get(str(gw.get_field("has_lawyer")).lower()),
            },
            "consents": {"intake": True, "recording": gw.get_variable("recording_ok"),
                         "share_with_selected_firm": False, "sms": False},
        }

    def _after_triage(self, gw: CallGateway) -> None:
        result = self.backend.triage(self._triage_payload(gw), self.provider_call_id(gw))
        gw.set_variable("referral_id", result.referral_id)
        gw.set_variable("firm_name", result.selected_firm.display_name if result.selected_firm else None)
        gw.set_variable("questions", [q.model_dump() for q in result.extended_intake_questions])
        if result.action == "transfer" and result.referral_id and result.selected_firm:
            gw.start_task("transfer_consent", "Offer the transfer and record the caller's decision.", [
                SaySpec(result.next_prompt),
                FieldSpec("transfer_consent", "Whether the caller wants to be transferred to the named firm now.",
                          "multiple_choice", choices=YES_NO),
            ])
        elif result.action == "extended_intake" and result.referral_id:
            self._start_extended(gw, result, "after_hours")
        else:
            # emergency_guidance, human_review, no_eligible_firm, consent_required: say exactly what the backend says.
            gw.end_call(f"Say this to the caller: {result.next_prompt}")

    def _after_transfer_consent(self, gw: CallGateway) -> None:
        referral_id = gw.get_variable("referral_id")
        consented = _yes(gw.get_field("transfer_consent"))
        try:
            auth = self.backend.authorize_transfer(referral_id, self.provider_call_id(gw), consented)
        except BackendConflict as exc:
            reason = "transfer_declined" if exc.reason == "caller_declined" else "live_transfer_unavailable"
            self._start_extended(gw, None, reason)
            return
        destination = auth.destination_e164
        if destination not in self.allowlist or normalize_phone(destination, self.region) != destination:
            log.error("backend destination rejected by voice allowlist")
            self._start_extended(gw, None, "live_transfer_unavailable")
            return
        attempt = self.backend.record_transfer_attempt(referral_id, self.provider_call_id(gw), auth)
        if attempt.dial and attempt.destination_e164 == destination:
            gw.transfer(destination, prompts.TRANSFER_HANDOFF.format(display_name=auth.display_name))

    def _start_extended(self, gw: CallGateway, result: TriageResult | None, reason: str) -> None:
        gw.set_variable("extended_reason", reason)
        firm = gw.get_variable("firm_name") or "the firm"
        questions = [FollowUpQuestion(**q) for q in gw.get_variable("questions", [])]
        checklist: list = []
        if result is not None:
            checklist.append(SaySpec(result.next_prompt))
        elif reason == "transfer_declined":
            checklist.append(SaySpec("No problem. I'll ask a few more questions so the firm can review your matter "
                                     "later instead. No lawyer has been arranged yet."))
        else:
            checklist.append(SaySpec("I can't complete a live connection right now. I'll ask a few more questions "
                                     "so the firm can review your matter later. No lawyer has been arranged yet."))
        checklist += [FieldSpec(f"x_{q.key}", q.question, question=q.question, required=False) for q in questions]
        checklist += [
            FieldSpec("share_consent", f"Permission to share a summary of this intake with {firm}.",
                      "multiple_choice", choices=YES_NO,
                      question=f"May we share a summary of what you've told us with {firm} so they can review it?"),
            FieldSpec("sms_consent", "Permission to text the caller status updates.", "multiple_choice",
                      choices=YES_NO, question="May we text you updates at your callback number? You can reply "
                      "STOP to opt out."),
        ]
        gw.start_task("extended_intake", prompts.EXTENDED_OBJECTIVE, checklist)

    def _after_extended_intake(self, gw: CallGateway) -> None:
        facts = {}
        for q in gw.get_variable("questions", []):
            value = gw.get_field(f"x_{q['key']}")
            facts[q["key"]] = ({"value": value, "provenance": "caller_stated"} if value
                               else {"value": None, "provenance": "unknown"})
        consents = {"intake": True, "share_with_selected_firm": _yes(gw.get_field("share_consent")),
                    "sms": _yes(gw.get_field("sms_consent")), "email": False}
        result = self.backend.extended_intake(gw.get_variable("referral_id"), self.provider_call_id(gw), facts,
                                              consents, gw.get_variable("extended_reason", "after_hours"))
        gw.end_call(f"Say this to the caller: {result.next_prompt}")

    # ---- helpers ---------------------------------------------------------------------------------------------
    def _event(self, gw: CallGateway, event_type: str, **extra) -> None:
        try:
            self.backend.post_event(self.provider_call_id(gw), event_type,
                                    f"{self.provider_call_id(gw)}:{event_type}", **extra)
        except BackendError as exc:
            log.warning("could not record %s: %s", event_type, type(exc).__name__)
