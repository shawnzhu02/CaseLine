# CaseLine — Implementation Plan and Handoff for a Coding Agent

> **Purpose:** Build an MVP of CaseLine, a consumer-facing, AI-powered telephone intake and law-firm referral service. This document is a working specification for an autonomous coding agent. Work in the existing repository, inspect before changing anything, and implement in small, verifiable milestones.
>
> **Repository:** https://github.com/shawnzhu02/CaseLine  
> **Voice platform documentation (authoritative):** https://goguava.ai/docs/everything.md  
> **Development environment:** Windows 11, PowerShell; use Windows-compatible commands in instructions.  
> **Status:** Agent-ready implementation specification. Public Guava docs were re-checked on 2026-09-26 for Windows CLI setup, inbound-agent patterns, `call.transfer(...)`, `guava.Client().send_sms(...)`, and deployment commands. The coding agent must still inspect the installed SDK/generated Guava template and the actual CaseLine repository before editing because the repo and account-specific Guava capabilities may differ.  
> **Product:** CaseLine — “One number. One conversation. The right legal help.”
>
> **MANDATORY TELECOM DECISION:** Guava is the **only** phone-number, inbound-call, outbound-call, live-transfer, and SMS provider. Do not add Twilio, Telnyx, Amazon Connect, or another telephony/SMS SDK, API, number, SIP trunk, or fallback. CaseLine remains responsible for matching, storage, secure referrals, and orchestration; a separate transactional **email** service (e.g., Resend) is allowed solely to email law firms or callers.

---

## Revision notes (2026-09-26) — read first

This copy of the spec was amended after inspecting the installed Guava SDK and building the first vertical slice.
Where the text below conflicts with these notes, **these notes win**. Details: `docs/decisions/0001-architecture.md`.

1. **CaseLine inbound number:** the Guava-managed number callers dial is **+1 484-968-7497 (`+14849687497`)** →
   `GUAVA_AGENT_NUMBER`. It is never a transfer or SMS destination.
2. **Python:** `requires-python = ">=3.12"`; the dev machine and CI use 3.13 (no 3.12 installed). `uv` manages envs
   (the Guava CLI's `guava run` already uses `uv run main.py`).
3. **Jobs:** Redis + RQ is **replaced by a Postgres outbox** (`notifications` table + `python -m caseline.workers.outbox`,
   `FOR UPDATE SKIP LOCKED`, bounded exponential backoff, idempotent on `event_key`). RQ workers need `os.fork` and do
   not run on Windows. `REDIS_URL`, the Redis service and the `redis`/`rq` dependencies are removed.
4. **Guava SDK verified against the installed `guava-sdk==0.45.0`** (the version `guava create` generates):
   - `call.transfer(destination, instructions)` — sends a soft transfer and returns `None`. There is **no**
     answered/connected/failed event. `on_session_end` reports `termination_reason="bot-transfer"`, which only means the
     agent left the call. Transfer attempts therefore stay `requested` until an **operator** records the outcome
     (`POST /v1/transfer-attempts/{id}/outcome`).
   - Inbound: `agent.listen_phone(number)` (not `inbound_phone(...).run()`); testing via `agent.chat()` /
     `agent.call_local()`; `guava run <dir> -- chat`.
   - Handlers: `@agent.on_call_start(call)`, generic `@agent.on_task_complete(call, task_id)`,
     `@agent.on_session_end(call, event)`; `call.id`, `call.call_info.from_number` (nullable).
   - `guava.Field` rejects `None` for `question`/`choices` — omit them when unset.
   - SMS: `guava.Client(api_key=...).send_sms(from_number, to_number, message)` returns `None` (no message id);
     no delivery-status or STOP events. SMS rows end in `submitted` / `delivery_unknown`; `GUAVA_SMS_ENABLED` stays
     `false` until the sender number's SMS capability and STOP handling are verified (**blocker**).
   - No HTTP webhooks/signatures: `POST /v1/calls/events` is fed by CaseLine's own voice agent (bearer auth), deduped
     on event id.
   - Offline test double: `guava.testing.mocks.MockCall`.
5. **Transfer authorization:** `authorize-transfer` returns `authorization_id` **and** a one-time
   `authorization_token` (hash stored). `transfer-attempts` requires both; replay with the same `Idempotency-Key`
   returns the same attempt with `dial: false`. Only **demo** destinations are dialable in this build; verified
   production partners get `409 production_transfer_not_configured` until a partner allowlist exists.
6. **New routing action `consent_required`** when intake consent is refused (only the refusal is stored).
7. **Share/SMS consent is asked after the firm is named** (extended intake), not during Stage A triage.
8. **Tests** run on in-memory SQLite (portable types) so they pass without Docker; CI also migrates/seeds Postgres 16.
9. **Scope of the first PR:** Phases 0–1 + the Guava voice app (mock-tested) + outbox with mock providers. Phase 3
   report storage/portal and Phase 4 admin UI/RBAC are **not** in it.
10. `.env` is loaded from the repository root by both apps, regardless of working directory.

---

## 0. Instructions to the implementing agent

1. **Inspect the repository first.** Read `README.md`, manifests, existing source code, infrastructure, tests, CI configuration, and the current Git branch. Reuse existing conventions and preserve working features. Do not blindly generate a second app or replace the project structure.
2. **Read the current Guava documentation** at the URL above, particularly Guava-managed inbound numbers, agent setup, field collection, native `call.transfer(...)`, `guava.Client().send_sms(...)`, tool calls, completion events, deployment, and recording/transcription/retention controls. Pin SDK versions. Check the exact API and SMS availability for the account/number. Do not assume Guava provides confirmed transfer-answer or SMS-delivery callbacks unless verified. If an integration detail is unavailable, provide a **mock Guava adapter and clearly documented blocker**; do not introduce another phone/SMS provider.
3. **Make incremental code changes**, not only a plan. Prefer a working vertical slice before broad scaffolding. Avoid unrelated refactoring. Follow existing repo tooling unless incompatible.
4. **Never invent credentials, phone numbers, firms, legal permissions, or API capabilities.** The user supplied exactly **two live demo transfer destinations** in Section 4A; use those numbers only as explicitly specified there, and do not invent law-firm identities or qualifications for the owners. Use `.env.example`, placeholders, fixtures, and the mock Guava implementation for development. Do not initiate real outbound calls/SMS/emails or share actual caller information during unattended tests.
5. **Safety constraints are product requirements:** the system must not offer legal advice, assert that it has established representation, or share sensitive information without the required authorization. Human review must be available for urgent/uncertain matters. Firm conflicts clearance and client acceptance remain with the firm.
6. After each phase, run tests, document commands/results, and keep `README.md` and `.env.example` current. If blocked on access or credentials, complete the mock-backed implementation and record precise blockers instead of guessing.
7. Preserve secrets in the local secret store/environment only; never commit `.env`, transcripts, real cases, authentication tokens, or personal information.

## 1. Product definition and MVP scope

### Consumer journey

1. Consumer calls a single CaseLine inbound number.
2. The voice agent identifies itself, explains that it is an AI intake/referral service rather than a lawyer, provides the relevant privacy/recording notices, and obtains required permission before recording or sharing information.
3. The caller describes their situation in their own words. The agent asks targeted questions to collect essential facts, assess urgency, and identify likely legal practice area and jurisdiction.
4. CaseLine's **own backend**, not the language model, filters participating firms for eligibility and selects an appropriate firm using documented, auditable criteria.
5. **Open and actually available:** after the caller agrees to the identified firm, attempt a live telephone transfer to its designated intake desk. Preserve the intake report. A transfer attempt is not a successful connection; handle no-answer/busy/failure explicitly.
6. **Closed, busy, not answering, or no transfer capability:** continue with extended intake, if appropriate; create a pending referral; securely notify the firm; send the consumer a permitted SMS/email with status and next steps. Do not promise a callback unless the firm has agreed to one.
7. **No eligible firm or unreliable classification:** clearly explain that no connection has been arranged, offer a human-review queue or relevant external referral information, and retain the case only as consented.
8. A firm reviews the referral, runs its own conflicts check, and independently accepts or declines the matter. CaseLine updates the referral and notifies the caller as appropriate.

### MVP boundaries

- Launch **one jurisdiction and 1–2 practice-area groups**, configurable rather than hard-coded. The Belfast fire scenario is an illustrative test case, **not** a confirmed launch jurisdiction or an assertion about any individual.
- Support a curated, manually onboarded set of participating firms. No crawling/scraping and no claims of objective “best lawyer” rankings.
- Have a manual/admin override for classification, routing, urgency, and referral reassignment.
- Support inbound voice, basic structured case summaries, office-hours checks, attempted live transfer, secure asynchronous referrals, and transactional caller updates.
- **Out of scope for the first vertical slice:** consumer payment, advanced AI firm rankings, all-jurisdiction coverage, document uploads, automated legal analysis, consumer mobile app, and deep law-firm CRM integration. Add only after the core referral flow is reliable and the commercial/regulatory model is reviewed.

### Non-negotiable states

`new` → `consent_pending` → `triage_in_progress` → `triage_ready` → one of:

- `transfer_pending` → `transfer_connected` **or** `transfer_failed` → `extended_intake`/`human_review`;
- `extended_intake` → `referral_pending` → `firm_notified` → `firm_accepted`/`firm_declined`/`expired`;
- `human_review` or `no_eligible_firm`;
- `closed` after documented resolution and applicable retention policy.

Keep **call state**, **case state**, and **referral state** separate; a caller may disconnect while a case remains open, and a case may have multiple sequential referrals. Do not send the same case simultaneously to multiple firms unless that is explicitly supported by the product policy and caller consent.

## 2. Proposed architecture

**Telecom dependency rule for the coding agent:** use a single Guava account, its Guava-managed CaseLine phone number, the Guava voice agent, native Guava `call.transfer` and native Guava `Client.send_sms`. No third-party telephony, SMS gateway, external number procurement or intermediary phone routing. Use Guava examples involving external contact centers only to learn documented Guava SDK calls; **do not reproduce those contact-center integrations**. The only permitted independently contracted communications service is **email** for firm case-report alerts and optional email updates. If Guava cannot provide a needed telecom feature for the selected account/region, record a blocker and mock it; do not route around this constraint.

```text
Guava-managed CaseLine phone number
       │
       ▼
Guava voice agent + native telephony
   ├── Inbound calls, natural-language intake
   ├── Authorized live call.transfer() to either demo number
   └── Guava Client.send_sms() for consented caller updates
       │ authenticated tool call / webhook
       ▼
CaseLine FastAPI backend
   ├── Consent and caller identity/verification
   ├── Case normalization and rule-based triage
   ├── Firm eligibility and auditable selection
   ├── Availability / business-hours service
   ├── Transfer authorization and outcome handling
   ├── Referral lifecycle and operator overrides
   ├── PostgreSQL persistence + Alembic migrations
   ├── Postgres outbox + worker: Guava SMS, email and retries (revision note 3)
   ├── Secure case-report delivery + private object storage
   └── Audit and operational telemetry
       ├── Guava telecom gateway (the only phone/SMS integration)
       ├── Independent email service (e.g., Resend), NOT telecom
       └── Next.js admin dashboard
```

Preferred implementation when there is no conflicting existing code:

| Layer | MVP choice | Notes |
| --- | --- | --- |
| Voice, numbers, call transfers, SMS | **Guava exclusively** | Provision/use a Guava-managed inbound number; use native `call.transfer(...)` and `guava.Client().send_sms(...)`; no second telecom/SMS vendor. |
| API | Python >=3.12 (3.13 used), FastAPI, Pydantic v2 | Version the API under `/v1`. |
| Persistence | PostgreSQL, SQLAlchemy 2, Alembic | Avoid storing personal information in unstructured logs. |
| Jobs | Postgres outbox table + polling worker | Replaces Redis + RQ (RQ needs `os.fork`; not Windows-compatible). |
| Admin UI | Next.js, TypeScript | Internal only for pilot; require authentication and role-based access. |
| SMS | **Guava only** | Mock Guava gateway locally; send through Guava client in supervised staging/production only after consent, sender-number capability and opt-out checks. |
| Email | Independent email service (e.g., Resend) | Email is distinct from telephony. Use for minimal firm alerts with secure case-report links and optional consented consumer emails. |
| Storage | Private S3-compatible bucket | Signed, expiring report access, no public object URLs. |
| Testing | pytest, HTTPX, provider mocks | Seed only fictional data. |
| Deployment | Guava for agent; managed hosting for API, DB, workers and dashboard | Separate staging and production. |

**Suggested directories (adapt after repo inspection):**

```text
CaseLine/
├── apps/
│   ├── voice/            # Guava agent + provider adapter
│   ├── api/              # FastAPI application + migrations
│   ├── admin/            # Next.js operator dashboard
│   └── worker/           # Queue consumers and scheduled jobs
├── packages/
│   └── schemas/          # OpenAPI spec or generated shared client
├── infrastructure/       # Docker Compose and deployment manifests
├── tests/                # Contract and end-to-end tests
├── docs/                 # Decisions, runbooks, integration notes
├── .env.example
└── README.md
```

## 3. Detailed voice-agent behavior

### Stage A — essential triage

The agent should prioritize natural storytelling, then ask **only for missing fields**, not read a rigid form. Required fields for routing:

- Caller name and **confirmed callback method** (do not assume caller ID is trustworthy).
- Language preference and any accessibility accommodation needed.
- Approximate place where events happened, caller location if relevant, and the jurisdiction where help is sought.
- What happened, what assistance is requested, and broad practice-area category.
- Urgency: immediate physical danger, imminent hearings/deadlines, detention, housing loss, or other predefined escalations. Do not compute or guarantee legal deadlines.
- Whether the caller has a lawyer already or previously contacted a prospective firm, when relevant for referral/conflict screening.
- Privacy/recording and report-sharing permissions as separately required by the applicable jurisdiction and vendor configuration.

Potential outcomes: `transfer`, `extended_intake`, `human_review`, `no_eligible_firm`, `emergency_guidance`. The agent **must not** invent a phone number or override the backend-selected firm.

### Stage B — extended intake

When an immediate connection cannot be completed, collect a **category-specific** list of follow-up details sufficient to make the initial referral useful. Example questions for a fictional property-fire/insurance matter:

- Date and general location of event; owner or tenant; whether anyone is currently unsafe.
- Existing insurer, whether a claim has been filed, and whether an adverse decision was received.
- Existing legal notices, scheduled proceedings, and caller-stated deadlines.
- Whether police/fire services were involved and whether supporting documents exist.
- Preferred contact time and whether voicemail/SMS/email are permitted.

Mark unknown, declined, inferred, and caller-confirmed information differently. Limit prolonged questioning when a caller is distressed or needs immediate help. If the caller disconnects, save only consented information and apply the agreed follow-up policy.

### Voice-agent guardrails

- Identify as AI; describe CaseLine as a legal **intake/referral** service.
- No legal advice, outcome prediction, attorney-client privilege promises, legal merit scoring, or guarantee of representation.
- Do not affirm unsupported criminal allegations as established facts; record them as caller statements.
- If immediate danger is disclosed, direct the caller to the correct local emergency service and do not continue routine intake ahead of safety escalation.
- If a deadline may be imminent or information is ambiguous, send for priority human review rather than reassuring the caller that there is time.
- Read back important contact and factual details before submission.
- Identify the firm before requesting permission for transfer/report sharing where required.

### Guava-only telecom adapter contract

Define one CaseLine telecom gateway implemented exclusively by the Guava SDK; provide a fake of **that same gateway** for automated tests. The names below are CaseLine application interfaces, not claimed SDK methods:

```python
class GuavaTelecomGateway(Protocol):
    def start_triage(self, call_id: str) -> None: ...
    def start_extended_intake(self, call_id: str, prompts: list[str]) -> None: ...
    def speak(self, call_id: str, message: str) -> None: ...
    def transfer(self, call_id: str, authorized_destination: str) -> str: ...
    def send_sms(self, from_number: str, to_number: str, message: str) -> str: ...
    def end_call(self, call_id: str) -> None: ...
```

**Verified Guava documentation examples (confirm against the installed SDK):**

```python
# Within the active Guava call, after backend authorization and caller agreement:
# guava-sdk 0.45.0 signature: Call.transfer(destination, instructions=None) -> None (no outcome event)
call.transfer("+12676804795", "Tell the caller which demo firm is receiving them.")

# Independently, for a consented caller update (never text the demo lawyer numbers by default):
guava.Client().send_sms(
    from_number=os.environ["GUAVA_AGENT_NUMBER"],
    to_number=verified_caller_number,
    message="CaseLine: We received your intake. Reply STOP to opt out.",
)
```

Relevant Guava documentation: https://goguava.ai/docs/everything.md, https://goguava.ai/docs/amazon-connect-appointment-reminder (Guava SMS API usage only; **do not integrate Amazon Connect**), and https://goguava.ai/docs/on-escalate (native `call.transfer` usage). Prefer a simpler direct-Guava inbound example such as https://goguava.ai/docs/inbound-form-filling for initial agent wiring. Check whether the Guava account and provisioned sender number support SMS, how incoming STOP/replies are managed, and which delivery-status events exist. If missing, keep the Guava SMS job **pending/manual** instead of introducing another SMS vendor. Explicitly document whether Guava exposes transfer answer/connect/fail events; if not, do **not** mark transfers successful solely because a transfer command was sent. Use an operator-confirmed status or another verified signal.

## 4. Firm directory, eligibility and selection

Store each onboarded firm with:

- `firm_id`, trading/legal name, regulator registration identifier, verification date/status.
- Authorized jurisdictions, practice areas and exclusions, languages, geographic/service coverage.
- Client/fee eligibility where disclosed (e.g., legal aid, contingency, fixed fee, initial consultation requirements); do not infer rates.
- Time zone using an IANA name; weekly office hours; dates of holidays and special closures.
- Intake/transfer number, fallback contact, preferred secure referral destination, accepted referral capacity.
- Current `accepting_referrals` and `accepting_live_calls` flags, last-updated timestamp, escalation SLA.
- Partnership/commercial relationship and legally approved disclosure wording (not in caller-facing rankings).

Selection sequence:

1. Normalize issue to a **provisional** practice-area category. Uncertain/unsupported categories go to human review.
2. Hard-filter for verified status, jurisdiction, category, explicit eligibility and current participation.
3. Consider language, geographic coverage, fee constraints, accessibility requirements and capacity.
4. Select deterministically among eligible firms using a documented allocation rule (e.g., rotation with capacity), storing a machine-readable explanation and rule version. Never bias by an undisclosed payment arrangement.
5. Calculate **firm-local** office hours using `zoneinfo` and exception dates; check actual live-call acceptance separately.
6. Before transfer, inform the caller which firm was selected; follow the approved consent/authorization policy.
7. On a failed transfer, retain the case, update the transfer attempt, continue intake if appropriate, and move to the async route or human review.

Do not represent the output as “the best lawyer.” A partner firm's internal conflict/acceptance check always takes precedence over routing.

## 4A. DEMO REQUIREMENT — two real lawyer transfer numbers

**Implement this in the demo, not just as documentation.** The user provided two numbers belonging to the people who will role-play lawyers at the demo's receiving firms. These are **real dialing destinations for a supervised demonstration**, not fictional placeholder phone numbers and not verified production law-firm records:

| Demo destination | Human-readable number | E.164 dialing number | Demo firm label |
| --- | --- | --- | --- |
| Demo Partner Firm A | **267 680 4795** | **`+12676804795`** | `Demo Partner Firm A` (placeholder name) |
| Demo Partner Firm B | **267 680 4795** | **`+12676804795`** | `Demo Partner Firm B` (placeholder name) |

Callers reach the CaseLine agent itself at **484 968 7497 (`+14849687497`)**, the Guava-managed inbound number (never a transfer or SMS target).

Do not infer or announce actual law-firm names, lawyer credentials, specialties, office locations, licensing, consent to receive real consumer case information, or real business hours from these phone numbers. Configure those attributes explicitly if the user provides them. Until then, use **fictional demonstration-only** eligibility rules, case data and labels; clearly identify the recipients as demo participants in the user interface and seeded database.

### Required demo behavior

1. Seed **two participating demo firms** with stable fixture IDs, the labels above and the exact transfer numbers. They must both appear as distinct destinations in the demo admin view and seed/configuration. Use the existing firm-matching path rather than giving the LLM permission to choose, invent or dial arbitrary numbers.
2. Provide **two reproducible scripted demo scenarios**: scenario A selects Partner Firm A and routes to `+12676804795`; scenario B selects Partner Firm B and routes to `+12676804795`. Both should use invented callers and sample case facts. Make the fixture's case category/jurisdiction and eligibility mapping explicit in code/config; do **not** claim that either actual recipient is qualified for those real-world legal matters.
3. For a supervised **live demo**, use a separate, explicitly enabled `demo` environment with an outbound-transfer allowlist containing **only** the two E.164 numbers. Keep the flag **off by default**. Require the team to confirm that the two receiving people agree to receive test calls and that the test account is authorized to make them before enabling it. Never dial either number during ordinary unit tests, continuous integration, a preview deployment or an autonomous agent run.
4. If the selected demo recipient is configured as open/accepting and the demo caller agrees, initiate the **live Guava transfer** to the exact selected number. Show the selected demo firm name in the spoken handoff. Record `transfer_requested`; mark `transfer_connected` only when supported by verified provider signals or human confirmation.
5. Include an after-hours/unavailable demo variant. When the selected firm is configured closed or a transfer cannot be completed, continue extended intake, create a **mocked** notification for that demo firm, and send a **mock Guava SMS** or mock email confirmation to the fictional caller. The two lawyer phone numbers are for **Guava voice transfer**; do not use them as SMS recipients by default.
6. If Firm A fails to answer, **do not silently switch to Firm B**. Any fallback must first pass the same eligibility checks, be enabled in the demo configuration, and obtain the caller's agreement to the newly named recipient. Otherwise, follow the extended-intake/referral path.
7. The demo override for business hours must be **visibly labeled simulated availability**, limited to the demo environment and disabled in production. It must never bypass the transfer allowlist, caller consent or backend authorization.

### Suggested environment configuration

Add these non-secret keys to `.env.example` and load them through validated settings. The live-demo flag and allowlist must be enforced **server-side**, not just in the UI or agent instructions:

```dotenv
DEMO_MODE=false
DEMO_LIVE_TRANSFER_ENABLED=false
DEMO_FIRM_A_NAME=Demo Partner Firm A
DEMO_FIRM_A_TRANSFER_NUMBER=+12676804795
DEMO_FIRM_B_NAME=Demo Partner Firm B
DEMO_FIRM_B_TRANSFER_NUMBER=+12676804795
DEMO_TRANSFER_ALLOWLIST=+12676804795
DEMO_SIMULATE_FIRM_AVAILABILITY=false
```

When `DEMO_MODE=false`, demo overrides must be inert. In real environments, verify actual partner records and only allow calls to approved destinations. For the supervised staging demonstration, document the steps to enable `DEMO_MODE=true` and `DEMO_LIVE_TRANSFER_ENABLED=true` **after** manual confirmation and a small, controlled test-call run. Never store a live dialing token in source control.

### Demo acceptance checks

- [ ] A mocked call in scenario A selects `Demo Partner Firm A` and returns a transfer authorization for `+12676804795`; scenario B selects `Demo Partner Firm B` and returns one for `+12676804795`.
- [ ] The Guava adapter uses exactly the server-approved E.164 destination, with no model-supplied override.
- [ ] An unauthorized number (including a transcription mistake), disabled live-demo flag, missing caller consent or no eligible demo firm prevents a real transfer.
- [ ] A failed/no-answer call remains recorded as attempted, not connected; no hidden automatic transfer to the other demo number occurs.
- [ ] A simulated after-hours scenario sends **mock** notifications without calling either destination.
- [ ] Unit and CI tests never generate real calls, real texts or real emails.
- [ ] One manually initiated, approved staging call to **each** number is documented as a supervised demo checklist item, **not** an autonomous test step.

## 5. Persistence: minimum tables and constraints

Use migrations and enforce foreign keys, useful indexes and unique provider event identifiers.

| Table | Essential fields |
| --- | --- |
| `call_sessions` | UUID, provider call ID UNIQUE, started/ended timestamps, state, caller ID if supplied, case ID nullable |
| `callers` | UUID, contact details (encrypted/protected), verification flags, locale and communication preferences |
| `consent_events` | UUID, caller/case/call refs, purpose (recording, sharing, SMS, email), policy version, allowed/denied, timestamp, capture method |
| `cases` | UUID, caller ID, case category, jurisdiction, case status, summary version, urgency flag, assigned operator, timestamps |
| `case_facts` | UUID, case ID, key, typed value, provenance, confirmed flag, updated timestamp |
| `firms` | UUID, verified name/registration, jurisdiction, categories, languages, capacity and participation flags |
| `firm_hours` | UUID, firm ID, IANA time zone, weekly hours, closures and exception dates |
| `referrals` | UUID, case ID, firm ID, consent ref, selection rationale/rule version, status, expiration, timestamps |
| `transfer_attempts` | UUID, referral ID, provider transfer ID, dial target, attempted/connected/failed timestamps, verified result |
| `notifications` | UUID, event key UNIQUE, referral/case ID, destination, channel, template/version, status, provider response ID, retry count |
| `report_versions` | UUID, referral ID, source case revision, storage key, produced timestamp, sharing/expiry policy |
| `audit_events` | UUID, actor/role, operation, target, result, timestamp, non-sensitive metadata |

Avoid making an intake transcript the authoritative case record. Structured facts should preserve attribution and uncertainty. Do not include transcripts or raw PII in application logs.

## 6. API contract (proposed; adapt to verified Guava webhooks)

All service-to-service endpoints require authentication and input validation. Webhook handlers must authenticate provider signatures where available and use idempotency keyed by event ID. For internal API requests, support an `Idempotency-Key` header where creates can be retried.

| Method/path | Purpose |
| --- | --- |
| `POST /v1/calls/events` | Receive authenticated Guava event updates and deduplicate retries. |
| `POST /v1/intake/triage` | Persist validated Stage A fields; evaluate eligibility and routing; return a bounded decision. |
| `PATCH /v1/cases/{case_id}/facts` | Add/confirm Stage B facts, gated by permissions and access checks. |
| `GET /v1/firms/{firm_id}/availability` | Return calculated open/accepting status and source freshness. |
| `POST /v1/referrals` | Create a consented referral to an eligible firm. |
| `POST /v1/referrals/{referral_id}/transfer-attempts` | Record authorized transfer and provider-confirmed result. |
| `POST /v1/referrals/{referral_id}/status` | Role-authorized acceptance/decline/callback update. |
| `GET /v1/admin/cases` | Paginated, authenticated list with least-privilege redaction. |
| `GET /health/live` and `GET /health/ready` | Process and dependency health. |

Example triage request using fictional test data:

```json
{
  "provider_call_id": "test-call-123",
  "caller": {
    "name": "Jason Example",
    "callback_number": "+447000000000",
    "preferred_language": "en"
  },
  "incident": {
    "location": "Belfast, Northern Ireland",
    "description": "Caller reports a house fire and wants legal assistance about an insurance dispute.",
    "desired_help": "Insurance/property advice",
    "caller_reported_deadline": null
  },
  "consent_event_ids": ["00000000-0000-4000-8000-000000000001"]
}
```

Example **illustrative** response (the backend creates IDs; the voice agent must not manufacture them):

```json
{
  "case_id": "00000000-0000-4000-8000-000000000002",
  "action": "transfer",
  "selected_firm": {
    "firm_id": "00000000-0000-4000-8000-000000000003",
    "display_name": "Example Partner Firm"
  },
  "transfer": {
    "authorization_id": "00000000-0000-4000-8000-000000000004",
    "expires_at": "2026-09-26T18:00:00Z"
  },
  "required_caller_message": "I can connect you to Example Partner Firm. Would you like me to transfer you?"
}
```

**Security design:** do not return arbitrary unrestricted dial destinations from public APIs. Once caller authorization is captured, resolve the firm-approved destination server-side and issue a short-lived transfer authorization consumed by the provider adapter. If the verified Guava integration requires the destination in the agent process, obtain it only from this authenticated authorization service.

Other `action` values: `extended_intake`, `human_review`, `no_eligible_firm`, `emergency_guidance`. Every outcome must specify permitted next steps. Never send a case report when sharing is not authorized.

## 7. Referral reports and communication

Generate a versioned, structured report with:

- Referral/case identifiers, creation timestamp, selected firm's name and case category/jurisdiction.
- Caller-provided facts, chronology, stated urgency and stated deadlines (without independently guaranteeing deadlines).
- Confirmed vs. unconfirmed information, unknowns and outstanding questions.
- Safe contact preferences, sharing permission, referral status and disclaimer that the firm must assess the matter independently.

Use an authenticated firm portal or **expiring signed link** with appropriate additional access control. Prefer a minimal alert email containing the case reference and portal link, not the full sensitive report. Encrypt data at rest and in transit. Apply a configurable deletion/retention policy to recordings, transcripts, case reports and provider data; do not assume the voice vendor's default retention is acceptable.

Notification jobs:

- After approved report sharing: send firm intake notification exactly once per referral/report version.
- After successful provider-confirmed transfer: notify the caller only if requested/permitted; distinguish connection from representation.
- After transfer failure or out-of-hours intake: notify the caller of **pending** status and next steps; do not promise a lawyer is already engaged.
- After firm acceptance/decline: update case status and send permitted follow-up.
- **All SMS must use Guava** via `guava.Client().send_sms(from_number=GUAVA_AGENT_NUMBER, to_number=verified_caller_number, message=...)` or the current equivalent confirmed in Guava docs. The sender must be an approved SMS-capable Guava number. Never send an SMS to the two demo lawyer numbers unless separately authorized for that exact purpose.
- Maintain separate job types: `send_guava_sms` for caller texts and `send_email` for minimum-necessary firm notifications. Do not install or configure a second SMS/telephone provider.
- Retry transient network errors with exponential backoff; use dead-letter or operator alert after retry exhaustion; deduplicate on a stable business event key. Unknown delivery status stays `submitted`/`unknown` until evidence arrives; never claim delivery just from a successful API response.
- Provide opt-out handling and template review for applicable SMS/email rules. Verify Guava number/country coverage, account entitlements, STOP processing and message-delivery event availability. If unavailable, document it as a blocker and do not silently send with another carrier.

## 8. Operator dashboard (pilot)

Implement minimal authenticated operator functions:

- View and search cases with status, jurisdiction, provisional category and urgency; redact unnecessary PII in list views.
- Open a case and review caller-confirmed facts, consent evidence, referral history, notification status and transfer outcome.
- View/maintain verified firm directory, eligibility rules, hours, exceptions, current capacity and live-call toggle.
- Manually reclassify, hold, select eligible firm, queue review, record a phone follow-up, and record firm acceptance/decline.
- View failures: disconnected calls, unhandled webhooks, failed transfers, undelivered notifications, stale firm availability and unacknowledged referrals.
- Audit all sensitive reads and mutations. Provide least-privilege roles (e.g., `admin`, `operator`, `firm_user`) and firm-level isolation. Add firm-facing portal only if needed to securely access reports in MVP.

## 9. Build sequence: complete in order

### Phase 0 — repository/documentation audit

- [ ] Clone/open the existing repository and report its actual structure, stack and working branch.
- [ ] Read current Guava docs and list exact verified methods/events for inbound numbers, field collection, native transfer, call outcomes and **Guava send_sms**. Verify whether the CaseLine Guava number can send SMS and how replies/STOP are handled.
- [ ] Document missing credentials/Guava account features and use a mock **Guava** adapter for unverified phone/SMS capabilities; an unrelated email service may be mocked separately.
- [ ] Create `docs/decisions/0001-architecture.md` with deviations from this proposed design.
- [ ] Add a development `.env.example` (no secrets) and ensure `.env` is gitignored.

**Done when:** the project builds/runs in its existing baseline and external SDK assumptions have been explicitly verified or isolated.

### Phase 1 — runnable vertical slice with mocks

- [ ] Implement FastAPI with a health endpoint, Pydantic input/output schemas and config loading.
- [ ] Add PostgreSQL schema/migrations and seed fictional firms with one open, one closed and one ineligible example. **Also seed the two explicitly provided demo partner destinations from Section 4A as separate demo-only fixtures**, without inventing real firm details.
- [ ] Implement rule-based intake classification (start with fixture/controlled mapping, no model ranking), jurisdiction/category eligibility and timezone-aware availability.
- [ ] Implement `POST /v1/intake/triage`, consent check and deterministic firm selection with recorded rationale.
- [ ] Add a mock voice-provider adapter and a CLI/test harness that exercises an entire inbound call scenario without making a real phone call.
- [ ] Cover open, after-hours, no-eligible-firm, urgent case and consent-denied branches, **plus deterministic selection and mocked transfer to each of the two demo numbers**.

**Done when:** one command starts the local dependencies/API and one test suite simulates all major triage outcomes reproducibly.

### Phase 2 — verified Guava integration and transfers

- [ ] Create/update the Guava agent according to **verified current** documentation.
- [ ] Implement privacy/consent notice, Stage A natural-language intake and Stage B category-specific questions.
- [ ] Send schema-validated data to authenticated backend endpoints.
- [ ] Execute a live transfer only after an approved firm is selected and caller permission is captured. **For the demo, support both Section 4A destinations behind an off-by-default live-transfer flag and strict E.164 allowlist.**
- [ ] Ingest provider call and transfer callbacks; never equate `transfer_requested` with `connected`.
- [ ] Implement transfer no-answer/busy/timeout/hangup fallback and retry-safe Guava-event processing.
- [ ] Configure a Guava-managed CaseLine inbound number directly with the Guava agent; no third-party telephone number or intermediary routing service. Confirm exact setup, account entitlements and operational limitations.

**Done when:** a staging/test telephone number can complete the simulated and actual test-call flow, including a failed-transfer fallback, **without** exposing real sensitive caller information.

### Phase 3 — asynchronous referral and notification

- [ ] Create background workers and persistent notification queue.
- [ ] Generate secure, versioned case reports from confirmed fields and label unknowns.
- [ ] Deliver a minimal firm email with secure report access, after authorized sharing.
- [ ] Implement caller SMS exclusively with `guava.Client().send_sms(...)` (or verified current Guava equivalent); use a Guava-managed SMS-capable number, explicit opt-in, STOP handling, retries, failure/unknown delivery states and idempotency. Email may be delivered by the configured independent email provider. Never fall back to another SMS provider.
- [ ] Implement firm accept/decline callbacks or admin updates, expiry/escalation and optional reassignment.

**Done when:** out-of-hours intake creates exactly one authorized referral and notification despite duplicate event delivery; pending/accepted/declined statuses remain distinct.

### Phase 4 — dashboard, security and controlled pilot

- [ ] Create the operator UI for case review, partner-firm configuration and referral tracking.
- [ ] Add RBAC, firm isolation, redaction, audit logs, rate limits, encrypted storage and signed report links.
- [ ] Add observability without PII: request/call correlation IDs, latency, classification uncertainty, transfer outcomes, referral acceptance and queue health.
- [ ] Validate disaster/restart behavior: incomplete calls, lost Guava callbacks, stale hours and duplicate notifications.
- [ ] Complete jurisdiction-specific legal/privacy review before real caller use; record approved notices, consumer payment/referral policies and retention schedules.
- [ ] Run a supervised pilot with a small set of verified firms and explicit incident response ownership.

**Done when:** the release checklist below passes and a supervised test confirms complete end-to-end operation.

## 10. Tests and acceptance criteria

At minimum, implement these automated scenarios (all fictional data):

| Test | Expected result |
| --- | --- |
| Open firm, eligible practice/jurisdiction, accepts live calls, transfer connected | Confirmed transfer with one referral and documented outcome. |
| Open firm but no answer/busy/timeout | Transfer failure recorded; extended intake or human follow-up starts; no false success message. |
| Firm closed in its local timezone or on a holiday | No live transfer; complete approved extended intake and create pending referral. |
| DST boundary | Hours computed using the firm's IANA timezone, not server-local time. |
| No firm in jurisdiction or category | Clear no-match result or human review; no arbitrary firm selected. |
| Low-confidence/ambiguous category | Human review; no unverified legal classification presented as certain. |
| Caller reports immediate danger | Appropriate emergency guidance; routine routing does not supersede safety. |
| Caller reports imminent legal deadline | Priority escalation; no fabricated deadline calculations. |
| Recording or report-sharing permission declined | Respect refusal; do not record/share contrary to approved policy. |
| Duplicate/reordered Guava event | State remains correct; no duplicate referrals or Guava SMS sends. |
| Guava SMS allowed, caller opted in | Mock adapter records exactly one correct SMS job; supervised Guava delivery only after manual enablement. |
| Guava SMS unavailable or caller opted out | No text and no fallback to another provider; safe manual/email follow-up where permitted. |
| Agent disconnects mid-intake | Graceful partial-case handling within consent and retention limits. |
| Wrong firm/user attempts report access | Access denied and event logged; no cross-firm leak. |
| Worker restarts mid-notification | Event is retryable and idempotent. |
| Report edited after initial delivery | New version retained; recipient sees the correct version and an explicit update if needed. |
| Demo scenario A/B with mock calling | Selected destinations are exactly `+12676804795` and `+12676804795`, respectively; no real calls. |
| Demo mode enabled but live transfers disabled | No real dialing, regardless of model output or simulated availability. |
| Demo caller declines transfer or destination is not allowlisted | Transfer blocked and non-call fallback recorded. |

Success metrics for the pilot: completed triage rate, transfer **connection** rate (not attempts), time to first human response, firm acceptance rate, abandoned calls, mistaken category/route rate, notification deliverability, consent capture completeness, and zero unauthorized report disclosures. Instrument these before onboarding real callers.

## 11. Windows PowerShell setup examples

**Do not overwrite existing project setup.** First inspect the README and manifests, then choose the matching commands. These examples assume Git, Node.js LTS, uv and Python 3.12+ (3.13 used) have been installed.

```powershell
# Clone only if you have not already cloned the repository:
git clone https://github.com/shawnzhu02/CaseLine.git
cd CaseLine

# Inspect before making structural changes:
git status
git branch --show-current
Get-ChildItem -Force

# Develop on a feature branch:
git switch -c feat/caseline-mvp

# If Python backend is new and will live at apps/api:
New-Item -ItemType Directory -Force apps/api
cd apps/api
uv venv -p 3.13 .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install fastapi "uvicorn[standard]" sqlalchemy alembic `
    "psycopg[binary]" pydantic-settings httpx pytest
# Install from a pinned requirements/lock file once created.

# After main.py exists:
python -m uvicorn main:app --reload

# Run tests from the appropriate project root, after setup:
python -m pytest -q
```

When adding a local database, prefer a repository `compose.yaml`, start it with `docker compose up -d`, and document a matching stop command `docker compose down`. Use a migration command defined by the project rather than hand-creating production tables. Add separate staging and production deployment instructions.

**Guava:** verify the CLI installation instructions on https://goguava.ai/docs/everything.md. Authenticate and create/configure the agent only after inspecting any existing `apps/voice` project. Do not run arbitrary remote install scripts without reviewing their source/approval under the developer's security policy.

## 12. Configuration inventory

Put placeholder names (not real values) in `.env.example` and validate them at startup:

```dotenv
APP_ENV=development
API_PUBLIC_BASE_URL=http://localhost:8000
DATABASE_URL=postgresql+psycopg://caseline:CHANGE_ME@localhost:5432/caseline
TELECOM_PROVIDER=guava
GUAVA_MODE=mock
GUAVA_API_KEY=
GUAVA_AGENT_NUMBER=+14849687497
GUAVA_WEBHOOK_SECRET=
GUAVA_SMS_ENABLED=false
GUAVA_SMS_FROM_NUMBER=
CASELINE_INTERNAL_API_TOKEN=
EMAIL_PROVIDER=mock
RESEND_API_KEY=
EMAIL_FROM=
# Demo-only inbound routing fixtures; live outbound transfer defaults OFF.
DEMO_MODE=false
DEMO_LIVE_TRANSFER_ENABLED=false
DEMO_FIRM_A_NAME=Demo Partner Firm A
DEMO_FIRM_A_TRANSFER_NUMBER=+12676804795
DEMO_FIRM_B_NAME=Demo Partner Firm B
DEMO_FIRM_B_TRANSFER_NUMBER=+12676804795
DEMO_TRANSFER_ALLOWLIST=+12676804795
DEMO_SIMULATE_FIRM_AVAILABILITY=false
STORAGE_BUCKET=
STORAGE_REGION=
REPORT_LINK_TTL_MINUTES=30
```

Treat environment-variable names as **CaseLine conventions**, not pre-existing Guava settings; update names to match actual SDK/configuration where required. `GUAVA_MODE=mock` permits an in-memory fake for local tests but **never another real telecom provider**. Enable `GUAVA_SMS_ENABLED=true` only after confirming the configured Guava sender supports outbound SMS, approved templates and compliant consent/STOP behavior. Use real secret management in production and rotation-friendly configuration.

## 13. Legal, privacy and commercial release gates

A qualified practitioner must approve the launch jurisdiction, jurisdiction-specific advertising and lawyer-referral rules, any consumer fee or partner compensation, required firm disclosures, call-recording and telecommunications consent, sensitive-data handling, and complaint/incident processes. CaseLine should disclose any materially relevant partner/commercial relationships in the manner required by the applicable rules. Do not assume charging the consumer bypasses referral regulations.

Implementation gates before production:

- Approved caller-facing notices/scripts, consent flow and correct local emergency escalation.
- Verified partner-firm authorization, eligible practice categories, permitted marketing/referral arrangement and documented onboarding criteria.
- Signed service/data-processing agreements as applicable; review Guava's telephony/SMS retention, subprocessors and cross-border data policies, and separately assess the chosen firm-email vendor.
- Documented data retention/deletion, privacy access requests, incident response, least-privilege access and backups.
- Guava-specific SMS consent/registration/opt-out configuration (including sender-number eligibility and STOP handling) and email sender authentication as required by applicable rules.
- Penetration review for webhook spoofing, cross-tenant report access, enumeration, prompt injection through caller speech and arbitrary outbound transfer-number injection.
- Human escalation capacity for urgent cases and incomplete/failed referrals.

## 14. Required final deliverables from the agent

At the end of implementation, provide:

1. A concise account of **what actually changed** with file paths and commit/PR references if available.
2. A working local dev procedure for Windows PowerShell, including services, env vars, database migrations, seed data, test commands and how to run a mock call.
3. A current `README.md`, `.env.example`, OpenAPI documentation and architecture/decision notes.
4. Test results for successful transfer, transfer failure, after-hours routing, no-match, consent-denied, duplicate events and unauthorized report access.
5. Clearly labeled **implemented / mocked / blocked / needs partner decision** items. Do not claim untested Guava transfer semantics or successful delivery to real firms.
6. The minimum remaining checklist for a supervised staging pilot, identifying the required **Guava** credential/managed number/SMS capability, permitted separate email configuration, legal approval and partner-firm input. **Include the exact manual steps to demonstrate routing to both user-provided numbers in Section 4A, separately, after obtaining both recipients' permission.**

**First concrete task:** audit the current CaseLine repository and current Guava documentation, including native SMS; then implement Phase 1 with fictional seeded firms **and the two explicitly specified demo transfer destinations** plus a mocked **Guava** inbound call. Demonstrate that scripted scenario A resolves to `+12676804795` and scenario B to `+12676804795` before configuring supervised real calling. Never run live outbound calls from automated tests.


---

## 15. Canonical target repository layout

This section is the **default implementation contract** if the existing repository does not already have an equivalent structure. If the repository already has working apps/packages, adapt these responsibilities into the existing layout instead of duplicating them.

```text
CaseLine/
├── .github/
│   └── workflows/
│       └── ci.yml
├── apps/
│   ├── api/
│   │   ├── pyproject.toml
│   │   ├── alembic.ini
│   │   ├── alembic/
│   │   │   └── versions/
│   │   ├── caseline/
│   │   │   ├── __init__.py
│   │   │   ├── main.py
│   │   │   ├── config.py
│   │   │   ├── db.py
│   │   │   ├── models.py
│   │   │   ├── schemas.py
│   │   │   ├── enums.py
│   │   │   ├── auth.py
│   │   │   ├── logging.py
│   │   │   ├── routers/
│   │   │   │   ├── health.py
│   │   │   │   ├── intake.py
│   │   │   │   ├── transfers.py
│   │   │   │   ├── referrals.py
│   │   │   │   └── admin.py
│   │   │   ├── services/
│   │   │   │   ├── matching.py
│   │   │   │   ├── availability.py
│   │   │   │   ├── consent.py
│   │   │   │   ├── transfer_authorization.py
│   │   │   │   ├── report_builder.py
│   │   │   │   └── notifications.py
│   │   │   ├── providers/
│   │   │   │   ├── guava_gateway.py
│   │   │   │   ├── guava_mock.py
│   │   │   │   ├── email_gateway.py
│   │   │   │   └── email_mock.py
│   │   │   └── workers/
│   │   │       └── jobs.py
│   │   └── tests/
│   │       ├── conftest.py
│   │       ├── test_triage.py
│   │       ├── test_matching.py
│   │       ├── test_transfer_security.py
│   │       ├── test_demo_routes.py
│   │       ├── test_idempotency.py
│   │       └── test_notifications.py
│   ├── voice/
│   │   ├── pyproject.toml
│   │   ├── main.py
│   │   ├── caseline_voice/
│   │   │   ├── agent.py
│   │   │   ├── backend_client.py
│   │   │   ├── prompts.py
│   │   │   ├── schemas.py
│   │   │   └── settings.py
│   │   └── tests/
│   │       └── test_backend_client.py
│   └── admin/                         # optional for first demo; implement by Phase 4
│       ├── package.json
│       └── ...
├── scripts/
│   ├── seed_demo.py
│   ├── smoke_demo.py
│   └── reset_dev_db.ps1
├── docs/
│   ├── architecture.md
│   ├── demo-runbook.md
│   ├── api-contract.md
│   └── decisions/
│       └── 0001-architecture.md
├── compose.yaml
├── .env.example
├── .gitignore
├── README.md
└── CASELINE_IMPLEMENTATION_PLAN.md
```

### File ownership rules

- `apps/voice` is the **only** place that imports the Guava SDK directly for call handling.
- `apps/api` owns matching, persistence, consent, transfer authorization, reports, audit state and async notifications.
- The voice agent may never select or construct a phone number on its own. It receives a short-lived transfer authorization from the backend.
- The backend may expose a dial destination to the Guava agent only after server-side allowlist, eligibility and consent checks.
- No package, config variable, import or webhook for another telephony/SMS vendor is permitted anywhere in the repository (documentation may name vendors only to prohibit them).

## 16. Python dependency and packaging contract

If the repository has no existing Python packaging standard, use Python >=3.12 and `pyproject.toml`. Pin compatible versions after the first successful install and commit the resulting lock file if the chosen package manager supports one.

Minimum API dependencies:

```toml
[project]
name = "caseline-api"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
  "fastapi",
  "uvicorn[standard]",
  "sqlalchemy>=2",
  "alembic",
  "psycopg[binary]",
  "pydantic>=2",
  "pydantic-settings",
  "httpx",
  "phonenumbers",
  "python-multipart",
]

[project.optional-dependencies]
dev = [
  "pytest",
  "pytest-asyncio",
  "pytest-cov",
  "ruff",
  "mypy",
]
```

Minimum voice dependencies:

```toml
[project]
name = "caseline-voice"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
  "guava-sdk==0.45.0",  # version generated by `guava create` (CLI 0.45.0)
  "httpx",
  "pydantic>=2",
  "pydantic-settings",
]
```

The official Guava docs currently show `pip install guava-sdk`; use the package/version generated or required by the current Guava CLI template if different. Do not guess a Guava version number before checking the generated project or package index.

## 17. Required typed configuration

Implement `Settings` using `pydantic-settings` and fail fast in non-development environments when required values are missing.

```python
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    api_public_base_url: str = "http://localhost:8000"
    database_url: str

    telecom_provider: str = "guava"
    guava_mode: str = "mock"
    guava_api_key: str | None = None
    guava_agent_number: str | None = None
    guava_sms_enabled: bool = False
    guava_sms_from_number: str | None = None

    caseline_internal_api_token: str

    demo_mode: bool = False
    demo_live_transfer_enabled: bool = False
    demo_firm_a_transfer_number: str = "+12676804795"
    demo_firm_b_transfer_number: str = "+12676804795"
    demo_transfer_allowlist: str = "+12676804795"
    demo_simulate_firm_availability: bool = False
```

Startup assertions:

1. `telecom_provider` must equal `guava`.
2. When `guava_mode=live`, `GUAVA_API_KEY` and `GUAVA_AGENT_NUMBER` must be present.
3. `DEMO_LIVE_TRANSFER_ENABLED=true` is invalid unless `DEMO_MODE=true`.
4. Every allowlisted transfer number must parse as E.164.
5. In demo mode, the allowlist must contain only the approved number unless the user explicitly changes this specification.
6. Never print secrets in logs or validation errors.

## 18. Domain enums and state machine

Use enums instead of free-form strings for persistent state.

```python
from enum import StrEnum

class CaseStatus(StrEnum):
    NEW = "new"
    CONSENT_PENDING = "consent_pending"
    TRIAGE_IN_PROGRESS = "triage_in_progress"
    TRIAGE_READY = "triage_ready"
    TRANSFER_PENDING = "transfer_pending"
    TRANSFER_CONNECTED = "transfer_connected"
    TRANSFER_FAILED = "transfer_failed"
    EXTENDED_INTAKE = "extended_intake"
    REFERRAL_PENDING = "referral_pending"
    FIRM_NOTIFIED = "firm_notified"
    FIRM_ACCEPTED = "firm_accepted"
    FIRM_DECLINED = "firm_declined"
    HUMAN_REVIEW = "human_review"
    NO_ELIGIBLE_FIRM = "no_eligible_firm"
    CLOSED = "closed"

class ReferralStatus(StrEnum):
    CREATED = "created"
    TRANSFER_AUTHORIZED = "transfer_authorized"
    TRANSFER_REQUESTED = "transfer_requested"
    TRANSFER_CONNECTED = "transfer_connected"
    TRANSFER_FAILED = "transfer_failed"
    PENDING_ASYNC = "pending_async"
    FIRM_NOTIFIED = "firm_notified"
    ACCEPTED = "accepted"
    DECLINED = "declined"
    EXPIRED = "expired"

class RoutingAction(StrEnum):
    TRANSFER = "transfer"
    EXTENDED_INTAKE = "extended_intake"
    HUMAN_REVIEW = "human_review"
    NO_ELIGIBLE_FIRM = "no_eligible_firm"
    EMERGENCY_GUIDANCE = "emergency_guidance"
```

Implement explicit transition validation. Invalid transitions return HTTP 409 and are audit-logged. Do not allow a transfer request to directly produce `FIRM_ACCEPTED`.

## 19. Exact backend request/response contract for the demo

### 19.1 `POST /v1/intake/triage`

Headers:

```text
Authorization: Bearer <CASELINE_INTERNAL_API_TOKEN>
Idempotency-Key: <provider-call-id>:triage:v1
Content-Type: application/json
```

Request:

```json
{
  "provider_call_id": "demo-call-001",
  "caller": {
    "name": "Alex Demo",
    "callback_number": "+12125550100",
    "preferred_language": "en"
  },
  "facts": {
    "jurisdiction": "DEMO_JURISDICTION",
    "practice_area": "DEMO_AREA_A",
    "issue_summary": "Fictional demo matter for Firm A",
    "immediate_danger": false,
    "caller_reported_deadline": null
  },
  "consents": {
    "intake": true,
    "share_with_selected_firm": false,
    "sms": false
  }
}
```

Response when a firm can potentially receive a live transfer:

```json
{
  "case_id": "<uuid>",
  "referral_id": "<uuid>",
  "action": "transfer",
  "selected_firm": {
    "firm_id": "demo-firm-a",
    "display_name": "Demo Partner Firm A"
  },
  "requires_transfer_consent": true,
  "transfer_authorization": null,
  "next_prompt": "I can connect you to Demo Partner Firm A. Would you like me to transfer you?"
}
```

The triage endpoint must **not** expose the phone number before the caller confirms transfer.

### 19.2 `POST /v1/referrals/{referral_id}/authorize-transfer`

Headers:

```text
Authorization: Bearer <CASELINE_INTERNAL_API_TOKEN>
Idempotency-Key: <provider-call-id>:transfer-consent:v1
```

Request:

```json
{
  "provider_call_id": "demo-call-001",
  "caller_consented": true
}
```

Response:

```json
{
  "authorization_id": "<uuid>",
  "destination_e164": "+12676804795",
  "display_name": "Demo Partner Firm A",
  "expires_at": "<UTC ISO-8601 timestamp about 2 minutes in the future>"
}
```

Server rules before returning `destination_e164`:

- referral exists and belongs to this case/call;
- selected firm remains eligible and accepting live calls;
- caller transfer consent is stored;
- `DEMO_MODE=true` and `DEMO_LIVE_TRANSFER_ENABLED=true` for the real demo path;
- destination is present in `DEMO_TRANSFER_ALLOWLIST`;
- authorization is one-time-use and short-lived;
- caller cannot supply or override the destination.

When live transfer is disabled, return HTTP 409 with a typed reason such as `live_transfer_disabled`; the voice agent then runs extended intake instead of dialing.

### 19.3 `POST /v1/referrals/{referral_id}/transfer-attempts`

Request immediately before calling `call.transfer(...)`:

```json
{
  "authorization_id": "<uuid>",
  "provider_call_id": "demo-call-001",
  "state": "requested"
}
```

The API atomically consumes the authorization and creates one transfer attempt. Duplicate requests with the same idempotency key return the same attempt.

If Guava exposes a verified transfer/connect/fail event, record it through an authenticated event path. If the current SDK exposes only transfer initiation, keep the attempt as `requested`/`unknown` until operator confirmation; never invent a `connected` result.

## 20. Matching engine: deterministic algorithm

Do not use an LLM to rank firms in the MVP. The LLM may help extract a provisional category, but final selection is deterministic and auditable.

Pseudo-code:

```python
def select_firm(case, firms, now_utc):
    eligible = []
    for firm in firms:
        if not firm.verified_for_demo_or_production:
            continue
        if not firm.accepting_referrals:
            continue
        if case.jurisdiction not in firm.jurisdictions:
            continue
        if case.practice_area not in firm.practice_areas:
            continue
        eligible.append(firm)

    if not eligible:
        return NoEligibleFirm()

    # stable ordering; later replace with persisted round-robin pointer
    eligible.sort(key=lambda f: (f.routing_priority, str(f.id)))

    for firm in eligible:
        availability = get_availability(firm, now_utc)
        if availability.accepting_live_calls:
            return TransferCandidate(firm)

    return AsyncReferralCandidate(eligible[0])
```

For the demo seeds:

- `DEMO_AREA_A` + `DEMO_JURISDICTION` → only `demo-firm-a` eligible → `+12676804795` after authorization.
- `DEMO_AREA_B` + `DEMO_JURISDICTION` → only `demo-firm-b` eligible → `+12676804795` after authorization.

These categories are fictional routing fixtures. They do not represent the recipients' real legal specialties.

## 21. Guava voice agent implementation contract

The current Guava docs show the following concepts and should be treated as the starting point:

- `guava.Agent(...)`
- `@agent.on_call_start`
- `call.set_task(...)`
- `guava.Say(...)`
- `guava.Field(...)`
- `@agent.on_task_complete("...")`
- `call.get_field(...)`
- `call.transfer(destination, handoff_instruction)`
- `guava.Client().send_sms(...)`
- inbound listening with `agent.listen_phone(number)` (verified in guava-sdk 0.45.0 and its generated template; `inbound_phone(...).run()` is not used).

The voice app must follow this flow:

```text
on inbound call
  -> create/lookup call session in CaseLine API
  -> privacy + AI identity notice
  -> collect Stage A fields with Guava Field checklist
  -> POST /v1/intake/triage
      -> emergency_guidance: give approved emergency wording and stop routine routing
      -> human_review: collect safe callback details and finish
      -> no_eligible_firm: clearly state no connection was arranged
      -> extended_intake: run Stage B and persist facts
      -> transfer:
           speak selected firm name
           ask explicit transfer consent
           if no -> extended intake
           if yes -> POST authorize-transfer
                     if 409 -> extended intake
                     if authorized -> POST transfer-attempt requested
                                      call.transfer(exact authorized E.164, handoff instruction)
```

Illustrative Guava skeleton; adapt only the SDK-specific entrypoint/signatures to the current generated Guava project:

```python
import os
import guava
from guava import Agent

from caseline_voice.backend_client import CaseLineBackend

backend = CaseLineBackend(
    base_url=os.environ["CASELINE_API_BASE_URL"],
    token=os.environ["CASELINE_INTERNAL_API_TOKEN"],
)

agent = Agent(
    name="CaseLine",
    organization="CaseLine",
    purpose="collect legal intake information and connect callers with participating firms",
)

@agent.on_call_start
def on_call_start(call: guava.Call) -> None:
    call.set_task(
        "triage",
        objective=(
            "Collect the minimum information needed to route the caller. "
            "Do not provide legal advice or promise representation."
        ),
        checklist=[
            guava.Say(
                "Thanks for calling CaseLine. I'm an AI intake assistant, not a lawyer. "
                "I can collect information and help connect you with a participating firm."
            ),
            guava.Field(key="caller_name", description="Ask for the caller's name.", field_type="text", required=True),
            guava.Field(key="issue_summary", description="Let the caller explain what happened in their own words.", field_type="text", required=True),
            guava.Field(key="location", description="Ask where the legal issue occurred or which jurisdiction applies, if known.", field_type="text", required=True),
            guava.Field(key="callback_number", description="Confirm the best callback number.", field_type="text", required=True),
        ],
    )

@agent.on_task_complete("triage")
def triage_complete(call: guava.Call) -> None:
    # Build a validated Pydantic request; never forward raw arbitrary model output.
    result = backend.triage_from_call(call)
    route_result(call, result)
```

### Handoff instruction

The handoff prompt sent to Guava transfer should be short and must not expose confidential information before the receiving person answers. Example:

```python
call.transfer(
    destination_e164,
    f"Tell the caller you're connecting them with {display_name}. Do not summarize confidential case facts over the transfer announcement.",
)
```

Do not have the model speak or dial the raw number. Do not use DTMF unless the selected demo destination later requires an IVR and that behavior is explicitly configured.

## 22. Backend client used by the Guava agent

Implement a small `httpx.Client` wrapper with hard timeouts and no automatic unsafe retries on non-idempotent requests.

```python
class CaseLineBackend:
    def __init__(self, base_url: str, token: str):
        self.client = httpx.Client(
            base_url=base_url,
            timeout=httpx.Timeout(8.0, connect=3.0),
            headers={"Authorization": f"Bearer {token}"},
        )

    def triage(self, payload: dict, idempotency_key: str) -> dict:
        response = self.client.post(
            "/v1/intake/triage",
            json=payload,
            headers={"Idempotency-Key": idempotency_key},
        )
        response.raise_for_status()
        return response.json()
```

Failure behavior:

- API timeout/unavailable: apologize, collect a safe callback number if not already captured, and end in `human_review`; do not guess a firm.
- HTTP 401/403: log a redacted configuration error and use human-review fallback.
- HTTP 409 on transfer authorization: do not dial; continue extended intake/fallback.
- HTTP 422: treat as internal schema bug, capture correlation ID, and use human-review fallback.

## 23. Database implementation details

Use UUID primary keys for production entities. Demo firms may additionally have stable slugs (`demo-firm-a`, `demo-firm-b`). Every table includes `created_at` and `updated_at` UTC timestamps where meaningful.

Minimum database constraints:

- `call_sessions.provider_call_id` unique.
- `notifications.event_key` unique.
- `provider_events(provider, provider_event_id)` unique if Guava exposes event IDs.
- `transfer_authorizations.token_hash` unique; store a hash, not the raw bearer token, if using opaque tokens.
- `transfer_authorizations.used_at` nullable; authorization is invalid after first use.
- unique firm slug.
- foreign keys use explicit delete behavior; do not cascade-delete audit history accidentally.
- indexes on `cases.status`, `referrals.status`, `referrals.firm_id`, `call_sessions.provider_call_id`, `notifications.status`.

Add tables not listed earlier if needed:

```text
transfer_authorizations
- id UUID PK
- referral_id UUID FK
- destination_e164 TEXT
- expires_at TIMESTAMPTZ
- used_at TIMESTAMPTZ NULL
- created_by TEXT

provider_events
- id UUID PK
- provider TEXT
- provider_event_id TEXT
- event_type TEXT
- received_at TIMESTAMPTZ
- payload_redacted JSONB
- UNIQUE(provider, provider_event_id)
```

Migration workflow:

```powershell
cd apps/api
.\.venv\Scripts\Activate.ps1
alembic revision --autogenerate -m "initial caseline schema"
alembic upgrade head
python ..\..\scripts\seed_demo.py
```

The coding agent must inspect autogenerated migrations before committing them.

## 24. Demo seed data contract

`python scripts/seed_demo.py` must be safe to run repeatedly. Use an upsert keyed on stable slug.

Seed exactly:

```python
DEMO_FIRMS = [
    {
        "slug": "demo-firm-a",
        "display_name": "Demo Partner Firm A",
        "transfer_number": "+12676804795",
        "jurisdictions": ["DEMO_JURISDICTION"],
        "practice_areas": ["DEMO_AREA_A"],
        "accepting_referrals": True,
        "accepting_live_calls": True,
        "is_demo": True,
    },
    {
        "slug": "demo-firm-b",
        "display_name": "Demo Partner Firm B",
        "transfer_number": "+12676804795",
        "jurisdictions": ["DEMO_JURISDICTION"],
        "practice_areas": ["DEMO_AREA_B"],
        "accepting_referrals": True,
        "accepting_live_calls": True,
        "is_demo": True,
    },
]
```

Never attach real lawyer names, credentials, addresses, practice areas or firm identities to these numbers unless the user explicitly provides and approves them.

## 25. Async jobs and notification implementation

Use the Postgres outbox (`notifications` rows + `python -m caseline.workers.outbox`); see revision note 3.

Job names:

```text
send_guava_sms(notification_id)
send_firm_email(notification_id)
expire_referral(referral_id)
reconcile_stale_transfer(transfer_attempt_id)
```

Rules:

- job payloads contain entity IDs, not full case narratives;
- workers reload current data from the database;
- every communication has a persisted notification row before enqueueing;
- jobs are idempotent using `notifications.event_key`;
- retries are bounded with exponential backoff;
- after max retries, state becomes `failed` and appears in the operator queue;
- Guava SMS is off in tests and CI;
- firm email defaults to mock until configured;
- never email the full transcript or unrestricted report URL.

## 26. `compose.yaml` development contract

If the repository does not already have local services, add:

```yaml
services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_DB: caseline
      POSTGRES_USER: caseline
      POSTGRES_PASSWORD: caseline_dev_only
    ports:
      - "5432:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U caseline -d caseline"]
      interval: 5s
      timeout: 5s
      retries: 10
    volumes:
      - caseline_postgres:/var/lib/postgresql/data

volumes:
  caseline_postgres:
```

These passwords are development-only. Production uses managed secret storage and must not reuse them.

## 27. GitHub Actions CI: required safe pipeline

Create `.github/workflows/ci.yml` unless equivalent CI already exists. CI must **never** use live Guava credentials and must never call the demo lawyers.

```yaml
name: CI

on:
  pull_request:
  push:
    branches: [main]

jobs:
  api-tests:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:16
        env:
          POSTGRES_DB: caseline_test
          POSTGRES_USER: caseline
          POSTGRES_PASSWORD: caseline
        ports:
          - 5432:5432
        options: >-
          --health-cmd "pg_isready -U caseline -d caseline_test"
          --health-interval 5s
          --health-timeout 5s
          --health-retries 10
    env:
      APP_ENV: test
      DATABASE_URL: postgresql+psycopg://caseline:caseline@localhost:5432/caseline_test
      TELECOM_PROVIDER: guava
      GUAVA_MODE: mock
      GUAVA_SMS_ENABLED: "false"
      CASELINE_INTERNAL_API_TOKEN: test-only-token
      DEMO_MODE: "true"
      DEMO_LIVE_TRANSFER_ENABLED: "false"
      DEMO_FIRM_A_TRANSFER_NUMBER: +12676804795
      DEMO_FIRM_B_TRANSFER_NUMBER: +12676804795
      DEMO_TRANSFER_ALLOWLIST: +12676804795
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.13"
      - name: Install API
        working-directory: apps/api
        run: python -m pip install -e ".[dev]"
      - name: Migrate
        working-directory: apps/api
        run: alembic upgrade head
      - name: Lint
        working-directory: apps/api
        run: ruff check .
      - name: Test
        working-directory: apps/api
        run: pytest -q --cov=caseline --cov-report=term-missing
```

If `apps/voice` contains meaningful pure-Python logic, add a second job that installs it and runs its unit tests with a mocked backend. Do not execute `guava run`, `guava deploy`, `call.transfer`, outbound calls or SMS in CI.

## 28. Required automated tests with exact demo assertions

At minimum, implement these test names or equivalent:

```text
test_demo_area_a_selects_only_firm_a
test_demo_area_b_selects_only_firm_b
test_triage_response_does_not_expose_phone_before_consent
test_authorize_transfer_returns_firm_a_number_after_consent
test_authorize_transfer_returns_firm_b_number_after_consent
test_transfer_blocked_when_demo_live_disabled
test_transfer_blocked_for_number_not_in_allowlist
test_transfer_authorization_is_single_use
test_transfer_authorization_expires
test_duplicate_triage_idempotency_returns_same_case
test_duplicate_transfer_attempt_does_not_redial
test_after_hours_routes_to_extended_intake
test_no_match_routes_to_human_review_or_no_eligible
test_api_failure_never_guesses_destination
test_sms_disabled_does_not_call_guava_client
test_ci_configuration_cannot_enable_live_transfer
```

Critical assertions:

```python
assert result_a.selected_firm.slug == "demo-firm-a"
assert auth_a.destination_e164 == "+12676804795"
assert result_b.selected_firm.slug == "demo-firm-b"
assert auth_b.destination_e164 == "+12676804795"
```

Search the repository during CI or a dedicated test for prohibited telecom dependencies:

```powershell
# Local Windows check. It should produce no relevant telephony integration matches.
git grep -n -i "twilio\|telnyx\|amazon connect" -- ':!CASELINE_IMPLEMENTATION_PLAN.md' ':!docs/**'
```

References to Amazon Connect in copied Guava documentation must not become runtime dependencies.

## 29. Exact local run procedure for Windows PowerShell

The coding agent must make these commands work or update the README with the repository-equivalent commands.

### 29.1 Clone and branch

```powershell
git clone https://github.com/shawnzhu02/CaseLine.git
cd CaseLine
git status
git switch -c feat/caseline-guava-mvp
```

If working in an already checked-out GitHub agent workspace, do not clone again; inspect the current branch and worktree instead.

### 29.2 Start dependencies

```powershell
Copy-Item .env.example .env
docker compose up -d
docker compose ps
```

### 29.3 Set up API

```powershell
cd apps\api
uv venv -p 3.13 .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
alembic upgrade head
cd ..\..
python scripts\seed_demo.py
```

### 29.4 Run API

Open PowerShell window 1:

```powershell
cd CaseLine\apps\api
.\.venv\Scripts\Activate.ps1
python -m uvicorn caseline.main:app --reload --host 127.0.0.1 --port 8000
```

Then verify:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health/live
Invoke-RestMethod http://127.0.0.1:8000/health/ready
```

### 29.5 Run tests

Open PowerShell window 2:

```powershell
cd CaseLine\apps\api
.\.venv\Scripts\Activate.ps1
pytest -q
```

### 29.6 Run mock demo

```powershell
cd CaseLine
python scripts\smoke_demo.py --scenario firm-a
python scripts\smoke_demo.py --scenario firm-b
python scripts\smoke_demo.py --scenario after-hours
```

Expected terminal output must clearly show:

```text
firm-a -> Demo Partner Firm A -> +12676804795 -> MOCK TRANSFER ONLY
firm-b -> Demo Partner Firm B -> +12676804795 -> MOCK TRANSFER ONLY
after-hours -> no live transfer -> extended intake/referral path
```

No actual call is allowed from `smoke_demo.py`.

## 30. Guava setup and supervised live-demo runbook

Official current public Guava docs show these Windows steps:

```powershell
irm https://goguava.ai/install.ps1 | iex
guava login
```

If there is no existing voice app:

```powershell
cd CaseLine\apps
guava create voice
```

**Do not run `guava create voice` over an existing non-empty voice app.** If the project already exists, inspect it and merge CaseLine logic into it.

Local Guava run, after the current template and environment are configured:

```powershell
cd CaseLine\apps\voice
guava run .
```

Current public deployment docs show:

```powershell
guava deploy up
guava deploy status
guava deploy logs -n 500
```

To stop the deployed agent:

```powershell
guava deploy down
```

### Supervised real-transfer checklist

Before setting live flags:

1. Confirm the two demo recipients have agreed to receive the test calls now.
2. Confirm the Guava account has a working inbound number assigned to the agent.
3. Confirm the CaseLine API is reachable from the deployed Guava agent over HTTPS.
4. Confirm `GUAVA_MODE=live` only in the intended staging/demo secret configuration.
5. Confirm `DEMO_MODE=true`.
6. Confirm `DEMO_LIVE_TRANSFER_ENABLED=true`.
7. Confirm allowlist is exactly `+12676804795`.
8. Confirm no real caller information is used; use fictional demo facts.
9. Call the CaseLine Guava number **484-968-7497 (`+14849687497`)** manually.
10. Run scenario A and verify the agent names `Demo Partner Firm A`, asks permission, then transfers to **267-680-4795**.
11. Reset the demo call and run scenario B; verify it names `Demo Partner Firm B`, asks permission, then transfers to **267-680-4795**.
12. Test caller refusal: the system must not dial.
13. Test simulated closed status: the system must not dial and must continue extended intake.
14. After the demo, set `DEMO_LIVE_TRANSFER_ENABLED=false` again.
15. Review Guava logs and CaseLine audit rows for both attempts without copying sensitive content into GitHub issues or PRs.

## 31. GitHub coding-agent execution protocol

A coding agent operating directly in GitHub should execute the task in this order:

1. Read this file and `README.md` fully.
2. Run `git status`, inspect the tree and identify existing package managers/frameworks.
3. Read the current Guava coding-agent starter and relevant SDK docs before changing Guava code.
4. Run the existing test suite before changes and save the baseline result in the PR description.
5. Implement Phase 1 first; do not begin live Guava calling until mock routing and tests pass.
6. Keep changes in small commits, for example:
   - `chore: add dev services and typed settings`
   - `feat: add case and referral persistence`
   - `feat: add deterministic firm matching`
   - `feat: add secure transfer authorization`
   - `test: cover demo firm routing and allowlist`
   - `feat: integrate guava voice transfer adapter`
   - `feat: add guava sms notification worker`
7. Run lint, migrations and tests before every PR update.
8. Do not commit `.env`, API keys, Guava credentials, real transcripts or real legal case data.
9. Do not add Twilio/Telnyx/Amazon Connect runtime packages or configuration.
10. Do not autonomously place live calls. Stop at the point where a human must enable the supervised live-demo flags.
11. Update `README.md` and `docs/demo-runbook.md` with exact commands that were actually tested.
12. In the PR description, list:
    - files changed;
    - architecture decisions;
    - exact test commands/results;
    - migrations added;
    - environment variables required;
    - `implemented`, `mocked`, `blocked`, `manual-demo-only` items;
    - any Guava SDK differences from this document.

## 32. Pull request Definition of Done

The GitHub implementation is not complete until all of the following are true:

- [ ] Repository installs from a clean checkout using documented Windows commands.
- [ ] `docker compose up -d` starts required local dependencies, or an existing repo-equivalent is documented.
- [ ] `alembic upgrade head` succeeds from an empty database.
- [ ] Demo seed is repeatable and creates both demo firms exactly once.
- [ ] API starts and both health endpoints pass.
- [ ] All tests pass locally and in GitHub Actions.
- [ ] Scenario A resolves to `+12676804795` only after consent/authorization.
- [ ] Scenario B resolves to `+12676804795` only after consent/authorization.
- [ ] No response exposes an unrestricted dial target before consent.
- [ ] Live transfer is disabled by default.
- [ ] CI can never make a real Guava call or send a Guava SMS.
- [ ] Guava is the only telephony/SMS runtime integration.
- [ ] No Twilio/Telnyx/Amazon Connect runtime dependency exists.
- [ ] The voice agent has safe backend-failure behavior and never invents a lawyer/firm/phone number.
- [ ] A transfer request is not treated as a successful connection without a verified signal or human confirmation.
- [ ] Duplicate events/requests do not create duplicate referrals, transfer attempts or notifications.
- [ ] Logging is redacted and does not contain full transcripts/API keys.
- [ ] `README.md`, `.env.example`, architecture docs and demo runbook match the implemented code.
- [ ] The coding agent stops before live calling and provides the manual supervised demo steps.

## 33. Source-of-truth Guava references for the coding agent

Read these before modifying the Guava portion because SDK examples can evolve:

- Complete docs corpus: `https://goguava.ai/docs/everything.md`
- Coding-agent starter: `https://goguava.ai/docs/coding-agent-starter.md`
- Quickstart / Windows CLI: `https://goguava.ai/docs/quickstart`
- Deployment: `https://goguava.ai/docs/deployment`
- Current examples demonstrating inbound `Agent`, `set_task`, `Field`, `on_task_complete`, `call.transfer(...)` and `guava.Client().send_sms(...)` are linked from the Guava docs corpus.

Public Guava documentation checked on **2026-09-26** shows Windows CLI installation with `irm https://goguava.ai/install.ps1 | iex`, browser authentication with `guava login`, project creation with `guava create`, local execution with `guava run`, native transfer through `call.transfer(...)`, SMS through `guava.Client().send_sms(...)`, and deployment through `guava deploy up/status/logs/down`. Public examples currently show more than one inbound listener form; use the installed SDK/generated template rather than forcing a stale example.

---

# Coding-agent start instruction

**Start implementation now, not another planning pass.** Inspect the existing GitHub repository and current Guava starter/docs, run the existing tests, then implement the mock-backed vertical slice through the two deterministic demo routes. Do not introduce Twilio or any other telecom provider. The first hard milestone is a green test suite proving:

```text
DEMO_AREA_A -> Demo Partner Firm A -> authorized destination +12676804795
DEMO_AREA_B -> Demo Partner Firm B -> authorized destination +12676804795
```

with **no real dialing in tests/CI** and with both phone numbers hidden until the backend has stored caller transfer consent and issued a short-lived authorization. Only after that milestone should the agent wire the live Guava `call.transfer(...)` path, leaving the real demo disabled by default for manual supervised activation.
