"""Regression tests for the final code review findings."""

from __future__ import annotations

from caseline.services import assessment as A
from tests.test_live_assessment import CALL, say


def test_fired_is_not_a_fire():
    facts = A.rule_extract("I was fired after I got hurt at work.", None)
    assert "incident" not in facts and "property_damage" not in facts
    assert facts.get("injury") is True


def test_multi_word_states_win():
    assert A.rule_extract("This happened in Charleston, West Virginia.", None)["state"] == "WV"
    assert A.rule_extract("Our apartment in Washington, DC flooded.", None)["state"] == "DC"
    assert A.rule_extract("Richmond, Virginia.", None)["state"] == "VA"


def test_empathetic_preface_maps_to_the_real_question():
    assert A.question_for_text("I'm sorry anyone got hurt. Is anyone in danger right now?") == "danger"
    assert A.question_for_text("Can I get the best number to reach you?") is None


def test_stale_yes_never_triggers_emergency(h):
    say(h, "caller", "My house burned down in Boston.")
    say(h, "agent", "Is anyone in danger right now?")
    say(h, "caller", "No.")
    say(h, "agent", "Can I get the best number to reach you?")
    s = say(h, "caller", "Yeah, it's 555 0100.")
    assert s["urgency"] != "Emergency" and s["action"] != "Emergency guidance (911)"


def test_live_mode_deadline_still_escalates_to_human_review(live):
    for speaker, text in [
        ("caller", "My house burned down."), ("caller", "Cambridge, Massachusetts."),
        ("agent", "Did you need any medical treatment?"), ("caller", "Yes, smoke inhalation at the hospital."),
        ("agent", "Were there any known problems with the property before the fire?"),
        ("caller", "Yes, we told the landlord the outlets were sparking, and I have a court hearing Friday."),
    ]:
        say(live, speaker, text)
    tri = live.triage(CALL, practice_area=None, jurisdiction=None, issue_summary="Fire").json()
    assert tri["action"] == "human_review"


def test_partial_resends_count_once(h):
    for text in ["My house", "My house burned", "My house burned down"]:
        h.post(f"/v1/calls/{CALL}/utterances", {"speaker": "caller", "text": text, "utterance_id": "u1"})
    s = h.post(f"/v1/calls/{CALL}/utterances",
               {"speaker": "caller", "text": "My house burned down", "utterance_id": "u1"}).json()
    assert s["finish_intake"] is False  # 1 utterance, not 4


def test_ask_is_returned_until_answered(h):
    first = say(h, "caller", "My house burned down.")
    again = say(h, "caller", "It was awful.")
    assert first["ask"] == again["ask"] and first["ask_key"] == "location"


def test_public_judge_runs_never_take_over_the_operator_board(h):
    say(h, "caller", "My house burned down in Boston.")
    h.post("/v1/live/simulate", {"call_id": "judge-abc12345", "speaker": "caller", "text": "My house burned down."},
           as_="operator")
    board = h.get("/v1/live/current", as_="operator").json()
    assert board["simulated"] is False and board["jurisdiction"] == "Massachusetts"


def test_broken_extractor_falls_back_to_rules(make_harness):
    class Broken:
        def extract(self, *a):
            raise RuntimeError("boom")

    h = make_harness(extractor=Broken())
    s = say(h, "caller", "My house burned down in Cambridge, Massachusetts.")
    assert s["jurisdiction"] == "Massachusetts"


def test_missing_llm_sdk_never_breaks_startup(monkeypatch):
    import builtins

    from caseline.config import Settings
    from caseline.main import build_extractor

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "anthropic":
            raise ModuleNotFoundError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert build_extractor(Settings(_env_file=None, app_env="test", assessment_llm_enabled=True)) is None
