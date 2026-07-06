import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  cacheLife: vi.fn(),
  cacheTag: vi.fn(),
  dbExecute: vi.fn(),
  search: vi.fn(),
}));

vi.mock("server-only", () => ({}));
vi.mock("next/cache", () => ({
  cacheLife: mocks.cacheLife,
  cacheTag: mocks.cacheTag,
}));
vi.mock("@/db", () => ({ db: { execute: mocks.dbExecute } }));
vi.mock("drizzle-orm", () => ({
  sql: (strings: TemplateStringsArray, ..._values: unknown[]) =>
    strings.join("?"),
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
vi.mock("@/lib/search/constants", () => ({
  ANON_MAX_COMPANIES: 5,
  ANON_MAX_POSTINGS: 10,
}));
vi.mock("@/lib/search/typesense-filters", () => ({ buildFilterString: vi.fn() }));
vi.mock("@/lib/search/pg-filters", () => ({ localesOrNoneClause: vi.fn() }));
vi.mock("@/lib/actions/search-input", () => ({ parseSearchFilters: vi.fn() }));
vi.mock("@/lib/search/params", () => ({
  firstOf: vi.fn(),
  idsOrUndefined: vi.fn(),
  parseRangeParam: vi.fn(),
}));

import { getCompanyLocationsGroupedWithMacros } from "../company";

beforeEach(() => {
  vi.clearAllMocks();
});

describe("getCompanyLocationsGroupedWithMacros — Regions cluster gate (#2940)", () => {
  /**
   * Order of dbExecute calls inside `getCompanyLocationsGroupedWithMacros`:
   *   1. `_fetchLocationsGrouped` posting-locations join (countries, regions, cities)
   *   2. `_fetchLocationsGrouped` aliasMap fetch (location_name)
   *   3. `_fetchCompanyMacroCluster` main macro_postings + member_country_count CTE
   *   4. `_fetchCompanyMacroCluster` member-name fetch
   *
   * Because `_fetchLocationsGrouped` and `_fetchCompanyMacroCluster` run
   * concurrently via Promise.all, the call order across the two helpers is
   * not guaranteed. We use `mockImplementation` keyed on SQL substrings so
   * the test passes regardless of interleaving.
   */
  function setupMocks(opts: { macros: Array<{ id: number; slug: string | null; name: string; postingCount: number; memberCountryCount: number }>; members: Array<{ macro_id: number; country_id: number; country_name: string }>; }) {
    mocks.dbExecute.mockImplementation((arg: unknown) => {
      const sql = String(arg);
      if (sql.includes("WITH active_locs AS") && sql.includes("hierarchy")) {
        // _fetchLocationsGrouped main query — return one country with one city
        return Promise.resolve([
          {
            location_id: 200, loc_slug: "berlin", loc_type: "city", loc_name: "Berlin", cnt: 5,
            region_id: null, region_slug: null, region_name: null,
            country_id: 100, country_slug: "germany", country_name: "Germany",
          },
        ]);
      }
      if (sql.includes("FROM location_name") && sql.includes("ANY") && !sql.includes("location_macro_member")) {
        // _fetchLocationsGrouped alias map
        return Promise.resolve([]);
      }
      if (sql.includes("WITH company_postings AS") && sql.includes("macro_postings")) {
        // _fetchCompanyMacroCluster main query
        return Promise.resolve(opts.macros.map((m) => ({
          macro_id: m.id,
          macro_slug: m.slug,
          macro_name: m.name,
          posting_count: m.postingCount,
          member_country_count: m.memberCountryCount,
        })));
      }
      if (sql.includes("location_macro_member") && sql.includes("country_name")) {
        return Promise.resolve(opts.members);
      }
      return Promise.resolve([]);
    });
  }

  it("returns macros where the company has postings spanning >=2 member countries", async () => {
    setupMocks({
      macros: [
        // EU: 2 member countries with hits — passes the gate
        { id: 4, slug: null, name: "EU", postingCount: 100, memberCountryCount: 2 },
      ],
      members: [
        { macro_id: 4, country_id: 100, country_name: "Germany" },
        { macro_id: 4, country_id: 101, country_name: "France" },
      ],
    });
    const out = await getCompanyLocationsGroupedWithMacros("co-1", "en");
    expect(out.macros).toHaveLength(1);
    expect(out.macros[0]).toEqual({
      id: 4,
      slug: "eu",
      name: "European Union",
      abbreviation: "EU",
      count: 100,
      memberCountryNames: ["Germany", "France"],
      memberCountryIds: [100, 101],
    });
  });

  /**
   * Gate enforcement: a company with all postings in a single member country
   * doesn't see the Regions cluster (the SQL HAVING clause filters
   * `member_country_count >= 2`). The fixture simulates the SQL having
   * already filtered out the macro — the action should return an empty
   * macros array.
   */
  it("excludes macros when SQL gate yields zero rows (single-country company)", async () => {
    setupMocks({ macros: [], members: [] });
    const out = await getCompanyLocationsGroupedWithMacros("co-2", "en");
    expect(out.macros).toEqual([]);
  });
});
