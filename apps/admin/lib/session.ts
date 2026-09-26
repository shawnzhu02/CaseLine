import "server-only";
import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { seal, unseal, type Session } from "./seal.ts";

const COOKIE = "caseline_session";
const TTL_MS = 8 * 60 * 60 * 1000; // one working shift

function secret(): string {
  const value = process.env.DASHBOARD_SESSION_SECRET;
  if (value) return value;
  if (process.env.NODE_ENV === "production") throw new Error("DASHBOARD_SESSION_SECRET is required");
  return "caseline-dashboard-dev-only-secret";
}

export async function getSession(): Promise<Session | null> {
  const raw = (await cookies()).get(COOKIE)?.value;
  return raw ? unseal(raw, secret()) : null;
}

export async function requireSession(): Promise<Session> {
  const session = await getSession();
  if (!session) redirect("/login");
  return session;
}

export async function startSession(token: string, name: string, role: string): Promise<void> {
  const exp = Date.now() + TTL_MS;
  (await cookies()).set(COOKIE, seal({ token, name, role, exp }, secret()), {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "strict",
    path: "/",
    maxAge: TTL_MS / 1000,
  });
}

export async function endSession(): Promise<void> {
  (await cookies()).delete(COOKIE);
}
