/**
 * Tests for the public /health endpoint.
 *
 * Pinned by colophon-group/jobseek#2774 (H2). The Docker image's
 * `HEALTHCHECK`, the compose stack's healthcheck, and the cloudflared
 * origin-up probe all hit this route without an Authorization header,
 * so the handler MUST be reachable without `MURMUR_TOKEN` configured
 * and MUST NOT call `requireBearer`. These tests pin both behaviours.
 */

import { describe, expect, it, beforeEach, afterEach } from "vitest";
import { GET } from "./route";

describe("GET /health", () => {
  const originalToken = process.env.MURMUR_TOKEN;

  beforeEach(() => {
    delete process.env.MURMUR_TOKEN;
  });

  afterEach(() => {
    if (originalToken === undefined) {
      delete process.env.MURMUR_TOKEN;
    } else {
      process.env.MURMUR_TOKEN = originalToken;
    }
  });

  it("returns 200 and { ok: true } with no Authorization header", async () => {
    const res = GET();
    expect(res.status).toBe(200);
    const body = (await res.json()) as { ok: boolean };
    expect(body).toEqual({ ok: true });
  });

  it("returns 200 even when MURMUR_TOKEN is unset (probes pre-date secret)", async () => {
    // Sanity: the bearered routes return 401 in this state. The health
    // route must not — otherwise the docker healthcheck flaps the
    // container before MURMUR_TOKEN is wired up by compose.
    expect(process.env.MURMUR_TOKEN).toBeUndefined();
    const res = GET();
    expect(res.status).toBe(200);
  });

  it("returns 200 even when MURMUR_TOKEN is set (probe never sends bearer)", async () => {
    process.env.MURMUR_TOKEN = "any-non-empty-value";
    const res = GET();
    expect(res.status).toBe(200);
  });
});
