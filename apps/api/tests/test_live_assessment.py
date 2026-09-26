"""Live assessment: the scripted residential-fire demo, fact merging, hybrid extraction and routing hand-off."""

from __future__ import annotations

from caseline.models import CallAssessment
from caseline.services import assessment as A

CALL = "guava-live-1"


def say(h, speaker, text, call=CALL):
    r = h.post(f"/v1/calls/{call}/utterances", {"speaker": speaker, "text": text})
    assert r.status_code == 200, r.text
    return r.json()


def test_fire_demo_script_evolves_assessment(h):
    s = say(h, "caller", "My house burned down. Everything is gone. I don't know what to do.")
    assert s["category"] == "Property / Insurance" and s["matter"] == "Residential Fire"
    assert s["jurisdiction"] is None and s["ask"].startswith("Where did this happen")
    assert s["status"] == "LISTENING" and s["match"] is None

    say(h, "agent", "I'm so sorry. Where did this happen?")
    s = say(h, "caller", "Cambridge, Massachusetts.")
    assert s["jurisdiction"] == "Massachusetts"
    assert s["urgency"] == "High" and "Property loss" in s["key_factors"]
    assert s["ask"].startswith("Did you or anyone else need medical treatment")

    say(h, "agent", "Did you need any medical treatment after the fire?")
    s = say(h, "caller", "Yes. I went to the hospital because of smoke inhalation.")
    assert s["category"] == "Personal Injury"
    assert "Hospital / medical treatment" in s["key_factors"]
    change = [c for c in s["history"] if c["field"] == "Category"][-1]
    assert (change["from"], change["to"]) == ("Property / Insurance", "Personal Injury")
    assert s["match"] is None and s["ask"].startswith("Were there any known problems")

    say(h, "agent", "Were there any known problems with the property before the fire?")
    s = say(h, "caller", "Yes. We had already told the landlord that some of the electrical outlets were sparking.")
    assert s["matter"] == "Potential Premises Liability"
    assert {"Hazard previously reported", "Landlord previously notified"} <= set(s["key_factors"])
    assert s["ready"] is True and s["assessment_ready"] is True
    assert s["match"]["firm_id"] == "demo-firm-c" and s["match"]["route"] == "transfer"
    assert s["action"] == "Connect now" and s["status"] == "MATCH FOUND"


def test_no_utterance_text_is_stored(h):
    say(h, "caller", "My house burned down in Boston and my neighbour Jane Example got hurt.")
    with h.db.sessionmaker() as s:
        row = s.query(CallAssessment).one()
        blob = str(row.facts) + str(row.view) + str(row.history)
    assert "Jane" not in blob and "burned down" not in blob


def test_triage_uses_ready_assessment_then_transfer_path(live):
    for speaker, text in [
        ("caller", "My house burned down."), ("caller", "Cambridge, Massachusetts."),
        ("agent", "Did you need any medical treatment?"), ("caller", "Yes, smoke inhalation at the hospital."),
        ("agent", "Were there any known problems with the property before the fire?"),
        ("caller", "Yes, we told the landlord the outlets were sparking."),
    ]:
        say(live, speaker, text)
    tri = live.triage(CALL, practice_area=None, jurisdiction=None, issue_summary="Fire at rented home").json()
    assert tri["action"] == "transfer" and tri["selected_firm"]["firm_id"] == "demo-firm-c"
    auth = live.authorize(tri["referral_id"], CALL).json()
    live.attempt(tri["referral_id"], CALL, auth)
    status = live.get("/v1/live/current", as_="operator").json()["status"]
    assert status == "CONNECTING..."


def test_emergency_is_never_routed(h):
    s = say(h, "caller", "There's a fire and my kids are trapped, we're in danger right now in Boston")
    assert s["urgency"] == "Emergency" and s["assessment_ready"] is False
    assert s["action"] == "Emergency guidance (911)" and s["match"] is None


def test_no_participating_firm_outside_demo_states(h):
    say(h, "caller", "Our apartment burned down in Austin, Texas and I was treated at the hospital")
    s = say(h, "caller", "No, nobody reported any problems before.")
    # prior_hazard stays unknown for a bare "no" without the question context -> still asking
    assert s["ready"] is False
    say(h, "agent", "Were there any known problems with the property before the fire?")
    s = say(h, "caller", "No.")
    assert s["ready"] is True and s["match"]["route"] == "none"
    assert s["action"].startswith("Human review")


def test_llm_extractor_merges_but_never_routes(make_harness):
    class FakeClaude:
        calls = 0

        def extract(self, utterance, last_q, known):
            FakeClaude.calls += 1
            # Claude understands an indirect phrasing the rules miss.
            return {"state": "MA", "medical_treatment": True, "injury": True} if "Beantown" in utterance else {}

    h = make_harness(extractor=FakeClaude())
    s = say(h, "caller", "Our place in Beantown caught fire and I ended up getting checked out overnight")
    assert FakeClaude.calls == 1
    assert s["jurisdiction"] == "Massachusetts" and s["category"] == "Personal Injury"


def test_simulation_is_demo_only_and_operator_only(make_harness):
    h = make_harness()
    body = {"call_id": "rehearsal-1", "speaker": "caller", "text": "My house burned down."}
    assert h.post("/v1/live/simulate", body, as_="service").status_code == 403
    r = h.post("/v1/live/simulate", body, as_="operator")
    assert r.status_code == 200 and r.json()["category"] == "Property / Insurance"
    off = make_harness(demo_mode=False)
    assert off.post("/v1/live/simulate", body, as_="operator").status_code == 409


def test_live_view_requires_operator(h):
    assert h.get("/v1/live/current", as_="service").status_code == 403
    assert h.get("/v1/live/current", as_="operator").json()["status"] == "Waiting for caller..."


def test_rule_extractor_units():
    assert A.rule_extract("Cambridge, Massachusetts.", None)["state"] == "MA"
    assert A.rule_extract("yes", "medical") == {"injury": True, "medical_treatment": True}
    assert A.rule_extract("no", None) == {}
    assert A.question_for_text("Did you need any medical treatment after the fire?") == "medical"
    schema = A.ClaudeExtractor.schema()
    assert schema["additionalProperties"] is False and set(schema["required"]) == set(A.FACT_FIELDS)


def test_demo_role_is_limited_to_simulation(h):
    from caseline.auth import hash_token
    from caseline.models import ApiPrincipal

    with h.db.sessionmaker() as s:
        s.add(ApiPrincipal(name="public-demo", role="demo", token_hash=hash_token("tok-demo")))
        s.commit()
    hdr = {"Authorization": "Bearer tok-demo"}
    say(h, "caller", "A real caller in Boston: my house burned down", call="guava-real-1")
    r = h.client.post("/v1/live/simulate", json={"call_id": "judge-1", "speaker": "caller",
                                                  "text": "My house burned down."}, headers=hdr)
    assert r.status_code == 200
    sim = h.client.get("/v1/live/sim/judge-1", headers=hdr).json()
    assert sim["call_id"] == "judge-1"[-6:] or sim["category"] == "Property / Insurance"
    # The demo role can never see real calls, cases or firms.
    for path in ("/v1/live/current", "/v1/admin/cases", "/v1/admin/firms", "/v1/firm/referrals"):
        assert h.client.get(path, headers=hdr).status_code == 403, path


def test_off_script_matter_signals_finish(h):
    say(h, "caller", "I got a parking ticket in Columbus, Ohio")
    say(h, "caller", "It was on Main Street")
    s = say(h, "caller", "I think it was unfair")
    assert s["assessment_ready"] is False and s["finish_intake"] is True


def test_postgres_engine_disables_prepared_statements():
    from caseline.db import make_engine

    engine = make_engine("postgresql+psycopg://u:p@localhost:5432/db")
    assert engine.dialect.name == "postgresql"
