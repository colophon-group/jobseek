import { describe, it, expect, vi, beforeEach } from "vitest";

// Mock server-only to prevent import error
vi.mock("server-only", () => ({}));

// Mock the cache façade. sessionCache no longer talks to `@/lib/redis`
// directly — it uses the kv* primitives exported by `@/lib/cache`.
vi.mock("@/lib/cache", () => ({
  kvGet: vi.fn(),
  kvSet: vi.fn(),
  kvDelete: vi.fn(),
  kvScan: vi.fn(),
  kvMget: vi.fn(),
}));

// Mock next/headers
const mockHeadersGet = vi.fn();
vi.mock("next/headers", () => ({
  headers: vi.fn().mockResolvedValue({
    get: (...args: unknown[]) => mockHeadersGet(...args),
  }),
  cookies: vi.fn(),
}));

// Mock Better Auth
const mockGetSession = vi.fn();
vi.mock("@/lib/auth", () => ({
  auth: {
    api: {
      getSession: (...args: unknown[]) => mockGetSession(...args),
    },
  },
}));

// Mock react cache to pass through
vi.mock("react", () => ({
  cache: (fn: (...args: unknown[]) => unknown) => fn,
}));

import { kvDelete, kvGet, kvMget, kvScan, kvSet } from "@/lib/cache";
import {
  getSession,
  getSessionUserId,
  invalidateSessionCache,
  invalidateAllUserSessionCacheEntries,
} from "../sessionCache";

const mockKvGet = kvGet as ReturnType<typeof vi.fn>;
const mockKvSet = kvSet as ReturnType<typeof vi.fn>;
const mockKvDelete = kvDelete as ReturnType<typeof vi.fn>;
const mockKvScan = kvScan as ReturnType<typeof vi.fn>;
const mockKvMget = kvMget as ReturnType<typeof vi.fn>;

describe("getSession", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("returns null when no session cookie is present", async () => {
    mockHeadersGet.mockReturnValue("");
    const result = await getSession();
    expect(result).toBeNull();
    // No cache lookup attempted without a token.
    expect(mockKvGet).not.toHaveBeenCalled();
  });

  it("returns cached session from Redis on cache hit", async () => {
    const sessionData = { user: { id: "u1", name: "Test" }, session: { id: "s1" } };
    mockHeadersGet.mockImplementation((name: string) => {
      if (name === "cookie") return "better-auth.session_token=abc123";
      return null;
    });
    mockKvGet.mockResolvedValue(sessionData);

    const result = await getSession();
    expect(result).toEqual(sessionData);
    // Key shape preserved: raw `session:<token>` (no `cache:` prefix).
    expect(mockKvGet).toHaveBeenCalledWith("session:abc123");
    expect(mockGetSession).not.toHaveBeenCalled();
  });

  it("falls back to DB on Redis cache miss and writes through with the session TTL", async () => {
    const sessionData = { user: { id: "u1", name: "Test" }, session: { id: "s1" } };
    mockHeadersGet.mockImplementation((name: string) => {
      if (name === "cookie") return "better-auth.session_token=token456";
      return null;
    });
    mockKvGet.mockResolvedValue(null);
    mockKvSet.mockResolvedValue(undefined);
    mockGetSession.mockResolvedValue(sessionData);

    const result = await getSession();
    expect(result).toEqual(sessionData);
    expect(mockGetSession).toHaveBeenCalled();
    // kvSet receives the structured value (not pre-JSON-stringified) and
    // the session TTL.
    expect(mockKvSet).toHaveBeenCalledWith(
      "session:token456",
      sessionData,
      { ttl: 300 },
    );
  });

  it("handles __Secure- prefixed cookie name", async () => {
    const sessionData = { user: { id: "u2" }, session: { id: "s2" } };
    mockHeadersGet.mockImplementation((name: string) => {
      if (name === "cookie") return "__Secure-better-auth.session_token=securetoken";
      return null;
    });
    mockKvGet.mockResolvedValue(sessionData);

    const result = await getSession();
    expect(result).toEqual(sessionData);
    expect(mockKvGet).toHaveBeenCalledWith("session:securetoken");
  });

  it("falls back to DB when Redis GET swallows an error and returns null", async () => {
    // kvGet swallows Redis errors by default — sessionCache sees a clean
    // null and proceeds to fetch from Better Auth.
    const sessionData = { user: { id: "u1" }, session: { id: "s1" } };
    mockHeadersGet.mockImplementation((name: string) => {
      if (name === "cookie") return "better-auth.session_token=tok";
      return null;
    });
    mockKvGet.mockResolvedValue(null);
    mockKvSet.mockResolvedValue(undefined);
    mockGetSession.mockResolvedValue(sessionData);

    const result = await getSession();
    expect(result).toEqual(sessionData);
    expect(mockGetSession).toHaveBeenCalled();
  });

  it("returns null when auth returns no session and does not cache the null", async () => {
    mockHeadersGet.mockImplementation((name: string) => {
      if (name === "cookie") return "better-auth.session_token=invalid";
      return null;
    });
    mockKvGet.mockResolvedValue(null);
    mockGetSession.mockResolvedValue(null);

    const result = await getSession();
    expect(result).toBeNull();
    // Null sentinels: a null DB result must NOT poison the cache — a
    // cached `null` would be indistinguishable from a miss (kvGet treats
    // null as "no value") and the DB call would repeat on every request.
    expect(mockKvSet).not.toHaveBeenCalled();
  });
});

describe("getSessionUserId", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("returns userId when session exists", async () => {
    mockHeadersGet.mockImplementation((name: string) => {
      if (name === "cookie") return "better-auth.session_token=tok";
      return null;
    });
    mockKvGet.mockResolvedValue({ user: { id: "user-42" }, session: { id: "s1" } });

    const result = await getSessionUserId();
    expect(result).toBe("user-42");
  });

  it("returns null when no session", async () => {
    mockHeadersGet.mockReturnValue("");

    const result = await getSessionUserId();
    expect(result).toBeNull();
  });
});

describe("invalidateSessionCache", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("deletes session key via the cache façade", async () => {
    mockKvDelete.mockResolvedValue(1);

    await invalidateSessionCache("my-token");
    // Real DEL (not stale-mark): kvDelete maps to `redis.del`.
    expect(mockKvDelete).toHaveBeenCalledWith("session:my-token");
  });

  it("does not throw when the façade reports an error", async () => {
    // kvDelete swallows by default; this asserts the contract that
    // invalidate is best-effort and never propagates.
    mockKvDelete.mockResolvedValue(0);

    await expect(invalidateSessionCache("my-token")).resolves.toBeUndefined();
  });

  it("next getSession() after invalidate misses Redis and refetches from DB", async () => {
    // Eviction contract: a true DEL (not stale-mark). The next read must
    // see a miss (kvGet -> null) and fall through to `auth.api.getSession`.
    const refreshed = { user: { id: "u-refreshed" }, session: { id: "s2" } };
    mockHeadersGet.mockImplementation((name: string) => {
      if (name === "cookie") return "better-auth.session_token=evicted-tok";
      return null;
    });
    // Step 1: invalidate the cache key. kvDelete returns the number of
    // keys removed (1 if present).
    mockKvDelete.mockResolvedValue(1);
    await invalidateSessionCache("evicted-tok");
    expect(mockKvDelete).toHaveBeenCalledWith("session:evicted-tok");

    // Step 2: next getSession() sees a miss and refetches.
    mockKvGet.mockResolvedValue(null);
    mockKvSet.mockResolvedValue(undefined);
    mockGetSession.mockResolvedValue(refreshed);

    const result = await getSession();

    expect(result).toEqual(refreshed);
    expect(mockKvGet).toHaveBeenCalledWith("session:evicted-tok");
    expect(mockGetSession).toHaveBeenCalled();
    // Write-through restores the cache for subsequent requests.
    expect(mockKvSet).toHaveBeenCalledWith(
      "session:evicted-tok",
      refreshed,
      { ttl: 300 },
    );
  });
});

describe("invalidateAllUserSessionCacheEntries", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("scans session:* and deletes only entries whose user.id matches", async () => {
    // Two scan pages exercise the cursor loop AND the per-page filter.
    // Cursor "0" (Upstash returns string) terminates iteration.
    mockKvScan
      .mockResolvedValueOnce([
        "42",
        ["session:cookie-a", "session:cookie-b", "session:cookie-c"],
      ])
      .mockResolvedValueOnce(["0", ["session:cookie-d"]]);
    mockKvMget
      .mockResolvedValueOnce([
        { user: { id: "target" }, session: { token: "t-a" } }, // match
        { user: { id: "other" }, session: { token: "t-b" } }, // skip
        { user: { id: "target" }, session: { token: "t-c" } }, // match
      ])
      .mockResolvedValueOnce([
        { user: { id: "target" }, session: { token: "t-d" } }, // match
      ]);
    mockKvDelete.mockResolvedValueOnce(2).mockResolvedValueOnce(1);

    const deleted = await invalidateAllUserSessionCacheEntries("target");

    expect(deleted).toBe(3);
    expect(mockKvScan).toHaveBeenCalledTimes(2);
    expect(mockKvScan.mock.calls[0][0]).toBe(0);
    expect(mockKvScan.mock.calls[0][1]).toEqual({
      match: "session:*",
      count: 100,
    });
    expect(mockKvScan.mock.calls[1][0]).toBe("42");
    // First DEL gets the two matches from page 1; second gets the
    // single match from page 2. The non-target row is NOT included.
    // Sweep semantics rethrow on transport error (swallowErrors: false).
    expect(mockKvDelete).toHaveBeenNthCalledWith(
      1,
      ["session:cookie-a", "session:cookie-c"],
      { swallowErrors: false },
    );
    expect(mockKvDelete).toHaveBeenNthCalledWith(
      2,
      ["session:cookie-d"],
      { swallowErrors: false },
    );
  });

  it("returns 0 when no keys match the namespace", async () => {
    mockKvScan.mockResolvedValueOnce(["0", []]);

    const deleted = await invalidateAllUserSessionCacheEntries("target");

    expect(deleted).toBe(0);
    expect(mockKvMget).not.toHaveBeenCalled();
    expect(mockKvDelete).not.toHaveBeenCalled();
  });

  it("returns 0 when the page contains only non-matching users", async () => {
    mockKvScan.mockResolvedValueOnce([
      "0",
      ["session:k1", "session:k2"],
    ]);
    mockKvMget.mockResolvedValueOnce([
      { user: { id: "u-other" } },
      { user: { id: "u-other" } },
    ]);

    const deleted = await invalidateAllUserSessionCacheEntries("target");

    expect(deleted).toBe(0);
    expect(mockKvDelete).not.toHaveBeenCalled();
  });

  it("tolerates malformed payloads (null entry, missing user.id)", async () => {
    mockKvScan.mockResolvedValueOnce([
      "0",
      ["session:k1", "session:k2", "session:k3"],
    ]);
    mockKvMget.mockResolvedValueOnce([
      null,
      { user: null },
      {} as unknown,
    ]);

    const deleted = await invalidateAllUserSessionCacheEntries("target");

    expect(deleted).toBe(0);
    expect(mockKvDelete).not.toHaveBeenCalled();
  });

  it("logs and returns the partial count when SCAN errors mid-sweep", async () => {
    mockKvScan
      .mockResolvedValueOnce(["1", ["session:k1"]])
      .mockRejectedValueOnce(new Error("redis down"));
    mockKvMget.mockResolvedValueOnce([{ user: { id: "target" } }]);
    mockKvDelete.mockResolvedValueOnce(1);
    const errSpy = vi.spyOn(console, "error").mockImplementation(() => {});

    const deleted = await invalidateAllUserSessionCacheEntries("target");

    expect(deleted).toBe(1);
    expect(errSpy).toHaveBeenCalled();
    errSpy.mockRestore();
  });
});
