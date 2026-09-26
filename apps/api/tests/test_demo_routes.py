"""Spec §4A / §28 critical demo assertions: A -> +12676804795, B -> +16173187562, only after consent."""

from __future__ import annotations

FIRM_A = "+12676804795"
FIRM_B = "+16173187562"


def _route(harness, call_id: str, area: str):
    tri = harness.triage(call_id, practice_area=area)
    assert tri.status_code == 200, tri.text
    result = tri.json()
    auth = harness.authorize(result["referral_id"], call_id)
    return result, auth


def test_demo_area_a_selects_only_firm_a(h):
    result = h.triage("call-a", practice_area="DEMO_AREA_A").json()
    assert result["action"] == "transfer"
    assert result["selected_firm"]["firm_id"] == "demo-firm-a"
    assert result["selected_firm"]["display_name"] == "Demo Partner Firm A"
    assert result["selected_firm"]["is_demo"] is True


def test_demo_area_b_selects_only_firm_b(h):
    result = h.triage("call-b", practice_area="DEMO_AREA_B").json()
    assert result["action"] == "transfer"
    assert result["selected_firm"]["firm_id"] == "demo-firm-b"
    assert result["selected_firm"]["display_name"] == "Demo Partner Firm B"


def test_triage_response_does_not_expose_phone_before_consent(h):
    for area in ("DEMO_AREA_A", "DEMO_AREA_B"):
        raw = h.triage(f"call-{area}", practice_area=area).text
        assert FIRM_A not in raw and FIRM_B not in raw
        assert "2676804795" not in raw and "6173187562" not in raw
        assert '"transfer_authorization":null' in raw.replace(" ", "")


def test_authorize_transfer_returns_firm_a_number_after_consent(live):
    result_a, auth_a = _route(live, "call-a", "DEMO_AREA_A")
    assert result_a["selected_firm"]["firm_id"] == "demo-firm-a"
    assert auth_a.status_code == 200, auth_a.text
    assert auth_a.json()["destination_e164"] == FIRM_A
    assert auth_a.json()["display_name"] == "Demo Partner Firm A"


def test_authorize_transfer_returns_firm_b_number_after_consent(live):
    result_b, auth_b = _route(live, "call-b", "DEMO_AREA_B")
    assert result_b["selected_firm"]["firm_id"] == "demo-firm-b"
    assert auth_b.status_code == 200, auth_b.text
    assert auth_b.json()["destination_e164"] == FIRM_B


def test_scenarios_never_cross_destinations(live):
    _, auth_a = _route(live, "call-a", "DEMO_AREA_A")
    _, auth_b = _route(live, "call-b", "DEMO_AREA_B")
    assert {auth_a.json()["destination_e164"], auth_b.json()["destination_e164"]} == {FIRM_A, FIRM_B}


def test_mock_transfer_attempt_uses_server_destination(live):
    result, auth = _route(live, "call-a", "DEMO_AREA_A")
    attempt = live.attempt(result["referral_id"], "call-a", auth.json())
    assert attempt.status_code == 200, attempt.text
    body = attempt.json()
    assert body["destination_e164"] == FIRM_A
    assert body["state"] == "requested"  # never "connected" without verified evidence
    assert body["dial"] is True


def test_firm_a_failure_does_not_silently_switch_to_firm_b(live):
    result, auth = _route(live, "call-a", "DEMO_AREA_A")
    attempt = live.attempt(result["referral_id"], "call-a", auth.json()).json()
    out = live.post(f"/v1/transfer-attempts/{attempt['transfer_attempt_id']}/outcome",
                    {"result": "no_answer", "source": "operator"})
    assert out.status_code == 200
    assert out.json()["state"] == "no_answer"
    # No new authorization to any destination was created, and the referral is still Firm A's.
    with live.db.sessionmaker() as s:
        from caseline.models import Referral, TransferAuthorization

        auths = s.query(TransferAuthorization).all()
        assert [a.destination_e164 for a in auths] == [FIRM_A]
        assert s.query(Referral).count() == 1
