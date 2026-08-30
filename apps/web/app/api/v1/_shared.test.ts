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
 *   2. The catch handler emits a stable structured warning so a sustained
 *      bypass is queryable in Loki / Vercel logs without exposing Redis config.
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
    limiterCalls.apiLimit.mockRejectedValue(
      Object.assign(new Error("SECRET_CANARY_PUBLIC_API"), { code: "ECONNREFUSED" }),
    );
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});

    const { checkRateLimit } = await import("./_shared");
    const result = await checkRateLimit(makeRequest());

    // Bypass preserved: caller sees `null`, not a thrown error, not a 429.
    expect(result).toBeNull();
    // Stable event and operation so Loki / Vercel queries can count bypasses.
    expect(warnSpy).toHaveBeenCalledTimes(1);
    const [event, payload] = warnSpy.mock.calls[0];
    expect(event).toBe("external_client_error");
    expect(payload).toMatchObject({
      service: "redis",
      operation: "public_api_rate_limit",
      code: "ECONNREFUSED",
    });
    expect(JSON.stringify(warnSpy.mock.calls)).not.toContain("SECRET_CANARY_PUBLIC_API");
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
    if (result && "headers" in result) {
      expect(result.headers.get("Cache-Control")).toBe("no-store");
      expect(result.headers.get("Access-Control-Allow-Origin")).toBe("*");
      expect(result.headers.get("Access-Control-Allow-Methods")).toBe("GET");
      expect(result.headers.get("X-RateLimit-Limit")).toBe("60");
      expect(result.headers.get("X-RateLimit-Remaining")).toBe("0");
    }
  });
});

describe("apiResponse status contract (#3213)", () => {
  it("uses 200 by default, honors status codes, and is never cacheable", async () => {
    const { apiResponse } = await import("./_shared");

    const response = apiResponse(
      { error: "Bad request" },
      {
        rateLimit: { limit: 30, remaining: 29, reset: 12345 },
        status: 400,
      },
    );

    expect(apiResponse({ ok: true }).status).toBe(200);
    expect(response.status).toBe(400);
    expect(response.headers.get("Cache-Control")).toBe("no-store");
    expect(response.headers.get("Vercel-CDN-Cache-Control")).toBeNull();
    expect(response.headers.get("X-RateLimit-Remaining")).toBe("29");
  });

  it("caches deterministic successes only at Vercel and omits caller metadata", async () => {
    const { sharedApiResponse } = await import("./_shared");

    const response = sharedApiResponse(
      { ok: true },
      { maxAge: 3600 },
    );

    expect(response.status).toBe(200);
    expect(response.headers.get("Cache-Control")).toBe(
      "public, max-age=0, must-revalidate",
    );
    expect(response.headers.get("Vercel-CDN-Cache-Control")).toBe(
      "public, max-age=3600",
    );
    expect(response.headers.get("Access-Control-Allow-Origin")).toBe("*");
    expect(response.headers.get("X-RateLimit-Limit")).toBeNull();
    expect(response.headers.get("X-RateLimit-Remaining")).toBeNull();
    expect(response.headers.get("X-RateLimit-Reset")).toBeNull();
  });
});

describe("parseApiLocale public v1 contract (#6132)", () => {
  it("uses the shared default and accepts every supported locale", async () => {
    const { parseApiLocale } = await import("./_shared");

    expect(parseApiLocale(new URLSearchParams())).toBe("en");
    for (const locale of ["en", "de", "fr", "it"]) {
      expect(parseApiLocale(new URLSearchParams({ locale }))).toBe(locale);
    }
  });

  it("returns a non-cacheable 400 with rate-limit metadata for unsupported locales", async () => {
    const { parseApiLocale } = await import("./_shared");
    const response = parseApiLocale(
      new URLSearchParams({ locale: "xx" }),
      { limit: 30, remaining: 29, reset: 12345 },
    );

    expect(response).toBeInstanceOf(Response);
    if (!(response instanceof Response)) return;
    expect(response.status).toBe(400);
    expect(await response.json()).toEqual({
      error: "Invalid 'locale' param. Supported: en, de, fr, it",
    });
    expect(response.headers.get("Cache-Control")).toBe("no-store");
    expect(response.headers.get("Access-Control-Allow-Origin")).toBe("*");
    expect(response.headers.get("X-RateLimit-Limit")).toBe("30");
    expect(response.headers.get("X-RateLimit-Remaining")).toBe("29");
  });
});

describe("public search-filter contract (#6132)", () => {
  it("rejects unknown finite-list values without caching the response", async () => {
    const { validatePublicEnumListParam, PUBLIC_WORK_MODE_VALUES } = await import(
      "./_shared"
    );
    const response = validatePublicEnumListParam(
      "wm",
      "remote,bogus",
      PUBLIC_WORK_MODE_VALUES,
    );

    expect(response?.status).toBe(400);
    expect(await response?.json()).toEqual({
      error: "Invalid 'wm' value(s): bogus. Supported: onsite, hybrid, remote",
    });
    expect(response?.headers.get("Cache-Control")).toBe("no-store");
  });

  it("rejects unresolved exact slugs instead of widening the search", async () => {
    const { validateResolvedPublicFilters } = await import("./_shared");
    const response = validateResolvedPublicFilters({
      unresolvedExplicitSlugs: { tech: ["not-a-technology"] },
    });

    expect(response?.status).toBe(400);
    expect(await response?.json()).toEqual({
      error:
        "Invalid 'tech' slug(s): not-a-technology. Use /api/v1/resolve for exact slugs.",
    });
  });

  it("pins Explore links to EUR when the public API applies a salary range", async () => {
    const { exploreUrl } = await import("./_shared");
    const url = new URL(
      exploreUrl(
        new URLSearchParams("q=engineer&sal=100000-&salcur=USD"),
        "de",
      ),
    );

    expect(url.pathname).toBe("/de/explore");
    expect(url.searchParams.get("q")).toBe("engineer");
    expect(url.searchParams.get("sal")).toBe("100000-");
    expect(url.searchParams.get("salcur")).toBe("EUR");
  });
});
