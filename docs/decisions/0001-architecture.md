# ADR 0001 — CaseLine MVP architecture and deviations from the spec

- Status: accepted (2026-09-26)
- Context: greenfield repository; implementation spec in `CASELINE_IMPLEMENTATION_PLAN.md`.
- Scope of this change: spec Phase 0 + Phase 1 + the Guava voice app wired to the backend (mock-tested). Stops before any live call.

## Decisions

1. **Guava is the only telephony/SMS provider.** The voice agent (`apps/voice`) is the only code that constructs a `guava.Agent`. The API only touches Guava for outbound SMS from the outbox worker, through an optional dependency (`pip install -e ".[guava]"`). No other telephony/SMS vendor appears in code or config; a test and a CI job enforce this.
2. **Python 3.13 locally, `requires-python >=3.12`.** Only 3.13 is installed on the dev machine; CI uses 3.13. The Guava deploy base image is `python-sandbox:3.14`.
3. **Postgres outbox instead of Redis + RQ.** RQ workers depend on `os.fork` and do not run natively on Windows. `notifications` is the queue: a row is written before any send, `event_key` is unique (idempotency), the worker (`python -m caseline.workers.outbox`) selects due rows with `FOR UPDATE SKIP LOCKED` on Postgres, retries with bounded exponential backoff and ends in `failed` (operator queue). Redis is removed from `compose.yaml`, CI and config.
4. **Unit tests run on in-memory SQLite** with portable column types (`Uuid`, JSON→JSONB variant, a UTC-enforcing `UTCDateTime`), so the suite passes without Docker. CI additionally runs `alembic upgrade head` + `alembic check` + the seed against Postgres 16.
5. **Transfer authorization is a one-time bearer token.** `authorize-transfer` returns `authorization_id` + `authorization_token` (only the SHA-256 hash is stored) + the destination, valid for 120 s. `transfer-attempts` consumes it atomically. Replaying `transfer-attempts` with the same `Idempotency-Key` returns the same attempt with `dial: false`, so a retry never redials. `authorize-transfer` is deliberately **not** replayed from the idempotency cache (the token must not be re-issued).
6. **Only demo destinations are dialable in this build.** `authorize-transfer` releases a number only when the firm is a demo firm, `DEMO_MODE=true`, `DEMO_LIVE_TRANSFER_ENABLED=true`, the number is E.164 and on `DEMO_TRANSFER_ALLOWLIST` (which config validation restricts to the approved number). Verified production partners return `409 production_transfer_not_configured` until a partner allowlist exists. The voice agent re-checks the destination against its own allowlist (capped to the same approved number) before calling `call.transfer`.
7. **Extra routing action `consent_required`.** When the caller refuses intake consent, nothing but the refusal is stored and the case is closed.
8. **Share and SMS consent are asked after the firm is named** (in extended intake), per the guardrail "identify the firm before requesting permission for transfer/report sharing".
9. **The voice call flow is SDK-free** (`caseline_voice/flow.py` drives a `CallGateway` protocol). `GuavaCallGateway` adapts `guava.Call`; `MockCallGateway` is the fake of the same interface. This lets `scripts/smoke_demo.py` run a full scripted call through the real flow and the real API in-process.
10. **Single internal bearer token** for voice→API and operator endpoints in the MVP. RBAC (`admin`/`operator`/`firm_user`), firm isolation, rate limits, field encryption and signed report links are Phase 4.

## Verified Guava facts (guava-sdk 0.45.0, installed and read on 2026-09-26)

| Capability | Verified API | Consequence |
| --- | --- | --- |
| Inbound | `agent.listen_phone(number)`; `agent.chat()`, `agent.call_local()` for testing | `main.py` modes `phone`/`chat`/`local`. |
| Tasks/fields | `call.set_task(task_id, objective=, checklist=[guava.Field, guava.Say, str])`; `guava.Field` rejects `None` for `question`/`choices` | Adapter omits unset values (covered by a test). |
| Task completion | `@agent.on_task_complete` (generic `(call, task_id)`) | One dispatcher in `flow.py`. |
| Session end | `@agent.on_session_end(call, event)`; `event.termination_reason` ∈ `user-hangup`, `bot-hangup`, `bot-failure`, `bot-transfer`, `voicemail` | Posted to `/v1/calls/events`. |
| Call id / caller id | `call.id`; `call.call_info.from_number` (may be `None`) | `provider_call_id = "guava-" + call.id`; caller ID informational only. |
| Transfer | `call.transfer(destination, instructions)` → soft `TransferCommand`; returns `None` | **No answer/connect/fail signal.** Attempts stay `requested`; `bot-transfer` is stored as `provider_signal` only. `connected` requires `POST /v1/transfer-attempts/{id}/outcome` by an operator. |
| SMS | `guava.Client(api_key=).send_sms(from_number, to_number, message)` → `None`; `next_sms(...)` polls inbound | No message id, delivery status or STOP handling. SMS rows end at `submitted` (`delivery_unknown`). |
| Webhooks | none documented | `/v1/calls/events` is fed by our own agent (bearer auth), deduplicated by event id. |
| Offline testing | `guava.testing.mocks.MockCall` | Used to assert the exact `TransferCommand` emitted. |

## Blockers / needs verification (recorded, mocked, not routed around)

- **SMS capability of `+14849687497`** and whether Guava handles STOP/HELP replies and 10DLC/TCPA registration. `GUAVA_SMS_ENABLED` stays `false` until verified.
- **Transfer outcome events.** If Guava adds answer/fail events, wire them into `/v1/calls/events` as `provider_verified` results.
- **`listen_phone` vs deployment binding.** Confirm whether `+14849687497` must be attached to a `guava create --phone` project (`guava.toml`) for `guava deploy up`; `guava.toml` is account-specific and git-ignored.
- **Recording/retention controls** in Guava are not documented in the SDK; review before real callers.
- The generated Guava SDK itself mentions an RCS sender implemented by a third party internally (disabled for release). CaseLine does not use RCS.

## Deferred (not in this change)

Phase 3 report builder + private storage + firm portal; Phase 4 Next.js admin dashboard, RBAC, firm isolation, rate limiting, field-level encryption, observability, pen-test items; referral expiry scheduler (`expire_referral`) and stale-transfer reconciler jobs.
