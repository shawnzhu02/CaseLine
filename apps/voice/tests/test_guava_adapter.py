"""GuavaCallGateway against guava-sdk's own offline MockCall: verifies the exact SDK commands we emit."""

from __future__ import annotations

from guava.commands import TransferCommand
from guava.testing.mocks import MockCall

from caseline_voice.telecom import FieldSpec, GuavaCallGateway, SaySpec


def test_transfer_emits_soft_transfer_to_exact_number():
    call = MockCall(session_id="s1")
    GuavaCallGateway(call).transfer("+12676804795", "Tell the caller you're connecting them.")
    transfers = [c for c in call._command_queue if isinstance(c, TransferCommand)]
    assert len(transfers) == 1
    assert transfers[0].to_number == "+12676804795"
    assert transfers[0].soft_transfer is True


def test_start_task_converts_specs():
    call = MockCall(session_id="s1")
    gw = GuavaCallGateway(call)
    gw.start_task("triage", "objective", [
        SaySpec("hi"), FieldSpec("caller_name", "name"),
        FieldSpec("ok", "consent", "multiple_choice", question="Okay?", choices=["yes", "no"]), "free text"])
    assert gw.call_id == "s1"
    assert call._field_keys_by_task_id["triage"] == ["caller_name", "ok"]


def test_every_flow_checklist_is_accepted_by_the_sdk():
    # Build every task the real flow can issue and push it through the real guava.Field/Say types.
    from caseline_voice.flow import CallFlow
    from caseline_voice.telecom import MockCallGateway
    from tests.test_flow import TRIAGE_FIELDS, FakeBackend, triage_result

    for result in (triage_result("transfer"), triage_result("extended_intake")):
        gw = MockCallGateway(fields={"intake_consent": "yes", **TRIAGE_FIELDS})
        flow = CallFlow(FakeBackend(triage_result=result), frozenset())
        flow.on_call_start(gw)
        flow.on_task_complete(gw, "consent")
        flow.on_task_complete(gw, "triage")
        for task_id, checklist in gw.tasks:
            GuavaCallGateway(MockCall()).start_task(task_id, "objective", checklist)


def test_caller_id_is_read_but_optional():
    assert GuavaCallGateway(MockCall()).caller_id_number == "+15555555555"
