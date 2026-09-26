"use client";

import { useEffect, useRef, useState } from "react";
import { simulateLine } from "../actions.ts";
import { playDemoLine } from "../demo-actions.ts";
import { SCRIPTS } from "@/lib/script.ts";

type Change = { step: number; field: string; from: string | null; to: string | null };
type Outcome =
  | { type: "transfer"; firm: string; state: string; simulated: boolean; note?: string }
  | { type: "email"; firm: string; status: string; simulated: boolean; subject: string; body: string };
type Snapshot = {
  call_id?: string;
  simulated?: boolean;
  version: number;
  status: string;
  jurisdiction?: string | null;
  category?: string | null;
  matter?: string | null;
  urgency?: string | null;
  key_factors?: string[];
  match?: { display_name: string | null; is_demo?: boolean; route: string } | null;
  action?: string | null;
  history?: Change[];
  outcome?: Outcome | null;
  transcript?: { speaker: "caller" | "agent"; text: string }[];
};

const ROWS: [label: string, key: "jurisdiction" | "category" | "matter" | "urgency"][] = [
  ["Jurisdiction", "jurisdiction"],
  ["Category", "category"],
  ["Matter", "matter"],
  ["Urgency", "urgency"],
];
const WAITING: Snapshot = { version: 0, status: "Waiting for caller..." };

export function LiveBoard({ mode = "operator" }: { mode?: "operator" | "public" }) {
  const isPublic = mode === "public";
  // Public page: "script" = scripted simulation started here; "live" = the latest real call on the demo line.
  const [source, setSource] = useState<"script" | "live">(isPublic ? "script" : "live");
  const [snap, setSnap] = useState<Snapshot>(WAITING);
  const [flash, setFlash] = useState<Set<string>>(new Set());
  const [lines, setLines] = useState<[string, string][]>([]);
  const [running, setRunning] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const failures = useRef(0);
  const transcriptEnd = useRef<HTMLDivElement | null>(null);
  const prev = useRef<Snapshot | null>(null);
  const callRef = useRef<string | null>(null);

  useEffect(() => {
    let alive = true;
    prev.current = null;
    setSnap(WAITING);
    const tick = async () => {
      let url: string;
      if (!isPublic) url = "/api/live";
      else if (source === "live") url = "/api/demo?live=1";
      else if (callRef.current) url = `/api/demo?call=${callRef.current}`;
      else return;
      try {
        const res = await fetch(url, { cache: "no-store" });
        if (res.status === 401 && !isPublic) window.location.href = "/login";
        if (!alive) return;
        if (!res.ok) {
          failures.current += 1;
          if (failures.current >= 3) setProblem("Connection problem, retrying…");
          return;
        }
        failures.current = 0;
        setProblem(null);
        const next: Snapshot = await res.json();
        const before = prev.current;
        if (before && next.call_id === before.call_id) {
          const changed = new Set<string>();
          for (const [, key] of ROWS) if (next[key] !== before[key]) changed.add(key);
          if ((next.match?.display_name ?? null) !== (before.match?.display_name ?? null)) changed.add("match");
          if (next.action !== before.action) changed.add("action");
          if (next.status !== before.status) changed.add("status");
          if (changed.size) {
            setFlash(changed);
            setTimeout(() => alive && setFlash(new Set()), 2500);
          }
        }
        prev.current = next;
        setSnap(next);
      } catch {
        failures.current += 1;
        if (failures.current >= 3 && alive) setProblem("Connection problem, retrying…");
      }
    };
    tick();
    const id = setInterval(tick, 1000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, [isPublic, source]);

  async function play(variant: "connect" | "refer") {
    setSource("script");
    setRunning(true);
    setLines([]);
    const callId = isPublic
      ? `judge-${Math.random().toString(36).slice(2, 10).padEnd(8, "0")}`
      : `rehearsal-${Date.now().toString(36)}`;
    callRef.current = callId;
    prev.current = null;
    setSnap(WAITING);
    try {
      for (const [i, line] of SCRIPTS[variant].entries()) {
        setLines((l) => [...l, [line.speaker, line.text]]);
        let ok = true;
        try {
          if (isPublic) ok = (await playDemoLine(callId, variant, i)).ok;
          else await simulateLine(callId, line.speaker, line.text, line.outcome ?? null);
        } catch {
          ok = false;
        }
        if (!ok) {
          setProblem("The demo service is busy. Please press play again.");
          break;
        }
        await new Promise((r) => setTimeout(r, line.speaker === "caller" ? 3200 : 1800));
      }
    } finally {
      setRunning(false);
    }
  }

  const lastChange = [...(snap.history ?? [])].reverse().find((c) => c.field === "Category" || c.field === "Matter");
  const factors = snap.key_factors ?? [];
  const cls = (key: string) => (flash.has(key) ? "live-value flash-on" : "live-value");
  const connecting = snap.status.startsWith("CONNECTING");
  const out = snap.outcome;
  // Live transcript from the server when available; otherwise the lines of the scripted demo being played.
  const convo: [string, string][] = snap.transcript?.length
    ? snap.transcript.map((t) => [t.speaker, t.text])
    : lines;
  useEffect(() => {
    transcriptEnd.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [convo.length]);

  return (
    <div className="live">
      <style>{LIVE_CSS}</style>
      <div className="live-head">
        <div>
          <div className="live-brand">CASELINE — LIVE ASSESSMENT</div>
          <div className="live-sub">
            {(isPublic ? source === "script" : snap.simulated)
              ? "Simulated call: scripted demo, invented caller"
              : snap.call_id ? `Live call …${snap.call_id}` : "Listening for a live call on the demo line"}
          </div>
        </div>
        <div className={`live-status ${connecting ? "connecting" : ""} ${flash.has("status") ? "flash-on" : ""}`}>
          {snap.status}
        </div>
      </div>

      {problem && <div className="live-problem" role="status">{problem}</div>}
      <div className="live-controls">
        <button type="button" onClick={() => play("connect")} disabled={running}>
          {running ? "Call in progress…" : "▶ Play demo call (firm open: live transfer)"}
        </button>
        <button type="button" className="ghost" onClick={() => play("refer")} disabled={running}>
          ▶ Play demo call (firm closed: referral email)
        </button>
        {isPublic && (
          <button type="button" className={source === "live" ? "" : "ghost"} disabled={running}
                  onClick={() => { callRef.current = null; setLines([]); setSource("live"); }}>
            ● Watch the live phone/browser call
          </button>
        )}
      </div>

      <div className="live-grid">
        <section className="live-card">
          {ROWS.map(([label, key]) => (
            <div className="live-row" key={key}>
              <div className="live-label">{label}</div>
              <div className={cls(key)}>{snap[key] ?? "—"}</div>
            </div>
          ))}
          <div className="live-row">
            <div className="live-label">Key factors</div>
            <div className="live-value">
              {factors.length ? <ul>{factors.map((f) => <li key={f}>{f}</li>)}</ul> : "—"}
            </div>
          </div>
          <div className="live-row">
            <div className="live-label">Match</div>
            <div className={cls("match")}>
              {snap.match?.display_name ?? (snap.match?.route === "none" ? "No participating firm" : "—")}
              {snap.match?.is_demo && <span className="live-tag">demo participant</span>}
            </div>
          </div>
          <div className="live-row">
            <div className="live-label">Action</div>
            <div className={cls("action")}>{snap.action ?? "—"}</div>
          </div>
        </section>

        <section className="live-side">
          {lastChange && (
            <div className="live-box">
              <div className="live-label">Assessment changed</div>
              <div className="live-change-text">
                {lastChange.from ?? "—"} <span aria-hidden>→</span> {lastChange.to ?? "—"}
              </div>
            </div>
          )}
          <div className="live-box">
            <div className="live-label">Live transcript</div>
            <div className="live-transcript">
              {convo.length === 0 && <p className="live-muted">Waiting for the conversation to start…</p>}
              {convo.map(([s, t], i) => (
                <p key={i} className={s === "caller" ? "t-caller" : "t-agent"}>
                  <b>{s === "caller" ? "Caller" : "CaseLine"}:</b> {t}
                </p>
              ))}
              <div ref={transcriptEnd} />
            </div>
          </div>
          <div className="live-box">
            <div className="live-label">What CaseLine was thinking</div>
            <ol className="live-trail">
              {(snap.history ?? []).filter((c) => c.to).map((c, i) => (
                <li key={i}><b>{c.field}:</b> {c.from ? `${c.from} → ` : ""}{c.to}</li>
              ))}
            </ol>
          </div>
        </section>
      </div>

      {out && (
        <section className={`live-output ${out.type}`}>
          <div className="live-label">Output</div>
          {out.type === "transfer" ? (
            <div className="live-call">
              <div className="live-ring" aria-hidden>☎</div>
              <div>
                <div className="live-call-title">Calling {out.firm}…</div>
                <div className="live-muted">
                  {out.simulated ? "Simulated: no phone call is placed from this page." : out.note}
                </div>
              </div>
            </div>
          ) : (
            <div className="live-email">
              <div className="live-email-meta">
                <div><span>To</span> {out.firm} intake {out.simulated ? "(simulated)" : ""}</div>
                <div><span>Subject</span> {out.subject}</div>
                <div><span>Status</span> {out.status}{out.simulated ? " (not sent)" : ""}</div>
              </div>
              <pre>{out.body}</pre>
              <div className="live-muted">The email carries a case reference and a secure, expiring link, never the caller&apos;s story.</div>
            </div>
          )}
        </section>
      )}

      <div className="live-foot">
        Internal assessment for routing only. The caller never hears a legal classification, and CaseLine
        never gives legal advice.
      </div>
    </div>
  );
}

const LIVE_CSS = `
.live{--ink:#f2f1ec;--mute:#9d9b92;--card:#1c1c1a;--line:#34332f;--acc:#7fd1b2;--hot:#f4c86a;
  background:#121211;color:var(--ink);margin:-20px -16px -60px;padding:28px clamp(16px,4vw,48px) 40px;min-height:calc(100vh - 50px)}
.live-head{display:flex;justify-content:space-between;align-items:flex-end;gap:16px;flex-wrap:wrap;margin-bottom:16px}
.live-brand{font-size:clamp(20px,3vw,30px);font-weight:700;letter-spacing:.06em}
.live-sub,.live-muted{color:var(--mute)}
.live-sub{margin-top:4px}
.live-status{font-size:clamp(18px,2.4vw,26px);font-weight:700;padding:10px 18px;border:2px solid var(--line);border-radius:10px}
.live-status.connecting{border-color:var(--acc);color:var(--acc);animation:pulse 1.2s ease-in-out infinite}
@keyframes pulse{50%{opacity:.45}}
.live-controls{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:18px}
.live-controls button{background:var(--acc);color:#0e1a15;font-weight:600}
.live-controls button.ghost{background:transparent;color:var(--ink);border:1px solid var(--line)}
.live-controls button:disabled{opacity:.55;cursor:default}
.live-grid{display:grid;grid-template-columns:minmax(0,3fr) minmax(0,2fr);gap:20px}
@media (max-width:860px){.live-grid{grid-template-columns:1fr}}
.live-card,.live-box,.live-output{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:18px 22px}
.live-row{display:grid;grid-template-columns:150px 1fr;gap:14px;padding:12px 0;border-bottom:1px solid var(--line)}
@media (max-width:520px){.live-row{grid-template-columns:1fr;gap:4px}}
.live-row:last-child{border-bottom:0}
.live-label{color:var(--mute);font-size:13px;text-transform:uppercase;letter-spacing:.08em}
.live-value{font-size:clamp(17px,2vw,22px);font-weight:600;transition:background .4s,color .4s;border-radius:6px;padding:0 6px;margin:0 -6px}
.live-value ul{margin:0;padding-left:20px;font-weight:500}
.flash-on{background:rgba(244,200,106,.18);color:var(--hot)}
.live-tag{font-size:12px;font-weight:500;margin-left:10px;padding:2px 8px;border-radius:999px;border:1px solid var(--line);color:var(--mute)}
.live-side{display:grid;gap:16px;align-content:start}
.live-box p{margin:6px 0}
.live-change-text{font-size:clamp(18px,2.2vw,24px);font-weight:700;color:var(--acc);margin-top:6px}
.live-trail{margin:8px 0 0;padding-left:20px}
.live-output{margin-top:20px;border-color:var(--acc)}
.live-call{display:flex;gap:18px;align-items:center;margin-top:10px}
.live-ring{font-size:42px;animation:pulse 1s ease-in-out infinite}
.live-call-title{font-size:clamp(22px,3vw,32px);font-weight:700;color:var(--acc)}
.live-email-meta{display:grid;gap:4px;margin:10px 0}
.live-email-meta span{display:inline-block;width:70px;color:var(--mute)}
.live-email pre{white-space:pre-wrap;font:inherit;background:#121211;border:1px solid var(--line);border-radius:8px;padding:14px;margin:0 0 8px}
.live-problem{background:rgba(244,200,106,.12);color:var(--hot);border:1px solid rgba(244,200,106,.4);border-radius:8px;padding:8px 12px;margin-bottom:12px}
.live-transcript{max-height:320px;overflow-y:auto;margin-top:6px;padding-right:6px}
.live-transcript .t-agent b{color:var(--acc)}.live-transcript .t-caller b{color:var(--hot)}
.live-foot{margin-top:20px;color:var(--mute);font-size:13px}
`;
