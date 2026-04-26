import { describe, it, expect, vi, beforeEach } from "vitest";

// Mock server-only to prevent import error
vi.mock("server-only", () => ({}));

// Mock Redis
vi.mock("@/lib/redis", () => ({
  redis: {
    get: vi.fn(),
    set: vi.fn(),
    del: vi.fn(),
    scan: vi.fn(),
  },
}));

import { redis } from "@/lib/redis";
import { cached, invalidate, invalidatePattern } from "../cache";

const mockGet = redis.get as ReturnType<typeof vi.fn>;
const mockSet = redis.set as ReturnType<typeof vi.fn>;
const mockDel = redis.del as ReturnType<typeof vi.fn>;
const mockScan = redis.scan as ReturnType<typeof vi.fn>;

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

describe("cached — single-flight stampede protection (#2676)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("collapses N concurrent identical fetches to one upstream call", async () => {
    /** Cold-start scenario: N concurrent requests for the same key all
     * miss the Redis cache. Without single-flight, all N would fan out to
     * the upstream. With single-flight, the first seeds an in-flight
     * promise and the rest await it. */
    mockGet.mockResolvedValue(null);
    mockSet.mockResolvedValue("OK");

    let resolveFetcher: ((value: { v: string }) => void) | undefined;
    const fetcher = vi.fn(
      () =>
        new Promise<{ v: string }>((res) => {
          resolveFetcher = res;
        }),
    );

    const callers = [
      cached("hot-key", fetcher, { ttl: 60 }),
      cached("hot-key", fetcher, { ttl: 60 }),
      cached("hot-key", fetcher, { ttl: 60 }),
      cached("hot-key", fetcher, { ttl: 60 }),
      cached("hot-key", fetcher, { ttl: 60 }),
    ];

    // Yield once so the cached() bodies progress past the await on
    // redis.get and seed the in-flight map.
    await Promise.resolve();

    expect(fetcher).toHaveBeenCalledTimes(1);

    resolveFetcher!({ v: "fresh" });
    const results = await Promise.all(callers);

    expect(results).toEqual([
      { v: "fresh" },
      { v: "fresh" },
      { v: "fresh" },
      { v: "fresh" },
      { v: "fresh" },
    ]);
    // SET is also single-flighted (only one writer).
    expect(mockSet).toHaveBeenCalledTimes(1);
  });

  it("releases the in-flight slot after success so the next call can start fresh", async () => {
    mockGet.mockResolvedValue(null);
    mockSet.mockResolvedValue("OK");
    const fetcher = vi.fn().mockResolvedValue("v1");

    await cached("k", fetcher, { ttl: 60 });
    // Simulate the next request after the first finished — fetcher should
    // run again because the in-flight slot was released.
    fetcher.mockResolvedValueOnce("v2");
    await cached("k", fetcher, { ttl: 60 });

    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("releases the in-flight slot after rejection so retry isn't permanently blocked", async () => {
    mockGet.mockResolvedValue(null);
    const failing = vi.fn().mockRejectedValue(new Error("upstream down"));

    await expect(
      cached("k", failing, { ttl: 60 }),
    ).rejects.toThrow("upstream down");

    // Subsequent caller against a recovered upstream must NOT be blocked
    // by a stale in-flight slot.
    const recovered = vi.fn().mockResolvedValue({ v: "ok" });
    mockSet.mockResolvedValue("OK");
    const result = await cached("k", recovered, { ttl: 60 });

    expect(result).toEqual({ v: "ok" });
    expect(recovered).toHaveBeenCalledOnce();
  });

  it("only collapses fetches with the same key", async () => {
    mockGet.mockResolvedValue(null);
    mockSet.mockResolvedValue("OK");

    const fetcherA = vi.fn().mockResolvedValue("a");
    const fetcherB = vi.fn().mockResolvedValue("b");

    const [a, b] = await Promise.all([
      cached("key-a", fetcherA, { ttl: 60 }),
      cached("key-b", fetcherB, { ttl: 60 }),
    ]);

    expect(a).toBe("a");
    expect(b).toBe("b");
    expect(fetcherA).toHaveBeenCalledOnce();
    expect(fetcherB).toHaveBeenCalledOnce();
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

describe("invalidatePattern", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("scans + deletes every key matching cache:<prefix>*", async () => {
    mockScan
      .mockResolvedValueOnce(["1", ["cache:loc-suggest:a", "cache:loc-suggest:b"]])
      .mockResolvedValueOnce(["0", ["cache:loc-suggest:c"]]);
    mockDel.mockResolvedValueOnce(2).mockResolvedValueOnce(1);

    const deleted = await invalidatePattern("loc-suggest:");

    expect(deleted).toBe(3);
    expect(mockScan).toHaveBeenCalledTimes(2);
    expect(mockScan.mock.calls[0][0]).toBe(0);
    expect(mockScan.mock.calls[0][1]).toEqual({
      match: "cache:loc-suggest:*",
      count: 100,
    });
    expect(mockScan.mock.calls[1][0]).toBe("1");
    expect(mockDel).toHaveBeenNthCalledWith(
      1,
      "cache:loc-suggest:a",
      "cache:loc-suggest:b",
    );
    expect(mockDel).toHaveBeenNthCalledWith(2, "cache:loc-suggest:c");
  });

  it("returns 0 when no keys match", async () => {
    mockScan.mockResolvedValueOnce(["0", []]);

    const deleted = await invalidatePattern("nonexistent:");

    expect(deleted).toBe(0);
    expect(mockDel).not.toHaveBeenCalled();
  });

  it("returns the partial count when SCAN errors mid-sweep", async () => {
    mockScan
      .mockResolvedValueOnce(["1", ["cache:x:a"]])
      .mockRejectedValueOnce(new Error("redis down"));
    mockDel.mockResolvedValueOnce(1);

    const deleted = await invalidatePattern("x:");

    expect(deleted).toBe(1);
  });
});
