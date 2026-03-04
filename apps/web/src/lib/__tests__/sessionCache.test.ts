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

import { redis } from "@/lib/redis";
import { getSession, getSessionUserId, invalidateSessionCache } from "../sessionCache";

const mockRedisGet = redis.get as ReturnType<typeof vi.fn>;
const mockRedisSet = redis.set as ReturnType<typeof vi.fn>;
const mockRedisDel = redis.del as ReturnType<typeof vi.fn>;

describe("getSession", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("returns null when no session cookie is present", async () => {
    mockHeadersGet.mockReturnValue("");
    const result = await getSession();
    expect(result).toBeNull();
  });

  it("returns cached session from Redis on cache hit", async () => {
    const sessionData = { user: { id: "u1", name: "Test" }, session: { id: "s1" } };
    mockHeadersGet.mockImplementation((name: string) => {
      if (name === "cookie") return "better-auth.session_token=abc123";
      return null;
    });
    mockRedisGet.mockResolvedValue(sessionData);

    const result = await getSession();
    expect(result).toEqual(sessionData);
    expect(mockRedisGet).toHaveBeenCalledWith("session:abc123");
    expect(mockGetSession).not.toHaveBeenCalled();
  });

  it("falls back to DB on Redis cache miss", async () => {
    const sessionData = { user: { id: "u1", name: "Test" }, session: { id: "s1" } };
    mockHeadersGet.mockImplementation((name: string) => {
      if (name === "cookie") return "better-auth.session_token=token456";
      return null;
    });
    mockRedisGet.mockResolvedValue(null);
    mockRedisSet.mockResolvedValue("OK");
    mockGetSession.mockResolvedValue(sessionData);

    const result = await getSession();
    expect(result).toEqual(sessionData);
    expect(mockGetSession).toHaveBeenCalled();
    expect(mockRedisSet).toHaveBeenCalledWith(
      "session:token456",
      JSON.stringify(sessionData),
      { ex: 300 },
    );
  });

  it("handles __Secure- prefixed cookie name", async () => {
    const sessionData = { user: { id: "u2" }, session: { id: "s2" } };
    mockHeadersGet.mockImplementation((name: string) => {
      if (name === "cookie") return "__Secure-better-auth.session_token=securetoken";
      return null;
    });
    mockRedisGet.mockResolvedValue(sessionData);

    const result = await getSession();
    expect(result).toEqual(sessionData);
    expect(mockRedisGet).toHaveBeenCalledWith("session:securetoken");
  });

  it("falls back to DB when Redis GET fails", async () => {
    const sessionData = { user: { id: "u1" }, session: { id: "s1" } };
    mockHeadersGet.mockImplementation((name: string) => {
      if (name === "cookie") return "better-auth.session_token=tok";
      return null;
    });
    mockRedisGet.mockRejectedValue(new Error("Redis down"));
    mockRedisSet.mockResolvedValue("OK");
    mockGetSession.mockResolvedValue(sessionData);

    const result = await getSession();
    expect(result).toEqual(sessionData);
    expect(mockGetSession).toHaveBeenCalled();
  });

  it("returns null when auth returns no session", async () => {
    mockHeadersGet.mockImplementation((name: string) => {
      if (name === "cookie") return "better-auth.session_token=invalid";
      return null;
    });
    mockRedisGet.mockResolvedValue(null);
    mockGetSession.mockResolvedValue(null);

    const result = await getSession();
    expect(result).toBeNull();
    // Should not attempt to cache null result
    expect(mockRedisSet).not.toHaveBeenCalled();
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
    mockRedisGet.mockResolvedValue({ user: { id: "user-42" }, session: { id: "s1" } });

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

  it("deletes session key from Redis", async () => {
    mockRedisDel.mockResolvedValue(1);

    await invalidateSessionCache("my-token");
    expect(mockRedisDel).toHaveBeenCalledWith("session:my-token");
  });

  it("does not throw on Redis error", async () => {
    mockRedisDel.mockRejectedValue(new Error("Redis unavailable"));

    await expect(invalidateSessionCache("my-token")).resolves.toBeUndefined();
  });
});
