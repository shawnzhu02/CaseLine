import Link from "next/link";
import { api, fmt } from "@/lib/api.ts";

type Row = {
  case_id: string; status: string; jurisdiction: string | null; practice_area: string | null; urgent: boolean;
  caller_initials: string | null; callback_masked: string | null; latest_referral_status: string | null;
  created_at: string;
};
type Page = { items: Row[]; total: number; limit: number; offset: number };

const QUEUES = [
  ["review", "Needs review"],
  ["urgent", "Urgent"],
  ["open", "All open"],
  ["", "Everything"],
] as const;

export default async function CasesPage({ searchParams }: { searchParams: Promise<{ queue?: string; offset?: string }> }) {
  const { queue = "review", offset = "0" } = await searchParams;
  const qs = new URLSearchParams({ limit: "50", offset });
  if (queue) qs.set("queue", queue);
  const data = await api<Page>(`/v1/admin/cases?${qs}`);
  const next = Number(offset) + data.limit;
  return (
    <>
      <h1>Cases</h1>
      <div className="tabs">
        {QUEUES.map(([key, label]) => (
          <Link key={key} href={`/cases?queue=${key}`} className={queue === key ? "active" : ""}>{label}</Link>
        ))}
      </div>
      <div className="panel">
        <table>
          <thead>
            <tr><th>Opened</th><th>Caller</th><th>Matter</th><th>Jurisdiction</th><th>Status</th><th>Referral</th></tr>
          </thead>
          <tbody>
            {data.items.map((c) => (
              <tr key={c.case_id}>
                <td><Link href={`/cases/${c.case_id}`}>{fmt(c.created_at)}</Link></td>
                <td>{c.caller_initials ?? "—"} <span className="muted">{c.callback_masked ?? ""}</span></td>
                <td>{c.practice_area ?? <span className="muted">unclassified</span>}</td>
                <td>{c.jurisdiction ?? "—"}</td>
                <td>
                  <span className="pill">{c.status}</span> {c.urgent && <span className="pill urgent">urgent</span>}
                </td>
                <td>{c.latest_referral_status ?? "—"}</td>
              </tr>
            ))}
            {data.items.length === 0 && <tr><td colSpan={6} className="muted">Nothing here.</td></tr>}
          </tbody>
        </table>
        <p className="muted">
          {data.total} case(s).{" "}
          {next < data.total && <Link href={`/cases?queue=${queue}&offset=${next}`}>Next page</Link>}
        </p>
      </div>
    </>
  );
}
