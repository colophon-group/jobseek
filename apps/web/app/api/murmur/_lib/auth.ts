/**
 * Bearer-token auth helper for every Murmur shim route.
 *
 * Reads `MURMUR_TOKEN` from process.env at module load time. Compares
 * the agent-supplied bearer with `crypto.timingSafeEqual` (constant-time)
 * to avoid leaking the secret length / prefix via response timing.
 *
 * The function returns either `null` (auth ok — proceed with the request)
 * or a Response object the route should return immediately. Routes call
 * this as their FIRST line of work — before reading the body, before
 * reading the claim-token header, before any I/O.
 *
 * Spec: Murmur DESIGN.md §3.6 (Demo-grade auth, single shared bearer),
 * §5.2 (jobseek demo path).
 *
 * @see colophon-group/jobseek#2759
 */

import { NextResponse } from "next/server";
import { timingSafeEqual } from "node:crypto";
import { HEADER_AUTHORIZATION } from "./headers";

const TOKEN_ENV_VAR = "MURMUR_TOKEN" as const;

/**
 * Read the configured token at call time (not module-load time) so tests
 * can mutate `process.env.MURMUR_TOKEN` between cases.
 */
function getConfiguredToken(): string | undefined {
  const t = process.env[TOKEN_ENV_VAR];
  if (typeof t !== "string" || t.length === 0) return undefined;
  return t;
}

/**
 * Constant-time comparison of two strings. Returns `false` when lengths
 * differ (avoids the obvious timing leak) and otherwise calls
 * `crypto.timingSafeEqual` on the byte buffers. Never throws.
 */
function constantTimeEquals(a: string, b: string): boolean {
  const ab = Buffer.from(a, "utf8");
  const bb = Buffer.from(b, "utf8");
  if (ab.length !== bb.length) return false;
  return timingSafeEqual(ab, bb);
}

/**
 * Verify the `Authorization: Bearer <token>` header against
 * `process.env.MURMUR_TOKEN`.
 *
 * @returns `null` when authorised; a 401 NextResponse otherwise.
 *   The 401 response body is `{ ok: false, errors: ["unauthorized"] }` —
 *   no detail leaks (missing vs wrong vs disabled all collapse to the
 *   same response).
 */
export function requireBearer(request: Request): NextResponse | null {
  const expected = getConfiguredToken();
  if (!expected) {
    // Server is misconfigured (no token set). Fail closed; an unauthorised
    // 401 is the safest answer — never accept any token.
    return unauthorized();
  }

  const header = request.headers.get(HEADER_AUTHORIZATION);
  if (!header) return unauthorized();

  // Accept "Bearer <token>" (canonical) and reject everything else.
  const match = /^Bearer\s+(.+)$/i.exec(header);
  if (!match) return unauthorized();

  const presented = match[1].trim();
  if (presented.length === 0) return unauthorized();

  if (!constantTimeEquals(presented, expected)) return unauthorized();

  return null;
}

function unauthorized(): NextResponse {
  return NextResponse.json(
    { ok: false, errors: ["unauthorized"] },
    { status: 401 },
  );
}
