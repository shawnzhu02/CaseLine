# CaseLine API contract (v1)

Interactive OpenAPI docs: run the API and open `http://127.0.0.1:8000/docs` (schema at `/openapi.json`).

All `/v1` endpoints require `Authorization: Bearer <CASELINE_INTERNAL_API_TOKEN>`. Errors use
`{"detail": {"reason": "<machine_reason>", "message": "..."}}`.

| Method / path | Idempotency | Purpose |
| --- | --- | --- |
| `POST /v1/intake/triage` | `Idempotency-Key` required; replay returns the stored response | Stage A fields → routing decision. Never returns a phone number. |
| `POST /v1/referrals/{id}/authorize-transfer` | header required, not replayed (token is one-time) | Records transfer consent, runs every gate, returns destination + one-time token. |
| `POST /v1/referrals/{id}/transfer-attempts` | required; replay returns same attempt with `dial:false` | Consumes the token, records `requested`. |
| `POST /v1/transfer-attempts/{id}/outcome` | single transition (`409` if already set) | Operator-confirmed `connected`/`failed`/`no_answer`/`busy`. |
| `POST /v1/referrals/{id}/extended-intake` | required; replay returns stored response | Stage B facts + share/SMS consent → `pending_async` + outbox rows. |
| `PATCH /v1/cases/{id}/facts` | natural (upsert by key) | Add/confirm facts with provenance. |
| `POST /v1/referrals/{id}/status` | state machine | Firm `accepted`/`declined`/`expired`. |
| `POST /v1/calls/events` | dedup on `(provider, provider_event_id)` | `call_started`, `session_ended`, `transfer_command_sent`. |
| `GET /v1/firms/{slug}/availability` | — | Firm-local open status + source (`configured_hours` / `simulated_demo_availability`). |
| `GET /v1/admin/cases` | — | Paginated, redacted case list. |
| `GET /health/live`, `GET /health/ready` | — | Liveness / DB readiness + safety flags. |

## Triage actions

`transfer`, `extended_intake`, `human_review`, `no_eligible_firm`, `emergency_guidance`, `consent_required`.
Each response lists `permitted_next_steps` and a `next_prompt` the agent relays verbatim.

## `authorize-transfer` 409 reasons

`caller_declined`, `firm_unavailable`, `demo_mode_disabled`, `production_transfer_not_configured`,
`live_transfer_disabled`, `destination_not_allowlisted`, `referral_not_transferable`, `call_mismatch`.

## `transfer-attempts` errors

`403 authorization_token_invalid`, `404 transfer_authorization_not_found`, `409 authorization_already_used`,
`409 authorization_expired`.

## Example (fictional)

```json
POST /v1/intake/triage
{"provider_call_id": "demo-call-001",
 "caller": {"name": "Alex Demo", "callback_number": "+12125550100", "preferred_language": "en"},
 "facts": {"jurisdiction": "DEMO_JURISDICTION", "practice_area": "DEMO_AREA_A",
           "issue_summary": "Fictional demo matter for Firm A", "immediate_danger": false,
           "caller_reported_deadline": null},
 "consents": {"intake": true, "share_with_selected_firm": false, "sms": false}}

200 {"case_id": "…", "referral_id": "…", "action": "transfer",
     "selected_firm": {"firm_id": "demo-firm-a", "display_name": "Demo Partner Firm A", "is_demo": true},
     "requires_transfer_consent": true, "transfer_authorization": null,
     "next_prompt": "I can connect you to Demo Partner Firm A, a demonstration participant. …"}
```
