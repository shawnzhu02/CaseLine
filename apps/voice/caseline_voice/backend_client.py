"""HTTP client for the CaseLine API (spec §22): hard timeouts, typed failures, no automatic retries."""

from __future__ import annotations

import httpx
from pydantic import ValidationError

from caseline_voice.schemas import ExtendedIntakeResult, TransferAttempt, TransferAuthorization, TriageResult


class BackendError(Exception):
    """Any failure talking to the backend. The agent must fall back to human review and never guess a firm."""


class BackendUnavailable(BackendError):
    pass


class BackendAuthError(BackendError):
    pass


class BackendSchemaError(BackendError):
    pass


class BackendConflict(BackendError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class CaseLineBackend:
    def __init__(self, base_url: str, token: str, client: httpx.Client | None = None) -> None:
        self.client = client or httpx.Client(base_url=base_url, timeout=httpx.Timeout(8.0, connect=3.0))
        self._auth = {"Authorization": f"Bearer {token}"}

    def _post(self, path: str, body: dict, idempotency_key: str | None = None) -> dict:
        headers = dict(self._auth)
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        try:
            r = self.client.post(path, json=body, headers=headers)
        except httpx.HTTPError as exc:
            raise BackendUnavailable(type(exc).__name__) from exc
        if r.status_code in (401, 403):
            raise BackendAuthError(f"http {r.status_code}")
        if r.status_code == 409:
            detail = (r.json() or {}).get("detail", {})
            raise BackendConflict(detail.get("reason", "conflict") if isinstance(detail, dict) else "conflict")
        if r.status_code == 422:
            raise BackendSchemaError("request rejected by schema validation")
        if r.status_code >= 400:
            raise BackendUnavailable(f"http {r.status_code}")
        return r.json()

    @staticmethod
    def _parse(model, data: dict):
        try:
            return model.model_validate(data)
        except ValidationError as exc:
            raise BackendSchemaError("unexpected response shape") from exc

    def triage(self, payload: dict, provider_call_id: str) -> TriageResult:
        return self._parse(TriageResult, self._post("/v1/intake/triage", payload, f"{provider_call_id}:triage:v1"))

    def authorize_transfer(self, referral_id: str, provider_call_id: str, consented: bool) -> TransferAuthorization:
        data = self._post(f"/v1/referrals/{referral_id}/authorize-transfer",
                          {"provider_call_id": provider_call_id, "caller_consented": consented},
                          f"{provider_call_id}:transfer-consent:v1")
        return self._parse(TransferAuthorization, data)

    def record_transfer_attempt(self, referral_id: str, provider_call_id: str,
                                auth: TransferAuthorization) -> TransferAttempt:
        data = self._post(f"/v1/referrals/{referral_id}/transfer-attempts",
                          {"authorization_id": auth.authorization_id,
                           "authorization_token": auth.authorization_token,
                           "provider_call_id": provider_call_id, "state": "requested"},
                          f"{provider_call_id}:transfer-attempt:{auth.authorization_id}")
        return self._parse(TransferAttempt, data)

    def extended_intake(self, referral_id: str, provider_call_id: str, facts: dict, consents: dict,
                        reason: str) -> ExtendedIntakeResult:
        data = self._post(f"/v1/referrals/{referral_id}/extended-intake",
                          {"provider_call_id": provider_call_id, "facts": facts, "consents": consents,
                           "reason": reason},
                          f"{provider_call_id}:extended-intake:v1")
        return self._parse(ExtendedIntakeResult, data)

    def post_utterance(self, provider_call_id: str, speaker: str, text: str,
                       utterance_id: str | None = None) -> dict:
        return self._post(f"/v1/calls/{provider_call_id}/utterances",
                          {"speaker": speaker, "text": text[:2000], "utterance_id": utterance_id})

    def post_event(self, provider_call_id: str, event_type: str, event_id: str, **extra) -> None:
        self._post("/v1/calls/events", {"provider_event_id": event_id, "provider_call_id": provider_call_id,
                                        "event_type": event_type, **extra})
