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
