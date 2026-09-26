import "server-only";
import { requireSession } from "./session.ts";

// Server-side only: the operator's token never reaches the browser.
const BASE = (process.env.CASELINE_API_BASE_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");

export class ApiError extends Error {
  constructor(public status: number, public reason: string, message: string) {
    super(message);
  }
}

export async function rawFetch(token: string, path: string, init: RequestInit = {}): Promise<Response> {
  return fetch(`${BASE}${path}`, {
    ...init,
    cache: "no-store",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json", ...(init.headers ?? {}) },
  });
}

export async function api<T = unknown>(path: string, init: RequestInit = {}): Promise<T> {
  const session = await requireSession();
  const res = await rawFetch(session.token, path, init);
  if (!res.ok) {
    let reason = `http_${res.status}`;
    let message = res.statusText;
    try {
      const body = await res.json();
      reason = body?.detail?.reason ?? reason;
      message = body?.detail?.message ?? reason;
    } catch {}
    throw new ApiError(res.status, reason, message);
  }
  return res.json() as Promise<T>;
}

export const post = <T = unknown>(path: string, body: unknown) =>
  api<T>(path, { method: "POST", body: JSON.stringify(body) });
export const patch = <T = unknown>(path: string, body: unknown) =>
  api<T>(path, { method: "PATCH", body: JSON.stringify(body) });

export function fmt(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("en-US", { dateStyle: "medium", timeStyle: "short" });
}
