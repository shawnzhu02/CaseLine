# Judge demo (live site)

**Open:** https://caseline-demo.vercel.app → redirects to the public `/demo` page (no login).

| Piece | Where | Notes |
| --- | --- | --- |
| Judge page + operator dashboard | https://caseline-demo.vercel.app (`/demo`, `/live`, `/cases`) | Vercel project `caseline-demo` (`apps/admin`) |
| API | https://caseline-api.vercel.app | Vercel project `caseline-api` (`apps/api`), Neon Postgres `caseline-db` |
| Voice agent | this laptop: `.\scripts\start_voice.ps1` | Guava org "Boston Hackathon"; answers **+1 484-968-7497** and a browser WebRTC link |

## Three ways to run it

1. **Scripted (always works, no phone):** on `/demo` press **Play demo call (firm open: live transfer)** or
   **(firm closed: referral email)**. The board shows the assessment changing (Property / Insurance → Personal Injury
   → Potential Premises Liability), CaseLine's reasoning trail, the match, and the output: *Calling Demo Partner Firm A…*
   or the drafted referral email. Nothing is dialed or sent.
2. **Real call from the browser:** on `/demo` click **talk to it in your browser** (Guava WebRTC), then back on
   `/demo` press **Watch the live phone/browser call**. Say the fire story (invented details). The board follows the
   real call.
3. **Real phone call:** call **+1 484-968-7497** and press **Watch the live phone/browser call**.

The public page shows only the assessment of the latest real call from the last 30 minutes. It never shows names,
numbers or anything the caller said. The full case, with contact details and audit, is on the operator dashboard
(`/live`, `/cases`), which requires sign-in.

## Outputs on a real call

- **Firm open + caller agrees:** the API authorizes a transfer only when `DEMO_LIVE_TRANSFER_ENABLED=true`. The agent
  then transfers through Guava to Demo Partner Firm A (**267-680-4795**), and the board shows **CONNECTING…**.
  **This is OFF until both demo lawyers confirm they will take calls.** Enable it as below.
- **Otherwise** (flag off, caller declines, or firm closed): the agent asks a few follow-ups plus share/text
  permission, then CaseLine creates the referral. The board shows the **drafted email** to the firm. Email sending is
  mocked, so nothing leaves the system.

## Turning live transfer on / off (after both recipients agree)

```powershell
cd apps\api
"true"  | vercel env add DEMO_LIVE_TRANSFER_ENABLED production --force   # on
vercel deploy --prod --yes
# after the demo:
"false" | vercel env add DEMO_LIVE_TRANSFER_ENABLED production --force
vercel deploy --prod --yes
```

`DEMO_SIMULATE_FIRM_AVAILABILITY=true` is set on the API, so demo firms count as open at any hour. It is labelled
"simulated availability" and is demo-only.

## Keep the agent running

- Start: `.\scripts\start_voice.ps1` (reads the git-ignored `apps\voice\.env`: Guava key, WebRTC code, API token).
  Leave that window open; closing it or sleeping the laptop takes the phone number offline.
- Only one copy may listen on the number. Stop the local one before any `guava deploy`.
- Operator sign-in token for the dashboard: `apps\api\.env.tokens` (`OPERATOR_TOKEN`, git-ignored).

## Secrets created for this deployment (all git-ignored, local only)

- `apps/api/.env.secrets`: internal API token, field-encryption key, report-link secret, dashboard session secret.
- `apps/api/.env.tokens`: operator (admin) token, public demo-role token, WebRTC code/URL.
- `apps/voice/.env`: Guava API key and agent settings. **Rotate the Guava key after the event**, since it was
  shared in chat.
