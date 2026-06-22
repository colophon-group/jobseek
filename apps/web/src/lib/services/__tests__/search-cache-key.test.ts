import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => {
  const keys: string[] = [];
  const provider = {
    listTopCompanies: vi.fn(async () => ({ companies: [], totalCompanies: 0 })),
  };
  return {
    keys,
    provider,
    cached: vi.fn(async (key: string, fetcher: () => Promise<unknown>) => {
      keys.push(key);
      return fetcher();
    }),
  };
});

vi.mock("server-only", () => ({}));
vi.mock("next/cache", () => ({
  cacheLife: vi.fn(),
}));
vi.mock("@/db", () => ({ db: { execute: vi.fn() } }));
vi.mock("drizzle-orm", () => ({ sql: vi.fn() }));
vi.mock("@/lib/cache", () => ({ cached: mocks.cached }));
vi.mock("@/lib/cache-ttl", () => ({
  CACHE_TTL_SHORT: 60,
  CACHE_TTL_MEDIUM: 300,
}));
vi.mock("@/lib/db-retry", () => ({
  withDbRetry: vi.fn((fn: () => Promise<unknown>) => fn()),
}));
vi.mock("@/lib/search", () => ({
  getSearchProvider: () => mocks.provider,
}));
vi.mock("@/lib/sessionCache", () => ({
  getSessionUserId: vi.fn(async () => null),
}));

import { listTopCompaniesAnonymous } from "../search";

beforeEach(() => {
  vi.clearAllMocks();
  mocks.keys.length = 0;
});

describe("search service cache keys", () => {
  it("versions anonymous top-company defaults so old ranking payloads cannot be reused", async () => {
    await listTopCompaniesAnonymous({
      languages: ["en"],
      locale: "en",
      offset: 0,
      limit: 10,
    });

    expect(mocks.keys).toEqual(["top-companies:v2:en:en:0:10"]);
  });
});
