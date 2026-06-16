/**
 * Regression test for issue #3056.
 *
 * `getWatchlistPostingYearCount` feeds the "N active · M in the last year"
 * row on watchlist detail pages. The active count already falls back to
 * Postgres when Typesense is unavailable; the year count used to catch
 * the same outage and return 0, producing misleading "0 in the last year"
 * stats for watchlists that still had matching postings in Postgres.
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
  getWatchlistPostingYearCount,
  getWatchlistPostings,
} from "../watchlists";

describe("getWatchlistPostingYearCount fallback (#3056)", () => {
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

  it("falls back to a filtered Postgres year count when Typesense fails", async () => {
    const typesenseError = Object.assign(new Error("read ECONNRESET"), {
      code: "ECONNRESET",
    });
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    mocks.tsSearch.mockRejectedValueOnce(typesenseError);
    mocks.dbExecute.mockResolvedValueOnce([{ cnt: 37 }]);

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

    expect(count).toBe(37);
    expect(mocks.withTypesenseRetry).toHaveBeenCalledTimes(1);
    expect(mocks.tsSearch).toHaveBeenCalledTimes(1);
    expect(mocks.isTypesenseUnavailableError).toHaveBeenCalledWith(typesenseError);
    expect(mocks.expandLocationIdsBatch).toHaveBeenCalledWith([1]);
    expect(mocks.expandOccupationIdsBatch).toHaveBeenCalledWith([2]);
    expect(mocks.withDbRetry).toHaveBeenCalledTimes(1);
    expect(mocks.dbExecute).toHaveBeenCalledTimes(1);
    expect(consoleError).toHaveBeenCalledWith(
      "[getWatchlistPostingYearCount] Typesense failed, falling back to Postgres",
      typesenseError,
    );

    const query = mocks.dbExecute.mock.calls[0]?.[0] as {
      text: string;
      values: unknown[];
    };
    expect(query.text).toContain("SELECT count(*)::int AS cnt FROM job_posting jp WHERE");
    expect(query.text).toContain("jp.description_r2_hash IS NOT NULL");
    expect(query.text).toContain("jp.first_seen_at >=");
    expect(query.text).toContain("jp.company_id = ANY");
    expect(query.text).toContain("jp.location_ids &&");
    expect(query.text).toContain("jp.occupation_id = ANY");
    expect(query.text).toContain("jp.salary_eur BETWEEN");
    expect(query.text).not.toContain("jp.is_active = true");
    expect(
      query.values.some(
        (value) =>
          value instanceof Date &&
          value.toISOString() === "2025-06-16T12:00:00.000Z",
      ),
    ).toBe(true);
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
});
