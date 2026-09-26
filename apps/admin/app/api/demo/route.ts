import { rawFetch } from "@/lib/api.ts";

// Public reads for the judge page, via a demo-role token that the API limits to:
//   ?call=judge-xxxx -> one simulated call started from this page
//   ?live=1          -> the latest real call's non-identifying assessment (only if enabled server-side)
export async function GET(req: Request) {
  const params = new URL(req.url).searchParams;
  const token = process.env.CASELINE_DEMO_TOKEN;
  if (!token) return Response.json({ error: "demo_not_configured" }, { status: 503 });
  let path: string;
  if (params.get("live") === "1") {
    path = "/v1/live/public-latest";
  } else {
    const call = params.get("call") ?? "";
    if (!/^judge-[a-z0-9]{6,20}$/.test(call)) return Response.json({ status: "Waiting for caller...", version: 0 });
    path = `/v1/live/sim/${call}`;
  }
  const res = await rawFetch(token, path);
  return new Response(await res.text(), {
    status: res.status,
    headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
  });
}
