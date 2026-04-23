import "server-only";
import { Ratelimit } from "@upstash/ratelimit";
import { Redis } from "@upstash/redis";
import { hasUpstashRedisConfig, redis } from "@/lib/redis";

interface LimiterLike {
  limit(
    key: string,
  ): Promise<{
    success: boolean;
    limit: number;
    remaining: number;
    reset: number;
    pending: Promise<unknown>;
  }>;
}

const LOCAL_LIMIT = 1_000_000;

function createLimiter(
  maxRequests: number,
  window: `${number} s`,
  prefix: string,
): LimiterLike {
  if (!hasUpstashRedisConfig) {
    return {
      limit: async () => ({
        success: true,
        limit: LOCAL_LIMIT,
        remaining: LOCAL_LIMIT,
        reset: Date.now() + 60_000,
        pending: Promise.resolve(),
      }),
    };
  }

  const limiter = new Ratelimit({
    redis: redis as unknown as Redis,
    limiter: Ratelimit.slidingWindow(maxRequests, window),
    prefix,
  });
  return {
    limit: limiter.limit.bind(limiter),
  };
}

/** Auth endpoints: 10 requests per 60 seconds per IP. */
export const authLimiter = createLimiter(10, "60 s", "rl:auth");

/** Password reset: 3 requests per 5 minutes per IP. */
export const passwordResetLimiter = createLimiter(3, "300 s", "rl:pw-reset");

/** Company request: 5 requests per hour per IP. */
export const companyRequestLimiter = createLimiter(5, "3600 s", "rl:company-req");

/** Public API (AI agents): 30 requests per 60 seconds per IP. */
export const apiLimiter = createLimiter(30, "60 s", "rl:api");
