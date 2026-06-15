import { beforeEach, describe, expect, it, vi } from "vitest";

// `vi.mock` hoists to the top of the file so its factories cannot close
// over module-scope variables. Use `vi.hoisted` to share mocks between
// the factory and the test bodies. Mirrors the pattern in
// `company.test.ts` and `explore-data-salary-currency.test.ts`.
const mocks = vi.hoisted(() => ({
  cacheLife: vi.fn(),
  cacheTag: vi.fn(),
  search: vi.fn(),
  buildFilterString: vi.fn(),
  parseSearchFilters: vi.fn(),
  parseRangeParam: vi.fn(),
  getCurrencyRates: vi.fn(),
  getSessionUserId: vi.fn(),
}));

vi.mock("server-only", () => ({}));

// `cacheLife` / `cacheTag` are no-ops outside a Cache Components-enabled
// runtime — see `company.test.ts` for the full rationale. The `'use cache'`
// directive itself is removed by the test transform pipeline.
vi.mock("next/cache", () => ({
  cacheLife: mocks.cacheLife,
  cacheTag: mocks.cacheTag,
}));

vi.mock("@/lib/search/typesense-client", () => ({
  getSearchClient: () => ({
    collections: () => ({ documents: () => ({ search: mocks.search }) }),
  }),
}));

vi.mock("@/db", () => ({ db: { execute: vi.fn() } }));

vi.mock("drizzle-orm", () => ({
  sql: (strings: TemplateStringsArray, ..._values: unknown[]) =>
    strings.join("?"),
}));
vi.mock("@/lib/sessionCache", () => ({
  getSessionUserId: mocks.getSessionUserId,
}));
vi.mock("@/lib/actions/locations", () => ({
  expandLocationIds: vi.fn(),
  expandLocationIdsBatch: vi.fn().mockResolvedValue([]),
}));
vi.mock("@/lib/actions/taxonomy", () => ({
  expandOccupationIds: vi.fn(),
  expandOccupationIdsBatch: vi.fn().mockResolvedValue([]),
}));
vi.mock("@/lib/search", () => ({ getSearchProvider: vi.fn() }));
vi.mock("@/lib/search/constants", () => ({
  ANON_MAX_COMPANIES: 5,
  ANON_MAX_POSTINGS: 10,
}));
vi.mock("@/lib/search/typesense-filters", () => ({
  // Capture the filter object so we can assert on the salary fields the
  // caller passed in.
  buildFilterString: mocks.buildFilterString,
  POSTING_BASE_FILTER: "is_active:=true",
}));
vi.mock("@/lib/search/pg-filters", () => ({ localesOrNoneClause: vi.fn() }));
vi.mock("@/lib/actions/search-input", () => ({
  parseSearchFilters: mocks.parseSearchFilters,
}));
vi.mock("@/lib/actions/search", () => ({
  getCurrencyRates: mocks.getCurrencyRates,
}));
vi.mock("@/lib/search/params", () => ({
  firstOf: (v: unknown) => (Array.isArray(v) ? v[0] : v),
  idsOrUndefined: (items: { id: number }[] | undefined) =>
    items && items.length > 0 ? items.map((i) => i.id) : undefined,
  parseRangeParam: (v: string | undefined) => mocks.parseRangeParam(v),
}));

import { getSimilarCompanies } from "../company";

function _candidatePool(ids: string[]) {
  return {
    hits: ids.map((id) => ({
      document: {
        id,
        slug: `co-${id}`,
        name: `Company ${id}`,
        icon: null,
      },
    })),
  };
}

function _facetResponse(idsWithCounts: Array<{ id: string; count: number }>) {
  return {
    facet_counts: [
      {
        counts: idsWithCounts.map((x) => ({ value: x.id, count: x.count })),
      },
    ],
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  // Two `client.search()` calls per filtered run: the candidate pool, then
  // the per-company facet. Default to two non-empty hits with positive
  // facet counts so the function reaches the `buildFilterString` call we
  // want to assert on.
  mocks.search
    .mockResolvedValueOnce(_candidatePool(["co-a", "co-b"]))
    .mockResolvedValueOnce(
      _facetResponse([
        { id: "co-a", count: 3 },
        { id: "co-b", count: 1 },
      ]),
    );
  mocks.buildFilterString.mockReturnValue("");
  mocks.parseSearchFilters.mockResolvedValue({
    keywords: [],
    locations: [],
    occupations: [],
    seniorities: [],
    technologies: [],
    workMode: [],
    employmentTypes: [],
  });
  mocks.parseRangeParam.mockReturnValue({ min: undefined, max: undefined });
  mocks.getCurrencyRates.mockResolvedValue([
    { currency: "USD", toEur: 0.92 },
    { currency: "CHF", toEur: 0.95 },
    { currency: "JPY", toEur: 0.006 },
  ]);
});

describe("getSimilarCompanies — salary EUR conversion (#3178)", () => {
  it("converts USD 100K filter to ~92000 EUR before building the Typesense filter (was 100000 pre-fix)", async () => {
    // This is the headline #3178 scenario, third call site (the
    // similar-companies strip). Pre-fix, `salaryMinEur` was assigned
    // directly from the user-currency amount produced by
    // `parseRangeParam`, so `salary_eur:[100000..]` was applied against
    // the EUR-indexed Typesense field — silently excluding $100K US
    // roles whose `salary_eur` ≈ 92,000 < 100,000.
    mocks.parseRangeParam.mockReturnValueOnce({ min: 100000, max: undefined });

    await getSimilarCompanies("co-source", 7, {
      offset: 0,
      limit: 10,
      searchParams: { sal: "100000-", salcur: "USD" },
      locale: "en",
    });

    expect(mocks.getCurrencyRates).toHaveBeenCalled();
    expect(mocks.buildFilterString).toHaveBeenCalledTimes(1);
    const filterArg = mocks.buildFilterString.mock.calls[0][0];
    expect(filterArg.salaryMinEur).toBe(92000);
    expect(filterArg.salaryMaxEur).toBeUndefined();
  });

  it("converts both min and max for a USD range filter", async () => {
    mocks.parseRangeParam.mockReturnValueOnce({ min: 50000, max: 150000 });

    await getSimilarCompanies("co-source", 7, {
      offset: 0,
      limit: 10,
      searchParams: { sal: "50000-150000", salcur: "USD" },
      locale: "en",
    });

    const filterArg = mocks.buildFilterString.mock.calls[0][0];
    expect(filterArg.salaryMinEur).toBe(46000); // 50000 * 0.92
    expect(filterArg.salaryMaxEur).toBe(138000); // 150000 * 0.92
  });

  it("converts JPY 10M filter to ~60000 EUR (extreme weak-currency case)", async () => {
    // From the #3178 issue body: pre-fix, "JPY 10M" (≈ EUR 60K, a low-end
    // senior Japan salary) became `salary_eur:[10000000..]` (EUR 10M),
    // excluding every posting on the platform.
    mocks.parseRangeParam.mockReturnValueOnce({
      min: 10_000_000,
      max: undefined,
    });

    await getSimilarCompanies("co-source", 7, {
      offset: 0,
      limit: 10,
      searchParams: { sal: "10000000-", salcur: "JPY" },
      locale: "en",
    });

    const filterArg = mocks.buildFilterString.mock.calls[0][0];
    expect(filterArg.salaryMinEur).toBe(60000);
  });

  it("leaves EUR 100K unchanged (identity branch — no rate lookup needed)", async () => {
    mocks.parseRangeParam.mockReturnValueOnce({ min: 100000, max: undefined });

    await getSimilarCompanies("co-source", 7, {
      offset: 0,
      limit: 10,
      searchParams: { sal: "100000-", salcur: "EUR" },
      locale: "en",
    });

    const filterArg = mocks.buildFilterString.mock.calls[0][0];
    expect(filterArg.salaryMinEur).toBe(100000);
  });

  it("defaults to EUR when `salcur` is absent from the URL (toolbar omits it when === EUR)", async () => {
    // The toolbar writes `salcur` to the URL only when it differs from
    // "EUR" (see `company-page.tsx::updateUrl` line 185-186), so an
    // absent `salcur` means the user is on the EUR display default —
    // the amount is already in EUR and no conversion should change it.
    mocks.parseRangeParam.mockReturnValueOnce({ min: 100000, max: undefined });

    await getSimilarCompanies("co-source", 7, {
      offset: 0,
      limit: 10,
      searchParams: { sal: "100000-" },
      locale: "en",
    });

    const filterArg = mocks.buildFilterString.mock.calls[0][0];
    expect(filterArg.salaryMinEur).toBe(100000);
  });

  it("does NOT fetch currency rates when no salary filter is active", async () => {
    // Defensive guard so the strip doesn't hit the rates cache on every
    // unfiltered render (e.g. when the user is only filtering by
    // location). Mirrors the same guard in
    // `explore-data.ts::fetchExploreData` and
    // `company-page-data.ts::fetchCompanyPageData`.
    mocks.parseRangeParam.mockReturnValueOnce({
      min: undefined,
      max: undefined,
    });

    await getSimilarCompanies("co-source", 7, {
      offset: 0,
      limit: 10,
      searchParams: { loc: "berlin" },
      locale: "en",
    });

    expect(mocks.getCurrencyRates).not.toHaveBeenCalled();
  });
});
