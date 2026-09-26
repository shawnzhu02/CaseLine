# Launch decisions and pilot plan

Code can't settle these. Each one blocks real callers (spec §13). Owner: founders, plus a licensed attorney in the
launch state.

## 1. Launch market (decide first)

- [ ] **One state** to launch in. It picks the referral rules, the emergency number wording, and call-recording
      consent (one-party vs all-party).
- [ ] **One or two practice areas** (for example landlord–tenant, or property insurance disputes). Replace the
      fictional `DEMO_AREA_*` / `property_insurance` / `housing` codes in `apps/api/caseline/services/matching.py`
      and the follow-up questions for each area.
- [ ] Languages to support at launch (the backend records `preferred_language`; the agent is English-only today).

## 2. Legal review (licensed attorney in the launch state)

- [ ] **Lawyer-referral-service rules.** Many states regulate lawyer referral services (registration, bar
      approval, advertising). ABA Model Rule 7.2(b) limits paying for recommendations; fee-sharing with non-lawyers
      is generally prohibited (Model Rule 5.4). The revenue model must fit these rules.
- [ ] **Revenue model** chosen to fit the above: flat subscription per firm, per-lead fee, consumer fee, or
      nonprofit/grant-funded.
- [ ] **Scripts approved**: AI disclosure, "not a lawyer / no legal advice", emergency guidance, the consent
      questions (`apps/voice/caseline_voice/prompts.py`, `apps/api/caseline/services/intake.py`).
- [ ] **Recording consent** wording for the launch state; decide whether calls are recorded at all.
- [ ] **Privacy notice + retention schedule** (how long cases, facts, consents and reports are kept).
- [ ] **Terms for participating firms**: conflicts check stays with the firm, response SLA, data handling,
      disclosure of any commercial relationship to callers.
- [ ] **SMS program terms** (if texting): opt-in wording, STOP/HELP, carrier registration.

## 3. Partner firms (2–5 for the pilot)

Collect for each firm (these map to `firms` / `firm_hours` rows):

| Field | Example | Notes |
| --- | --- | --- |
| Legal name + bar/registration ID | | verification before `verification_status=verified` |
| Practice areas + exclusions | landlord–tenant (tenant side only) | |
| Counties / cities served | | |
| Languages | English, Spanish | |
| Office hours + holidays (time zone) | Mon–Fri 9–5 America/New_York | drives live-transfer eligibility |
| Intake phone for live transfers | | goes on the transfer allowlist (production allowlist is a code change: today only demo numbers are dialable) |
| Referral email (intake inbox) | | receives alert + secure report link |
| Capacity (open referrals at once) | 10 | |
| Fee arrangements they offer | free consult, contingency | shown to callers only as approved wording |
| Signed participation agreement | | |

## 4. Before the pilot (engineering)

- [ ] Production transfer allowlist for verified firms (replace the demo-only gate in
      `services/transfer_authorization.py`), reviewed by a second person.
- [ ] Guava answers in `docs/guava-open-questions.md`; SMS enabled only if STOP handling is confirmed.
- [ ] Resend domain verified; `EMAIL_PROVIDER=resend`.
- [ ] Separate production environment (new secrets, `APP_ENV=production`, demo flags off; config refuses otherwise).
- [ ] Backups and restore test on the production database; key escrow for `CASELINE_FIELD_ENCRYPTION_KEY`.
- [ ] Penetration review: webhook spoofing, cross-firm report access, prompt injection via caller speech,
      transfer-number injection (spec §13).
- [ ] Someone on call for the human-review queue during pilot hours.

## 5. Supervised pilot

- Duration 2–4 weeks, 2–5 firms, business hours only at first, every call reviewed in the dashboard.
- **Metrics** (weekly): completed intakes; transfer **connection** rate (operator-recorded, not attempts); time to
  first human response; firm acceptance rate; abandoned calls; wrong-category/route rate; message
  deliverability; consent capture completeness; unauthorized disclosures (must be zero).
- **Stop criteria:** any unauthorized disclosure, any transfer to a non-allowlisted number, or a firm complaint
  about misleading referrals → pause and review.
