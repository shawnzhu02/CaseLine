# CaseLine demo runbook

Two demo destinations (spec §4A). These are real people role-playing lawyers; the labels are placeholders and
nothing about them (firm name, credentials, specialty, hours) is real.

| Demo firm | Transfer number | Routed by (fictional fixture) |
| --- | --- | --- |
| Demo Partner Firm A (`demo-firm-a`) | 267-680-4795 (`+12676804795`) | practice area "Demo matter A" (`DEMO_AREA_A`) in "CaseLine demo region" |
| Demo Partner Firm B (`demo-firm-b`) | 617-318-7562 (`+16173187562`) | practice area "Demo matter B" (`DEMO_AREA_B`) in "CaseLine demo region" |

CaseLine inbound number (Guava-managed): **484-968-7497 (`+14849687497`)**. It is never a transfer or SMS target.

Demo hours (fictional): every day 08:00–20:00 America/New_York. Outside those hours the demo takes the
after-hours path unless `DEMO_SIMULATE_FIRM_AVAILABILITY=true` (labelled "SIMULATED AVAILABILITY").

## 1. Mock demo (safe anywhere, no calls)

```powershell
cd apps\api
.\.venv\Scripts\Activate.ps1
cd ..\..
python scripts\smoke_demo.py --scenario firm-a
python scripts\smoke_demo.py --scenario firm-b
python scripts\smoke_demo.py --scenario after-hours
python scripts\smoke_demo.py --scenario all      # adds "declined" and "live-disabled"
```

Expected (verified 2026-09-26):

```text
firm-a -> Demo Partner Firm A -> +12676804795 -> MOCK TRANSFER ONLY (attempt recorded as 'requested', not connected)
firm-b -> Demo Partner Firm B -> +16173187562 -> MOCK TRANSFER ONLY (attempt recorded as 'requested', not connected)
after-hours -> Demo Partner Firm A -> no live transfer -> extended intake/referral path (mock email x1, mock Guava SMS x1)
declined -> Demo Partner Firm B -> no live transfer -> extended intake/referral path (mock email x1, mock Guava SMS x1)
live-disabled -> Demo Partner Firm A -> no live transfer -> extended intake/referral path (mock email x1, mock Guava SMS x1)
```

## 2. Phone call with live transfer DISABLED (safe rehearsal)

The agent answers `+14849687497`, does intake, names the firm, and — because the API refuses to authorize — never
dials; it switches to extended intake instead.

PowerShell window 1 (API):

```powershell
cd apps\api; .\.venv\Scripts\Activate.ps1
$env:DEMO_MODE = "true"; $env:DEMO_LIVE_TRANSFER_ENABLED = "false"
$env:CASELINE_INTERNAL_API_TOKEN = "<long random value>"
alembic upgrade head; python ..\..\scripts\seed_demo.py
python -m uvicorn caseline.main:app --host 127.0.0.1 --port 8000
```

PowerShell window 2 (voice agent, runs locally and connects to Guava's cloud):

```powershell
guava login        # once
$env:CASELINE_API_BASE_URL = "http://127.0.0.1:8000"
$env:CASELINE_INTERNAL_API_TOKEN = "<same value>"
guava run apps\voice             # phone mode on +14849687497
# or text-only rehearsal, no phone at all:
guava run apps\voice -- chat
```

## 3. Supervised real-transfer demo (manual only — never automated)

Before setting any live flag:

1. Confirm **both** demo recipients (267-680-4795 and 617-318-7562) have agreed to receive test calls now.
2. Confirm `+14849687497` is the Guava number attached to this agent (`guava numbers list`).
3. Confirm the API is reachable from the agent (local `guava run` → `http://127.0.0.1:8000`; a `guava deploy`ed agent needs a public HTTPS API URL).
4. Confirm only fictional caller details will be used.
5. Set, in the API window only:
   ```powershell
   $env:DEMO_MODE = "true"
   $env:DEMO_LIVE_TRANSFER_ENABLED = "true"
   $env:DEMO_TRANSFER_ALLOWLIST = "+12676804795,+16173187562"
   ```
   Restart uvicorn and check `Invoke-RestMethod http://127.0.0.1:8000/health/ready` shows `demo_live_transfer_enabled: True`.
   (Outside 08:00–20:00 New York time also set `$env:DEMO_SIMULATE_FIRM_AVAILABILITY = "true"`.)
6. Start the agent: `guava run apps\voice`.

Scenario A (Firm A):

7. Call **484-968-7497** from a test phone. Agree to intake. Invent a caller (e.g. "Alex Demo"). Say the matter is
   **"demo matter A"** in the **"CaseLine demo region"**, nobody is in danger, no deadlines, give a callback number.
8. Verify the agent says "I can connect you to **Demo Partner Firm A**, a demonstration participant…" and asks permission.
9. Say yes. Verify the call transfers to **267-680-4795**.
10. After the call, record what happened (replace the id from `GET /v1/admin/cases` / the DB):
    ```powershell
    $h = @{ Authorization = "Bearer <token>" }
    Invoke-RestMethod -Method Post -Headers $h -ContentType application/json `
      -Uri http://127.0.0.1:8000/v1/transfer-attempts/<attempt_id>/outcome `
      -Body '{"result":"connected","source":"operator"}'      # or "no_answer" / "busy" / "failed"
    ```

Scenario B (Firm B):

11. Call **484-968-7497** again. Same steps but say **"demo matter B"**.
12. Verify the agent names **Demo Partner Firm B**, asks permission, then transfers to **617-318-7562**. Record the outcome.

Negative checks:

13. Call again, choose demo matter A, and **decline** the transfer: the agent must not dial and must move to extended intake.
14. Set `$env:DEMO_SIMULATE_FIRM_AVAILABILITY = "false"` and run outside demo hours (or set `accepting_live_calls=false`
    on the firm): the agent must not dial and must continue extended intake.
15. If Firm A does not answer, confirm nothing dialed Firm B automatically.

Afterwards:

16. Set `$env:DEMO_LIVE_TRANSFER_ENABLED = "false"` and restart the API (or just close the window).
17. Review Guava logs (`guava conversations`) and CaseLine `audit_events`/`transfer_attempts`. Do not paste call
    content into GitHub issues or PRs.

## Guava deployment (optional)

`guava deploy up apps\voice` needs a `guava.toml` project binding (account-specific, git-ignored). Create one with
`guava create <tmp-dir> --direction inbound --phone +14849687497` and copy its `guava.toml` into `apps\voice`, then
set `CASELINE_API_BASE_URL` / `CASELINE_INTERNAL_API_TOKEN` as deployment secrets. A deployed agent can only reach a
publicly reachable HTTPS API. Stop with `guava deploy down apps\voice`.
