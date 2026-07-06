import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { NextRequest } from "next/server";

// Mock server-only to prevent import error
vi.mock("server-only", () => ({}));

// Mock Redis
vi.mock("@/lib/redis", () => ({
  redis: {
    get: vi.fn(),
    set: vi.fn(),
    del: vi.fn(),
    eval: vi.fn(),
    evalsha: vi.fn(),
    scriptLoad: vi.fn(),
  },
}));

const mockRateLimitState = vi.hoisted(() => ({
  limitResult: {
    success: true,
    limit: 30,
    remaining: 29,
    reset: Date.now() + 60000,
  },
  limiterConstructors: [] as Array<{
    limiter: { type: "slidingWindow"; tokens: number; window: string };
    prefix: string;
  }>,
  // Captures the identifier that was passed to `Ratelimit.limit()` so tests can
  // assert the rate-limit key, not just the response.
  limitCalls: [] as string[],
}));

// Mock @upstash/ratelimit
vi.mock("@upstash/ratelimit", () => {
  class MockRatelimit {
    redis: unknown;
    limiter: unknown;
    prefix: string;

    constructor(opts: { redis: unknown; limiter: unknown; prefix: string }) {
      this.redis = opts.redis;
      this.limiter = opts.limiter;
      this.prefix = opts.prefix;
      mockRateLimitState.limiterConstructors.push(
        opts as {
          limiter: { type: "slidingWindow"; tokens: number; window: string };
          prefix: string;
        },
      );
    }

    async limit(identifier: string) {
      mockRateLimitState.limitCalls.push(identifier);
      return { ...mockRateLimitState.limitResult };
    }

    static slidingWindow(tokens: number, window: string) {
      return { type: "slidingWindow", tokens, window };
    }
  }

  return { Ratelimit: MockRatelimit };
});

// Mock siteConfig for _shared.ts
vi.mock("@/content/config", () => ({
  siteConfig: { url: "https://example.com" },
}));

import {
  getClientIp,
} from "../rate-limit";
import { checkRateLimit } from "../../../app/api/v1/_shared";

function fakeRequest(ip = "1.2.3.4"): NextRequest {
  return new NextRequest("https://example.com/api/v1/test", {
    headers: { "x-forwarded-for": ip },
  });
}

function makeHeaders(init: Record<string, string>): Headers {
  return new Headers(init);
}

describe("rate-limit configuration", () => {
  it("constructs every limiter with the intended prefix and sliding window", () => {
    expect(
      mockRateLimitState.limiterConstructors.map(({ limiter, prefix }) => ({
        prefix,
        tokens: limiter.tokens,
        window: limiter.window,
      })),
    ).toEqual([
      { prefix: "rl:auth", tokens: 10, window: "60 s" },
      { prefix: "rl:pw-reset", tokens: 3, window: "300 s" },
      { prefix: "rl:company-req", tokens: 5, window: "3600 s" },
      { prefix: "rl:api", tokens: 30, window: "60 s" },
    ]);
  });
});

describe("checkRateLimit", () => {
  beforeEach(() => {
    mockRateLimitState.limitCalls.length = 0;
    mockRateLimitState.limitResult = {
      success: true,
      limit: 30,
      remaining: 29,
      reset: Date.now() + 60000,
    };
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("returns rate limit info when under limit", async () => {
    const result = await checkRateLimit(fakeRequest());
    expect(result).not.toBeNull();
    expect(result).toHaveProperty("limit", 30);
    expect(result).toHaveProperty("remaining", 29);
  });

  it("returns 429 NextResponse when limit exceeded", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-01-01T00:00:00.000Z"));

    mockRateLimitState.limitResult = {
      success: false,
      limit: 30,
      remaining: 0,
      reset: Date.now() + 30000,
    };

    const result = await checkRateLimit(fakeRequest());
    expect(result).toBeDefined();
    expect((result as Response).status).toBe(429);

    const headers = (result as Response).headers;
    expect(headers.get("Retry-After")).toBe("30");
    expect(headers.get("X-RateLimit-Limit")).toBe("30");
    expect(headers.get("X-RateLimit-Remaining")).toBe("0");
    expect(headers.get("X-RateLimit-Reset")).toBe(
      String(mockRateLimitState.limitResult.reset),
    );
    await expect((result as Response).json()).resolves.toEqual({
      error: "Too many requests",
    });
  });

  it("passes the last x-forwarded-for hop to the limiter", async () => {
    await checkRateLimit(fakeRequest("9.9.9.9, 10.0.0.1, 203.0.113.7"));

    expect(mockRateLimitState.limitCalls).toEqual(["203.0.113.7"]);
  });

  it("falls back to a deterministic shared key when no IP headers are present", async () => {
    const req = new NextRequest("https://example.com/api/v1/test");

    await checkRateLimit(req);

    expect(mockRateLimitState.limitCalls).toEqual(["unknown"]);
  });
});

// Regression: see issue #3219. Vercel **appends** the real client IP to
// `x-forwarded-for`, so the **first** entry is attacker-controlled. Using
// the first entry as a rate-limit key let any caller bypass every limit
// (e.g. password-stuffing on Better Auth, DoS on /api/v1/search) by varying
// a single header on each request.
describe("getClientIp (issue #3219 — x-forwarded-for spoofing)", () => {
  it("prefers x-real-ip over x-forwarded-for", () => {
    const h = makeHeaders({
      "x-forwarded-for": "1.2.3.4, 10.0.0.1, 203.0.113.7",
      "x-real-ip": "203.0.113.7",
    });
    expect(getClientIp(h)).toBe("203.0.113.7");
  });

  it("returns the LAST entry of x-forwarded-for (Vercel's appended IP), not the first", () => {
    // First entry "9.9.9.9" is what a malicious client would supply; the
    // rest is what Vercel appends.
    const h = makeHeaders({
      "x-forwarded-for": "9.9.9.9, 10.0.0.1, 203.0.113.7",
    });
    const ip = getClientIp(h);
    expect(ip).toBe("203.0.113.7");
    expect(ip).not.toBe("9.9.9.9");
  });

  it("does NOT trust a spoofed first entry even when only one extra hop exists", () => {
    const h = makeHeaders({
      "x-forwarded-for": "evil.spoofed.ip, 203.0.113.7",
    });
    expect(getClientIp(h)).toBe("203.0.113.7");
  });

  it("falls back to 'unknown' when no IP headers are present", () => {
    expect(getClientIp(makeHeaders({}))).toBe("unknown");
  });

  it("handles a single-entry x-forwarded-for (no comma)", () => {
    expect(getClientIp(makeHeaders({ "x-forwarded-for": "203.0.113.7" }))).toBe(
      "203.0.113.7",
    );
  });

  it("trims whitespace and skips trailing empty tokens", () => {
    expect(
      getClientIp(makeHeaders({ "x-forwarded-for": "9.9.9.9, 203.0.113.7,  " })),
    ).toBe("203.0.113.7");
  });

  it("handles IPv6 addresses in the last position", () => {
    expect(
      getClientIp(
        makeHeaders({ "x-forwarded-for": "9.9.9.9, 2001:db8::1" }),
      ),
    ).toBe("2001:db8::1");
  });
});

// Regression: assert that the rate-limit key actually passed into
// `Ratelimit.limit()` is the authoritative IP, not the attacker-controlled
// first entry of `x-forwarded-for`. This is the surface that issue #3219
// exploits — even if `getClientIp()` is correct in isolation, the call
// site must call it correctly.
describe("checkRateLimit identifier (issue #3219)", () => {
  beforeEach(() => {
    mockRateLimitState.limitCalls.length = 0;
    mockRateLimitState.limitResult = {
      success: true,
      limit: 30,
      remaining: 29,
      reset: Date.now() + 60000,
    };
  });

  it("keys the API rate limit by Vercel's appended IP, not the spoofed first entry", async () => {
    const spoofed = "9.9.9.9";
    const real = "203.0.113.7";
    const req = new NextRequest("https://example.com/api/v1/test", {
      headers: { "x-forwarded-for": `${spoofed}, ${real}` },
    });

    await checkRateLimit(req);

    expect(mockRateLimitState.limitCalls).toHaveLength(1);
    expect(mockRateLimitState.limitCalls[0]).toBe(real);
    expect(mockRateLimitState.limitCalls[0]).not.toBe(spoofed);
  });

  it("prefers x-real-ip even when x-forwarded-for is present", async () => {
    const req = new NextRequest("https://example.com/api/v1/test", {
      headers: {
        "x-forwarded-for": "9.9.9.9, 10.0.0.1",
        "x-real-ip": "203.0.113.7",
      },
    });

    await checkRateLimit(req);

    expect(mockRateLimitState.limitCalls).toHaveLength(1);
    expect(mockRateLimitState.limitCalls[0]).toBe("203.0.113.7");
  });

  it("buckets repeated requests with different spoofed first entries under the same real IP", async () => {
    const real = "203.0.113.7";
    for (const spoofed of ["1.1.1.1", "2.2.2.2", "3.3.3.3"]) {
      const req = new NextRequest("https://example.com/api/v1/test", {
        headers: { "x-forwarded-for": `${spoofed}, ${real}` },
      });
      await checkRateLimit(req);
    }

    expect(mockRateLimitState.limitCalls).toEqual([real, real, real]);
  });
});
