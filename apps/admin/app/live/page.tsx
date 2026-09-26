import { requireSession } from "@/lib/session.ts";
import { LiveBoard } from "./board.tsx";

export const metadata = { title: "CaseLine — Live Assessment" };

export default async function LivePage() {
  await requireSession();
  return <LiveBoard />;
}
