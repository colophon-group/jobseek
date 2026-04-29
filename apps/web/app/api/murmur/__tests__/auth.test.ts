/**
 * Tests for `_lib/auth.ts` — `requireBearer`.
 *
 * Verifies:
 *   - missing `Authorization` → 401 with stable error envelope
 *   - wrong token             → 401 (constant-time comparison)
 *   - misconfigured server    → 401 (fail closed)
 *   - correct token           → null (proceed)
 *
 * @see colophon-group/jobseek#2759
 */

import { describe, it, expect, beforeEach } from "vitest";
import { requireBearer } from "../_lib/auth";

beforeEach(() => {
  process.env.MURMUR_TOKEN = "test-token";
});

const make = (auth?: string): Request => {
  const headers = new Headers();
  if (auth) headers.set("authorization", auth);
  return new Request("https://test.local", { headers });
};

describe("requireBearer", () => {
  it("returns 401 when the Authorization header is missing", async () => {
    const res = requireBearer(make());
    expect(res).not.toBeNull();
    expect(res!.status).toBe(401);
    const body = await res!.json();
    expect(body).toEqual({ ok: false, errors: ["unauthorized"] });
  });

  it("returns 401 when the token is wrong", async () => {
    const res = requireBearer(make("Bearer wrong-token"));
    expect(res).not.toBeNull();
    expect(res!.status).toBe(401);
  });

  it("accepts the correct token (returns null)", () => {
    const res = requireBearer(make("Bearer test-token"));
    expect(res).toBeNull();
  });

  it("accepts case-insensitive 'bearer' prefix", () => {
    const res = requireBearer(make("bearer test-token"));
    expect(res).toBeNull();
  });

  it("rejects malformed Authorization headers (no Bearer prefix)", () => {
    const res = requireBearer(make("test-token"));
    expect(res).not.toBeNull();
    expect(res!.status).toBe(401);
  });

  it("rejects an empty bearer token", () => {
    const res = requireBearer(make("Bearer "));
    expect(res).not.toBeNull();
    expect(res!.status).toBe(401);
  });

  it("fails closed when MURMUR_TOKEN is unset", async () => {
    delete process.env.MURMUR_TOKEN;
    const res = requireBearer(make("Bearer anything"));
    expect(res).not.toBeNull();
    expect(res!.status).toBe(401);
  });

  it("rejects a token of different length without throwing", () => {
    // The constant-time helper short-circuits on length mismatch.
    const res = requireBearer(make("Bearer xx"));
    expect(res).not.toBeNull();
    expect(res!.status).toBe(401);
  });
});
