import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => {
  const keys: string[] = [];
  const provider = {
    search: vi.fn(async () => ({ companies: [], totalCompanies: 0 })),
    listTopCompanies: vi.fn(async () => ({ companies: [], totalCompanies: 0 })),
  };
  return {
    keys,
    dbLoads: { count: 0 },
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
vi.mock("@/db", () => {
  mocks.dbLoads.count += 1;
  return { db: { execute: vi.fn() } };
});
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

import { listTopCompaniesAnonymous, listTopCompanies, searchJobs } from "../search";

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
    expect(mocks.dbLoads.count).toBe(0);
  });
});


describe("search enrichment propagation", () => {
  const params = { languages: ["en"], locale: "en", offset: 0, limit: 10 };

  it("separates omitted-count ranking from website cached results", async () => {
    await listTopCompanies({ ...params, includeYearCounts: false });
    await listTopCompaniesAnonymous(params);
    await listTopCompaniesAnonymous({ ...params, includeYearCounts: true });
    expect(mocks.keys).toEqual([
      "top-companies:v2:en:en:0:10:without-year-counts",
      "top-companies:v2:en:en:0:10",
      "top-companies:v2:en:en:0:10",
    ]);
    expect(mocks.provider.listTopCompanies).toHaveBeenNthCalledWith(1, {
      ...params, includeYearCounts: false,
    });
    expect(mocks.provider.listTopCompanies).toHaveBeenNthCalledWith(2, params);
  });

  it("forwards omitted-count keyword search without changing website defaults", async () => {
    const searchParams = { ...params, keywords: ["engineer"] };
    await searchJobs({ ...searchParams, includeYearCounts: false });
    await searchJobs(searchParams);
    expect(mocks.provider.search).toHaveBeenNthCalledWith(1, {
      ...searchParams, includeYearCounts: false,
    });
    expect(mocks.provider.search).toHaveBeenNthCalledWith(2, searchParams);
  });
});
