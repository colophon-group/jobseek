import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

/**
 * Issue colophon-group/jobseek#3175 — silent `} catch {}` around the
 * apiLimiter.limit() call swallowed every Redis/Upstash failure, so a
 * region-local Redis blip would let every `/api/v1/*` request bypass
 * rate-limiting with no log, no metric. These specs lock in the
 * observability contract:
 *
 *   1. When the limiter throws, the request still degrades open (the
 *      original behaviour — fail-closed would lock the public API down
 *      during a Redis incident).
 *   2. The catch handler emits a stable `[rate-limit] redis bypass`
 *      warning so a sustained bypass is queryable in Loki / Vercel logs.
 */

vi.mock("server-only", () => ({}));

const limiterCalls = vi.hoisted(() => ({
  apiLimit: vi.fn(),
  getClientIp: vi.fn(),
}));

vi.mock("@/lib/rate-limit", () => ({
  apiLimiter: { limit: limiterCalls.apiLimit },
  getClientIp: limiterCalls.getClientIp,
}));

// `apiResponse` (also exported by `_shared`) imports `@/content/config`,
// which pulls in MDX and other heavy modules. Only `checkRateLimit` is
// under test, so stub the content config out.
vi.mock("@/content/config", () => ({
  siteConfig: { url: "https://example.com" },
}));

function makeRequest(): NextRequest {
  return new NextRequest("https://example.com/api/v1/search", {
    headers: { "x-forwarded-for": "203.0.113.7" },
  });
}

describe("checkRateLimit — Redis bypass observability (#3175)", () => {
  beforeEach(() => {
    limiterCalls.apiLimit.mockReset();
    limiterCalls.getClientIp.mockReset();
    limiterCalls.getClientIp.mockReturnValue("203.0.113.7");
  });

  afterEach(() => {
    vi.resetModules();
    vi.restoreAllMocks();
  });

  it("logs a warn when the limiter throws (Redis outage) and still degrades open", async () => {
    limiterCalls.apiLimit.mockRejectedValue(new Error("ECONNREFUSED"));
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});

    const { checkRateLimit } = await import("./_shared");
    const result = await checkRateLimit(makeRequest());

    // Bypass preserved: caller sees `null`, not a thrown error, not a 429.
    expect(result).toBeNull();
    // Stable event prefix so Loki / Vercel queries can count bypasses.
    expect(warnSpy).toHaveBeenCalledTimes(1);
    const [message, errArg] = warnSpy.mock.calls[0];
    expect(message).toBe("[rate-limit] redis bypass");
    expect(errArg).toBeInstanceOf(Error);
    expect((errArg as Error).message).toBe("ECONNREFUSED");
  });

  it("does NOT log when the limiter succeeds (no false bypass signal)", async () => {
    limiterCalls.apiLimit.mockResolvedValue({
      success: true,
      limit: 60,
      remaining: 59,
      reset: Date.now() + 60_000,
    });
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});

    const { checkRateLimit } = await import("./_shared");
    const result = await checkRateLimit(makeRequest());

    expect(warnSpy).not.toHaveBeenCalled();
    // The success path returns the RateLimitInfo object, not null.
    expect(result).toMatchObject({ limit: 60, remaining: 59 });
  });

  it("does NOT log when the limiter blocks (429 is not a bypass)", async () => {
    limiterCalls.apiLimit.mockResolvedValue({
      success: false,
      limit: 60,
      remaining: 0,
      reset: Date.now() + 60_000,
    });
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});

    const { checkRateLimit } = await import("./_shared");
    const result = await checkRateLimit(makeRequest());

    expect(warnSpy).not.toHaveBeenCalled();
    // Blocked requests return a NextResponse with status 429.
    expect(result).not.toBeNull();
    expect(result && "status" in result && result.status).toBe(429);
  });
});

describe("apiResponse status contract (#3213)", () => {
  it("uses 200 by default but honors explicit non-2xx status codes", async () => {
    const { apiResponse } = await import("./_shared");

    expect(apiResponse({ ok: true }).status).toBe(200);
    expect(apiResponse({ error: "Bad request" }, { status: 400 }).status).toBe(400);
  });
});
