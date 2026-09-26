"""Call flow (spec §21). SDK-free: drives any CallGateway, so it runs against the mock in tests and demos.

The backend decides everything that matters (routing, firm, destination). This module only collects fields,
relays backend prompts, asks for consent and dials the exact backend-authorized, allowlisted destination.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
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
    def __init__(self, backend: CaseLineBackend, transfer_allowlist: frozenset[str], phone_region: str = "US",
                 live_assessment: bool = False):
        self.backend = backend
        self.allowlist = transfer_allowlist
        self.region = phone_region
        self.live = live_assessment

    # ---- lifecycle -------------------------------------------------------------------------------------------
    @staticmethod
    def provider_call_id(gw: CallGateway) -> str:
        return f"guava-{gw.call_id}"

    def on_call_start(self, gw: CallGateway) -> None:
        self._event(gw, "call_started", caller_id_number=normalize_phone(gw.caller_id_number, self.region))
        gw.start_task("consent", prompts.TRIAGE_OBJECTIVE, [
            SaySpec(prompts.NOTICE),
            "If the caller says anyone is in immediate danger, tell them to hang up and call 911 right away.",
            FieldSpec("intake_consent", "Whether the caller agrees to continue (covers the recording notice and "
                      "collecting their details). Ask once, briefly.", "multiple_choice",
                      question=prompts.CONSENT_QUESTION, choices=YES_NO),
        ])

    def on_task_complete(self, gw: CallGateway, task_id: str) -> None:
        handler = {"consent": self._after_consent, "triage": self._after_triage,
                   "story": self._after_story,
                   "transfer_consent": self._after_transfer_consent,
                   "extended_intake": self._after_extended_intake}.get(task_id)
        if handler is None and task_id.startswith("q_"):
            handler = self._after_question
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
        # One question covers both: they agreed to continue after hearing the recording notice.
        gw.set_variable("recording_ok", True)
        if self.live:
            # Routing fields come from the live assessment; the backend steers follow-up questions.
            # Fast, deterministic path: one task for the story, then one task per follow-up question chosen by the
            # backend's live assessment, then connect. Each task has exactly one field, so the agent can't stall.
            gw.start_task("story", prompts.LIVE_TRIAGE_OBJECTIVE, [
                FieldSpec("issue_summary", "What happened and where (city and state), in the caller's own words, "
                          "summarized faithfully without legal conclusions.",
                          question="What happened, and where? Which city and state?"),
            ])
            return
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
                # Live mode doesn't ask: fall back to caller ID (unverified, so not marked confirmed).
                "callback_number": normalize_phone(gw.get_field("callback_number") or
                                                   (gw.caller_id_number if self.live else None), self.region),
                "callback_confirmed": not self.live,  # classic mode reads it back (TRIAGE_READ_BACK)
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
            gw.start_task("transfer_consent", "Offer the transfer in one sentence; record the answer. Nothing else.", [
                FieldSpec("transfer_consent", "Whether the caller wants to be connected now.", "multiple_choice",
                          question=result.next_prompt, choices=YES_NO),
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

    # ---- live assessment -------------------------------------------------------------------------------------
    def on_speech(self, gw: CallGateway, speaker: str, text: str, utterance_id: str | None = None) -> None:
        """Relay one (debounced) utterance to the live board/transcript. Failures never affect the call."""
        if not self.live or not text.strip():
            return
        try:
            result = self.backend.post_utterance(self.provider_call_id(gw), speaker, text, utterance_id)
        except BackendError as exc:
            log.warning("live assessment unavailable: %s", type(exc).__name__)
            return
        # Streaming speech only feeds the live board and transcript; the call itself is driven by question tasks.
        del result

    MAX_FOLLOW_UPS = 3

    def _after_story(self, gw: CallGateway) -> None:
        story = str(gw.get_field("issue_summary") or "").strip() or "(no details given)"
        try:
            result = self.backend.post_utterance(self.provider_call_id(gw), "caller", story, "field-story")
        except BackendError as exc:
            log.warning("assessment unavailable after story: %s", type(exc).__name__)
            result = {}
        self._live_step(gw, result)

    def _after_question(self, gw: CallGateway) -> None:
        question = gw.get_variable("pending_question") or ""
        answer = str(gw.get_field("answer") or "").strip() or "(no answer)"
        pid = self.provider_call_id(gw)
        try:
            # Post the exact question first so a bare yes/no is interpreted against it.
            self.backend.post_utterance(pid, "agent", question)
            result = self.backend.post_utterance(pid, "caller", answer, f"field-{gw.get_variable('pending_key')}")
        except BackendError as exc:
            log.warning("assessment unavailable after follow-up: %s", type(exc).__name__)
            result = {}
        self._live_step(gw, result)

    def _live_step(self, gw: CallGateway, result: dict) -> None:
        """Ask the next backend-chosen question as its own task, or go straight to routing (connect)."""
        asked = gw.get_variable("asked_questions") or []
        key, question = result.get("ask_key"), result.get("ask")
        done = (result.get("urgency") == "Emergency" or result.get("assessment_ready") or not key or not question
                or key in asked or len(asked) >= self.MAX_FOLLOW_UPS)
        log.info("live step: asked=%s next=%s ready=%s done=%s", asked, key, result.get("assessment_ready"), done)
        if done:
            self._after_triage(gw)
            return
        gw.set_variable("asked_questions", [*asked, key])
        gw.set_variable("pending_key", key)
        gw.set_variable("pending_question", question)
        gw.start_task(f"q_{key}", "Ask this one question in one short sentence and record the answer. Nothing else.",
                      [FieldSpec("answer", "The caller's answer, in their own words.", question=question)])

    # ---- helpers ---------------------------------------------------------------------------------------------
    def _event(self, gw: CallGateway, event_type: str, **extra) -> None:
        try:
            self.backend.post_event(self.provider_call_id(gw), event_type,
                                    f"{self.provider_call_id(gw)}:{event_type}", **extra)
        except BackendError as exc:
            log.warning("could not record %s: %s", event_type, type(exc).__name__)


class SpeechRelay:
    """Debounces caller speech (Guava re-sends a growing utterance under the same id) and posts it off the SDK's
    event thread, so a slow API call never stalls the conversation."""

    def __init__(self, flow: CallFlow, delay_seconds: float,
                 timer_factory: Callable[[float, Callable[[], None]], Any] | None = None) -> None:
        self.flow = flow
        self.delay = delay_seconds
        self._timer_factory = timer_factory or (lambda d, fn: threading.Timer(d, fn))
        self._pending: dict[str, Any] = {}
        self._lock = threading.Lock()
        self._call_locks: dict[str, threading.Lock] = {}

    def _call_lock(self, call_id: str) -> threading.Lock:
        with self._lock:
            return self._call_locks.setdefault(call_id, threading.Lock())

    def _post(self, gw: CallGateway, speaker: str, text: str, utterance_id: str | None) -> None:
        # One post at a time per call, in order, so fact updates never race each other.
        with self._call_lock(gw.call_id):
            self.flow.on_speech(gw, speaker, text, utterance_id)

    def caller(self, gw: CallGateway, text: str, utterance_id: str | None) -> None:
        key = f"{gw.call_id}:{utterance_id or 'none'}"

        def fire() -> None:
            with self._lock:
                self._pending.pop(key, None)
            self._post(gw, "caller", text, utterance_id)

        with self._lock:
            old = self._pending.pop(key, None)
            if old is not None:
                old.cancel()
            timer = self._timer_factory(self.delay, fire)
            self._pending[key] = timer
        timer.start()

    def agent(self, gw: CallGateway, text: str) -> None:
        threading.Thread(target=self._post, args=(gw, "agent", text, None), daemon=True).start()
