import { beforeEach, describe, expect, it, vi } from "vitest";

// Mocks must be hoisted so vi.mock factories can reach them.
const mocks = vi.hoisted(() => ({
  cacheLife: vi.fn(),
  cacheTag: vi.fn(),
  search: vi.fn(),
  dbExecute: vi.fn(),
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
vi.mock("@/db", () => ({ db: { execute: mocks.dbExecute } }));
vi.mock("drizzle-orm", () => ({
  sql: (strings: TemplateStringsArray, ..._values: unknown[]) =>
    strings.join("?"),
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
});

describe("getGlobalLocationsGrouped — Regions cluster (#2940)", () => {
  /**
   * Returns the Regions cluster at the top of the response, sorted by
   * count desc, with the canonical display name ("European Union" rather
   * than the DB-stored abbreviation "EU") so that downstream chip
   * rendering uses the consistent label called for in the issue test plan.
   */
  it("includes macros with active postings, sorted by count, with canonical names", async () => {
    // Order of dbExecute calls in `_fetchGlobalLocationsGrouped`:
    //   1. SELECT id, slug, type, parent_id FROM location  (hierarchy)
    //   2. SELECT location_id, locale, name FROM location_name  (display names)
    //   3. SELECT macro_id, country_name FROM location_macro_member ...
    //      (only when there are macros with non-zero counts)
    mocks.dbExecute
      .mockResolvedValueOnce([
        { id: 4, slug: null, type: "macro", parent_id: null },
        { id: 1, slug: null, type: "macro", parent_id: null },
        { id: 5, slug: null, type: "macro", parent_id: null },
        { id: 100, slug: "germany", type: "country", parent_id: null },
        { id: 200, slug: "berlin", type: "city", parent_id: 100 },
      ])
      .mockResolvedValueOnce([
        { location_id: 4, locale: "en", name: "EU" },
        { location_id: 1, locale: "en", name: "EMEA" },
        { location_id: 5, locale: "en", name: "DACH" },
        { location_id: 100, locale: "en", name: "Germany" },
        { location_id: 200, locale: "en", name: "Berlin" },
      ])
      .mockResolvedValueOnce([
        { macro_id: 4, country_name: "Germany" },
        { macro_id: 4, country_name: "France" },
        { macro_id: 5, country_name: "Germany" },
        { macro_id: 5, country_name: "Austria" },
        { macro_id: 5, country_name: "Switzerland" },
      ]);

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
    });
    expect(out.macros[1]).toEqual({
      id: 4,
      slug: "eu",
      name: "European Union",
      abbreviation: "EU",
      count: 146,
      memberCountryNames: ["Germany", "France"],
    });
    expect(out.macros[2]).toEqual({
      id: 5,
      slug: "dach",
      name: "DACH (Germany, Austria, Switzerland)",
      abbreviation: "DACH",
      count: 6,
      memberCountryNames: ["Germany", "Austria", "Switzerland"],
    });
    // Country tier still works
    expect(out.countries.length).toBeGreaterThan(0);
    expect(out.countries[0].countryName).toBe("Germany");
  });

  /**
   * Sentinel: macros without active postings (no facet entry) are dropped
   * — the cluster only ever shows actionable filters.
   */
  it("drops macros that have zero active-posting facet count", async () => {
    mocks.dbExecute
      .mockResolvedValueOnce([
        { id: 4, slug: null, type: "macro", parent_id: null },
        { id: 9, slug: null, type: "macro", parent_id: null },
        { id: 100, slug: "germany", type: "country", parent_id: null },
      ])
      .mockResolvedValueOnce([
        { location_id: 4, locale: "en", name: "EU" },
        { location_id: 9, locale: "en", name: "Worldwide" },
        { location_id: 100, locale: "en", name: "Germany" },
      ])
      .mockResolvedValueOnce([]); // empty member table — should not break

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
  it("returns empty shape when Typesense is unreachable", async () => {
    mocks.search.mockRejectedValue(new Error("Typesense down"));
    const out = await getGlobalLocationsGrouped("en");
    expect(out).toEqual({ macros: [], countries: [] });
  });
});
