import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  cacheLife: vi.fn(),
  cacheTag: vi.fn(),
  fetchLocationDocumentsBySlugs: vi.fn(),
  fetchLocationDocumentsByIds: vi.fn(),
  fetchOccupationDocuments: vi.fn(),
  fetchSeniorityDocuments: vi.fn(),
  fetchTechnologyDocuments: vi.fn(),
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
  fetchLocationDocumentsBySlugs: mocks.fetchLocationDocumentsBySlugs,
  fetchLocationDocumentsByIds: mocks.fetchLocationDocumentsByIds,
  fetchOccupationDocuments: mocks.fetchOccupationDocuments,
  fetchSeniorityDocuments: mocks.fetchSeniorityDocuments,
  fetchTechnologyDocuments: mocks.fetchTechnologyDocuments,
  fetchLocationDescendants: vi.fn(),
  fetchLocationDocumentsWithAncestors: vi.fn(),
  fetchLocationMacroDocuments: vi.fn(),
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

import { resolveLocationSlugs } from "../locations";
import {
  resolveOccupationSlugs,
  resolveSenioritySlugs,
  resolveTechnologySlugs,
} from "../taxonomy";

beforeEach(() => {
  vi.clearAllMocks();
  mocks.fetchLocationDocumentsByIds.mockResolvedValue([]);
});

describe("Typesense taxonomy slug resolvers", () => {
  it("resolves localized location and parent labels exactly by slug", async () => {
    mocks.fetchLocationDocumentsBySlugs.mockResolvedValue([
      {
        location_id: 10,
        slug: "zürich",
        type: "city",
        name_en: "Zurich",
        name_de: "Zürich",
        parent_id: 20,
      },
    ]);
    mocks.fetchLocationDocumentsByIds.mockResolvedValue([
      {
        location_id: 20,
        slug: "zurich-canton",
        type: "region",
        name_en: "Canton of Zurich",
        name_de: "Kanton Zürich",
      },
    ]);

    const resolved = await resolveLocationSlugs(["zürich"], "de");
    expect(resolved.get("zürich")).toEqual({
      id: 10,
      slug: "zürich",
      name: "Zürich",
      type: "city",
      parentName: "Kanton Zürich",
    });
  });

  it("selects only requested occupation, seniority and technology slugs", async () => {
    mocks.fetchOccupationDocuments.mockResolvedValue([
      { occupation_id: 1, slug: "software-engineer", name: "Software Engineer" },
      { occupation_id: 2, slug: "designer", name: "Designer" },
    ]);
    mocks.fetchSeniorityDocuments.mockResolvedValue([
      { seniority_id: 3, slug: "senior", name: "Senior" },
      { seniority_id: 4, slug: "junior", name: "Junior" },
    ]);
    mocks.fetchTechnologyDocuments.mockResolvedValue([
      { technology_id: 5, slug: "rust", name: "Rust" },
      { technology_id: 6, slug: "go", name: "Go" },
    ]);

    const [occupations, seniorities, technologies] = await Promise.all([
      resolveOccupationSlugs(["designer"], "en"),
      resolveSenioritySlugs(["senior"], "en"),
      resolveTechnologySlugs(["go"]),
    ]);
    expect([...occupations.keys()]).toEqual(["designer"]);
    expect([...seniorities.keys()]).toEqual(["senior"]);
    expect([...technologies.keys()]).toEqual(["go"]);
  });

  it("keeps canonical sorting before the location cache boundary", async () => {
    mocks.fetchLocationDocumentsBySlugs.mockResolvedValue([]);
    await resolveLocationSlugs(["zürich", "berlin"], "en");
    expect(mocks.fetchLocationDocumentsBySlugs).toHaveBeenCalledWith([
      "berlin",
      "zürich",
    ]);
  });
});
