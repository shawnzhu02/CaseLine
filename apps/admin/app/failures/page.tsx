import Link from "next/link";
import { api, fmt } from "@/lib/api.ts";
import { retryNotification } from "../actions.ts";
import { Flash } from "../flash.tsx";

type Failures = {
  notifications: { id: string; case_id: string; channel: string; status: string; reason: string | null; retry_count: number }[];
  unresolved_transfers: { transfer_attempt_id: string; referral_id: string; attempted_at: string }[];
  unacknowledged_referrals: { referral_id: string; case_id: string; firm_id: string; notified_at: string }[];
  stale_firm_availability: { firm_id: string; updated_at: string }[];
  calls_without_case: { provider_call_id: string; termination_reason: string | null }[];
};

export default async function FailuresPage({ searchParams }: { searchParams: Promise<{ ok?: string; err?: string }> }) {
  const { ok, err } = await searchParams;
  const f = await api<Failures>("/v1/admin/failures");
  return (
    <>
      <h1>Needs attention</h1>
      <Flash ok={ok} err={err} />
      <div className="panel">
        <h2>Messages not sent ({f.notifications.length})</h2>
        <table>
          <thead><tr><th>Case</th><th>Channel</th><th>Status</th><th>Reason</th><th></th></tr></thead>
          <tbody>
            {f.notifications.map((n) => (
              <tr key={n.id}>
                <td><Link href={`/cases/${n.case_id}`}>{n.case_id.slice(0, 8)}</Link></td>
                <td>{n.channel}</td><td><span className="pill">{n.status}</span></td>
                <td>{n.reason ?? "—"} <span className="muted">({n.retry_count} tries)</span></td>
                <td>
                  {n.status === "failed" && (
                    <form action={retryNotification}><input type="hidden" name="id" value={n.id} /><button type="submit">Retry</button></form>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="muted">"Blocked" messages were stopped by policy (no consent, SMS disabled, opted out). Follow up by phone.</p>
      </div>
      <div className="panel">
        <h2>Transfers with no recorded outcome ({f.unresolved_transfers.length})</h2>
        <ul>{f.unresolved_transfers.map((t) => <li key={t.transfer_attempt_id}>Attempt {t.transfer_attempt_id.slice(0, 8)} at {fmt(t.attempted_at)}</li>)}</ul>
      </div>
      <div className="panel">
        <h2>Referrals the firm hasn't answered ({f.unacknowledged_referrals.length})</h2>
        <ul>
          {f.unacknowledged_referrals.map((r) => (
            <li key={r.referral_id}><Link href={`/cases/${r.case_id}`}>{r.firm_id}</Link> — notified {fmt(r.notified_at)}</li>
          ))}
        </ul>
      </div>
      <div className="panel">
        <h2>Firm availability not updated recently ({f.stale_firm_availability.length})</h2>
        <ul>{f.stale_firm_availability.map((s) => <li key={s.firm_id}>{s.firm_id} — last updated {fmt(s.updated_at)}</li>)}</ul>
      </div>
      <div className="panel">
        <h2>Calls that ended before a case was opened ({f.calls_without_case.length})</h2>
        <ul>{f.calls_without_case.map((c) => <li key={c.provider_call_id}><code>{c.provider_call_id}</code> {c.termination_reason}</li>)}</ul>
      </div>
    </>
  );
}
