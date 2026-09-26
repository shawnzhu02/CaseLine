# Deploying CaseLine (staging / demo)

Target: one Render Blueprint (`render.yaml`) running five pieces:

| Render service | What | Image / runtime |
| --- | --- | --- |
| `caseline-api` | FastAPI backend, runs `alembic upgrade head` + seed before each deploy | `apps/api/Dockerfile` |
| `caseline-outbox` | Outbox worker: firm emails, caller texts, referral expiry, stale-transfer sweep | same image, `python -m caseline.workers.outbox` |
| `caseline-voice` | Guava voice agent answering **+1 484-968-7497** (outbound-only connection to Guava) | `apps/voice/Dockerfile` |
| `caseline-admin` | Operator dashboard (Next.js) | Node, `apps/admin` |
| `caseline-db` | Managed PostgreSQL | Render Postgres |

Render was chosen because one blueprint covers web services, workers and Postgres. Any Docker host works the same way.

## 0. Guava account check (once)

The number `+14849687497` lives in the **Boston Hackathon** Guava org (`6ab7f4cf7b00c3f140ce5686`), not the personal
workspace the CLI logs into by default.

```powershell
guava org list
guava org use 6ab7f4cf7b00c3f140ce5686
guava numbers list          # must show +1 (484) 968-7497
```

Use an API key **from that org** as `GUAVA_API_KEY`. The key pasted into chat on 2026-09-26 should be rotated once a
fresh one is stored in Render.

## 1. Generate secrets locally

```powershell
cd apps\api; .\.venv\Scripts\Activate.ps1
python -m caseline.cli generate-keys
# CASELINE_FIELD_ENCRYPTION_KEY=...   REPORT_LINK_SECRET=...   CASELINE_INTERNAL_API_TOKEN=...
```

Keep them in a password manager. **Losing `CASELINE_FIELD_ENCRYPTION_KEY` makes stored caller details unreadable.**

## 2. Create the blueprint

Render dashboard → **New → Blueprint** → select `shawnzhu02/CaseLine` → apply. Then fill the `sync: false` values:

| Where | Key | Value |
| --- | --- | --- |
| env group `caseline-shared` | `CASELINE_INTERNAL_API_TOKEN`, `CASELINE_FIELD_ENCRYPTION_KEY`, `REPORT_LINK_SECRET` | from step 1 |
| env group `caseline-shared` | `API_PUBLIC_BASE_URL` | `https://caseline-api.onrender.com` (your API URL) |
| `caseline-voice` | `GUAVA_API_KEY` | Boston Hackathon org key |
| `caseline-voice` | `CASELINE_API_BASE_URL` | the API URL |
| `caseline-voice` | `CASELINE_INTERNAL_API_TOKEN` | same as the env group |
| `caseline-admin` | `CASELINE_API_BASE_URL` | the API URL |

Redeploy. Check `https://<api>/health/ready` → `database: up`, `demo_live_transfer_enabled: false`.

## 3. Create operator accounts

Render → `caseline-api` → **Shell**:

```bash
python -m caseline.cli create-principal --name shawn --role admin
python -m caseline.cli create-principal --name ops-1 --role operator
python -m caseline.cli create-principal --name firm-a-intake --role firm_user --firm demo-firm-a
python -m caseline.cli deactivate-principal --name ops-1     # when someone leaves
```

Each command prints a token **once**. Operators sign in to `caseline-admin` with their own token; every action is
audited under that name.

## 4. Voice agent

Only **one** process may listen on `+14849687497`. When `caseline-voice` is running on Render, do not also run
`guava run apps\voice` locally; suspend the Render worker first if you want to test locally.

Alternative: Guava Hosting. `guava deploy up apps\voice` needs a `guava.toml` (create one in a temp folder with
`guava create tmp --direction inbound --phone +14849687497`, copy `guava.toml` into `apps\voice`; it is git-ignored)
and an `apps\voice\.env` (git-ignored) with `CASELINE_API_BASE_URL`, `CASELINE_INTERNAL_API_TOKEN` and
`GUAVA_AGENT_NUMBER`. Your role in that org is "developer"; an admin may need to allow deploys.

## 5. Supervised live-transfer demo

Follow `docs/demo-runbook.md` §3. On Render, set `DEMO_LIVE_TRANSFER_ENABLED=true` in `caseline-shared`, redeploy
the API, run the demo, then set it back to `false` and redeploy.

## Local dashboard development

```powershell
cd apps\admin
npm ci
$env:CASELINE_API_BASE_URL = "http://127.0.0.1:8000"
npm run dev        # http://localhost:3000 — sign in with a token from create-principal
```

## Limits to know

- The rate limiter is per API process (in memory). Keep `caseline-api` at one instance, or move limits to the edge.
- Real firm email needs `EMAIL_PROVIDER=resend`, `RESEND_API_KEY` and a verified `EMAIL_FROM` domain. Until then
  emails are mocked.
- Guava SMS stays off (`GUAVA_SMS_ENABLED=false`) until the questions in `docs/guava-open-questions.md` are answered.
