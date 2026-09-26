import { redirect } from "next/navigation";
import { getSession } from "@/lib/session.ts";

// Operators land on their queue; everyone else (judges, visitors) lands on the public demo.
export default async function Home() {
  redirect((await getSession()) ? "/cases" : "/demo");
}
