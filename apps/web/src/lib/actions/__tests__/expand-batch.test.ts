import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  cacheLife: vi.fn(),
  cacheTag: vi.fn(),
  fetchLocationDescendants: vi.fn(),
  fetchOccupationDocuments: vi.fn(),
}));

vi.mock("server-only", () => ({}));
vi.mock("next/cache", () => ({
  cacheLife: mocks.cacheLife,
  cacheTag: mocks.cacheTag,
}));
vi.mock("@/lib/cache-tags", () => ({
  typeaheadLocationsCacheTag: () => "typeahead:locations",
  typeaheadOccupationsCacheTag: () => "typeahead:occupations",
  typeaheadSenioritiesCacheTag: () => "typeahead:seniorities",
  typeaheadTechnologiesCacheTag: () => "typeahead:technologies",
}));
vi.mock("@/lib/cache", () => ({
  cached: vi.fn((_key: string, fn: () => unknown) => fn()),
}));
vi.mock("@/lib/cache-ttl", () => ({ CACHE_TTL_LONG: 3600 }));
vi.mock("@/lib/search/typesense-taxonomy", () => ({
  fetchLocationDescendants: mocks.fetchLocationDescendants,
  fetchOccupationDocuments: mocks.fetchOccupationDocuments,
  fetchLocationDocumentsByIds: vi.fn(),
  fetchLocationDocumentsBySlugs: vi.fn(),
  fetchLocationDocumentsWithAncestors: vi.fn(),
  fetchLocationMacroDocuments: vi.fn(),
  fetchSeniorityDocuments: vi.fn(),
  fetchTechnologyDocuments: vi.fn(),
}));
vi.mock("@/lib/search/typesense-client", () => ({
  getTypesenseClient: vi.fn(),
}));
vi.mock("@/lib/search/typesense-filters", () => ({
  buildFilterString: () => "",
  POSTING_BASE_FILTER: "is_active:true",
}));
vi.mock("@/lib/search/typeahead-boost", () => ({
  boostByFilterMatches: (xs: unknown) => xs,
}));

import { expandLocationIdsBatch } from "../locations";
import { expandOccupationIdsBatch } from "../taxonomy";

beforeEach(() => {
  vi.clearAllMocks();
});

describe("expandLocationIdsBatch", () => {
  it("uses one Typesense descendant lookup with sorted, deduplicated seeds", async () => {
    mocks.fetchLocationDescendants.mockResolvedValue([
      { location_id: 20 },
      { location_id: 1 },
      { location_id: 10 },
    ]);

    await expect(expandLocationIdsBatch([10, 1, 10])).resolves.toEqual([1, 10, 20]);
    expect(mocks.fetchLocationDescendants).toHaveBeenCalledOnce();
    expect(mocks.fetchLocationDescendants).toHaveBeenCalledWith([1, 10]);
  });

  it("short-circuits empty input", async () => {
    await expect(expandLocationIdsBatch([])).resolves.toEqual([]);
    expect(mocks.fetchLocationDescendants).not.toHaveBeenCalled();
  });
});

describe("expandOccupationIdsBatch", () => {
  it("derives the transitive descendant union from Typesense parent metadata", async () => {
    mocks.fetchOccupationDocuments.mockResolvedValue([
      { occupation_id: 1 },
      { occupation_id: 10, parent_id: 1 },
      { occupation_id: 11, parent_id: 10 },
      { occupation_id: 2 },
    ]);

    await expect(expandOccupationIdsBatch([1, 2, 1])).resolves.toEqual([1, 2, 10, 11]);
    expect(mocks.fetchOccupationDocuments).toHaveBeenCalledOnce();
    expect(mocks.fetchOccupationDocuments).toHaveBeenCalledWith("en");
  });

  it("drops unknown seeds and short-circuits empty input", async () => {
    mocks.fetchOccupationDocuments.mockResolvedValue([{ occupation_id: 1 }]);
    await expect(expandOccupationIdsBatch([999])).resolves.toEqual([]);
    await expect(expandOccupationIdsBatch([])).resolves.toEqual([]);
    expect(mocks.fetchOccupationDocuments).toHaveBeenCalledOnce();
  });
});
