import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

/**
 * Issue colophon-group/jobseek#3223 — `passwordResetLimiter` was defined in
 * `@/lib/rate-limit` (3 requests / 300 s, prefix `rl:pw-reset`) but never
 * called in production. Better-Auth's password-reset flow runs through the
 * `/api/auth/[...all]` catch-all, which only consulted the broader
 * `authLimiter` (10/60s). An attacker could issue ten reset emails per
 * minute per IP via Resend.
 *
 * These specs lock in the wiring:
 *   1. Requests to `/api/auth/request-password-reset` (POST), `/api/auth/
 *      reset-password` (POST), and `/api/auth/reset-password/:token` (GET)
 *      consult `passwordResetLimiter` BEFORE `authLimiter`.
 *   2. The 4th reset-path request in a 300 s window returns 429 with a
 *      `Retry-After` header.
 *   3. Non-reset auth paths (sign-in, sign-up, OAuth callbacks) are NOT
 *      gated by `passwordResetLimiter` — `authLimiter` alone still applies.
 *   4. Redis outages degrade OPEN (we never fail-closed on auth), matching
 *      the pre-existing `authLimiter` failure mode.
 *   5. The rate-limit key is the platform-authoritative IP from
 *      `getClientIp`, which is hardened against `x-forwarded-for`
 *      spoofing (#3219).
 */

vi.mock("server-only", () => ({}));

// Capture each call to either limiter so tests can assert call order and
// per-limiter call counts.
const limiterCalls = vi.hoisted(() => ({
  authLimit: vi.fn(),
  passwordResetLimit: vi.fn(),
  getClientIp: vi.fn(),
}));

vi.mock("@/lib/rate-limit", () => ({
  authLimiter: { limit: limiterCalls.authLimit },
  passwordResetLimiter: { limit: limiterCalls.passwordResetLimit },
  getClientIp: limiterCalls.getClientIp,
}));

const betterAuthHandlers = vi.hoisted(() => ({
  GET: vi.fn(),
  POST: vi.fn(),
}));

vi.mock("better-auth/next-js", () => ({
  toNextJsHandler: () => ({
    GET: betterAuthHandlers.GET,
    POST: betterAuthHandlers.POST,
  }),
}));

// `@/lib/auth` re-exports a fully-configured Better-Auth instance which
// pulls in drizzle, the username plugin, etc. We only care that the
// route's pre-handler logic runs before delegating, so stub it out.
vi.mock("@/lib/auth", () => ({ auth: { handler: vi.fn() } }));

function makeRequest(
  method: "GET" | "POST",
  pathname: string,
  ip = "203.0.113.7",
): NextRequest {
  const url = new URL(pathname, "https://example.com").toString();
  return new NextRequest(url, {
    method,
    headers: { "x-forwarded-for": ip },
  });
}

const RESET_OK = { success: true, limit: 3, remaining: 2, reset: 0 };
const AUTH_OK = { success: true, limit: 10, remaining: 9, reset: 0 };

describe("auth catch-all route — passwordResetLimiter wiring (#3223)", () => {
  beforeEach(() => {
    limiterCalls.authLimit.mockReset();
    limiterCalls.passwordResetLimit.mockReset();
    limiterCalls.getClientIp.mockReset();
    betterAuthHandlers.GET.mockReset();
    betterAuthHandlers.POST.mockReset();

    limiterCalls.getClientIp.mockReturnValue("203.0.113.7");
    limiterCalls.authLimit.mockResolvedValue(AUTH_OK);
    limiterCalls.passwordResetLimit.mockResolvedValue(RESET_OK);
    betterAuthHandlers.GET.mockResolvedValue(new Response("ok"));
    betterAuthHandlers.POST.mockResolvedValue(new Response("ok"));
  });

  afterEach(() => {
    vi.resetModules();
  });

  // --- A: reset paths consult passwordResetLimiter ----------------------

  it("POST /api/auth/request-password-reset consults passwordResetLimiter before delegating", async () => {
    const { POST } = await import("../route");

    const res = await POST(makeRequest("POST", "/api/auth/request-password-reset"));

    expect(res.status).toBe(200);
    expect(limiterCalls.passwordResetLimit).toHaveBeenCalledTimes(1);
    expect(limiterCalls.passwordResetLimit.mock.calls[0][0]).toBe("203.0.113.7");
    expect(betterAuthHandlers.POST).toHaveBeenCalledTimes(1);
  });

  it("POST /api/auth/reset-password consults passwordResetLimiter before delegating", async () => {
    const { POST } = await import("../route");

    const res = await POST(makeRequest("POST", "/api/auth/reset-password"));

    expect(res.status).toBe(200);
    expect(limiterCalls.passwordResetLimit).toHaveBeenCalledTimes(1);
    expect(betterAuthHandlers.POST).toHaveBeenCalledTimes(1);
  });

  it("GET /api/auth/reset-password/:token consults passwordResetLimiter (token-validity oracle)", async () => {
    const { GET } = await import("../route");

    const res = await GET(
      makeRequest("GET", "/api/auth/reset-password/some-token-abcdef"),
    );

    expect(res.status).toBe(200);
    expect(limiterCalls.passwordResetLimit).toHaveBeenCalledTimes(1);
    expect(betterAuthHandlers.GET).toHaveBeenCalledTimes(1);
  });

  // --- B: 4th request in window is rejected with 429 --------------------

  it("blocks the 4th reset request within the 300 s window with 429 + Retry-After", async () => {
    // First three calls succeed (3/300s — the limiter contract), the
    // fourth returns success:false.
    limiterCalls.passwordResetLimit
      .mockResolvedValueOnce(RESET_OK)
      .mockResolvedValueOnce(RESET_OK)
      .mockResolvedValueOnce(RESET_OK)
      .mockResolvedValueOnce({
        success: false,
        limit: 3,
        remaining: 0,
        reset: Date.now() + 250_000,
      });

    const { POST } = await import("../route");

    for (let i = 0; i < 3; i++) {
      const res = await POST(
        makeRequest("POST", "/api/auth/request-password-reset"),
      );
      expect(res.status).toBe(200);
    }

    const blocked = await POST(
      makeRequest("POST", "/api/auth/request-password-reset"),
    );

    expect(blocked.status).toBe(429);
    expect(blocked.headers.get("Retry-After")).toBeTruthy();
    // Better-Auth handler is NOT invoked when the limiter blocks.
    expect(betterAuthHandlers.POST).toHaveBeenCalledTimes(3);
  });

  // --- C: non-reset paths skip passwordResetLimiter ---------------------

  it("POST /api/auth/sign-in/email does NOT consult passwordResetLimiter", async () => {
    const { POST } = await import("../route");

    const res = await POST(makeRequest("POST", "/api/auth/sign-in/email"));

    expect(res.status).toBe(200);
    expect(limiterCalls.passwordResetLimit).not.toHaveBeenCalled();
    expect(limiterCalls.authLimit).toHaveBeenCalledTimes(1);
    expect(betterAuthHandlers.POST).toHaveBeenCalledTimes(1);
  });

  it("POST /api/auth/sign-up/email does NOT consult passwordResetLimiter", async () => {
    const { POST } = await import("../route");

    const res = await POST(makeRequest("POST", "/api/auth/sign-up/email"));

    expect(res.status).toBe(200);
    expect(limiterCalls.passwordResetLimit).not.toHaveBeenCalled();
    expect(limiterCalls.authLimit).toHaveBeenCalledTimes(1);
  });

  it("GET /api/auth/callback/github (OAuth callback) does NOT consult passwordResetLimiter", async () => {
    const { GET } = await import("../route");

    const res = await GET(makeRequest("GET", "/api/auth/callback/github"));

    expect(res.status).toBe(200);
    expect(limiterCalls.passwordResetLimit).not.toHaveBeenCalled();
    expect(limiterCalls.authLimit).toHaveBeenCalledTimes(1);
  });

  // --- D: defence-in-depth — authLimiter also applies on reset paths ----

  it("reset paths still consult authLimiter (defence-in-depth on the 10/60s axis)", async () => {
    const { POST } = await import("../route");

    await POST(makeRequest("POST", "/api/auth/request-password-reset"));

    expect(limiterCalls.passwordResetLimit).toHaveBeenCalledTimes(1);
    expect(limiterCalls.authLimit).toHaveBeenCalledTimes(1);
  });

  it("authLimiter still gates reset paths if passwordResetLimiter allows but authLimiter blocks", async () => {
    limiterCalls.passwordResetLimit.mockResolvedValue(RESET_OK);
    limiterCalls.authLimit.mockResolvedValue({
      success: false,
      limit: 10,
      remaining: 0,
      reset: Date.now() + 30_000,
    });

    const { POST } = await import("../route");

    const res = await POST(makeRequest("POST", "/api/auth/request-password-reset"));

    expect(res.status).toBe(429);
    expect(betterAuthHandlers.POST).not.toHaveBeenCalled();
  });

  // --- E: Redis outage degrades open ------------------------------------

  it("passwordResetLimiter throwing (Redis outage) lets the request through", async () => {
    limiterCalls.passwordResetLimit.mockRejectedValue(new Error("ECONNREFUSED"));

    const { POST } = await import("../route");

    const res = await POST(makeRequest("POST", "/api/auth/request-password-reset"));

    expect(res.status).toBe(200);
    expect(limiterCalls.authLimit).toHaveBeenCalledTimes(1);
    expect(betterAuthHandlers.POST).toHaveBeenCalledTimes(1);
  });

  it("authLimiter throwing (Redis outage) lets the request through", async () => {
    limiterCalls.authLimit.mockRejectedValue(new Error("ECONNREFUSED"));

    const { POST } = await import("../route");

    const res = await POST(makeRequest("POST", "/api/auth/sign-in/email"));

    expect(res.status).toBe(200);
    expect(betterAuthHandlers.POST).toHaveBeenCalledTimes(1);
  });

  // --- E2: Redis bypass is logged so the outage is queryable (#3175) ----

  it("logs `[auth-rate-limit] redis bypass` when authLimiter throws (so a Redis outage is visible in Loki, not silent)", async () => {
    limiterCalls.authLimit.mockRejectedValue(new Error("ECONNREFUSED"));
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});

    const { POST } = await import("../route");
    const res = await POST(makeRequest("POST", "/api/auth/sign-in/email"));

    // Bypass behaviour preserved.
    expect(res.status).toBe(200);
    expect(betterAuthHandlers.POST).toHaveBeenCalledTimes(1);
    // Stable event prefix so a sustained outage is queryable.
    expect(warnSpy).toHaveBeenCalledTimes(1);
    const [message, errArg] = warnSpy.mock.calls[0];
    expect(message).toBe("[auth-rate-limit] redis bypass");
    expect(errArg).toBeInstanceOf(Error);
    expect((errArg as Error).message).toBe("ECONNREFUSED");
    warnSpy.mockRestore();
  });

  it("logs `[auth-rate-limit] pw-reset redis bypass` when passwordResetLimiter throws on a reset path (email-bombing vector observability)", async () => {
    limiterCalls.passwordResetLimit.mockRejectedValue(new Error("ECONNREFUSED"));
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});

    const { POST } = await import("../route");
    const res = await POST(
      makeRequest("POST", "/api/auth/request-password-reset"),
    );

    // Bypass behaviour preserved (request still reaches the handler).
    expect(res.status).toBe(200);
    expect(betterAuthHandlers.POST).toHaveBeenCalledTimes(1);
    // Two-axis defence-in-depth means authLimiter still ran with no
    // throw, so only the pw-reset bypass log fires.
    expect(warnSpy).toHaveBeenCalledTimes(1);
    const [message, errArg] = warnSpy.mock.calls[0];
    expect(message).toBe("[auth-rate-limit] pw-reset redis bypass");
    expect(errArg).toBeInstanceOf(Error);
    expect((errArg as Error).message).toBe("ECONNREFUSED");
    warnSpy.mockRestore();
  });

  it("does NOT log when neither limiter throws (no false bypass signal)", async () => {
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});

    const { POST } = await import("../route");
    await POST(makeRequest("POST", "/api/auth/sign-in/email"));

    expect(warnSpy).not.toHaveBeenCalled();
    warnSpy.mockRestore();
  });

  // --- F: rate-limit key is the platform-authoritative IP ---------------

  it("uses getClientIp output as the rate-limit identifier (#3219 hardening)", async () => {
    limiterCalls.getClientIp.mockReturnValue("198.51.100.42");

    const { POST } = await import("../route");

    await POST(makeRequest("POST", "/api/auth/request-password-reset"));

    expect(limiterCalls.passwordResetLimit.mock.calls[0][0]).toBe("198.51.100.42");
    expect(limiterCalls.authLimit.mock.calls[0][0]).toBe("198.51.100.42");
  });

  // --- G: passwordResetLimiter is consulted BEFORE authLimiter ---------

  it("calls passwordResetLimiter before authLimiter on reset paths (so the tighter axis wins on coincident exhaustion)", async () => {
    const order: string[] = [];
    limiterCalls.passwordResetLimit.mockImplementation(async () => {
      order.push("pw-reset");
      return RESET_OK;
    });
    limiterCalls.authLimit.mockImplementation(async () => {
      order.push("auth");
      return AUTH_OK;
    });

    const { POST } = await import("../route");

    await POST(makeRequest("POST", "/api/auth/request-password-reset"));

    expect(order).toEqual(["pw-reset", "auth"]);
  });
});
