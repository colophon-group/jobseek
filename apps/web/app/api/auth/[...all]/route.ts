import { auth } from "@/lib/auth";
import { toNextJsHandler } from "better-auth/next-js";
import {
  authLimiter,
  getClientIp,
  passwordResetLimiter,
} from "@/lib/rate-limit";
import { type NextRequest, NextResponse } from "next/server";

const { GET: authGet, POST: authPost } = toNextJsHandler(auth);

function rateLimitResponse(reset: number): NextResponse {
  const retryAfter = Math.ceil((reset - Date.now()) / 1000);
  return new NextResponse("Too Many Requests", {
    status: 429,
    headers: {
      "Retry-After": String(Math.max(1, retryAfter)),
    },
  });
}

/**
 * Better-Auth password-reset endpoints (v1.4.x, the version pinned in
 * `apps/web/package.json`). The catch-all rewrites `/api/auth/<x>` -> the
 * Better-Auth handler's internal path `/<x>`, but `request.nextUrl.pathname`
 * carries the full URL path, so we match on the `/api/auth/...` prefix.
 *
 *   - POST /api/auth/request-password-reset   -- triggers sendResetPassword
 *                                                (THE email-bombing vector)
 *   - GET  /api/auth/reset-password/:token    -- token validity callback
 *                                                (enumeration oracle)
 *   - POST /api/auth/reset-password           -- submit new password
 *                                                (token-stuffing vector)
 *
 * The legacy `/forget-password` alias only exists for the email-otp plugin,
 * which we don't enable. See colophon-group/jobseek#3223.
 */
function isPasswordResetPath(pathname: string): boolean {
  return (
    pathname === "/api/auth/request-password-reset" ||
    pathname === "/api/auth/reset-password" ||
    pathname.startsWith("/api/auth/reset-password/")
  );
}

/**
 * Apply the tighter password-reset limiter (3/300s) on top of the broader
 * `authLimiter` (10/60s) when the request targets a reset endpoint. Both
 * limiters are keyed on the same IP-extraction function so an attacker
 * cannot cycle the `x-forwarded-for` header to bypass either layer (see
 * `getClientIp` + issue #3219). Returns the 429 response if either limiter
 * blocks, or `null` to indicate the request may proceed.
 *
 * Redis outage degrades open (matches the existing `authLimiter` behaviour
 * — fail-closed would lock all users out of auth during a Redis incident).
 */
async function applyAuthLimits(
  request: NextRequest,
): Promise<NextResponse | null> {
  const ip = getClientIp(request.headers);
  const pathname = request.nextUrl.pathname;

  if (isPasswordResetPath(pathname)) {
    try {
      const { success, reset } = await passwordResetLimiter.limit(ip);
      if (!success) return rateLimitResponse(reset);
    } catch (err) {
      // Redis unavailable — degrade open, mirroring authLimiter behaviour.
      // Log at warn so a Redis outage that disables the tight password-reset
      // bucket (3/300s, the email-bombing vector) is queryable in Loki under
      // `[auth-rate-limit] pw-reset redis bypass`. See #3175.
      console.warn("[auth-rate-limit] pw-reset redis bypass", err);
    }
  }

  try {
    const { success, reset } = await authLimiter.limit(ip);
    if (!success) return rateLimitResponse(reset);
  } catch (err) {
    // Redis unavailable — allow request through. Log at warn so a Redis
    // outage that silently disables the broader auth bucket (10/60s) is
    // queryable in Loki under `[auth-rate-limit] redis bypass`. See #3175.
    console.warn("[auth-rate-limit] redis bypass", err);
  }
  return null;
}

export async function GET(request: NextRequest) {
  // The GET handler is reached for OAuth callbacks and the password-reset
  // token-validity redirect (`/reset-password/:token`). Rate-limiting the
  // latter prevents using the endpoint as a token-validity oracle; OAuth
  // callbacks share the IP-keyed `authLimiter` bucket as before.
  const limited = await applyAuthLimits(request);
  if (limited) return limited;
  return authGet(request);
}

export async function POST(request: NextRequest) {
  const limited = await applyAuthLimits(request);
  if (limited) return limited;
  return authPost(request);
}
