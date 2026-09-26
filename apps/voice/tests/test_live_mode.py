"""Live-assessment mode: speech relay, backend steering, and the SDK accepting the live checklist."""

from __future__ import annotations

from guava.testing.mocks import MockCall

from caseline_voice import prompts
from caseline_voice.backend_client import BackendUnavailable
from caseline_voice.flow import CallFlow, SpeechRelay
from caseline_voice.telecom import GuavaCallGateway, MockCallGateway
from tests.test_flow import FakeBackend


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
    return CallFlow(backend, frozenset({"+16173187562"}), live_assessment=True)


def test_live_triage_checklist_has_no_model_chosen_routing_fields():
    flow = flow_for(LiveBackend([]))
    gw = MockCallGateway(fields={"intake_consent": "yes"})
    flow.on_call_start(gw)
    flow.on_task_complete(gw, "consent")
    task_id, checklist = gw.tasks[-1]
    keys = [getattr(i, "key", None) for i in checklist]
    assert task_id == "triage" and "jurisdiction" not in keys and "practice_area" not in keys
    # And the real SDK accepts it, including completion criteria.
    GuavaCallGateway(MockCall()).start_task("triage", "o", checklist, completion_criteria=prompts.LIVE_COMPLETION)


def test_backend_questions_are_injected_once_and_ready_signal_sent_once():
    backend = LiveBackend([
        {"ask": "Where did this happen? Which city and state?", "assessment_ready": False},
        {"ask": None, "assessment_ready": True},
        {"ask": None, "assessment_ready": True},
    ])
    flow = flow_for(backend)
    gw = MockCallGateway()
    flow.on_speech(gw, "caller", "My house burned down.")
    flow.on_speech(gw, "agent", "Where did this happen?")
    flow.on_speech(gw, "caller", "Cambridge, Massachusetts.")
    flow.on_speech(gw, "caller", "Thanks.")
    assert gw.instructions[0] == prompts.ASK_NEXT.format(question="Where did this happen? Which city and state?")
    assert gw.instructions.count(prompts.ASSESSMENT_READY) == 1
    assert ("agent", "Where did this happen?", None) in backend.utterances


def test_emergency_instruction_preempts_routing():
    flow = flow_for(LiveBackend([{"urgency": "Emergency", "assessment_ready": False, "ask": "Where?"}]))
    gw = MockCallGateway()
    flow.on_speech(gw, "caller", "we're trapped")
    assert gw.instructions == [prompts.EMERGENCY_NOW]


def test_backend_failure_is_silent_and_never_dials():
    flow = flow_for(LiveBackend([BackendUnavailable("timeout")]))
    gw = MockCallGateway()
    flow.on_speech(gw, "caller", "hello")
    assert gw.instructions == [] and gw.transfers == []


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
