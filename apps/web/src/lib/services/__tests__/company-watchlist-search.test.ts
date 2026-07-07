import { beforeEach, describe, expect, it, vi } from "vitest";
import { withTestEnv } from "@/test-utils/env";

const mocks = vi.hoisted(() => ({
  cacheLife: vi.fn(),
  cacheTag: vi.fn(),
  cached: vi.fn(
    (_key: string, fetcher: () => Promise<unknown>, _options: unknown) => fetcher(),
  ),
  search: vi.fn(),
  dbExecute: vi.fn(),
  buildFilterString: vi.fn(() => ""),
}));

vi.mock("server-only", () => ({}));
vi.mock("next/cache", () => ({
  cacheLife: mocks.cacheLife,
  cacheTag: mocks.cacheTag,
}));
vi.mock("@/lib/cache", () => ({
  cached: mocks.cached,
}));
vi.mock("@/lib/search/typesense-client", () => ({
  getSearchClient: () => ({
    collections: () => ({ documents: () => ({ search: mocks.search }) }),
  }),
}));
vi.mock("@/db", () => ({ db: { execute: mocks.dbExecute } }));
vi.mock("drizzle-orm", () => ({
  sql: (strings: TemplateStringsArray, ..._values: unknown[]) =>
    strings.join("?"),
}));
vi.mock("@/lib/sessionCache", () => ({ getSessionUserId: vi.fn() }));
vi.mock("@/lib/services/locations", () => ({
  expandLocationIds: vi.fn(),
  expandLocationIdsBatch: vi.fn().mockResolvedValue([]),
}));
vi.mock("@/lib/services/taxonomy", () => ({
  expandOccupationIds: vi.fn(),
  expandOccupationIdsBatch: vi.fn().mockResolvedValue([]),
}));
vi.mock("@/lib/search", () => ({ getSearchProvider: vi.fn() }));
vi.mock("@/lib/search/constants", () => ({
  ANON_MAX_COMPANIES: 5,
  ANON_MAX_POSTINGS: 10,
}));
vi.mock("@/lib/search/typesense-filters", () => ({
  POSTING_BASE_FILTER: "is_active:true && has_content:!=false",
  buildFilterString: mocks.buildFilterString,
}));
vi.mock("@/lib/search/typesense-retry", () => ({
  isRetryableError: (err: unknown) =>
    typeof err === "object" &&
    err !== null &&
    "code" in err &&
    (err as { code?: unknown }).code === "ECONNRESET",
  isTypesenseRateLimitError: (err: unknown) =>
    typeof err === "object" &&
    err !== null &&
    (
      ("httpStatus" in err && (err as { httpStatus?: unknown }).httpStatus === 429) ||
      ("message" in err &&
        typeof (err as { message?: unknown }).message === "string" &&
        (err as { message: string }).message.includes("HTTP code 429"))
    ),
  isTypesenseUnavailableError: (err: unknown) =>
    typeof err === "object" &&
    err !== null &&
    (
      ("code" in err && (err as { code?: unknown }).code === "ECONNRESET") ||
      ("message" in err &&
        typeof (err as { message?: unknown }).message === "string" &&
        (err as { message: string }).message.includes("TYPESENSE_SEARCH_KEY"))
    ),
  withTypesenseRetry: (fn: () => Promise<unknown>) => fn(),
}));
vi.mock("@/lib/search/pg-filters", () => ({ localesOrNoneClause: vi.fn() }));
vi.mock("@/lib/services/search-input", () => ({ parseSearchFilters: vi.fn() }));
vi.mock("@/lib/search/params", () => ({
  firstOf: vi.fn(),
  idsOrUndefined: vi.fn(),
  parseRangeParam: vi.fn(),
}));

import { searchCompaniesForWatchlist } from "../company";

const searchMock = mocks.search;
const buildFilterStringMock = mocks.buildFilterString;
const TEST_ENV = {
  DATABASE_URL:
    process.env.DATABASE_URL ?? "postgresql://test:test@localhost:5432/test",
  TYPESENSE_HOST: process.env.TYPESENSE_HOST,
  TYPESENSE_PORT: process.env.TYPESENSE_PORT,
  TYPESENSE_PROTOCOL: process.env.TYPESENSE_PROTOCOL,
  TYPESENSE_SEARCH_KEY: process.env.TYPESENSE_SEARCH_KEY,
};

const companyHit = (overrides: Record<string, unknown> = {}) => ({
  id: "co-1",
  name: "Acme Corp",
  slug: "acme",
  icon: "https://cdn.x/icon.png",
  logo: null,
  website: "https://acme.example",
  description: "We build things in English.",
  description_de: "Wir bauen Dinge.",
  industry_id: 7,
  industry_name: "Software",
  industry_name_de: "Software",
  employee_count_range: 3,
  founded_year: 2015,
  active_posting_count: 42,
  ...overrides,
});

withTestEnv(TEST_ENV);

beforeEach(() => {
  vi.clearAllMocks();
  searchMock.mockReset();
  buildFilterStringMock.mockReset();
  buildFilterStringMock.mockReturnValue("");
});

describe("searchCompaniesForWatchlist", () => {
  it("includes companies with zero active postings in unfiltered search", async () => {
    searchMock.mockResolvedValue({
      found: 1,
      hits: [{ document: companyHit({ active_posting_count: 0 }) }],
    });

    const out = await searchCompaniesForWatchlist({
      query: "Acme",
      locale: "en",
      offset: 0,
      limit: 20,
    });

    expect(out.companies).toHaveLength(1);
    expect(out.companies[0].activeMatches).toBe(0);
    expect(searchMock).toHaveBeenCalledWith(
      expect.not.objectContaining({ filter_by: expect.stringContaining("active_posting_count") }),
    );
  });

  it("does not exclude zero-posting companies when filtering by industry", async () => {
    searchMock.mockResolvedValue({
      found: 1,
      hits: [{ document: companyHit({ active_posting_count: 0 }) }],
    });

    await searchCompaniesForWatchlist({
      industryId: 7,
      locale: "en",
      offset: 0,
      limit: 20,
    });

    expect(searchMock).toHaveBeenCalledWith(
      expect.objectContaining({ filter_by: "industry_id:=7" }),
    );
  });

  it("keeps starred ordering without requiring active postings", async () => {
    searchMock
      .mockResolvedValueOnce({
        found: 1,
        hits: [{ document: companyHit({ active_posting_count: 0 }) }],
      })
      .mockResolvedValueOnce({ found: 0, hits: [] });

    const out = await searchCompaniesForWatchlist({
      locale: "en",
      offset: 0,
      limit: 20,
      starredCompanyIds: ["co-1"],
    });

    expect(out.companies).toHaveLength(1);
    expect(searchMock).toHaveBeenNthCalledWith(
      1,
      expect.objectContaining({ filter_by: "id:[co-1]" }),
    );
    expect(searchMock).toHaveBeenNthCalledWith(
      2,
      expect.objectContaining({ filter_by: "id:!=[co-1]" }),
    );
  });

  it("includes a searched zero-posting company when the watchlist has filters", async () => {
    buildFilterStringMock.mockReturnValue("location_ids:=[42]");
    searchMock
      .mockResolvedValueOnce({
        facet_counts: [{ counts: [], stats: { total_values: 0 } }],
      })
      .mockResolvedValueOnce({
        found: 1,
        hits: [{ document: companyHit({ active_posting_count: 0 }) }],
      })
      .mockResolvedValueOnce({
        facet_counts: [{ counts: [], stats: { total_values: 0 } }],
      })
      .mockResolvedValueOnce({
        hits: [{ document: companyHit({ active_posting_count: 0 }) }],
      });

    const out = await searchCompaniesForWatchlist({
      query: "Acme",
      locale: "en",
      offset: 0,
      limit: 20,
      locationIds: [42],
    });

    expect(out).toMatchObject({
      total: 1,
      companies: [{ id: "co-1", activeMatches: 0 }],
    });
  });

  it("includes a searched active company with zero matches for the current watchlist filters", async () => {
    buildFilterStringMock.mockReturnValue("technology_ids:=[99]");
    searchMock
      .mockResolvedValueOnce({
        facet_counts: [{ counts: [], stats: { total_values: 0 } }],
      })
      .mockResolvedValueOnce({
        found: 1,
        hits: [{ document: companyHit({ active_posting_count: 42 }) }],
      })
      .mockResolvedValueOnce({
        facet_counts: [{ counts: [], stats: { total_values: 0 } }],
      })
      .mockResolvedValueOnce({
        hits: [{ document: companyHit({ active_posting_count: 42 }) }],
      });

    const out = await searchCompaniesForWatchlist({
      query: "Acme",
      locale: "en",
      offset: 0,
      limit: 20,
      technologyIds: [99],
    });

    expect(out).toMatchObject({
      total: 1,
      companies: [{ id: "co-1", activeMatches: 0 }],
    });
  });
});
