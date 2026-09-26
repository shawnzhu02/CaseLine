# CaseLine architecture (as implemented)

```text
Caller ──dials──► +14849687497 (Guava-managed number)
                        │
                        ▼
            apps/voice  (guava-sdk 0.45.0, `guava run` / `guava deploy`)
            main.py → caseline_voice.agent.build_agent()
            flow.py (SDK-free call flow) ─ telecom.GuavaCallGateway ─ guava.Call
                        │  HTTPS + Bearer token + Idempotency-Key
                        ▼
            apps/api  (FastAPI, SQLAlchemy 2, Alembic)
            ├─ /v1/intake/triage ........ consent, safety, classification, deterministic matching
            ├─ /v1/referrals/{id}/authorize-transfer ... gates + one-time token + destination
            ├─ /v1/referrals/{id}/transfer-attempts .... consume token, record "requested"
            ├─ /v1/transfer-attempts/{id}/outcome ...... operator-confirmed result
            ├─ /v1/referrals/{id}/extended-intake ...... facts, consents, outbox rows
            ├─ /v1/referrals/{id}/status ............... firm accept/decline/expire
            ├─ /v1/calls/events ........................ call lifecycle (dedup by event id)
            └─ /v1/admin/cases, /v1/firms/{slug}/availability, /health/*
                        │
                        ▼
            PostgreSQL (SQLite in unit tests)
            notifications = outbox ◄── python -m caseline.workers.outbox
                                          ├─ Guava SMS (mock unless GUAVA_SMS_ENABLED + live)
                                          └─ Email (mock unless EMAIL_PROVIDER=resend)
```

## State

Call, case and referral state are separate (`call_sessions.state`, `cases.status`, `referrals.status`), with explicit transition tables in `apps/api/caseline/state.py`. Invalid transitions return `409 invalid_transition` and write an audit row. A transfer request can never produce `firm_accepted` directly.

## Transfer path (the only way a number reaches the dialer)

1. Triage selects a firm deterministically (`services/matching.py`, rule `match-v1`) and returns `action=transfer` with **no phone number**.
2. The agent names the firm and asks for consent.
3. `authorize-transfer` records the transfer consent, re-checks availability, demo mode, the live-transfer flag and the allowlist, then returns the destination + one-time token (120 s).
4. The agent re-checks the destination against its own allowlist, records the attempt (`dial: true` only on first recording), then calls `call.transfer(destination, handoff)`.
5. The attempt stays `requested` until an operator records `connected`/`failed`/`no_answer`/`busy`. A failure never triggers an automatic transfer to another firm.

## Data handling

- Structured facts carry provenance (`caller_stated`, `caller_confirmed`, `inferred`, `unknown`, `declined`); transcripts are not stored.
- Logs go through `RedactingFormatter` (phone numbers and emails masked). Admin list views show initials and the last two digits only.
- Notification bodies contain a case reference, never the narrative.
