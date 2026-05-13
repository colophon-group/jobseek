import "server-only";
import { Ratelimit } from "@upstash/ratelimit";
import { redis } from "@/lib/redis";

/**
 * Extract the platform-authoritative client IP from request headers.
 *
 * Threat model: `x-forwarded-for` is a list that any upstream hop (including
 * the client) can prepend to. Vercel **appends** the real client IP as the
 * last entry, so the **first** entry is attacker-controlled. Using the first
 * entry as a rate-limit key lets an attacker bypass every limit by sending
 * `X-Forwarded-For: <random-ip>` on each request.
 *
 * Preference order:
 *   1. `x-real-ip` — Vercel sets this to a single platform-authoritative IP.
 *   2. Last non-empty entry of `x-forwarded-for` — the entry Vercel appended.
 *   3. `"unknown"` — degrade closed: all unidentified callers share a key.
 *
 * Reference: https://vercel.com/docs/edge-network/headers/request-headers
 */
export function getClientIp(headers: Headers): string {
  const real = headers.get("x-real-ip")?.trim();
  if (real) return real;
  const xff = headers.get("x-forwarded-for");
  if (xff) {
    // Walk from the right; skip empty/whitespace tokens to be tolerant of
    // malformed headers (e.g. trailing commas).
    const parts = xff.split(",");
    for (let i = parts.length - 1; i >= 0; i--) {
      const candidate = parts[i]?.trim();
      if (candidate) return candidate;
    }
  }
  return "unknown";
}

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

/** Public API (AI agents): 30 requests per 60 seconds per IP. */
export const apiLimiter = new Ratelimit({
  redis,
  limiter: Ratelimit.slidingWindow(30, "60 s"),
  prefix: "rl:api",
});
