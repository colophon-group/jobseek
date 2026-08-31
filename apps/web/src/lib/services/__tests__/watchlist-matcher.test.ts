import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

const mocks = vi.hoisted(() => ({
  getCurrencyRates: vi.fn(),
  resolveLocationSlugs: vi.fn(),
  resolveOccupationSlugs: vi.fn(),
  resolveSenioritySlugs: vi.fn(),
  resolveTechnologySlugs: vi.fn(),
  multiSearch: vi.fn(),
  singleSearch: vi.fn(),
}));

vi.mock("@/lib/services/search", () => ({
  getCurrencyRates: mocks.getCurrencyRates,
}));
vi.mock("@/lib/services/locations", () => ({
  resolveLocationSlugs: mocks.resolveLocationSlugs,
}));
vi.mock("@/lib/services/taxonomy", () => ({
  resolveOccupationSlugs: mocks.resolveOccupationSlugs,
  resolveSenioritySlugs: mocks.resolveSenioritySlugs,
  resolveTechnologySlugs: mocks.resolveTechnologySlugs,
}));
vi.mock("@/lib/search/typesense-client", () => ({
  getSearchClient: () => ({
    collections: () => ({ documents: () => ({ search: mocks.singleSearch }) }),
    multiSearch: { perform: mocks.multiSearch },
  }),
}));
vi.mock("@/lib/search/typesense-retry", async (importOriginal) => {
  const actual = await importOriginal<
    typeof import("@/lib/search/typesense-retry")
  >();
  return {
    ...actual,
    withTypesenseRetry: (operation: () => Promise<unknown>) => operation(),
  };
});
import {
  compileWatchlistMatcherSources,
  matchCompiledWatchlistsInWindow,
} from "../watchlist-matcher";

function posting(id: string, firstSeenAt: number) {
  return {
    document: {
      id,
      title: `Role ${id}`,
      source_url: `https://example.test/${id}`,
      first_seen_at: firstSeenAt,
      is_active: true,
      company_id: "company-1",
      company_name: "Acme",
      company_slug: "acme",
      location_names: ["Zurich"],
    },
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.getCurrencyRates.mockResolvedValue([
    { currency: "USD", toEur: 0.9 },
  ]);
  mocks.resolveLocationSlugs.mockResolvedValue(
    new Map([
      [
        "zurich",
        {
          id: 10,
          slug: "zurich",
          name: "Zürich",
          type: "city",
          parentName: "Schweiz",
        },
      ],
    ]),
  );
  mocks.resolveOccupationSlugs.mockResolvedValue(
    new Map([["engineering", { id: 20, slug: "engineering", name: "Engineering" }]]),
  );
  mocks.resolveSenioritySlugs.mockResolvedValue(
    new Map([["senior", { id: 30, slug: "senior", name: "Senior" }]]),
  );
  mocks.resolveTechnologySlugs.mockResolvedValue(
    new Map([["typescript", { id: 40, slug: "typescript", name: "TypeScript" }]]),
  );
});

describe("compileWatchlistMatcherSources", () => {
  it("batch-resolves current taxonomy, currency, and owner locale semantics", async () => {
    const compiled = await compileWatchlistMatcherSources([
      {
        watchlistId: "watchlist-1",
        watchlistLabel: "Backend",
        filters: {
          keywords: ["staff"],
          locationSlugs: ["zurich"],
          occupationSlugs: ["engineering"],
          senioritySlugs: ["senior"],
          technologySlugs: ["typescript"],
          workMode: ["remote"],
          employmentType: ["full_time"],
          salaryMin: 100_000,
          salaryCurrency: "USD",
          experienceMin: 3,
        },
        companyIds: ["company-1"],
        locale: "de",
        jobLanguages: [],
      },
      {
        watchlistId: "watchlist-2",
        watchlistLabel: "All roles",
        filters: { anyCompany: true, locationSlugs: ["zurich"] },
        companyIds: ["company-2"],
        locale: "de",
        jobLanguages: ["*"],
      },
    ]);

    expect(mocks.resolveLocationSlugs).toHaveBeenCalledTimes(1);
    expect(mocks.resolveLocationSlugs).toHaveBeenCalledWith(["zurich"], "de");
    expect(mocks.getCurrencyRates).toHaveBeenCalledTimes(1);
    expect(compiled[0]?.candidateFilters).toMatchObject({
      companyIds: ["company-1"],
      keywords: ["staff"],
      locationIds: [10],
      occupationIds: [20],
      seniorityIds: [30],
      technologyIds: [40],
      workMode: ["remote"],
      employmentType: ["full_time"],
      salaryMin: 90_000,
      experienceMin: 3,
      languages: ["de"],
    });
    expect(compiled[1]?.candidateFilters).toMatchObject({
      companyIds: [],
      anyCompany: true,
      locationIds: [10],
      languages: [],
    });
  });
});

describe("matchCompiledWatchlistsInWindow", () => {
  it("uses one multi-search and deduplicates posting IDs with all labels", async () => {
    const start = new Date("2026-08-24T00:00:00.000Z");
    const end = new Date("2026-08-31T00:00:00.000Z");
    const shared = posting("shared", end.getTime() / 1_000 - 20);
    mocks.multiSearch.mockResolvedValue({
      results: [
        {
          found: 2,
          hits: [posting("newest", end.getTime() / 1_000 - 10), shared],
        },
        {
          found: 2,
          hits: [shared, posting("older", start.getTime() / 1_000 + 10)],
        },
      ],
    });

    const result = await matchCompiledWatchlistsInWindow({
      watchlists: [
        {
          watchlistId: "watchlist-1",
          watchlistLabel: "Backend",
          candidateFilters: {
            companyIds: ["company-1"],
            locationIds: [10],
            languages: ["de"],
          },
        },
        {
          watchlistId: "watchlist-2",
          watchlistLabel: "Remote",
          candidateFilters: {
            companyIds: [],
            anyCompany: true,
            workMode: ["remote"],
            languages: ["en"],
          },
        },
      ],
      windowStart: start,
      windowEnd: end,
      limitPerWatchlist: 20,
    });

    expect(mocks.multiSearch).toHaveBeenCalledTimes(1);
    const request = mocks.multiSearch.mock.calls[0]?.[0] as {
      searches: Array<{ filter_by: string; sort_by: string }>;
    };
    expect(request.searches).toHaveLength(2);
    for (const search of request.searches) {
      expect(search.filter_by).toContain("is_active:true");
      expect(search.filter_by).toContain(
        `first_seen_at:>=${start.getTime() / 1_000}`,
      );
      expect(search.filter_by).toContain(
        `first_seen_at:<${end.getTime() / 1_000}`,
      );
      expect(search.sort_by).toBe("first_seen_at:desc");
    }
    expect(request.searches[0]?.filter_by).toContain("location_ids:[10]");
    expect(request.searches[0]?.filter_by).toContain("locales:[de,_none]");
    expect(request.searches[1]?.filter_by).toContain("location_types:[remote]");

    expect(result.window).toEqual({
      windowStart: start.toISOString(),
      windowEnd: end.toISOString(),
      boundary: "[windowStart, windowEnd)",
    });
    expect(result.postings.map((value) => value.id)).toEqual([
      "newest",
      "shared",
      "older",
    ]);
    expect(
      result.postings.find((value) => value.id === "shared")?.matchedWatchlists,
    ).toEqual([
      { id: "watchlist-1", label: "Backend" },
      { id: "watchlist-2", label: "Remote" },
    ]);
    expect(result.watchlists).toEqual([
      { id: "watchlist-1", label: "Backend", total: 2, returned: 2, truncated: false },
      { id: "watchlist-2", label: "Remote", total: 2, returned: 2, truncated: false },
    ]);
  });
});
