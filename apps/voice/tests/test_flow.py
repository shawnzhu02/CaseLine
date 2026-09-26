"""Call-flow behaviour against the mock gateway and a scripted fake backend. Never dials."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from caseline_voice import prompts
from caseline_voice.backend_client import BackendConflict, BackendUnavailable
from caseline_voice.flow import CallFlow, normalize_phone
from caseline_voice.schemas import ExtendedIntakeResult, TransferAttempt, TransferAuthorization, TriageResult
from caseline_voice.settings import APPROVED_DEMO_NUMBERS, VoiceSettings
from caseline_voice.telecom import MockCallGateway

FIRM_A = "+16173187562"  # the demo line all demo lawyers share
TRIAGE_FIELDS = {"caller_name": "Alex Demo", "issue_summary": "Fictional demo matter", "immediate_danger": "no",
                 "jurisdiction": "CaseLine demo region", "practice_area": "Demo matter A",
                 "callback_number": "(212) 555-0100"}


def triage_result(action="transfer", **kw) -> TriageResult:
    base = {"case_id": "case-1", "referral_id": "ref-1", "action": action, "requires_transfer_consent": True,
            "selected_firm": {"firm_id": "demo-firm-a", "display_name": "Demo Partner Firm A", "is_demo": True},
            "next_prompt": "I can connect you to Demo Partner Firm A. Would you like me to transfer you?",
            "extended_intake_questions": [{"key": "event_date", "question": "When did this happen?"}]}
    return TriageResult.model_validate({**base, **kw})


@dataclass
class FakeBackend:
    triage_result: TriageResult | Exception = field(default_factory=triage_result)
    authorize_result: TransferAuthorization | Exception | None = None
    attempt_dial: bool = True
    calls: list = field(default_factory=list)

    def triage(self, payload, provider_call_id):
        self.calls.append(("triage", payload))
        if isinstance(self.triage_result, Exception):
            raise self.triage_result
        return self.triage_result

    def authorize_transfer(self, referral_id, provider_call_id, consented):
        self.calls.append(("authorize", consented))
        if isinstance(self.authorize_result, Exception):
            raise self.authorize_result
        return self.authorize_result

    def record_transfer_attempt(self, referral_id, provider_call_id, auth):
        self.calls.append(("attempt", auth.authorization_id))
        return TransferAttempt(transfer_attempt_id="t1", destination_e164=auth.destination_e164,
                               display_name=auth.display_name, state="requested", dial=self.attempt_dial)

    def extended_intake(self, referral_id, provider_call_id, facts, consents, reason):
        self.calls.append(("extended", {"facts": facts, "consents": consents, "reason": reason}))
        return ExtendedIntakeResult(case_id="case-1", referral_id=referral_id, next_prompt="Thanks, pending.")

    def post_event(self, *a, **kw):
        self.calls.append(("event", a[1]))


def auth(dest=FIRM_A) -> TransferAuthorization:
    return TransferAuthorization(authorization_id="a1", authorization_token="tok", destination_e164=dest,
                                 display_name="Demo Partner Firm A")


def run_to_transfer_consent(backend: FakeBackend, consent="yes") -> MockCallGateway:
    flow = CallFlow(backend, VoiceSettings(_env_file=None).transfer_allowlist)
    gw = MockCallGateway(call_id="abc", fields={"intake_consent": "yes", "recording_ok": "yes", **TRIAGE_FIELDS})
    flow.on_call_start(gw)
    flow.on_task_complete(gw, "consent")
    flow.on_task_complete(gw, "triage")
    gw.fields["transfer_consent"] = consent
    flow.on_task_complete(gw, "transfer_consent")
    return gw


def test_happy_path_dials_exact_backend_destination():
    backend = FakeBackend(authorize_result=auth())
    gw = run_to_transfer_consent(backend)
    assert [t for t, _ in gw.tasks] == ["consent", "triage", "transfer_consent"]
    assert len(gw.transfers) == 1
    dest, instructions = gw.transfers[0]
    assert dest == FIRM_A
    assert "Connecting you now" in instructions and FIRM_A not in instructions
    triage_payload = backend.calls[1][1]
    assert triage_payload["facts"]["practice_area"] == "DEMO_AREA_A"
    assert triage_payload["caller"]["callback_number"] == "+12125550100"
    assert triage_payload["provider_call_id"] == "guava-abc"


def test_caller_declines_transfer_no_dial_goes_extended():
    backend = FakeBackend(authorize_result=BackendConflict("caller_declined"))
    gw = run_to_transfer_consent(backend, consent="no")
    assert gw.transfers == []
    assert gw.current_task == "extended_intake"
    assert gw.variables["extended_reason"] == "transfer_declined"


def test_live_transfer_disabled_no_dial():
    backend = FakeBackend(authorize_result=BackendConflict("live_transfer_disabled"))
    gw = run_to_transfer_consent(backend)
    assert gw.transfers == []
    assert gw.current_task == "extended_intake"


def test_destination_outside_voice_allowlist_never_dialed():
    backend = FakeBackend(authorize_result=auth("+15555550199"))
    gw = run_to_transfer_consent(backend)
    assert gw.transfers == []
    assert ("attempt", "a1") not in backend.calls
    assert gw.current_task == "extended_intake"


def test_duplicate_attempt_does_not_redial():
    backend = FakeBackend(authorize_result=auth(), attempt_dial=False)
    gw = run_to_transfer_consent(backend)
    assert gw.transfers == []


def test_api_failure_never_guesses_destination():
    backend = FakeBackend(triage_result=BackendUnavailable("timeout"))
    flow = CallFlow(backend, frozenset(APPROVED_DEMO_NUMBERS))
    gw = MockCallGateway(fields={"intake_consent": "yes", **TRIAGE_FIELDS})
    flow.on_call_start(gw)
    flow.on_task_complete(gw, "consent")
    flow.on_task_complete(gw, "triage")
    assert gw.transfers == []
    assert gw.ended_with == prompts.BACKEND_FALLBACK


@pytest.mark.parametrize("action", ["emergency_guidance", "human_review", "no_eligible_firm"])
def test_non_transfer_actions_relay_backend_prompt(action):
    backend = FakeBackend(triage_result=triage_result(action, referral_id=None, selected_firm=None,
                                                      next_prompt=f"backend says {action}"))
    flow = CallFlow(backend, frozenset(APPROVED_DEMO_NUMBERS))
    gw = MockCallGateway(fields={"intake_consent": "yes", **TRIAGE_FIELDS})
    flow.on_call_start(gw)
    flow.on_task_complete(gw, "consent")
    flow.on_task_complete(gw, "triage")
    assert gw.transfers == []
    assert f"backend says {action}" in gw.ended_with


def test_intake_consent_declined_stops_without_backend_triage():
    backend = FakeBackend()
    flow = CallFlow(backend, frozenset(APPROVED_DEMO_NUMBERS))
    gw = MockCallGateway(fields={"intake_consent": "no"})
    flow.on_call_start(gw)
    flow.on_task_complete(gw, "consent")
    assert gw.ended_with == prompts.CONSENT_DECLINED
    assert not any(c[0] == "triage" for c in backend.calls)


def test_after_hours_extended_intake_submits_facts_and_consents():
    backend = FakeBackend(triage_result=triage_result("extended_intake", next_prompt="Firm A isn't available"))
    flow = CallFlow(backend, frozenset(APPROVED_DEMO_NUMBERS))
    gw = MockCallGateway(fields={"intake_consent": "yes", **TRIAGE_FIELDS})
    flow.on_call_start(gw)
    flow.on_task_complete(gw, "consent")
    flow.on_task_complete(gw, "triage")
    assert gw.current_task == "extended_intake"
    gw.fields.update({"x_event_date": "last Tuesday", "share_consent": "yes", "sms_consent": "no"})
    flow.on_task_complete(gw, "extended_intake")
    _, sent = backend.calls[-1]
    assert sent["facts"]["event_date"] == {"value": "last Tuesday", "provenance": "caller_stated"}
    assert sent["consents"] == {"intake": True, "share_with_selected_firm": True, "sms": False, "email": False}
    assert sent["reason"] == "after_hours"
    assert gw.transfers == []


def test_voice_allowlist_cannot_be_widened():
    s = VoiceSettings(_env_file=None, voice_transfer_allowlist="+16173187562,+12676804795,+15555550199")
    assert s.transfer_allowlist == frozenset({"+16173187562"})


def test_default_inbound_number():
    assert VoiceSettings(_env_file=None).guava_agent_number == "+14849687497"


def test_normalize_phone():
    assert normalize_phone("617-318-7562", "US") == FIRM_A
    assert normalize_phone("not a number", "US") is None
