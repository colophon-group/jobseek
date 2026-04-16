import { describe, it, expect, vi, beforeEach } from "vitest";
import { NextRequest } from "next/server";

// Mock server-only to prevent import error
vi.mock("server-only", () => ({}));

// Mock Redis
vi.mock("@/lib/redis", () => ({
  hasUpstashRedisConfig: true,
  redis: {
    get: vi.fn(),
    set: vi.fn(),
    del: vi.fn(),
    eval: vi.fn(),
    evalsha: vi.fn(),
    scriptLoad: vi.fn(),
  },
}));

let mockLimitResult = {
  success: true,
  limit: 30,
  remaining: 29,
  reset: Date.now() + 60000,
};

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
    }

    async limit(_identifier: string) {
      return { ...mockLimitResult };
    }

    static slidingWindow(_tokens: number, _window: string) {
      return { type: "slidingWindow" };
    }
  }

  return { Ratelimit: MockRatelimit };
});

// Mock siteConfig for _shared.ts
vi.mock("@/content/config", () => ({
  siteConfig: { url: "https://example.com" },
}));

import {
  authLimiter,
  passwordResetLimiter,
  companyRequestLimiter,
  apiLimiter,
} from "../rate-limit";
import { checkRateLimit } from "../../../app/api/v1/_shared";

function fakeRequest(ip = "1.2.3.4"): NextRequest {
  return new NextRequest("https://example.com/api/v1/test", {
    headers: { "x-forwarded-for": ip },
  });
}

describe("rate-limit exports", () => {
  it("authLimiter exists and has limit method", () => {
    expect(authLimiter).toBeDefined();
    expect(typeof authLimiter.limit).toBe("function");
  });

  it("passwordResetLimiter exists and has limit method", () => {
    expect(passwordResetLimiter).toBeDefined();
    expect(typeof passwordResetLimiter.limit).toBe("function");
  });

  it("companyRequestLimiter exists and has limit method", () => {
    expect(companyRequestLimiter).toBeDefined();
    expect(typeof companyRequestLimiter.limit).toBe("function");
  });

  it("apiLimiter exists and has limit method", () => {
    expect(apiLimiter).toBeDefined();
    expect(typeof apiLimiter.limit).toBe("function");
  });

  it("limiter.limit returns a rate limit result", async () => {
    const result = await authLimiter.limit("127.0.0.1");
    expect(result).toHaveProperty("success");
    expect(result).toHaveProperty("limit");
    expect(result).toHaveProperty("remaining");
    expect(result).toHaveProperty("reset");
  });
});

describe("checkRateLimit", () => {
  beforeEach(() => {
    mockLimitResult = {
      success: true,
      limit: 30,
      remaining: 29,
      reset: Date.now() + 60000,
    };
  });

  it("returns rate limit info when under limit", async () => {
    const result = await checkRateLimit(fakeRequest());
    expect(result).not.toBeNull();
    expect(result).toHaveProperty("limit", 30);
    expect(result).toHaveProperty("remaining", 29);
  });

  it("returns 429 NextResponse when limit exceeded", async () => {
    mockLimitResult = {
      success: false,
      limit: 30,
      remaining: 0,
      reset: Date.now() + 30000,
    };

    const result = await checkRateLimit(fakeRequest());
    expect(result).toBeDefined();
    expect((result as Response).status).toBe(429);

    const headers = (result as Response).headers;
    expect(headers.get("Retry-After")).toBeDefined();
    expect(headers.get("X-RateLimit-Limit")).toBe("30");
    expect(headers.get("X-RateLimit-Remaining")).toBe("0");
  });
});
