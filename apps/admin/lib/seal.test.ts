import assert from "node:assert/strict";
import { test } from "node:test";
import { seal, unseal } from "./seal.ts";

const s = { token: "tok", name: "alice", role: "operator", exp: Date.now() + 60_000 };

test("round trip", () => {
  assert.deepEqual(unseal(seal(s, "secret"), "secret"), s);
});

test("wrong secret or tampering is rejected", () => {
  const sealed = seal(s, "secret");
  assert.equal(unseal(sealed, "other"), null);
  assert.equal(unseal(sealed.slice(0, -2) + "AA", "secret"), null);
  assert.equal(unseal("garbage", "secret"), null);
});

test("expired sessions are rejected", () => {
  assert.equal(unseal(seal({ ...s, exp: Date.now() - 1 }, "secret"), "secret"), null);
});
