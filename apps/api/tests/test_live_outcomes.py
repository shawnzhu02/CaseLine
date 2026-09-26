"""Demo outputs: live transfer or drafted referral email, for simulated and real calls; public live view."""

from __future__ import annotations

from tests.conftest import NIGHT
from tests.test_live_assessment import CALL, say

FIRE = [
    ("caller", "My house burned down."), ("caller", "Cambridge, Massachusetts."),
    ("agent", "Did you need any medical treatment?"), ("caller", "Yes, smoke inhalation at the hospital."),
    ("agent", "Were there any known problems with the property before the fire?"),
    ("caller", "Yes, we told the landlord the outlets were sparking."),
]


def sim(h, text, speaker="caller", outcome=None, call="judge-abc123"):
    body = {"call_id": call, "speaker": speaker, "text": text, **({"outcome": outcome} if outcome else {})}
    r = h.post("/v1/live/simulate", body, as_="operator")
    assert r.status_code == 200, r.text
    return r.json()


def test_simulated_connect_outcome(h):
    for speaker, text in FIRE:
        sim(h, text, speaker)
    s = sim(h, "Yes, please connect me.", outcome="connect")
    assert s["status"] == "CONNECTING..."
    assert s["outcome"]["type"] == "transfer" and s["outcome"]["simulated"] is True
    assert s["outcome"]["firm"] == "Premises Liability Lawyer (demo)"


def test_simulated_referral_email_outcome(h):
    for speaker, text in FIRE:
        sim(h, text, speaker)
    s = sim(h, "Please just send my details, I'll wait for a call back.", outcome="refer")
    assert s["status"] == "REFERRAL DRAFTED"
    out = s["outcome"]
    assert out["type"] == "email" and "Premises Liability Lawyer (demo)" in out["body"]
    assert "conflicts check" in out["body"] and "Alex" not in out["body"]


def test_real_call_after_hours_ends_in_drafted_email(make_harness):
    h = make_harness(now=NIGHT)
    for speaker, text in FIRE:
        say(h, speaker, text)
    tri = h.triage(CALL, practice_area=None, jurisdiction=None, issue_summary="Fire").json()
    assert tri["action"] == "extended_intake"
    h.extended(tri["referral_id"], CALL, share=True)
    s = h.get("/v1/live/current", as_="operator").json()
    assert s["status"] == "REFERRAL SENT"
    assert s["outcome"]["type"] == "email" and s["outcome"]["simulated"] is False
    assert s["outcome"]["subject"].startswith("CaseLine referral")


def test_real_call_transfer_outcome(live):
    for speaker, text in FIRE:
        say(live, speaker, text)
    tri = live.triage(CALL, practice_area=None, jurisdiction=None, issue_summary="Fire").json()
    auth = live.authorize(tri["referral_id"], CALL).json()
    live.attempt(tri["referral_id"], CALL, auth)
    s = live.get("/v1/live/current", as_="operator").json()
    assert s["outcome"]["type"] == "transfer" and s["outcome"]["state"] == "requested"
    assert "+1267" not in str(s)


def test_public_latest_is_off_by_default_and_never_shows_simulations(make_harness):
    h = make_harness()
    say(h, "caller", "My house burned down in Boston.")
    assert h.get("/v1/live/public-latest", as_="operator").json()["status"] == "Live call view is off"
    on = make_harness(public_demo_show_live_calls=True)
    sim(on, "My house burned down.")
    assert on.get("/v1/live/public-latest", as_="operator").json()["status"] == "Waiting for caller..."
    say(on, "caller", "My house burned down in Boston.")
    snap = on.get("/v1/live/public-latest", as_="operator").json()
    assert snap["category"] == "Property / Insurance" and snap["jurisdiction"] == "Massachusetts"


def test_transcript_off_by_default(h):
    say(h, "caller", "My house burned down.")
    assert h.get("/v1/live/current", as_="operator").json()["transcript"] == []


def test_live_transcript_when_enabled_and_purged_after_window(make_harness):
    from datetime import timedelta

    h = make_harness(live_transcript_enabled=True)
    say(h, "agent", "CaseLine here. Okay to continue?")
    h.post(f"/v1/calls/{CALL}/utterances", {"speaker": "caller", "text": "My house", "utterance_id": "u1"})
    h.post(f"/v1/calls/{CALL}/utterances", {"speaker": "caller", "text": "My house burned down", "utterance_id": "u1"})
    t = h.get("/v1/live/current", as_="operator").json()["transcript"]
    assert t == [{"speaker": "agent", "text": "CaseLine here. Okay to continue?"},
                 {"speaker": "caller", "text": "My house burned down"}]
    h.clock.now += timedelta(minutes=h.settings.live_transcript_retention_minutes + 1)
    say(h, "caller", "hello", call="guava-other-call")  # any new activity triggers the purge
    with h.db.sessionmaker() as s:
        from caseline.models import CallAssessment, CallSession

        old = s.query(CallAssessment).join(CallSession).filter(CallSession.provider_call_id == CALL).one()
        assert old.transcript is None


def test_transcript_refused_in_production():
    import pytest
    from pydantic import ValidationError

    from caseline.config import Settings

    with pytest.raises(ValidationError):
        Settings(_env_file=None, app_env="production", live_transcript_enabled=True,
                 caseline_internal_api_token="x" * 20, caseline_field_encryption_key="k", report_link_secret="s")
