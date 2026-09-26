import { rawFetch } from "@/lib/api.ts";
import { getSession } from "@/lib/session.ts";

// Same-origin JSON endpoint the live board polls; the operator token stays server-side.
export async function GET() {
  const session = await getSession();
  if (!session) return Response.json({ error: "signed_out" }, { status: 401 });
  const res = await rawFetch(session.token, "/v1/live/current");
  const body = await res.text();
  return new Response(body, {
    status: res.status,
    headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
  });
}
