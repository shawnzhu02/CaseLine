export function Flash({ ok, err }: { ok?: string; err?: string }) {
  if (err) return <div className="flash err" role="alert">{err}</div>;
  if (ok) return <div className="flash ok" role="status">{ok}</div>;
  return null;
}
