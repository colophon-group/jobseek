import { describe, it, expect, vi } from "vitest";

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
      return { success: true, limit: 10, remaining: 9, reset: Date.now() + 60000 };
    }

    static slidingWindow(_tokens: number, _window: string) {
      return { type: "slidingWindow" };
    }
  }

  return { Ratelimit: MockRatelimit };
});

import { authLimiter, passwordResetLimiter, companyRequestLimiter } from "../rate-limit";

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

  it("authLimiter.limit returns a rate limit result", async () => {
    const result = await authLimiter.limit("127.0.0.1");
    expect(result).toHaveProperty("success");
    expect(result).toHaveProperty("limit");
    expect(result).toHaveProperty("remaining");
    expect(result).toHaveProperty("reset");
  });

  it("passwordResetLimiter.limit returns a rate limit result", async () => {
    const result = await passwordResetLimiter.limit("127.0.0.1");
    expect(result).toHaveProperty("success");
  });

  it("companyRequestLimiter.limit returns a rate limit result", async () => {
    const result = await companyRequestLimiter.limit("127.0.0.1");
    expect(result).toHaveProperty("success");
  });
});
