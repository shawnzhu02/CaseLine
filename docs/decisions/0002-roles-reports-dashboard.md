# ADR 0002: Roles, encrypted PII, firm reports, operator dashboard, deployment

- Status: accepted (2026-09-26)
- Builds on ADR 0001. Covers spec Phase 3 (async referral reports) and most of Phase 4.

## Decisions

1. **Roles.** `service` (the voice agent, via `CASELINE_INTERNAL_API_TOKEN`), `operator`, `admin` (superset), and
   `firm_user` (bound to one firm). Personal tokens are created with `python -m caseline.cli create-principal`, shown
   once, and stored as SHA-256 hashes in `api_principals`. Voice endpoints accept only `service`, and operator
   endpoints only `operator`/`admin`. A firm user who asks for another firm's referral gets **404** (not 403), so
   referral IDs can't be enumerated, and the attempt is audited as `denied_cross_firm`.
2. **Field-level encryption.** `callers.name`, `callback_number`, `email` and `accessibility_needs` are Fernet
   ciphertext (`EncryptedText`). Staging/demo/production refuse to start without `CASELINE_FIELD_ENCRYPTION_KEY`.
   Encrypted columns can't be searched by value, which is intended. Migration 0002 encrypts existing rows in place
   and decrypts on downgrade.
3. **Share consent is per firm.** `consent_events.subject_firm_slug` is required for sharing. Consent given for Firm A
   never authorizes sending to Firm B. After reassignment an operator must record new consent from a follow-up call
   (`POST /v1/admin/cases/{id}/consents`) before `POST /v1/admin/referrals/{id}/send`. Share consent captured in Stage
   A, before any firm is named, no longer counts.
4. **Versioned reports.** `report_versions` (unique per referral + case revision) holds an immutable JSON snapshot:
   facts grouped as confirmed / caller-stated / unknown-or-declined, a disclaimer, and no transcript. A fact change
   after sharing creates a new version plus exactly one `firm_referral_updated` alert. Old links show a "newer
   version exists" banner.
5. **Report access.** Firm alert emails carry an HMAC-signed link (`/r/{token}`, TTL `REPORT_EMAIL_LINK_TTL_HOURS`,
   default 72 h) instead of the narrative. The dashboard mints short links (`REPORT_LINK_TTL_MINUTES`, 30). Every
   open is audited, responses are `no-store`/`no-referrer`, and the link endpoint has its own rate limit. Tradeoff:
   anyone holding an unexpired email link can read the report. A firm portal login for `firm_user` exists
   (`/v1/firm/referrals`, `/v1/referrals/{id}/report`) and can replace links when firms have accounts.
6. **Sweeps in the outbox worker.** `expire_referrals` (past `expires_at`, sent to human review) and
   `flag_stale_transfers` (still `requested` after 15 min, case sent to human review). Neither ever invents a
   transfer result.
7. **Operator dashboard** (`apps/admin`, Next.js 16 App Router, server components + server actions). Operators sign
   in with their personal token, which the dashboard keeps in an AES-256-GCM-sealed, httpOnly, SameSite=strict
   cookie (8 h). The token never reaches browser JavaScript, and every API call carries the operator's own identity.
   Transfer numbers are never displayed (masked to last two digits).
8. **Hardening.** `X-Request-ID` correlation IDs; request logs carry route templates, status and latency only; an
   in-memory per-token/IP rate limiter (single-instance limitation documented); optional CORS allowlist; security
   headers on the dashboard.
9. **Deployment.** A Render Blueprint (`render.yaml`) runs the API (migrations + seed as pre-deploy), the outbox
   worker, the voice agent as a background worker (Guava agents only make outbound connections) and the dashboard,
   plus managed Postgres. `guava deploy` remains an alternative for the voice agent.

## Still open

Production transfer allowlist for verified firms; Guava transfer-outcome and SMS/STOP capabilities
(`docs/guava-open-questions.md`); legal/launch decisions (`docs/launch-decisions.md`); a shared rate-limit store for
multiple API instances; firm-user login UI.
