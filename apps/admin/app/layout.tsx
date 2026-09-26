import type { Metadata } from "next";
import Link from "next/link";
import { logout } from "./actions.ts";
import { getSession } from "@/lib/session.ts";
import "./globals.css";

export const metadata: Metadata = { title: "CaseLine Operations", robots: { index: false, follow: false } };

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const session = await getSession();
  return (
    <html lang="en">
      <body>
        {session && (
          <header className="top">
            <strong>CaseLine Ops</strong>
            <nav>
              <Link href="/live">Live</Link>
              <Link href="/cases">Cases</Link>
              <Link href="/firms">Firms</Link>
              <Link href="/failures">Needs attention</Link>
            </nav>
            <span className="muted">
              {session.name} · {session.role}
            </span>
            <form action={logout}>
              <button className="secondary" type="submit">Sign out</button>
            </form>
          </header>
        )}
        <main>{children}</main>
      </body>
    </html>
  );
}
