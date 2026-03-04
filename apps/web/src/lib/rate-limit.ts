import "server-only";
import { Ratelimit } from "@upstash/ratelimit";
import { redis } from "@/lib/redis";

/** Auth endpoints: 10 requests per 60 seconds per IP. */
export const authLimiter = new Ratelimit({
  redis,
  limiter: Ratelimit.slidingWindow(10, "60 s"),
  prefix: "rl:auth",
});

/** Password reset: 3 requests per 5 minutes per IP. */
export const passwordResetLimiter = new Ratelimit({
  redis,
  limiter: Ratelimit.slidingWindow(3, "300 s"),
  prefix: "rl:pw-reset",
});

/** Company request: 5 requests per hour per IP. */
export const companyRequestLimiter = new Ratelimit({
  redis,
  limiter: Ratelimit.slidingWindow(5, "3600 s"),
  prefix: "rl:company-req",
});
