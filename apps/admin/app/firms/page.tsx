import { api, fmt } from "@/lib/api.ts";
import { updateFirm } from "../actions.ts";
import { Flash } from "../flash.tsx";

type Firm = {
  firm_id: string; display_name: string; is_demo: boolean; is_fixture: boolean; verification_status: string;
  jurisdictions: string[]; practice_areas: string[]; timezone: string; accepting_referrals: boolean;
  accepting_live_calls: boolean; max_open_referrals: number; open_now: boolean; availability_reason: string;
  availability_updated_at: string; has_transfer_number: boolean;
};

function Toggle({ firm, field, value, label }: { firm: string; field: string; value: boolean; label: string }) {
  return (
    <form action={updateFirm} className="inline">
      <input type="hidden" name="firm_id" value={firm} />
      <input type="hidden" name="field" value={field} />
      <input type="hidden" name="value" value={String(!value)} />
      <span>{value ? "On" : "Off"}</span>
      <button type="submit" className="secondary" aria-label={`${value ? "Turn off" : "Turn on"} ${label}`}>
        {value ? "Turn off" : "Turn on"}
      </button>
    </form>
  );
}

export default async function FirmsPage({ searchParams }: { searchParams: Promise<{ ok?: string; err?: string }> }) {
  const { ok, err } = await searchParams;
  const firms = await api<Firm[]>("/v1/admin/firms");
  return (
    <>
      <h1>Participating firms</h1>
      <Flash ok={ok} err={err} />
      <div className="panel">
        <table>
          <thead>
            <tr><th>Firm</th><th>Coverage</th><th>Open now</th><th>Accepting referrals</th><th>Live transfers</th><th>Updated</th></tr>
          </thead>
          <tbody>
            {firms.map((f) => (
              <tr key={f.firm_id}>
                <td>
                  {f.display_name}{" "}
                  {f.is_demo && <span className="pill">demo participant</span>}
                  {f.is_fixture && <span className="pill">fictional fixture</span>}
                  <div className="muted">{f.verification_status}{!f.has_transfer_number && " · no transfer line"}</div>
                </td>
                <td>{f.jurisdictions.join(", ")}<div className="muted">{f.practice_areas.join(", ")}</div></td>
                <td>{f.open_now ? "Yes" : "No"}<div className="muted">{f.availability_reason} · {f.timezone}</div></td>
                <td><Toggle firm={f.firm_id} field="accepting_referrals" value={f.accepting_referrals} label="referrals" /></td>
                <td><Toggle firm={f.firm_id} field="accepting_live_calls" value={f.accepting_live_calls} label="live transfers" /></td>
                <td className="muted">{fmt(f.availability_updated_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="muted">
        Transfer numbers are never shown here. Live transfers also require the server-side demo flag and allowlist.
      </p>
    </>
  );
}
