# Questions for Guava (ready to send)

Send to your Guava contact or support. Answers change what CaseLine can switch on.

---

**Subject:** CaseLine — questions about transfers, SMS and data retention (org 6ab7f4cf…, number +1 484-968-7497)

Hi Guava team,

We're building CaseLine, an inbound legal-intake agent on guava-sdk 0.45.0, answering +1 (484) 968-7497 in our
"Boston Hackathon" org. A few questions before we go beyond supervised demos:

1. **Transfer outcomes.** `Call.transfer()` sends a soft transfer and returns nothing. Is there any way to learn
   whether the destination answered, was busy, or didn't pick up? For example an event, a webhook, a field on
   `BotSessionEnded`, or the conversations API. Today we only see `termination_reason="bot-transfer"`.
2. **Transfer failure behaviour.** If the destination doesn't answer, does the caller return to the agent, hear an
   error, or get disconnected? Is there a ring timeout we can set?
3. **SMS on our number.** Can +1 484-968-7497 send SMS through `Client.send_sms()`? Is it registered for A2P 10DLC,
   or do we need to register a brand/campaign?
4. **STOP / HELP handling.** Does Guava process STOP/UNSUBSCRIBE/HELP replies automatically, and can we query or be
   notified of opt-outs? Is there a delivery-status or message-ID API for `send_sms`?
5. **Recording and transcripts.** Are calls recorded and/or transcribed by default? How long are audio and
   transcripts retained, where are they stored (region), and can we set retention or delete per call? Can recording
   be turned off per call if a caller declines?
6. **Subprocessors / DPA.** Can you share your subprocessor list and a data processing agreement? We handle
   sensitive legal-intake information.
7. **Deployment.** For `guava deploy`, how should we provide secrets (API base URL, bearer token) to the deployed
   agent? Does a number need to be bound to a project (`guava create --phone`) for `listen_phone` to receive calls
   in production?
8. **Concurrency.** How many simultaneous inbound calls can one agent process handle, and does
   `guava deploy scale` route calls across replicas for the same number?

Thanks!

---

## Where each answer lands in the code

| Question | If "yes" | File |
| --- | --- | --- |
| 1 | Post a `provider_verified` outcome from `on_session_end` / the event | `apps/voice/caseline_voice/flow.py`, `apps/api/caseline/routers/calls.py` |
| 3–4 | Set `GUAVA_SMS_ENABLED=true`, `GUAVA_SMS_FROM_NUMBER`; wire STOP into `callers.sms_opted_out` | `apps/api/caseline/workers/outbox.py` |
| 5 | Record-consent gating per call; retention job | `apps/voice/caseline_voice/flow.py` |
| 7 | Replace Render worker with `guava deploy` if preferred | `docs/deployment.md` |
