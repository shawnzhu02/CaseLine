import { login } from "../actions.ts";
import { Flash } from "../flash.tsx";

export default async function LoginPage({ searchParams }: { searchParams: Promise<{ err?: string }> }) {
  const { err } = await searchParams;
  return (
    <div className="panel" style={{ maxWidth: 440, margin: "60px auto" }}>
      <h1>CaseLine Operations</h1>
      <Flash err={err} />
      <p className="muted">
        Sign in with your personal operator token. An admin creates it with{" "}
        <code>python -m caseline.cli create-principal --name you --role operator</code>.
      </p>
      <form action={login} style={{ display: "grid", gap: 10 }}>
        <input name="token" type="password" autoComplete="off" placeholder="Operator token" required />
        <button type="submit">Sign in</button>
      </form>
    </div>
  );
}
