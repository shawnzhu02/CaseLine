"""Live-assessment mode: speech relay, backend steering, and the SDK accepting the live checklist."""

from __future__ import annotations

from guava.testing.mocks import MockCall

from caseline_voice import prompts
from caseline_voice.backend_client import BackendUnavailable
from caseline_voice.flow import CallFlow, SpeechRelay
from caseline_voice.telecom import GuavaCallGateway, MockCallGateway
from tests.test_flow import FakeBackend, triage_result


class LiveBackend(FakeBackend):
    def __init__(self, responses):
        super().__init__()
        self.responses = list(responses)
        self.utterances = []

    def post_utterance(self, provider_call_id, speaker, text, utterance_id=None):
        self.utterances.append((speaker, text, utterance_id))
        if speaker == "agent":
            return {}
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def flow_for(backend):
    return CallFlow(backend, frozenset({"+12676804795"}), live_assessment=True)


def test_live_triage_checklist_has_no_model_chosen_routing_fields():
    flow = flow_for(LiveBackend([]))
    gw = MockCallGateway(fields={"intake_consent": "yes"})
    flow.on_call_start(gw)
    flow.on_task_complete(gw, "consent")
    task_id, checklist = gw.tasks[-1]
    keys = [getattr(i, "key", None) for i in checklist]
    assert task_id == "story" and "jurisdiction" not in keys and "practice_area" not in keys
    # And the real SDK accepts it, including completion criteria.
    GuavaCallGateway(MockCall()).start_task("triage", "o", checklist, completion_criteria=prompts.LIVE_COMPLETION)


def live_flow(responses):
    backend = LiveBackend(responses)
    flow = flow_for(backend)
    gw = MockCallGateway(call_id="c1", caller_id_number="+12125550100",
                         fields={"intake_consent": "yes", "issue_summary": "My house burned down in Boston"})
    flow.on_call_start(gw)
    flow.on_task_complete(gw, "consent")
    return backend, flow, gw


def test_story_then_one_task_per_backend_question_then_connect():
    backend, flow, gw = live_flow([
        {"ask": "Did you or anyone else need medical treatment after this?", "ask_key": "medical"},
        {"ask": "Were there any known problems with the property before this happened?", "ask_key": "prior_hazard"},
        {"assessment_ready": True},
    ])
    backend.triage_result = triage_result("transfer")
    assert gw.current_task == "story"
    flow.on_task_complete(gw, "story")
    assert gw.current_task == "q_medical"
    gw.fields["answer"] = "Yes, smoke inhalation"
    flow.on_task_complete(gw, "q_medical")
    assert gw.current_task == "q_prior_hazard"
    gw.fields["answer"] = "Yes, we told the landlord"
    flow.on_task_complete(gw, "q_prior_hazard")
    assert gw.current_task == "transfer_consent"  # straight to connect, no extra questions
    # the question text was posted before each answer, so a bare yes/no is interpreted correctly
    assert ("agent", "Did you or anyone else need medical treatment after this?", None) in backend.utterances


def test_never_loops_even_if_backend_keeps_asking():
    same = {"ask": "Where did this happen?", "ask_key": "location"}
    backend, flow, gw = live_flow([same, same, same, same])
    backend.triage_result = triage_result("human_review", referral_id=None, selected_firm=None)
    flow.on_task_complete(gw, "story")
    gw.fields["answer"] = "not sure"
    flow.on_task_complete(gw, "q_location")
    assert gw.ended_with is not None  # repeated question -> proceeds to routing, which ends the call


def test_follow_ups_are_capped():
    backend, flow, gw = live_flow([{"ask": f"Q{i}?", "ask_key": f"k{i}"} for i in range(6)])
    backend.triage_result = triage_result("human_review", referral_id=None, selected_firm=None)
    flow.on_task_complete(gw, "story")
    for i in range(CallFlow.MAX_FOLLOW_UPS):
        gw.fields["answer"] = "hmm"
        flow.on_task_complete(gw, f"q_k{i}")
    assert sum(1 for t, _ in gw.tasks if t.startswith("q_")) == CallFlow.MAX_FOLLOW_UPS
    assert gw.ended_with is not None


def test_emergency_goes_straight_to_routing():
    backend, flow, gw = live_flow([{"urgency": "Emergency", "ask": "Where?", "ask_key": "location"}])
    backend.triage_result = triage_result("emergency_guidance", referral_id=None, selected_firm=None,
                                          next_prompt="Call 911 now.")
    flow.on_task_complete(gw, "story")
    assert "911" in gw.ended_with


def test_backend_failure_still_routes_safely():
    backend, flow, gw = live_flow([BackendUnavailable("x")])
    backend.triage_result = BackendUnavailable("x")
    flow.on_task_complete(gw, "story")
    assert gw.ended_with == prompts.BACKEND_FALLBACK and gw.transfers == []


def test_speech_relay_never_steers_the_call():
    flow = flow_for(LiveBackend([{"ask": "Where?", "ask_key": "location", "assessment_ready": True}]))
    gw = MockCallGateway()
    flow.on_speech(gw, "caller", "hello")
    assert gw.instructions == []


def test_classic_mode_ignores_speech():
    backend = LiveBackend([])
    flow = CallFlow(backend, frozenset())
    flow.on_speech(MockCallGateway(), "caller", "hello")
    assert backend.utterances == []


def test_relay_debounces_partial_utterances():
    timers = []

    class FakeTimer:
        def __init__(self, delay, fn):
            self.fn, self.cancelled = fn, False
            timers.append(self)

        def start(self):
            pass

        def cancel(self):
            self.cancelled = True

    backend = LiveBackend([{"ask": None}])
    relay = SpeechRelay(flow_for(backend), 0.9, timer_factory=FakeTimer)
    gw = MockCallGateway(call_id="c1")
    relay.caller(gw, "My house", "u1")
    relay.caller(gw, "My house burned down", "u1")
    assert timers[0].cancelled and not timers[1].cancelled
    timers[1].fn()
    assert backend.utterances == [("caller", "My house burned down", "u1")]


def test_unexpected_error_still_ends_the_call():
    class Boom(LiveBackend):
        def post_utterance(self, *a, **k):
            raise RuntimeError("unexpected")

    flow = flow_for(Boom([]))
    gw = MockCallGateway(fields={"intake_consent": "yes", "issue_summary": "x"})
    flow.on_call_start(gw)
    flow.on_task_complete(gw, "consent")
    flow._after_triage = lambda gw: (_ for _ in ()).throw(ValueError("bug"))  # simulate a bug downstream
    flow.on_task_complete(gw, "story")
    assert gw.ended_with == prompts.BACKEND_FALLBACK


def test_follow_up_questions_are_optional():
    backend, flow, gw = live_flow([{"ask": "Where?", "ask_key": "location"}])
    flow.on_task_complete(gw, "story")
    field = gw.tasks[-1][1][0]
    assert gw.current_task == "q_location" and field.required is False
