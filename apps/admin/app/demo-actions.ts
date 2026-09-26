"use server";

import { rawFetch } from "@/lib/api.ts";
import { SCRIPTS } from "@/lib/script.ts";

// Public (no login): only plays fixed script lines into a simulated call, using a demo-role token that the API
// restricts to simulation. Visitors cannot send arbitrary text.
function demoToken(): string {
  const token = process.env.CASELINE_DEMO_TOKEN;
  if (!token) throw new Error("CASELINE_DEMO_TOKEN is not configured");
  return token;
}

const CALL_ID = /^judge-[a-z0-9]{6,20}$/;

export async function playDemoLine(callId: string, variant: "connect" | "refer", index: number) {
  const script = SCRIPTS[variant];
  if (!CALL_ID.test(callId) || !script || !Number.isInteger(index) || index < 0 || index >= script.length) {
    return { ok: false };
  }
  const line = script[index];
  const res = await rawFetch(demoToken(), "/v1/live/simulate", {
    method: "POST",
    body: JSON.stringify({ call_id: callId, speaker: line.speaker, text: line.text, outcome: line.outcome ?? null }),
  });
  return { ok: res.ok };
}
