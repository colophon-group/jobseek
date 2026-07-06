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

/**
 * Raw key-value primitives over the shared Redis client.
 *
 * These exist so callers with bespoke caching semantics (e.g. session cache,
 * which uses its own `session:<token>` namespace, has a different "cache
 * null result" policy than `cached()`, and needs SCAN+MGET sweeps) can stay
 * inside the cache façade rather than reaching into `@/lib/redis` directly.
 *
 * Invariant: outside this file and `@/lib/rate-limit` (which hands the
 * singleton to `@upstash/ratelimit`), `@/lib/redis` must not be imported —
 * everything else routes through this façade so error semantics, mocking,
 * and future client swaps are single-sourced.
 *
 * Unlike `cached()`, these primitives do NOT prefix keys with `cache:`. The
 * caller owns the namespace.
 */

interface KvGetOptions {
  /** If true (default), swallow Redis errors and return null. */
  swallowErrors?: boolean;
}

/**
 * Read a key. Returns `null` on miss, Redis error (when `swallowErrors`),
 * or stored null. Upstash auto-parses JSON values written via `kvSet`.
 */
export async function kvGet<T>(
  key: string,
  opts: KvGetOptions = {},
): Promise<T | null> {
  const swallow = opts.swallowErrors ?? true;
  try {
    const value = await redis.get<T>(key);
    return value ?? null;
  } catch (err) {
    if (!swallow) throw err;
    return null;
  }
}

interface KvSetOptions {
  /** TTL in seconds. */
  ttl: number;
  /** If true (default), swallow Redis errors. */
  swallowErrors?: boolean;
}

/**
 * Write a key with a TTL. The value is JSON-stringified for storage,
 * matching `cached()`'s wire format so `kvGet` (and `redis.get<T>`) parse
 * it back to its original shape.
 */
export async function kvSet<T>(
  key: string,
  value: T,
  opts: KvSetOptions,
): Promise<void> {
  const swallow = opts.swallowErrors ?? true;
  try {
    await redis.set(key, JSON.stringify(value), { ex: opts.ttl });
  } catch (err) {
    if (!swallow) throw err;
  }
}

interface KvDeleteOptions {
  /** If true (default), swallow Redis errors and return 0. */
  swallowErrors?: boolean;
}

/**
 * Delete one or more keys. Returns the count of keys actually removed
 * (0 if none existed or on swallowed error). Variadic-equivalent: pass
 * `[k1, k2, …]` to delete in a single round-trip.
 */
export async function kvDelete(
  keys: string | string[],
  opts: KvDeleteOptions = {},
): Promise<number> {
  const swallow = opts.swallowErrors ?? true;
  const list = Array.isArray(keys) ? keys : [keys];
  if (list.length === 0) return 0;
  try {
    return await redis.del(...list);
  } catch (err) {
    if (!swallow) throw err;
    return 0;
  }
}

/**
 * Multi-get a list of keys in one round-trip. Returns one entry per key
 * (same order); missing keys are `null`. Throws on Redis error so callers
 * with sweep semantics can decide whether to abort or continue.
 */
export async function kvMget<T>(keys: string[]): Promise<Array<T | null>> {
  if (keys.length === 0) return [];
  return (await redis.mget(...keys)) as Array<T | null>;
}

/**
 * One step of a SCAN iteration. Returns `[nextCursor, keys]`. Upstash
 * signals completion with cursor `"0"` (string). Throws on Redis error
 * so callers running namespaced sweeps decide whether to abort.
 *
 * `match` is passed through verbatim — the caller owns key-namespace
 * conventions (unlike `invalidatePattern`, which prepends `cache:`).
 */
export async function kvScan(
  cursor: string | number,
  opts: { match: string; count?: number },
): Promise<[string | number, string[]]> {
  return (await redis.scan(cursor, {
    match: opts.match,
    count: opts.count ?? 100,
  })) as [string | number, string[]];
}
