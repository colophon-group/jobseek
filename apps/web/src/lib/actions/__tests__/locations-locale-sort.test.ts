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

describe("getGlobalLocationsGrouped — locale-aware display sorting", () => {
  it("sorts country names with the caller locale", async () => {
    mocks.dbExecute
      .mockResolvedValueOnce([
        { id: 100, slug: "austria", type: "country", parent_id: null },
        { id: 200, slug: "switzerland", type: "country", parent_id: null },
        { id: 300, slug: "zambia", type: "country", parent_id: null },
      ])
      .mockResolvedValueOnce([
        { location_id: 100, locale: "sv", name: "Österreich" },
        { location_id: 200, locale: "sv", name: "Schweiz" },
        { location_id: 300, locale: "sv", name: "Zambia" },
      ]);

    mocks.search.mockResolvedValue({
      facet_counts: [
        {
          field_name: "location_ids",
          counts: [
            { value: "100", count: 1 },
            { value: "200", count: 1 },
            { value: "300", count: 1 },
          ],
        },
      ],
    });

    const out = await getGlobalLocationsGrouped("sv");

    expect(out.countries.map((c) => c.countryName)).toEqual([
      "Schweiz",
      "Zambia",
      "Österreich",
    ]);
  });
});
