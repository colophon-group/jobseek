import "server-only";
import { redis } from "@/lib/redis";

interface CacheOptions<T = unknown> {
  /** TTL in seconds. */
  ttl: number;
  /** Skip caching the result if this predicate returns true. */
  skipIf?: (data: T) => boolean;
}

/**
 * In-process single-flight registry.
 *
 * On a cold start, N concurrent requests for the same key all miss the
 * Redis cache and would otherwise each fan out to the upstream (Typesense /
 * Postgres). The map collapses concurrent identical fetches **within a
 * single Node instance** to one upstream call; the others await the same
 * promise. Per-instance only — across Vercel's autoscaled instances the
 * Redis cache layer is the cross-instance dedup once it warms up.
 *
 * The map is keyed by the user-supplied cache key (NOT the `cache:`-prefixed
 * Redis key) so callers using identical `cached(...)` inputs share the
 * same in-flight promise. Entries are deleted in a `finally` block so a
 * failed upstream call doesn't pin the slot indefinitely.
 */
const _inflight = new Map<string, Promise<unknown>>();

/**
 * Redis-backed cache-aside wrapper with in-process single-flight
 * stampede protection.
 *
 * Shared across all Vercel serverless instances (unlike `unstable_cache`
 * which is per-process). Falls back to the fetcher on Redis errors.
 */
export async function cached<T>(
  key: string,
  fetcher: () => Promise<T>,
  options: CacheOptions<T>,
): Promise<T> {
  const fullKey = `cache:${key}`;

  try {
    const hit = await redis.get<T>(fullKey);
    if (hit !== null && hit !== undefined) return hit;
  } catch {
    // Redis unavailable — fall through to fetcher
  }

  // Cold-start stampede protection: collapse concurrent identical fetches
  // within this instance to a single upstream call. The first concurrent
  // caller seeds the in-flight map; later callers await its promise.
  const existing = _inflight.get(key) as Promise<T> | undefined;
  if (existing !== undefined) return existing;

  const inflight = (async () => {
    try {
      const data = await fetcher();
      if (!options.skipIf?.(data)) {
        try {
          await redis.set(fullKey, JSON.stringify(data), { ex: options.ttl });
        } catch {
          // Redis unavailable — data still returned from fetcher
        }
      }
      return data;
    } finally {
      // Always release the slot, even on fetcher rejection, so the next
      // caller gets a chance to retry against a healthy upstream.
      _inflight.delete(key);
    }
  })();

  _inflight.set(key, inflight);
  return inflight;
}

/**
 * Invalidate a cached key.
 *
 * Used by the crawler to bust stale caches after batch processing.
 */
export async function invalidate(key: string): Promise<void> {
  try {
    await redis.del(`cache:${key}`);
  } catch {
    // Best effort
  }
}

/**
 * Delete every cached key whose unprefixed name starts with ``prefix``.
 *
 * Used by the post-crawler-sync invalidation hook (`/api/internal/
 * invalidate-typeahead`) to drop stale typeahead suggestions after
 * taxonomy mutations. Iterates Upstash via SCAN cursors so a partial
 * sweep doesn't block on a giant single command.
 *
 * Returns the number of keys confirmed deleted (best effort; Redis errors
 * stop the sweep early and the count reflects what completed).
 */
export async function invalidatePattern(prefix: string): Promise<number> {
  const match = `cache:${prefix}*`;
  let cursor: string | number = 0;
  let deleted = 0;
  try {
    do {
      const [next, keys] = (await redis.scan(cursor, {
        match,
        count: 100,
      })) as [string | number, string[]];
      cursor = next;
      if (keys.length > 0) {
        deleted += await redis.del(...keys);
      }
      // Upstash returns "0" (string) when iteration is complete.
    } while (String(cursor) !== "0");
  } catch {
    // Partial sweep is acceptable — TTL backstop will catch the rest.
  }
  return deleted;
}
