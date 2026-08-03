import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  cacheLife: vi.fn(),
  cacheTag: vi.fn(),
  search: vi.fn(),
  locationDocs: [] as Array<Record<string, unknown>>,
}));

vi.mock("server-only", () => ({}));
vi.mock("next/cache", () => ({
  cacheLife: mocks.cacheLife,
  cacheTag: mocks.cacheTag,
}));
vi.mock("@/lib/sessionCache", () => ({ getSessionUserId: vi.fn() }));
vi.mock("@/lib/actions/locations", () => ({
  expandLocationIds: vi.fn(),
  expandLocationIdsBatch: vi.fn().mockResolvedValue([]),
}));
vi.mock("@/lib/actions/taxonomy", () => ({
  expandOccupationIds: vi.fn(),
  expandOccupationIdsBatch: vi.fn().mockResolvedValue([]),
}));
vi.mock("@/lib/search", () => ({ getSearchProvider: vi.fn() }));
vi.mock("@/lib/search/typesense-client", () => ({
  getSearchClient: () => ({
    collections: () => ({ documents: () => ({ search: mocks.search }) }),
  }),
}));
vi.mock("@/lib/search/typesense-taxonomy", () => ({
  fetchLocationMacroDocuments: () =>
    mocks.locationDocs.filter((doc) => doc.type === "macro"),
  fetchLocationDocumentsWithAncestors: () => mocks.locationDocs,
  fetchLocationDocumentsByIds: (ids: number[]) =>
    mocks.locationDocs.filter((doc) => ids.includes(doc.location_id as number)),
}));
vi.mock("@/lib/search/constants", () => ({
  ANON_MAX_COMPANIES: 5,
  ANON_MAX_POSTINGS: 10,
}));
vi.mock("@/lib/search/typesense-filters", () => ({
  buildFilterString: vi.fn(),
  POSTING_BASE_FILTER: "is_active:true",
}));
vi.mock("@/lib/search/pg-filters", () => ({ localesOrNoneClause: vi.fn() }));
vi.mock("@/lib/actions/search-input", () => ({ parseSearchFilters: vi.fn() }));
vi.mock("@/lib/search/params", () => ({
  firstOf: vi.fn(),
  idsOrUndefined: vi.fn(),
  parseRangeParam: vi.fn(),
}));

import { getCompanyLocationsGroupedWithMacros, suggestIndustries } from "../company";

beforeEach(() => {
  vi.clearAllMocks();
  mocks.locationDocs = [];
});

describe("getCompanyLocationsGroupedWithMacros — Regions cluster gate (#2940)", () => {
  /** Configure the direct-location and ancestor-expanded company facets. */
  function setupMocks(memberCounts: Array<{ id: number; count: number }>) {
    mocks.locationDocs = [
      { id: "4", location_id: 4, slug: "eu", name_en: "EU", type: "macro", member_country_ids: [100, 101] },
      { id: "100", location_id: 100, slug: "germany", name_en: "Germany", type: "country" },
      { id: "101", location_id: 101, slug: "france", name_en: "France", type: "country" },
      { id: "200", location_id: 200, slug: "berlin", name_en: "Berlin", type: "city", parent_id: 100 },
    ];
    mocks.search.mockImplementation((args: { facet_by?: string }) =>
      Promise.resolve({
        facet_counts: [{
          field_name: args.facet_by,
          counts: args.facet_by === "location_direct_ids"
            ? [{ value: "200", count: 5 }]
            : [
                { value: "4", count: 100 },
                ...memberCounts.map((entry) => ({ value: String(entry.id), count: entry.count })),
              ],
          stats: { total_values: args.facet_by === "location_direct_ids" ? 1 : 3 },
        }],
      }),
    );
  }

  it("returns macros where the company has postings spanning >=2 member countries", async () => {
    setupMocks([{ id: 100, count: 70 }, { id: 101, count: 30 }]);
    const out = await getCompanyLocationsGroupedWithMacros("co-1", "en");
    expect(out.macros).toHaveLength(1);
    expect(out.macros[0]).toEqual({
      id: 4,
      slug: "eu",
      name: "European Union",
      abbreviation: "EU",
      count: 100,
      memberCountryNames: ["France", "Germany"],
      memberCountryIds: [101, 100],
    });
  });

  /** A company with postings in one member country does not see the region. */
  it("excludes macros when only one member country has matching postings", async () => {
    setupMocks([{ id: 100, count: 100 }]);
    const out = await getCompanyLocationsGroupedWithMacros("co-2", "en");
    expect(out.macros).toEqual([]);
  });

  it("returns an empty optional surface during a Typesense outage", async () => {
    mocks.search.mockRejectedValue(
      Object.assign(new Error("read ECONNRESET"), { code: "ECONNRESET" }),
    );

    await expect(
      getCompanyLocationsGroupedWithMacros("co-3", "en"),
    ).resolves.toEqual({ countries: [], macros: [] });
  });

  it("propagates unexpected Typesense errors", async () => {
    mocks.search.mockRejectedValue(
      Object.assign(new Error("Request failed with HTTP code 429"), {
        httpStatus: 429,
      }),
    );

    await expect(
      getCompanyLocationsGroupedWithMacros("co-4", "en"),
    ).rejects.toThrow("429");
  });

  it("applies the same outage policy to industry suggestions", async () => {
    mocks.search.mockRejectedValueOnce(
      Object.assign(new Error("read ECONNRESET"), { code: "ECONNRESET" }),
    );
    await expect(suggestIndustries({ query: "tech", locale: "en" })).resolves.toEqual([]);

    mocks.search.mockRejectedValueOnce(
      Object.assign(new Error("Request failed with HTTP code 429"), {
        httpStatus: 429,
      }),
    );
    await expect(suggestIndustries({ query: "tech", locale: "en" })).rejects.toThrow("429");
  });
});
