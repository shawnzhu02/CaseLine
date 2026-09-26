import Link from "next/link";
import { api, fmt } from "@/lib/api.ts";
import {
  openReport,
  overrideCase,
  reassign,
  recordConsent,
  recordOutcome,
  referralDecision,
  sendReferral,
} from "../../actions.ts";
import { Flash } from "../../flash.tsx";

type Detail = {
  case_id: string; status: string; jurisdiction: string | null; practice_area: string | null;
  practice_area_confidence: string | null; urgent: boolean; urgency_reason: string | null; routing_action: string | null;
  summary_version: number; created_at: string;
  caller: { name: string | null; callback_number: string | null; callback_verified: boolean; preferred_language: string | null; sms_opted_out: boolean } | null;
  facts: { key: string; value: unknown; provenance: string; confirmed: boolean }[];
  consents: { purpose: string; allowed: boolean; firm: string | null; policy_version: string; captured_at: string }[];
  calls: { provider_call_id: string; state: string; termination_reason: string | null; started_at: string; ended_at: string | null }[];
  referrals: { referral_id: string; firm_id: string; firm_name: string; status: string; rule_version: string; created_at: string; expires_at: string | null }[];
  transfer_attempts: { transfer_attempt_id: string; referral_id: string; state: string; dial_target_masked: string; provider_signal: string | null; result_source: string | null; attempted_at: string }[];
  notifications: { id: string; channel: string; template: string; status: string; status_reason: string | null; retry_count: number; created_at: string }[];
  history: { at: string; actor: string; operation: string; result: string }[];
};
type Firm = { firm_id: string; display_name: string; accepting_referrals: boolean };

const show = (v: unknown) => (v === null || v === undefined || v === "" ? "—" : typeof v === "object" ? JSON.stringify(v) : String(v));

export default async function CasePage({ params, searchParams }: {
  params: Promise<{ id: string }>; searchParams: Promise<{ ok?: string; err?: string }>;
}) {
  const { id } = await params;
  const { ok, err } = await searchParams;
  const [c, firms] = await Promise.all([api<Detail>(`/v1/admin/cases/${id}`), api<Firm[]>("/v1/admin/firms")]);
  const hidden = <input type="hidden" name="case_id" value={c.case_id} />;

  return (
    <>
      <p><Link href="/cases">← Cases</Link></p>
      <h1>
        Case <code>{c.case_id.slice(0, 8)}</code> <span className="pill">{c.status}</span>{" "}
        {c.urgent && <span className="pill urgent">urgent: {c.urgency_reason}</span>}
      </h1>
      <Flash ok={ok} err={err} />
      <p className="banner">Caller details are confidential. This view is audited under your name.</p>

      <div className="grid">
        <div className="panel">
          <h2>Caller</h2>
          {c.caller ? (
            <dl>
              <dt>Name</dt><dd>{show(c.caller.name)}</dd>
              <dt>Callback</dt><dd>{show(c.caller.callback_number)} {c.caller.callback_verified && <span className="muted">(read back)</span>}</dd>
              <dt>Language</dt><dd>{show(c.caller.preferred_language)}</dd>
              <dt>SMS opt-out</dt><dd>{c.caller.sms_opted_out ? "yes" : "no"}</dd>
            </dl>
          ) : <p className="muted">No caller details (intake consent refused).</p>}
        </div>
        <div className="panel">
          <h2>Routing</h2>
          <dl>
            <dt>Matter</dt><dd>{show(c.practice_area)} <span className="muted">({show(c.practice_area_confidence)})</span></dd>
            <dt>Jurisdiction</dt><dd>{show(c.jurisdiction)}</dd>
            <dt>Action</dt><dd>{show(c.routing_action)}</dd>
            <dt>Opened</dt><dd>{fmt(c.created_at)}</dd>
            <dt>Summary version</dt><dd>{c.summary_version}</dd>
          </dl>
        </div>
      </div>

      <div className="panel">
        <h2>Facts</h2>
        <table>
          <thead><tr><th>Fact</th><th>Value</th><th>Source</th></tr></thead>
          <tbody>
            {c.facts.map((f) => (
              <tr key={f.key}><td>{f.key.replaceAll("_", " ")}</td><td>{show(f.value)}</td>
                <td>{f.confirmed ? "confirmed" : f.provenance.replaceAll("_", " ")}</td></tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="panel">
        <h2>Referrals</h2>
        <table>
          <thead><tr><th>Firm</th><th>Status</th><th>Created</th><th>Expires</th><th>Actions</th></tr></thead>
          <tbody>
            {c.referrals.map((r) => (
              <tr key={r.referral_id}>
                <td>{r.firm_name} <span className="muted">({r.rule_version})</span></td>
                <td><span className="pill">{r.status}</span></td>
                <td>{fmt(r.created_at)}</td>
                <td>{fmt(r.expires_at)}</td>
                <td>
                  {["transfer_connected", "firm_notified"].includes(r.status) && (
                    <form action={referralDecision} className="inline">
                      {hidden}<input type="hidden" name="referral_id" value={r.referral_id} />
                      <select name="status" defaultValue="accepted">
                        <option value="accepted">Firm accepted</option>
                        <option value="declined">Firm declined</option>
                        <option value="expired">Expired</option>
                      </select>
                      <button type="submit">Record</button>
                    </form>
                  )}
                  {r.status === "created" && c.status === "extended_intake" && (
                    <form action={sendReferral} className="inline">
                      {hidden}<input type="hidden" name="referral_id" value={r.referral_id} />
                      <button type="submit">Send to firm</button>
                    </form>
                  )}
                  <form action={openReport} className="inline">
                    {hidden}<input type="hidden" name="referral_id" value={r.referral_id} />
                    <button type="submit" className="secondary">Open report</button>
                  </form>
                </td>
              </tr>
            ))}
            {c.referrals.length === 0 && <tr><td colSpan={5} className="muted">No referrals.</td></tr>}
          </tbody>
        </table>
      </div>

      <div className="panel">
        <h2>Transfer attempts</h2>
        <p className="muted">Guava does not report whether a transfer was answered. Record what actually happened.</p>
        <table>
          <thead><tr><th>When</th><th>To</th><th>State</th><th>Signal</th><th>Record outcome</th></tr></thead>
          <tbody>
            {c.transfer_attempts.map((a) => (
              <tr key={a.transfer_attempt_id}>
                <td>{fmt(a.attempted_at)}</td><td>{a.dial_target_masked}</td>
                <td><span className="pill">{a.state}</span> {a.result_source && <span className="muted">by {a.result_source}</span>}</td>
                <td>{show(a.provider_signal)}</td>
                <td>
                  {a.state === "requested" && (
                    <form action={recordOutcome} className="inline">
                      {hidden}<input type="hidden" name="attempt_id" value={a.transfer_attempt_id} />
                      <select name="result" defaultValue="connected">
                        <option value="connected">Connected</option>
                        <option value="no_answer">No answer</option>
                        <option value="busy">Busy</option>
                        <option value="failed">Failed</option>
                      </select>
                      <button type="submit">Save</button>
                    </form>
                  )}
                </td>
              </tr>
            ))}
            {c.transfer_attempts.length === 0 && <tr><td colSpan={5} className="muted">No transfer attempts.</td></tr>}
          </tbody>
        </table>
      </div>

      <div className="grid">
        <div className="panel">
          <h2>Change case status</h2>
          <form action={overrideCase} style={{ display: "grid", gap: 8 }}>
            {hidden}
            <select name="status" defaultValue="human_review">
              <option value="human_review">Hold for human review</option>
              <option value="triage_ready">Ready to route again</option>
              <option value="closed">Close</option>
            </select>
            <input name="note" placeholder="Reason (required)" required />
            <button type="submit">Apply</button>
          </form>
        </div>
        <div className="panel">
          <h2>Refer to another firm</h2>
          <form action={reassign} style={{ display: "grid", gap: 8 }}>
            {hidden}
            <select name="firm_id">
              {firms.filter((f) => f.accepting_referrals).map((f) => (
                <option key={f.firm_id} value={f.firm_id}>{f.display_name}</option>
              ))}
            </select>
            <input name="note" placeholder="Why (required)" required />
            <button type="submit">Create referral</button>
          </form>
          <p className="muted">Eligibility is re-checked by the API. Record share consent for the new firm before sending.</p>
        </div>
        <div className="panel">
          <h2>Record consent from a follow-up call</h2>
          <form action={recordConsent} style={{ display: "grid", gap: 8 }}>
            {hidden}
            <select name="purpose">
              <option value="share_with_selected_firm">Share summary with firm</option>
              <option value="sms">Text updates</option>
              <option value="email">Email updates</option>
            </select>
            <select name="firm_id" defaultValue="">
              <option value="">(firm — required for sharing)</option>
              {firms.map((f) => <option key={f.firm_id} value={f.firm_id}>{f.display_name}</option>)}
            </select>
            <select name="allowed"><option value="yes">Caller agreed</option><option value="no">Caller refused</option></select>
            <button type="submit">Record</button>
          </form>
        </div>
      </div>

      <div className="panel">
        <h2>Consents</h2>
        <table>
          <thead><tr><th>When</th><th>Purpose</th><th>Firm</th><th>Answer</th><th>Policy</th></tr></thead>
          <tbody>
            {c.consents.map((x, i) => (
              <tr key={i}><td>{fmt(x.captured_at)}</td><td>{x.purpose}</td><td>{show(x.firm)}</td>
                <td>{x.allowed ? "allowed" : "refused"}</td><td className="muted">{x.policy_version}</td></tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="panel">
        <h2>Messages</h2>
        <table>
          <thead><tr><th>When</th><th>Channel</th><th>Template</th><th>Status</th></tr></thead>
          <tbody>
            {c.notifications.map((n) => (
              <tr key={n.id}><td>{fmt(n.created_at)}</td><td>{n.channel}</td><td>{n.template}</td>
                <td><span className="pill">{n.status}</span> <span className="muted">{show(n.status_reason)}</span></td></tr>
            ))}
            {c.notifications.length === 0 && <tr><td colSpan={4} className="muted">No messages.</td></tr>}
          </tbody>
        </table>
      </div>

      <div className="panel">
        <h2>Calls and history</h2>
        <ul>
          {c.calls.map((call) => (
            <li key={call.provider_call_id}>Call {fmt(call.started_at)} — {call.state} {call.termination_reason && `(${call.termination_reason})`}</li>
          ))}
        </ul>
        <table>
          <thead><tr><th>When</th><th>Who</th><th>What</th><th>Result</th></tr></thead>
          <tbody>
            {c.history.map((h, i) => (
              <tr key={i}><td>{fmt(h.at)}</td><td>{h.actor}</td><td>{h.operation}</td><td>{h.result}</td></tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
