import "server-only";
import { cache } from "react";
import { headers } from "next/headers";
import { auth } from "@/lib/auth";
import { kvDelete, kvGet, kvMget, kvScan, kvSet } from "@/lib/cache";

const SESSION_TTL = 300; // 5 minutes

type SessionResult = Awaited<ReturnType<typeof auth.api.getSession>>;

function sessionKey(token: string): string {
  return `session:${token}`;
}

function extractToken(cookieHeader: string): string | null {
  for (const part of cookieHeader.split(";")) {
    const trimmed = part.trim();
    if (trimmed.startsWith("__Secure-better-auth.session_token=")) {
      return trimmed.slice("__Secure-better-auth.session_token=".length);
    }
    if (trimmed.startsWith("better-auth.session_token=")) {
      return trimmed.slice("better-auth.session_token=".length);
    }
  }
  return null;
}

async function fetchSession(): Promise<SessionResult> {
  const headersList = await headers();
  const cookieHeader = headersList.get("cookie") ?? "";
  const token = extractToken(cookieHeader);
  if (!token) return null;

  // Try Redis cache first. `kvGet` swallows Redis errors by default and
  // returns null on miss OR transport failure, matching the pre-façade
  // "fall through to DB on Redis blip" behaviour.
  const cached = await kvGet<SessionResult>(sessionKey(token));
  if (cached) return cached;

  // Cache miss — fetch from DB via Better Auth
  let result: SessionResult;
  try {
    result = await auth.api.getSession({ headers: headersList });
  } catch {
    // DB unavailable (e.g. statement timeout) — treat as unauthenticated
    return null;
  }
  if (!result) return null;

  // Store in Redis for subsequent requests. `kvSet` swallows on its own;
  // we still get the DB result back to the caller either way.
  await kvSet(sessionKey(token), result, { ttl: SESSION_TTL });

  return result;
}

/**
 * Per-request cached session getter (full user object).
 *
 * Checks Redis first (shared across all serverless instances),
 * then falls back to Better Auth DB query on cache miss.
 * React's `cache()` deduplicates within a single server render.
 */
export const getSession = cache(fetchSession);

/**
 * Lightweight session check — returns just the userId.
 *
 * Uses the Redis-backed getSession() instead of a separate DB query.
 */
export async function getSessionUserId(): Promise<string | null> {
  const session = await getSession();
  return session?.user?.id ?? null;
}

/**
 * Invalidate a session in Redis cache.
 *
 * Called on sign-out, password change, session revocation, etc.
 * Eviction is a true `DEL` (not stale-marking) so subsequent reads miss
 * and re-fetch from Better Auth.
 */
export async function invalidateSessionCache(token: string): Promise<void> {
  // `kvDelete` swallows Redis errors by default. We don't care about the
  // count — TTL would clean up eventually if the DEL silently fails.
  await kvDelete(sessionKey(token));
}

/**
 * Bust every `session:*` Redis cache entry whose cached `SessionResult`
 * belongs to ``userId``.
 *
 * The Redis cache key is `session:<signed-cookie-value>` (NOT
 * `session:<raw-token>` — the cookie value is `HMAC(secret, token)`
 * appended to the token), so callers that only have a list of raw
 * session tokens from the DB can't construct the cache keys directly.
 * SCAN over the namespace and filter by the payload's `user.id` is the
 * pragmatic way to bust all of a user's devices in one shot.
 *
 * Used by `renameUsername` so a username change is visible on every
 * device the user is logged into within seconds (vs. the 5-min cache
 * TTL otherwise). Iterates Upstash via SCAN cursors so a partial sweep
 * doesn't block on a giant single command. Best-effort: failures are
 * logged and swallowed because the rename has already succeeded — TTL
 * would still self-heal eventually.
 */
export async function invalidateAllUserSessionCacheEntries(
  userId: string,
): Promise<number> {
  let cursor: string | number = 0;
  let deleted = 0;
  try {
    do {
      const [next, keys] = await kvScan(cursor, {
        match: "session:*",
        count: 100,
      });
      cursor = next;
      if (keys.length === 0) continue;

      // mget returns the parsed JSON for each value (or null). Each cached
      // entry is the full SessionResult: `{ session: {...}, user: {...} }`.
      const values = await kvMget<{ user?: { id?: string } }>(keys);
      const toDelete: string[] = [];
      for (let i = 0; i < keys.length; i++) {
        if (values[i]?.user?.id === userId) toDelete.push(keys[i]);
      }
      if (toDelete.length > 0) {
        // Rethrow inside the loop so the outer catch logs + returns the
        // partial count. `kvDelete` would swallow by default; we want the
        // sweep to abort cleanly mid-iteration on transient errors.
        deleted += await kvDelete(toDelete, { swallowErrors: false });
      }
    } while (String(cursor) !== "0");
  } catch (err) {
    console.error(
      "[invalidateAllUserSessionCacheEntries] redis scan failed",
      err,
    );
  }
  return deleted;
}
