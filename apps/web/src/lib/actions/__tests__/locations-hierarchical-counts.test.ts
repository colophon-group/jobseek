/**
 * Regression tests for issue #3033 — hierarchical-modal summed counts.
 *
 * Background: `job_posting` documents carry ancestor-expanded
 * `location_ids` (the exporter promotes city -> region -> country -> macro
 * onto each posting). Faceting on `location_ids` therefore gives the
 * **true subtree count** for every parent ID directly. The previous
 * implementation ignored these direct facet entries and summed children
 * city counts to fake a parent total, which:
 *
 *   1. Under-counts when a posting is tagged at the parent tier with no
 *      city counterpart (e.g. "Chile" without a city).
 *   2. Under-counts when a mid-rank city falls below the
 *      top-`max_facet_values` cutoff.
 *
 * Production case from the issue: `loc=chile&occ=fullstack-developer`
 * showed "Chile (4) / Santiago (4)" while selecting Chile yielded 10+ jobs.
 *
 * Failing-on-main: these assertions assume `country.countryCount` is the
 * **direct** facet count, not the sum of children. They fail against the
 * previous behaviour.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";

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

/**
 * Helper: a hierarchy with Chile (country) -> Santiago region -> Santiago
 * city + Valparaíso city. The facet returns counts that exercise the
 * #3033 bug shape — country count is HIGHER than the sum of its city
 * counts because some postings are tagged at country/region tier with
 * no city.
 */
function chileSetup() {
  mocks.dbExecute
    .mockResolvedValueOnce([
      // Locations: country, region, two cities + a macro for completeness
      { id: 100, slug: "americas", type: "macro", parent_id: null },
      { id: 200, slug: "chile", type: "country", parent_id: null },
      { id: 210, slug: "santiago-region", type: "region", parent_id: 200 },
      { id: 220, slug: "santiago", type: "city", parent_id: 210 },
      { id: 230, slug: "valparaiso", type: "city", parent_id: 200 },
    ])
    .mockResolvedValueOnce([
      { location_id: 100, locale: "en", name: "Americas" },
      { location_id: 200, locale: "en", name: "Chile" },
      { location_id: 210, locale: "en", name: "Santiago Region" },
      { location_id: 220, locale: "en", name: "Santiago" },
      { location_id: 230, locale: "en", name: "Valparaíso" },
    ])
    .mockResolvedValueOnce([]); // empty macro members

  mocks.search.mockImplementation((args: { filter_by?: string }) => {
    const isMacroQuery = (args.filter_by ?? "").includes("location_ids:[");
    if (isMacroQuery) {
      return Promise.resolve({
        facet_counts: [
          {
            field_name: "location_ids",
            counts: [{ value: "100", count: 12 }],
          },
        ],
      });
    }
    // The country-tier facet — ancestor-expanded, so Chile carries the
    // full subtree count (12), but cities only carry their own counts
    // (4 + 7 = 11). The 1 missing posting is tagged "Chile" with no
    // city: it appears only in the Chile facet entry.
    return Promise.resolve({
      facet_counts: [
        {
          field_name: "location_ids",
          counts: [
            { value: "200", count: 12 }, // Chile (ancestor count, true subtree)
            { value: "210", count: 11 }, // Santiago region
            { value: "220", count: 4 },  // Santiago city
            { value: "230", count: 7 },  // Valparaíso city
          ],
        },
      ],
    });
  });
}

describe("getGlobalLocationsGrouped — hierarchical counts (#3033)", () => {
  /**
   * THE bug: country count must use the direct facet entry for the country
   * ID, not the sum of city counts (which would be 11 — under-counting
   * the 1 posting tagged "Chile" with no city).
   */
  it("uses the direct facet count for country, not the sum of children", async () => {
    chileSetup();
    const out = await getGlobalLocationsGrouped("en");

    const chile = out.countries.find((c) => c.countryId === 200);
    expect(chile).toBeDefined();
    // Direct facet count = 12 (true subtree), NOT 4 + 7 = 11 (children sum).
    expect(chile!.countryCount).toBe(12);
  });

  /**
   * Region counts also come from the direct facet entry — same rule one
   * level down. Santiago region = 11 directly; sum-of-cities (just one
   * city: Santiago) is 4, which would under-count by 7.
   */
  it("uses the direct facet count for region, not the sum of cities", async () => {
    chileSetup();
    const out = await getGlobalLocationsGrouped("en");

    const chile = out.countries.find((c) => c.countryId === 200);
    const santiagoRegion = chile!.regions.find((r) => r.regionId === 210);
    expect(santiagoRegion).toBeDefined();
    // Direct facet count = 11; sum-of-cities (only Santiago=4) would be 4.
    expect(santiagoRegion!.regionCount).toBe(11);
  });

  /**
   * Sentinel for the all-country-level edge case: when every posting is
   * tagged at country tier (no cities surface in the facet), the country
   * still appears in the modal with its true count. The previous filter
   * `g.regions.some((r) => r.locations.length > 0)` dropped these.
   */
  it("keeps countries that have direct facet counts but no city facet entries", async () => {
    mocks.dbExecute
      .mockResolvedValueOnce([
        { id: 300, slug: "lichtenstein", type: "country", parent_id: null },
      ])
      .mockResolvedValueOnce([
        { location_id: 300, locale: "en", name: "Liechtenstein" },
      ])
      .mockResolvedValueOnce([]);

    mocks.search.mockImplementation((args: { filter_by?: string }) => {
      const isMacroQuery = (args.filter_by ?? "").includes("location_ids:[");
      if (isMacroQuery) {
        return Promise.resolve({ facet_counts: [] });
      }
      return Promise.resolve({
        facet_counts: [
          {
            field_name: "location_ids",
            counts: [{ value: "300", count: 5 }], // country tier only
          },
        ],
      });
    });

    const out = await getGlobalLocationsGrouped("en");
    const lichtenstein = out.countries.find((c) => c.countryId === 300);
    expect(lichtenstein).toBeDefined();
    expect(lichtenstein!.countryCount).toBe(5);
  });
});
