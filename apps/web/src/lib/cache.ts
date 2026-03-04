import "server-only";
import { redis } from "@/lib/redis";

interface CacheOptions {
  /** TTL in seconds. */
  ttl: number;
}

/**
 * Redis-backed cache-aside wrapper.
 *
 * Shared across all Vercel serverless instances (unlike `unstable_cache`
 * which is per-process). Falls back to the fetcher on Redis errors.
 */
export async function cached<T>(
  key: string,
  fetcher: () => Promise<T>,
  options: CacheOptions,
): Promise<T> {
  const fullKey = `cache:${key}`;

  try {
    const hit = await redis.get<T>(fullKey);
    if (hit !== null && hit !== undefined) return hit;
  } catch {
    // Redis unavailable — fall through to fetcher
  }

  const data = await fetcher();

  try {
    await redis.set(fullKey, JSON.stringify(data), { ex: options.ttl });
  } catch {
    // Redis unavailable — data still returned from fetcher
  }

  return data;
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
