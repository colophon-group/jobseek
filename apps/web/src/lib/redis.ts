import "server-only";
import { Redis } from "@upstash/redis";

export interface RedisClientLike {
  get<T = unknown>(key: string): Promise<T | null>;
  set(
    key: string,
    value: string,
    options?: { ex?: number },
  ): Promise<unknown>;
  del(key: string): Promise<unknown>;
}

export const hasUpstashRedisConfig = Boolean(
  process.env.UPSTASH_REDIS_REST_URL &&
    process.env.UPSTASH_REDIS_REST_TOKEN,
);

const inMemoryStore = new Map<string, { value: string; expiresAt: number | null }>();

const inMemoryRedis: RedisClientLike = {
  async get<T = unknown>(key: string): Promise<T | null> {
    const entry = inMemoryStore.get(key);
    if (!entry) return null;
    if (entry.expiresAt !== null && entry.expiresAt <= Date.now()) {
      inMemoryStore.delete(key);
      return null;
    }
    try {
      return JSON.parse(entry.value) as T;
    } catch {
      return entry.value as T;
    }
  },
  async set(key: string, value: string, options?: { ex?: number }): Promise<"OK"> {
    const expiresAt =
      options?.ex && options.ex > 0
        ? Date.now() + options.ex * 1000
        : null;
    inMemoryStore.set(key, { value, expiresAt });
    return "OK";
  },
  async del(key: string): Promise<number> {
    return inMemoryStore.delete(key) ? 1 : 0;
  },
};

export const redis: RedisClientLike = hasUpstashRedisConfig
  ? new Redis({
      url: process.env.UPSTASH_REDIS_REST_URL!,
      token: process.env.UPSTASH_REDIS_REST_TOKEN!,
    })
  : inMemoryRedis;
