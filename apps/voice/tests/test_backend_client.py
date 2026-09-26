"""Backend client failure semantics (spec §22), using an in-process mock transport. No network."""

from __future__ import annotations

import httpx
import pytest

from caseline_voice.backend_client import (
    BackendAuthError,
    BackendConflict,
    BackendSchemaError,
    BackendUnavailable,
    CaseLineBackend,
)

TRIAGE = {"case_id": "c", "referral_id": "r", "action": "transfer", "requires_transfer_consent": True,
          "selected_firm": {"firm_id": "demo-firm-a", "display_name": "Demo Partner Firm A", "is_demo": True},
          "next_prompt": "I can connect you...", "extended_intake_questions": []}


def backend(handler) -> CaseLineBackend:
    client = httpx.Client(base_url="http://api.test", transport=httpx.MockTransport(handler))
    return CaseLineBackend("http://api.test", "tok", client=client)


def test_triage_sends_auth_and_idempotency_key():
    seen = {}

    def handler(request: httpx.Request):
        seen.update(auth=request.headers["Authorization"], key=request.headers["Idempotency-Key"])
        return httpx.Response(200, json=TRIAGE)

    result = backend(handler).triage({"x": 1}, "guava-abc")
    assert result.selected_firm.firm_id == "demo-firm-a"
    assert seen == {"auth": "Bearer tok", "key": "guava-abc:triage:v1"}


@pytest.mark.parametrize("status,exc", [(401, BackendAuthError), (403, BackendAuthError),
                                        (422, BackendSchemaError), (500, BackendUnavailable),
                                        (503, BackendUnavailable)])
def test_http_errors_are_typed(status, exc):
    with pytest.raises(exc):
        backend(lambda r: httpx.Response(status, json={})).triage({}, "c")


def test_timeout_is_unavailable():
    def handler(request):
        raise httpx.ConnectTimeout("boom")

    with pytest.raises(BackendUnavailable):
        backend(handler).triage({}, "c")


def test_409_carries_reason():
    b = backend(lambda r: httpx.Response(409, json={"detail": {"reason": "live_transfer_disabled"}}))
    with pytest.raises(BackendConflict) as info:
        b.authorize_transfer("r", "c", True)
    assert info.value.reason == "live_transfer_disabled"


def test_unexpected_shape_is_schema_error():
    with pytest.raises(BackendSchemaError):
        backend(lambda r: httpx.Response(200, json={"nope": True})).triage({}, "c")
