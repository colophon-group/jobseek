import { beforeEach, describe, expect, it, vi } from "vitest";

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
  fetchLocationMacroDocuments: () => [],
  fetchLocationDocumentsWithAncestors: () => mocks.locationDocs,
  fetchLocationDocumentsByIds: () => mocks.locationDocs,
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

describe("getGlobalLocationsGrouped — locale-aware display sorting", () => {
  it("sorts country names with the caller locale", async () => {
    mocks.locationDocs = [
      { id: "100", location_id: 100, slug: "austria", type: "country", name_en: "Austria", name_de: "Österreich" },
      { id: "200", location_id: 200, slug: "switzerland", type: "country", name_en: "Switzerland", name_de: "Schweiz" },
      { id: "300", location_id: 300, slug: "zambia", type: "country", name_en: "Zambia", name_de: "Sambia" },
    ];

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

    const out = await getGlobalLocationsGrouped("de");

    expect(out.countries.map((c) => c.countryName)).toEqual([
      "Österreich",
      "Sambia",
      "Schweiz",
    ]);
  });
});
