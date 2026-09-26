"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";
import { ApiError, patch, post, rawFetch } from "@/lib/api.ts";
import { endSession, startSession } from "@/lib/session.ts";

function back(path: string, key: "ok" | "err", text: string): never {
  revalidatePath(path);
  redirect(`${path}?${key}=${encodeURIComponent(text)}`);
}

async function run(path: string, okText: string, fn: () => Promise<unknown>): Promise<never> {
  try {
    await fn();
  } catch (e) {
    if (e instanceof ApiError) back(path, "err", `${e.reason}: ${e.message}`);
    throw e;
  }
  back(path, "ok", okText);
}

const str = (f: FormData, k: string) => String(f.get(k) ?? "").trim();

export async function login(form: FormData) {
  const token = str(form, "token");
  if (!token) redirect("/login?err=Enter%20your%20token");
  const res = await rawFetch(token, "/v1/me");
  if (!res.ok) redirect("/login?err=Token%20not%20recognised");
  const me = (await res.json()) as { name: string; role: string };
  if (!["operator", "admin"].includes(me.role)) redirect("/login?err=Operator%20or%20admin%20role%20required");
  await startSession(token, me.name, me.role);
  redirect("/cases");
}

export async function logout() {
  await endSession();
  redirect("/login");
}

export async function recordOutcome(form: FormData) {
  const caseId = str(form, "case_id");
  await run(`/cases/${caseId}`, "Transfer outcome recorded", () =>
    post(`/v1/transfer-attempts/${str(form, "attempt_id")}/outcome`, { result: str(form, "result"), source: "operator" }));
}

export async function referralDecision(form: FormData) {
  const caseId = str(form, "case_id");
  await run(`/cases/${caseId}`, "Referral updated", () =>
    post(`/v1/referrals/${str(form, "referral_id")}/status`, { status: str(form, "status") }));
}

export async function overrideCase(form: FormData) {
  const caseId = str(form, "case_id");
  await run(`/cases/${caseId}`, "Case status changed", () =>
    post(`/v1/admin/cases/${caseId}/status`, { status: str(form, "status"), note: str(form, "note") }));
}

export async function recordConsent(form: FormData) {
  const caseId = str(form, "case_id");
  await run(`/cases/${caseId}`, "Consent recorded", () =>
    post(`/v1/admin/cases/${caseId}/consents`, {
      purpose: str(form, "purpose"),
      allowed: str(form, "allowed") === "yes",
      firm_id: str(form, "firm_id") || null,
    }));
}

export async function reassign(form: FormData) {
  const caseId = str(form, "case_id");
  await run(`/cases/${caseId}`, "New referral created (not sent yet)", () =>
    post(`/v1/admin/cases/${caseId}/reassign`, { firm_id: str(form, "firm_id"), note: str(form, "note") }));
}

export async function sendReferral(form: FormData) {
  const caseId = str(form, "case_id");
  await run(`/cases/${caseId}`, "Referral sent", () => post(`/v1/admin/referrals/${str(form, "referral_id")}/send`, {}));
}

export async function openReport(form: FormData) {
  const caseId = str(form, "case_id");
  let url = "";
  try {
    url = (await post<{ url: string }>(`/v1/referrals/${str(form, "referral_id")}/report-link`, {})).url;
  } catch (e) {
    if (e instanceof ApiError) back(`/cases/${caseId}`, "err", `${e.reason}: ${e.message}`);
    throw e;
  }
  redirect(url);
}

export async function updateFirm(form: FormData) {
  const field = str(form, "field");
  await run("/firms", "Firm updated", () =>
    patch(`/v1/admin/firms/${str(form, "firm_id")}`, { [field]: str(form, "value") === "true" }));
}

export async function retryNotification(form: FormData) {
  await run("/failures", "Message re-queued", () => post(`/v1/admin/notifications/${str(form, "id")}/retry`, {}));
}
