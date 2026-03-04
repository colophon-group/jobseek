import { describe, it, expect, vi, beforeEach } from "vitest";

// Mock server-only to prevent import error
vi.mock("server-only", () => ({}));

// Mock Redis
vi.mock("@/lib/redis", () => ({
  redis: {
    get: vi.fn(),
    set: vi.fn(),
    del: vi.fn(),
  },
}));

import { redis } from "@/lib/redis";
import { cached, invalidate } from "../cache";

const mockGet = redis.get as ReturnType<typeof vi.fn>;
const mockSet = redis.set as ReturnType<typeof vi.fn>;
const mockDel = redis.del as ReturnType<typeof vi.fn>;

describe("cached", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("returns cached value on cache hit", async () => {
    mockGet.mockResolvedValue({ name: "cached-data" });
    const fetcher = vi.fn().mockResolvedValue({ name: "fresh-data" });

    const result = await cached("test-key", fetcher, { ttl: 60 });

    expect(result).toEqual({ name: "cached-data" });
    expect(mockGet).toHaveBeenCalledWith("cache:test-key");
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("calls fetcher and stores on cache miss", async () => {
    mockGet.mockResolvedValue(null);
    mockSet.mockResolvedValue("OK");
    const fetcher = vi.fn().mockResolvedValue({ name: "fresh-data" });

    const result = await cached("test-key", fetcher, { ttl: 120 });

    expect(result).toEqual({ name: "fresh-data" });
    expect(fetcher).toHaveBeenCalledOnce();
    expect(mockSet).toHaveBeenCalledWith(
      "cache:test-key",
      JSON.stringify({ name: "fresh-data" }),
      { ex: 120 },
    );
  });

  it("falls back to fetcher on Redis GET error", async () => {
    mockGet.mockRejectedValue(new Error("Redis connection failed"));
    mockSet.mockResolvedValue("OK");
    const fetcher = vi.fn().mockResolvedValue({ name: "fallback-data" });

    const result = await cached("test-key", fetcher, { ttl: 60 });

    expect(result).toEqual({ name: "fallback-data" });
    expect(fetcher).toHaveBeenCalledOnce();
  });

  it("still returns data on Redis SET error", async () => {
    mockGet.mockResolvedValue(null);
    mockSet.mockRejectedValue(new Error("Redis write failed"));
    const fetcher = vi.fn().mockResolvedValue({ name: "data" });

    const result = await cached("test-key", fetcher, { ttl: 60 });

    expect(result).toEqual({ name: "data" });
    expect(fetcher).toHaveBeenCalledOnce();
  });

  it("treats undefined cache value as miss", async () => {
    mockGet.mockResolvedValue(undefined);
    mockSet.mockResolvedValue("OK");
    const fetcher = vi.fn().mockResolvedValue("value");

    const result = await cached("key", fetcher, { ttl: 60 });

    expect(result).toBe("value");
    expect(fetcher).toHaveBeenCalledOnce();
  });
});

describe("invalidate", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("deletes the prefixed key", async () => {
    mockDel.mockResolvedValue(1);

    await invalidate("test-key");

    expect(mockDel).toHaveBeenCalledWith("cache:test-key");
  });

  it("does not throw on Redis error", async () => {
    mockDel.mockRejectedValue(new Error("Redis unavailable"));

    await expect(invalidate("test-key")).resolves.toBeUndefined();
  });
});
