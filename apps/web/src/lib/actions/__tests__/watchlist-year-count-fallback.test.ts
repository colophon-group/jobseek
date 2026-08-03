/**
 * Watchlist posting read degradation tests.
 *
 * The Supabase `job_posting` mirror is no longer a valid fallback read plane.
 * When Typesense is unavailable these readers fail closed to empty/zero data;
 * they must never expose stale crawler rows from Postgres (#6167/#6249).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

const mocks = vi.hoisted(() => {
  type SqlChunk = { text: string; values: unknown[] };

  function isSqlChunk(value: unknown): value is SqlChunk {
    return (
      typeof value === "object" &&
      value !== null &&
      "text" in value &&
      "values" in value
    );
  }

  const sqlTag = Object.assign(
    (strings: TemplateStringsArray, ...values: unknown[]): SqlChunk => {
      const chunk: SqlChunk = { text: "", values: [] };
      strings.forEach((part, index) => {
        chunk.text += part;
        if (index >= values.length) return;
        const value = values[index];
        if (isSqlChunk(value)) {
          chunk.text += value.text;
          chunk.values.push(...value.values);
        } else {
          chunk.text += "?";
          chunk.values.push(value);
        }
      });
      return chunk;
    },
    {
      join: (chunks: SqlChunk[], separator: SqlChunk): SqlChunk => ({
        text: chunks.map((chunk) => chunk.text).join(separator.text),
        values: chunks.flatMap((chunk, index) =>
          index === 0 ? chunk.values : [...separator.values, ...chunk.values],
        ),
      }),
    },
  );

  return {
    buildFilterString: vi.fn(),
    dbExecute: vi.fn(),
    expandLocationIdsBatch: vi.fn(),
    expandOccupationIdsBatch: vi.fn(),
    getSessionUserId: vi.fn(),
    getViewerLanguages: vi.fn(),
    isTypesenseUnavailableError: vi.fn(),
    localesOrNoneClause: vi.fn(),
    sqlTag,
    tsSearch: vi.fn(),
    withTypesenseRetry: vi.fn(),
    withDbRetry: vi.fn(),
  };
});

vi.mock("next/server", () => ({ after: (cb: () => unknown) => cb() }));
vi.mock("next/cache", () => ({ updateTag: vi.fn() }));

vi.mock("@/db", () => ({
  db: {
    execute: (...args: unknown[]) => mocks.dbExecute(...args),
  },
}));

vi.mock("@/db/schema", () => ({
  watchlist: {},
  watchlistCompany: {},
  company: {},
}));

vi.mock("drizzle-orm", () => ({
  sql: mocks.sqlTag,
  eq: (..._args: unknown[]) => ({ _isEq: true }),
  and: (..._args: unknown[]) => ({ _isAnd: true }),
}));

vi.mock("@/lib/actions/locations", () => ({
  expandLocationIdsBatch: mocks.expandLocationIdsBatch,
  resolveLocationSlugs: vi.fn().mockResolvedValue(new Map()),
}));

vi.mock("@/lib/actions/taxonomy", () => ({
  expandOccupationIdsBatch: mocks.expandOccupationIdsBatch,
  resolveOccupationSlugs: vi.fn().mockResolvedValue(new Map()),
  resolveSenioritySlugs: vi.fn().mockResolvedValue(new Map()),
  resolveTechnologySlugs: vi.fn().mockResolvedValue(new Map()),
}));

vi.mock("@/lib/services/taxonomy", () => ({
  expandOccupationIdsBatch: mocks.expandOccupationIdsBatch,
  resolveOccupationSlugs: vi.fn().mockResolvedValue(new Map()),
  resolveSenioritySlugs: vi.fn().mockResolvedValue(new Map()),
  resolveTechnologySlugs: vi.fn().mockResolvedValue(new Map()),
}));

vi.mock("@/lib/cache", () => ({
  cached: vi.fn((_key: string, factory: () => Promise<unknown>) => factory()),
  invalidate: vi.fn(),
  invalidatePattern: vi.fn(),
}));

vi.mock("@/lib/cache-tags", () => ({
  watchlistCacheTag: vi.fn(() => "watchlist:tag"),
}));

vi.mock("@/lib/cache-ttl", () => ({
  CACHE_TTL_SHORT: 60,
  CACHE_TTL_POPULAR: 120,
  CACHE_TTL_LONG: 3600,
}));

vi.mock("@/lib/db-retry", () => ({
  withDbRetry: vi.fn((fn: () => Promise<unknown>) => {
    mocks.withDbRetry();
    return fn();
  }),
}));

vi.mock("@/lib/indexnow", () => ({
  notifyIndexNow: vi.fn(),
  logIndexNowResult: vi.fn(),
}));

vi.mock("@/lib/plans", () => ({
  canCreateWatchlist: vi.fn().mockResolvedValue({ allowed: true }),
  getUserPlan: vi.fn().mockResolvedValue("free"),
  PLAN_LIMITS: { free: { canReceiveAlerts: false }, paid: { canReceiveAlerts: true } },
}));

vi.mock("@/lib/search/constants", () => ({
  ANON_MAX_WATCHLIST_POSTINGS: 50,
  COMPANY_BATCH_SIZE: 100,
}));

vi.mock("@/lib/search/pg-filters", () => ({
  localesOrNoneClause: mocks.localesOrNoneClause,
}));

vi.mock("@/lib/search/typesense-client", () => ({
  getSearchClient: () => ({
    collections: () => ({
      documents: () => ({
        search: mocks.tsSearch,
      }),
    }),
  }),
}));

vi.mock("@/lib/search/typesense-filters", () => ({
  buildFilterString: mocks.buildFilterString,
  POSTING_BASE_FILTER: "is_active:true && has_content:!=false",
  POSTING_FLOW_FILTER: "has_content:!=false",
}));

vi.mock("@/lib/search/typesense-retry", () => ({
  isTypesenseUnavailableError: mocks.isTypesenseUnavailableError,
  withTypesenseRetry: mocks.withTypesenseRetry,
}));

vi.mock("@/lib/search/typesense-watchlist", () => ({
  upsertWatchlist: vi.fn(),
  deleteWatchlist: vi.fn(),
  updateWatchlistField: vi.fn(),
}));

vi.mock("@/lib/sessionCache", () => ({
  getSessionUserId: mocks.getSessionUserId,
}));

vi.mock("@/lib/viewer", () => ({
  getViewerLanguages: mocks.getViewerLanguages,
}));

vi.mock("@/lib/watchlist-slug", () => ({
  generateUniqueSlug: vi.fn(),
  insertWatchlistWithUniqueSlug: vi.fn(),
}));

vi.mock("@/lib/watchlist-utils", () => ({
  isTrivialWatchlist: vi.fn(() => false),
  buildFilterCacheKey: vi.fn(() => "filters"),
}));

import {
  getPublicWatchlistPostings,
  getWatchlistPostingDisplayCounts,
  getWatchlistPostingYearCount,
  getWatchlistPostings,
} from "../watchlists";
import { typesenseQueryStringLength } from "@/lib/search/typesense-query-size";

function makeUuid(index: number): string {
  return `00000000-0000-0000-0000-${String(index).padStart(12, "0")}`;
}

function postingHit(id: string, textMatch: number, firstSeenAt: number) {
  return {
    text_match: textMatch,
    document: {
      id,
      title: `Posting ${id}`,
      source_url: `https://example.com/${id}`,
      first_seen_at: firstSeenAt,
      is_active: true,
      company_id: "company-1",
      company_name: "Example",
      company_slug: "example",
    },
  };
}

describe("watchlist posting read degradation (#6167)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-06-16T12:00:00.000Z"));
    mocks.buildFilterString.mockReturnValue("");
    mocks.expandLocationIdsBatch.mockResolvedValue([1, 10, 11]);
    mocks.expandOccupationIdsBatch.mockResolvedValue([2, 20]);
    mocks.getSessionUserId.mockResolvedValue(null);
    mocks.isTypesenseUnavailableError.mockImplementation((err: unknown) => {
      return (
        typeof err === "object" &&
        err !== null &&
        "code" in err &&
        (err as { code?: unknown }).code === "ECONNRESET"
      );
    });
    mocks.localesOrNoneClause.mockReturnValue(undefined);
    mocks.withTypesenseRetry.mockImplementation((fn: () => Promise<unknown>) => fn());
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("returns zero without querying Postgres when the year count is unavailable", async () => {
    const typesenseError = Object.assign(new Error("read ECONNRESET"), {
      code: "ECONNRESET",
    });
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    mocks.tsSearch.mockRejectedValueOnce(typesenseError);

    const count = await getWatchlistPostingYearCount({
      companyIds: ["11111111-1111-1111-1111-111111111111"],
      keywords: ["Staff Engineer"],
      locationIds: [1],
      occupationIds: [2],
      seniorityIds: [3],
      technologyIds: [4],
      workMode: ["remote"],
      employmentType: ["full_time"],
      salaryMin: 100000,
      salaryMax: 200000,
      experienceMin: 3,
      experienceMax: 7,
    });

    expect(count).toBe(0);
    expect(mocks.withTypesenseRetry).toHaveBeenCalledTimes(1);
    expect(mocks.tsSearch).toHaveBeenCalledTimes(1);
    expect(mocks.isTypesenseUnavailableError).toHaveBeenCalledWith(typesenseError);
    expect(mocks.expandLocationIdsBatch).not.toHaveBeenCalled();
    expect(mocks.expandOccupationIdsBatch).not.toHaveBeenCalled();
    expect(mocks.withDbRetry).not.toHaveBeenCalled();
    expect(mocks.dbExecute).not.toHaveBeenCalled();
    expect(consoleError).toHaveBeenCalledWith(
      "external_client_error",
      expect.objectContaining({
        service: "typesense",
        operation: "watchlist_posting_year_count",
        code: "ECONNRESET",
      }),
    );
  });

  it("returns an empty authenticated posting page without querying Postgres on outage", async () => {
    const typesenseError = Object.assign(new Error("read ECONNRESET"), {
      code: "ECONNRESET",
    });
    vi.spyOn(console, "error").mockImplementation(() => {});
    mocks.getSessionUserId.mockResolvedValueOnce("user-1");
    mocks.tsSearch.mockRejectedValueOnce(typesenseError);

    const result = await getWatchlistPostings({
      companyIds: ["11111111-1111-1111-1111-111111111111"],
      offset: 40,
      limit: 20,
    });

    expect(result).toEqual({ postings: [], total: 0 });
    expect(mocks.getSessionUserId).toHaveBeenCalledTimes(1);
    expect(mocks.isTypesenseUnavailableError).toHaveBeenCalledWith(typesenseError);
    expect(mocks.dbExecute).not.toHaveBeenCalled();
    expect(mocks.withDbRetry).not.toHaveBeenCalled();
  });

  it("returns an empty session-free public posting page on outage", async () => {
    const typesenseError = Object.assign(new Error("read ECONNRESET"), {
      code: "ECONNRESET",
    });
    vi.spyOn(console, "error").mockImplementation(() => {});
    mocks.tsSearch.mockRejectedValueOnce(typesenseError);

    const result = await getPublicWatchlistPostings({
      companyIds: ["11111111-1111-1111-1111-111111111111"],
      offset: 40,
      limit: 20,
    });

    expect(result).toEqual({ postings: [], total: 0, truncated: true });
    expect(mocks.getSessionUserId).not.toHaveBeenCalled();
    expect(mocks.isTypesenseUnavailableError).toHaveBeenCalledWith(typesenseError);
    expect(mocks.dbExecute).not.toHaveBeenCalled();
    expect(mocks.withDbRetry).not.toHaveBeenCalled();
  });

  it("does not reroute Typesense 429 rate limits to Postgres", async () => {
    const rateLimitError = Object.assign(new Error("Too Many Requests"), {
      httpStatus: 429,
    });
    mocks.tsSearch.mockRejectedValueOnce(rateLimitError);

    await expect(
      getWatchlistPostingYearCount({
        companyIds: ["11111111-1111-1111-1111-111111111111"],
      }),
    ).rejects.toBe(rateLimitError);

    expect(mocks.withTypesenseRetry).toHaveBeenCalledTimes(1);
    expect(mocks.isTypesenseUnavailableError).toHaveBeenCalledWith(rateLimitError);
    expect(mocks.dbExecute).not.toHaveBeenCalled();
    expect(mocks.expandLocationIdsBatch).not.toHaveBeenCalled();
    expect(mocks.expandOccupationIdsBatch).not.toHaveBeenCalled();
  });

  it("does not reroute active-posting Typesense 429s to Postgres", async () => {
    const rateLimitError = Object.assign(new Error("Too Many Requests"), {
      httpStatus: 429,
    });
    mocks.tsSearch.mockRejectedValueOnce(rateLimitError);

    await expect(
      getWatchlistPostings({
        companyIds: ["11111111-1111-1111-1111-111111111111"],
        offset: 0,
        limit: 20,
      }),
    ).rejects.toBe(rateLimitError);

    expect(mocks.withTypesenseRetry).toHaveBeenCalledTimes(1);
    expect(mocks.isTypesenseUnavailableError).toHaveBeenCalledWith(rateLimitError);
    expect(mocks.dbExecute).not.toHaveBeenCalled();
  });

  it("keeps cached public posting snapshots session-free (#5980)", async () => {
    mocks.tsSearch.mockResolvedValue({ found: 0, hits: [] });

    const result = await getPublicWatchlistPostings({
      companyIds: ["11111111-1111-1111-1111-111111111111"],
      offset: 0,
      limit: 20,
    });

    expect(result).toEqual({ postings: [], total: 0 });
    expect(mocks.getSessionUserId).not.toHaveBeenCalled();
    expect(mocks.tsSearch).toHaveBeenCalledTimes(1);
  });

  it("batches active posting queries before the Typesense GET limit (#3477)", async () => {
    const companyIds = Array.from({ length: 99 }, (_, i) => makeUuid(i + 1));
    mocks.tsSearch.mockResolvedValue({ found: 0, hits: [] });

    const result = await getWatchlistPostings({
      companyIds,
      offset: 0,
      limit: 20,
    });

    expect(result).toEqual({ postings: [], total: 0 });
    expect(mocks.tsSearch.mock.calls.length).toBeGreaterThan(1);
    for (const [params] of mocks.tsSearch.mock.calls) {
      expect(typesenseQueryStringLength(params)).toBeLessThan(4000);
      const filter = (params as { filter_by?: string }).filter_by ?? "";
      const idsInFilter = filter.match(/[0-9a-f-]{36}/g) ?? [];
      expect(idsInFilter.length).toBeLessThan(companyIds.length);
    }
    expect(mocks.dbExecute).not.toHaveBeenCalled();
  });

  it("preserves global keyword relevance and freshness across company batches", async () => {
    const companyIds = Array.from({ length: 101 }, (_, i) => makeUuid(i + 1));
    let rowBatch = 0;
    mocks.getSessionUserId.mockResolvedValueOnce("user-1");
    mocks.tsSearch.mockImplementation((params: { per_page?: number }) => {
      if (params.per_page === 0) return { found: 2, hits: [] };
      rowBatch += 1;
      if (rowBatch === 1) {
        return {
          found: 2,
          hits: [
            postingHit("best", 100, 100),
            postingHit("older-tie", 80, 300),
          ],
        };
      }
      if (rowBatch === 2) {
        return {
          found: 2,
          hits: [
            postingHit("second-best", 90, 50),
            postingHit("newer-tie", 80, 400),
          ],
        };
      }
      return { found: 0, hits: [] };
    });

    const result = await getWatchlistPostings({
      companyIds,
      keywords: ["staff", "engineer"],
      offset: 1,
      limit: 2,
    });

    expect(rowBatch).toBeGreaterThan(1);
    expect(result.postings.map((posting) => posting.id)).toEqual([
      "second-best",
      "newer-tie",
    ]);
    const rowQueries = mocks.tsSearch.mock.calls
      .map(([params]) => params as { per_page?: number; page?: number; sort_by?: string })
      .filter((params) => params.per_page !== 0);
    expect(rowQueries.length).toBeGreaterThan(1);
    expect(rowQueries).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          per_page: 3,
          page: 1,
          sort_by: "_text_match:desc,first_seen_at:desc",
        }),
      ]),
    );
    expect(mocks.dbExecute).not.toHaveBeenCalled();
  });

  it("batches year-count queries instead of returning zero for large watchlists (#3477)", async () => {
    const companyIds = Array.from({ length: 99 }, (_, i) => makeUuid(i + 1));
    mocks.tsSearch.mockResolvedValue({ found: 5, hits: [] });

    const count = await getWatchlistPostingYearCount({
      companyIds,
    });

    expect(mocks.tsSearch.mock.calls.length).toBeGreaterThan(1);
    expect(count).toBe(mocks.tsSearch.mock.calls.length * 5);
    for (const [params] of mocks.tsSearch.mock.calls) {
      expect(typesenseQueryStringLength(params)).toBeLessThan(4000);
      const filter = (params as { filter_by?: string }).filter_by ?? "";
      const idsInFilter = filter.match(/[0-9a-f-]{36}/g) ?? [];
      expect(idsInFilter.length).toBeLessThan(companyIds.length);
    }
    expect(mocks.dbExecute).not.toHaveBeenCalled();
  });

  it("includes work mode and employment type in SSR active and year counts", async () => {
    const combinedFilter =
      "location_types:[remote] && employment_type:[full_time]";
    mocks.buildFilterString.mockReturnValueOnce(combinedFilter);
    mocks.tsSearch
      .mockResolvedValueOnce({ found: 11, hits: [] })
      .mockResolvedValueOnce({ found: 27, hits: [] });

    const result = await getWatchlistPostingDisplayCounts({
      id: "watchlist-1",
      slug: "remote-full-time",
      title: "Remote full-time",
      description: null,
      isPublic: true,
      alertsEnabled: false,
      filters: {
        workMode: ["remote"],
        employmentType: ["full_time"],
      },
      sourceWatchlistId: null,
      createdAt: "2026-01-01T00:00:00.000Z",
      owner: {
        id: "user-1",
        username: "alice",
        displayUsername: null,
        name: "Alice",
      },
      companies: [
        {
          id: "11111111-1111-1111-1111-111111111111",
          name: "Example",
          slug: "example",
          icon: null,
        },
      ],
    });

    expect(result).toEqual({ activeJobs: 11, yearJobs: 27 });
    expect(mocks.buildFilterString).toHaveBeenCalledWith(
      expect.objectContaining({
        workMode: ["remote"],
        employmentTypes: ["full_time"],
      }),
    );
    expect(mocks.tsSearch).toHaveBeenCalledTimes(2);
    for (const [params] of mocks.tsSearch.mock.calls) {
      expect((params as { filter_by: string }).filter_by).toContain(combinedFilter);
    }
  });
});
