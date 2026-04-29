/**
 * Public health endpoint for the murmur-shim container.
 *
 * Three callers depend on this route, none of which can present a
 * bearer token:
 *
 *   1. The Docker image's `HEALTHCHECK` directive (busybox `wget`
 *      against `http://localhost:8080/health`).
 *   2. The compose-level healthcheck in H3's docker-compose.yml.
 *   3. Cloudflared's `originRequest.proxyAddress` upstream-up probe in
 *      the H4 deploy.
 *
 * Auth in this app is per-route (each handler calls `requireBearer`
 * itself — there is no global Hono-style middleware). Leaving this
 * handler off the bearer path is therefore sufficient to make it
 * publicly reachable; we don't need an explicit exemption.
 *
 * The body is intentionally trivial (`{ ok: true }`). It exists only to
 * prove the Node process is up and the routing layer answers; the route
 * does NOT touch the database or the Python venv on purpose — a degraded
 * DB or a missing venv-mount must not cause the container to be marked
 * unhealthy and restart-looped, since the same shim is what reports the
 * problem to the agent.
 *
 * @see colophon-group/jobseek#2774 (H2 — Dockerfile + image)
 */

import { NextResponse } from "next/server";

// Force the route handler to be evaluated per-request rather than
// statically pre-rendered; Next 16 otherwise treats simple GET handlers
// without dynamic markers as cacheable, which would mask real outages
// (a stale cached "ok" served while the process is wedged).
export const dynamic = "force-dynamic";

export function GET(): NextResponse {
  return NextResponse.json({ ok: true });
}
