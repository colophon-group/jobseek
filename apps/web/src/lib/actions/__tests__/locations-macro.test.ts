import { beforeEach, describe, expect, it, vi } from "vitest";

// Mocks must be hoisted so vi.mock factories can reach them.
const mocks = vi.hoisted(() => ({
  cacheLife: vi.fn(),
  cacheTag: vi.fn(),
  search: vi.fn(),
  locationDocs: [] as Array<Record<string, unknown>>,
  cached: vi.fn((_key: string, fn: () => unknown) => fn()),
}));

vi.mock("server-only", () => ({}));
vi.mock("next/cache", () => ({
  cacheLife: mocks.cacheLife,
  cacheTag: mocks.cacheTag,
}));
vi.mock("@/lib/cache", () => ({ cached: mocks.cached }));
vi.mock("@/lib/cache-tags", () => ({ typeaheadLocationsCacheTag: () => "tag" }));
vi.mock("@/lib/search/typesense-client", () => ({
  getTypesenseClient: () => ({
    collections: () => ({ documents: () => ({ search: mocks.search }) }),
  }),
}));
vi.mock("@/lib/search/typesense-taxonomy", () => ({
  fetchLocationMacroDocuments: () =>
    mocks.locationDocs.filter((doc) => doc.type === "macro"),
  fetchLocationDocumentsWithAncestors: () => mocks.locationDocs,
  fetchLocationDocumentsByIds: (ids: number[]) =>
    mocks.locationDocs.filter((doc) => ids.includes(doc.location_id as number)),
  fetchLocationDocumentsBySlugs: () => [],
  fetchLocationDescendants: () => [],
}));
vi.mock("@/lib/search/typesense-filters", () => ({
  buildFilterString: () => "",
  POSTING_BASE_FILTER: "is_active:true",
}));
vi.mock("@/lib/search/typeahead-boost", () => ({
  boostByFilterMatches: (xs: unknown) => xs,
}));

import { getGlobalLocationsGrouped } from "../locations";

beforeEach(() => {
  vi.clearAllMocks();
  mocks.locationDocs = [];
});

describe("getGlobalLocationsGrouped — Regions cluster (#2940)", () => {
  /**
   * Returns the Regions cluster at the top of the response, sorted by
   * count desc, with the canonical display name ("European Union" rather
   * than the DB-stored abbreviation "EU") so that downstream chip
   * rendering uses the consistent label called for in the issue test plan.
   */
  it("includes macros with active postings, sorted by count, with canonical names", async () => {
    // Location hierarchy and membership come from the taxonomy collection;
    // only posting counts require the two Typesense facet searches below.
    mocks.locationDocs = [
      { id: "4", location_id: 4, slug: "eu", type: "macro", name_en: "EU", member_country_ids: [100, 101] },
      { id: "1", location_id: 1, slug: "emea", type: "macro", name_en: "EMEA" },
      { id: "5", location_id: 5, slug: "dach", type: "macro", name_en: "DACH", member_country_ids: [100, 102, 103] },
      { id: "100", location_id: 100, slug: "germany", type: "country", name_en: "Germany" },
      { id: "101", location_id: 101, slug: "france", type: "country", name_en: "France" },
      { id: "102", location_id: 102, slug: "austria", type: "country", name_en: "Austria" },
      { id: "103", location_id: 103, slug: "switzerland", type: "country", name_en: "Switzerland" },
      { id: "200", location_id: 200, slug: "berlin", type: "city", name_en: "Berlin", parent_id: 100 },
    ];

    // Two parallel Typesense calls: country-tier facet (top-500) AND a
    // dedicated macro-only facet. Country-tier truncation can drop low-
    // count macros; the macro-only call always surfaces every macro
    // with at least one matching posting.
    mocks.search.mockImplementation((args: { filter_by?: string }) => {
      const isMacroQuery = (args.filter_by ?? "").includes("location_ids:[");
      if (isMacroQuery) {
        // Dedicated macro-only facet — emulates how DACH=6 still appears
        // even though it'd be below the country-tier 500-cap in production.
        return Promise.resolve({
          facet_counts: [
            {
              field_name: "location_ids",
              counts: [
                { value: "4", count: 146 },
                { value: "1", count: 1433 },
                { value: "5", count: 6 },
              ],
            },
          ],
        });
      }
      return Promise.resolve({
        facet_counts: [
          {
            field_name: "location_ids",
            counts: [
              // The broad facet can include macro ancestors as well as the
              // country/city rows. It must not duplicate EU under "Other".
              { value: "4", count: 146 },
              { value: "100", count: 50 },
              { value: "200", count: 25 },
            ],
          },
        ],
      });
    });

    const out = await getGlobalLocationsGrouped("en");

    // Macros sorted by count desc, with canonical names
    expect(out.macros).toHaveLength(3);
    expect(out.macros[0]).toEqual({
      id: 1,
      slug: "emea",
      name: "Europe, Middle East & Africa",
      abbreviation: "EMEA",
      count: 1433,
      memberCountryNames: [],
      memberCountryIds: [],
    });
    expect(out.macros[1]).toEqual({
      id: 4,
      slug: "eu",
      name: "European Union",
      abbreviation: "EU",
      count: 146,
      memberCountryNames: ["France", "Germany"],
      memberCountryIds: [101, 100],
    });
    expect(out.macros[2]).toEqual({
      id: 5,
      slug: "dach",
      name: "DACH (Germany, Austria, Switzerland)",
      abbreviation: "DACH",
      count: 6,
      memberCountryNames: ["Austria", "Germany", "Switzerland"],
      memberCountryIds: [102, 100, 103],
    });
    // Country tier still works
    expect(out.countries.length).toBeGreaterThan(0);
    expect(out.countries[0].countryName).toBe("Germany");
    const hierarchyLocationIds = out.countries.flatMap((country) =>
      country.regions.flatMap((region) => region.locations.map((location) => location.id)),
    );
    expect(hierarchyLocationIds).not.toContain(4);
  });

  /**
   * Sentinel: macros without active postings (no facet entry) are dropped
   * — the cluster only ever shows actionable filters.
   */
  it("drops macros that have zero active-posting facet count", async () => {
    mocks.locationDocs = [
      { id: "4", location_id: 4, slug: "eu", type: "macro", name_en: "EU" },
      { id: "9", location_id: 9, slug: "worldwide", type: "macro", name_en: "Worldwide" },
      { id: "100", location_id: 100, slug: "germany", type: "country", name_en: "Germany" },
    ];

    mocks.search.mockImplementation((args: { filter_by?: string }) => {
      const isMacroQuery = (args.filter_by ?? "").includes("location_ids:[");
      if (isMacroQuery) {
        return Promise.resolve({
          facet_counts: [
            {
              field_name: "location_ids",
              counts: [{ value: "4", count: 100 }], // only EU has postings
            },
          ],
        });
      }
      return Promise.resolve({
        facet_counts: [
          {
            field_name: "location_ids",
            counts: [{ value: "100", count: 50 }],
          },
        ],
      });
    });

    const out = await getGlobalLocationsGrouped("en");
    expect(out.macros).toHaveLength(1);
    expect(out.macros[0].abbreviation).toBe("EU");
    expect(out.macros[0].memberCountryNames).toEqual([]); // empty member table tolerated
  });

  /**
   * Typesense outage: keep working but return an empty response instead of
   * propagating. The modal renders the empty-state message in this case.
   */
  it("returns empty shape when a macro-region lookup receives a nested 503", async () => {
    mocks.search.mockRejectedValue(
      Object.assign(new Error("Typesense unavailable"), {
        response: { status: 503 },
      }),
    );
    const out = await getGlobalLocationsGrouped("en");
    expect(out).toEqual({ macros: [], countries: [] });
  });

  it("propagates unexpected Typesense errors", async () => {
    mocks.search.mockRejectedValue(
      Object.assign(new Error("Request failed with HTTP code 429"), {
        httpStatus: 429,
      }),
    );

    await expect(getGlobalLocationsGrouped("en")).rejects.toThrow("429");
  });
});
