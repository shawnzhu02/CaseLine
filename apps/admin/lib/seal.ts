// AES-256-GCM sealing for the session cookie. Pure Node crypto; no framework imports (unit-testable).
import { createCipheriv, createDecipheriv, createHash, randomBytes } from "node:crypto";

export type Session = { token: string; name: string; role: string; exp: number };

function key(secret: string): Buffer {
  return createHash("sha256").update(secret).digest();
}

export function seal(session: Session, secret: string): string {
  const iv = randomBytes(12);
  const cipher = createCipheriv("aes-256-gcm", key(secret), iv);
  const body = Buffer.concat([cipher.update(JSON.stringify(session), "utf8"), cipher.final()]);
  return [iv, cipher.getAuthTag(), body].map((b) => b.toString("base64url")).join(".");
}

export function unseal(value: string, secret: string, nowMs: number = Date.now()): Session | null {
  try {
    const [iv, tag, body] = value.split(".").map((p) => Buffer.from(p, "base64url"));
    const decipher = createDecipheriv("aes-256-gcm", key(secret), iv);
    decipher.setAuthTag(tag);
    const session = JSON.parse(Buffer.concat([decipher.update(body), decipher.final()]).toString("utf8"));
    return typeof session.exp === "number" && session.exp > nowMs ? (session as Session) : null;
  } catch {
    return null;
  }
}
