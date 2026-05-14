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
    mget: vi.fn(),
  },
}));

import { redis } from "@/lib/redis";
import {
  cached,
  invalidate,
  invalidatePattern,
  kvDelete,
  kvGet,
  kvMget,
  kvScan,
  kvSet,
} from "../cache";

const mockGet = redis.get as ReturnType<typeof vi.fn>;
const mockSet = redis.set as ReturnType<typeof vi.fn>;
const mockDel = redis.del as ReturnType<typeof vi.fn>;
const mockScan = redis.scan as ReturnType<typeof vi.fn>;
const mockMget = redis.mget as ReturnType<typeof vi.fn>;

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

describe("kvGet", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("passes the raw key through without `cache:` prefixing", async () => {
    mockGet.mockResolvedValue({ id: 1 });

    const value = await kvGet<{ id: number }>("session:tok-1");

    expect(value).toEqual({ id: 1 });
    expect(mockGet).toHaveBeenCalledWith("session:tok-1");
  });

  it("returns null on miss", async () => {
    mockGet.mockResolvedValue(null);

    const value = await kvGet("k");

    expect(value).toBeNull();
  });

  it("swallows Redis errors and returns null by default", async () => {
    mockGet.mockRejectedValue(new Error("Redis down"));

    const value = await kvGet("k");

    expect(value).toBeNull();
  });

  it("rethrows Redis errors when swallowErrors=false", async () => {
    mockGet.mockRejectedValue(new Error("Redis down"));

    await expect(kvGet("k", { swallowErrors: false })).rejects.toThrow(
      "Redis down",
    );
  });
});

describe("kvSet", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("JSON-stringifies the value and forwards the TTL as `ex`", async () => {
    mockSet.mockResolvedValue("OK");

    await kvSet("session:tok", { user: { id: "u1" } }, { ttl: 300 });

    expect(mockSet).toHaveBeenCalledWith(
      "session:tok",
      JSON.stringify({ user: { id: "u1" } }),
      { ex: 300 },
    );
  });

  it("swallows Redis errors by default", async () => {
    mockSet.mockRejectedValue(new Error("Redis write failed"));

    await expect(
      kvSet("k", { a: 1 }, { ttl: 60 }),
    ).resolves.toBeUndefined();
  });

  it("rethrows Redis errors when swallowErrors=false", async () => {
    mockSet.mockRejectedValue(new Error("Redis write failed"));

    await expect(
      kvSet("k", { a: 1 }, { ttl: 60, swallowErrors: false }),
    ).rejects.toThrow("Redis write failed");
  });
});

describe("kvDelete", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("deletes a single key and returns the count", async () => {
    mockDel.mockResolvedValue(1);

    const n = await kvDelete("session:tok");

    expect(n).toBe(1);
    expect(mockDel).toHaveBeenCalledWith("session:tok");
  });

  it("deletes a list of keys in one call (variadic spread)", async () => {
    mockDel.mockResolvedValue(3);

    const n = await kvDelete(["session:a", "session:b", "session:c"]);

    expect(n).toBe(3);
    expect(mockDel).toHaveBeenCalledWith("session:a", "session:b", "session:c");
  });

  it("returns 0 without touching Redis when the list is empty", async () => {
    const n = await kvDelete([]);

    expect(n).toBe(0);
    expect(mockDel).not.toHaveBeenCalled();
  });

  it("swallows Redis errors and returns 0 by default", async () => {
    mockDel.mockRejectedValue(new Error("Redis unavailable"));

    const n = await kvDelete("k");

    expect(n).toBe(0);
  });

  it("rethrows Redis errors when swallowErrors=false (sweep semantics)", async () => {
    mockDel.mockRejectedValue(new Error("Redis unavailable"));

    await expect(
      kvDelete(["a", "b"], { swallowErrors: false }),
    ).rejects.toThrow("Redis unavailable");
  });
});

describe("kvMget", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("returns parsed values in key order, with null for misses", async () => {
    mockMget.mockResolvedValue([{ id: 1 }, null, { id: 3 }]);

    const values = await kvMget<{ id: number }>(["a", "b", "c"]);

    expect(values).toEqual([{ id: 1 }, null, { id: 3 }]);
    expect(mockMget).toHaveBeenCalledWith("a", "b", "c");
  });

  it("returns [] without touching Redis when keys is empty", async () => {
    const values = await kvMget(["" as never].slice(0, 0));

    expect(values).toEqual([]);
    expect(mockMget).not.toHaveBeenCalled();
  });

  it("propagates Redis errors so callers can decide whether to abort", async () => {
    mockMget.mockRejectedValue(new Error("Redis down"));

    await expect(kvMget(["a"])).rejects.toThrow("Redis down");
  });
});

describe("kvScan", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("forwards the raw match (no `cache:` prefix) and returns [cursor, keys]", async () => {
    mockScan.mockResolvedValue(["42", ["session:a", "session:b"]]);

    const [cursor, keys] = await kvScan(0, { match: "session:*" });

    expect(cursor).toBe("42");
    expect(keys).toEqual(["session:a", "session:b"]);
    expect(mockScan).toHaveBeenCalledWith(0, {
      match: "session:*",
      count: 100,
    });
  });

  it("honours a custom count", async () => {
    mockScan.mockResolvedValue(["0", []]);

    await kvScan("5", { match: "session:*", count: 25 });

    expect(mockScan).toHaveBeenCalledWith("5", {
      match: "session:*",
      count: 25,
    });
  });

  it("propagates Redis errors so callers can decide whether to abort", async () => {
    mockScan.mockRejectedValue(new Error("Redis down"));

    await expect(kvScan(0, { match: "session:*" })).rejects.toThrow(
      "Redis down",
    );
  });
});
