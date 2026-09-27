# CaseLine

Winner of the Guava AI Hackathon on 9/26/2026

**One number. One conversation. The right legal help.**

CaseLine is an AI telephone intake and law-firm referral service. A caller dials one Guava-managed number
(**+1 484-968-7497**), an AI intake assistant (not a lawyer) collects the essentials, CaseLine's backend — not the
language model — selects a participating firm with deterministic, auditable rules, and the caller is either
transferred live (after consenting) or referred asynchronously.

> **Telecom rule:** Guava is the only phone-number, call, transfer and SMS provider. Email (Resend) is separate.
> See `CASELINE_IMPLEMENTATION_PLAN.md` (spec, with 2026-09-26 revision notes) and `docs/decisions/0001-architecture.md`.

## Status

| Area | State |
| --- | --- |
| API: triage, deterministic matching, timezone-aware hours, consent, state machines, audit, idempotency | **implemented** |
| Secure transfer authorization (one-time token, allowlist, live flag off by default) | **implemented** |
| Two demo destinations (Firm A `+12676804795`, Firm B `+16173187562`) + fictional fixture firms | **implemented** (seeded) |
| Guava voice agent (`apps/voice`, guava-sdk 0.45.0) wired to the API | **implemented**, tested with mocks; not yet run on a live call |
| Extended intake → pending referral → outbox (firm email, caller SMS) | **implemented**; providers **mocked** |
| Versioned firm reports, signed expiring links, firm-isolated report API | **implemented** |
| Roles (admin / operator / firm_user / service), encrypted caller PII, audit, rate limits, request IDs | **implemented** |
| Operator dashboard (`apps/admin`, Next.js): review queue, case detail, transfer outcomes, firm toggles, failures | **implemented** |
| Referral expiry + stale-transfer sweeps | **implemented** (outbox worker) |
| Deployment (Render Blueprint: API, worker, voice agent, dashboard, Postgres) | **configured**, not yet deployed |
| Guava SMS | **blocked** until SMS capability + STOP handling of the sender number are verified (`GUAVA_SMS_ENABLED=false`) |
| Transfer connect/fail detection | **blocked by SDK** (no events); operator records the outcome in the dashboard |
| Real partner firms, launch state, legal review | **needs partner/legal decision**, see `docs/launch-decisions.md` |
| Live transfers to the demo numbers | **manual-demo-only**, see `docs/demo-runbook.md` |

## Layout

```text
apps/api/        FastAPI backend (caseline/), Alembic migrations, pytest suite, Dockerfile, CLI
apps/voice/      Guava voice agent: main.py, caseline_voice/ (flow, backend client, Guava adapter), Dockerfile
apps/admin/      Operator dashboard (Next.js 16, server-rendered; operators sign in with personal tokens)
scripts/         seed_demo.py, smoke_demo.py (mock end-to-end demo), reset_dev_db.ps1
docs/            architecture, API contract, deployment, demo runbook, Guava questions, launch decisions, ADRs
compose.yaml     local Postgres
render.yaml      Render Blueprint for staging/demo
```

## Local development (Windows PowerShell)

Prerequisites: Python 3.12+ (3.13 used), [uv](https://docs.astral.sh/uv/), Guava CLI (`guava --version` ≥ 0.45),
Docker Desktop (optional — only for Postgres; everything also runs on SQLite).

```powershell
git clone git@github.com:shawnzhu02/CaseLine.git
cd CaseLine
Copy-Item .env.example .env        # edit locally; never commit .env

# Database: Postgres via Docker ...
docker compose up -d
docker compose ps
# ... or no Docker: put DATABASE_URL=sqlite:///./caseline-dev.db in .env

# API
cd apps\api
uv venv -p 3.13 .venv
.\.venv\Scripts\Activate.ps1
uv pip install -e ".[dev]"
alembic upgrade head
python ..\..\scripts\seed_demo.py        # idempotent; prints 2 DEMO + 3 fixture firms
python -m uvicorn caseline.main:app --reload --host 127.0.0.1 --port 8000
```

In another window:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health/live
Invoke-RestMethod http://127.0.0.1:8000/health/ready
Start-Process http://127.0.0.1:8000/docs        # OpenAPI
```

Outbox worker (sends mock SMS/email for pending referrals):

```powershell
cd apps\api; .\.venv\Scripts\Activate.ps1
python -m caseline.workers.outbox            # loop; add --once for a single pass
```

Stop Postgres: `docker compose down`. Reset the local schema + reseed: `.\scripts\reset_dev_db.ps1`.

## Tests and mock demo

```powershell
cd apps\api;  .\.venv\Scripts\Activate.ps1;  ruff check . ..\..\scripts;  pytest -q      # 92 passed
cd ..\..
python scripts\smoke_demo.py --scenario all       # scripted call through the real flow + API, no real calls

cd apps\voice
uv venv -p 3.13 .venv; .\.venv\Scripts\Activate.ps1; uv pip install -e ".[dev]"
ruff check .; pytest -q                                                                # 27 passed
```

Unit tests use in-memory SQLite and mock gateways; CI (`.github/workflows/ci.yml`) also migrates and seeds a
Postgres 16 service. Nothing in tests, CI or `smoke_demo.py` can dial, text or email.

## Operator dashboard

```powershell
cd apps\api; .\.venv\Scripts\Activate.ps1
python -m caseline.cli create-principal --name you --role operator     # prints your token once
cd ..\admin
npm ci
$env:CASELINE_API_BASE_URL = "http://127.0.0.1:8000"
npm run dev                  # http://localhost:3000, sign in with the token
npm run typecheck; npm test  # 3 session-sealing tests
```

Queues (needs review / urgent / open), full case detail (audited), record transfer outcomes and firm decisions,
reassign with fresh consent, open the firm report, toggle firm availability, and retry failed messages.
Deployment: `docs/deployment.md`.

## Voice agent

```powershell
guava login                               # once
$env:CASELINE_API_BASE_URL = "http://127.0.0.1:8000"
$env:CASELINE_INTERNAL_API_TOKEN = "<same token as the API>"
guava run apps\voice -- chat              # text chat, no phone
guava run apps\voice                      # answers +14849687497
```

With `DEMO_LIVE_TRANSFER_ENABLED=false` (default) the agent never dials: it names the firm, and if the caller
agrees, the API refuses authorization and the agent continues extended intake. Supervised live-transfer steps for
both demo numbers are in `docs/demo-runbook.md`.

## Configuration

See `.env.example`. Key safety settings (validated at startup, see `apps/api/caseline/config.py`):

- `TELECOM_PROVIDER` must be `guava`; `GUAVA_MODE=mock` by default.
- `DEMO_LIVE_TRANSFER_ENABLED=true` is rejected unless `DEMO_MODE=true`; `APP_ENV=test` can never use live Guava.
- `DEMO_TRANSFER_ALLOWLIST` may contain only `+12676804795` and `+16173187562`; every number must be E.164.
- Demo flags are rejected in production; `CASELINE_INTERNAL_API_TOKEN` must be set outside development.

## Before real callers

Qualified legal review of scripts, notices, referral rules and jurisdiction; verified partner firms; Guava SMS/STOP
and recording-retention review; RBAC, encryption and pen-test items from Phase 4. See spec §13.
