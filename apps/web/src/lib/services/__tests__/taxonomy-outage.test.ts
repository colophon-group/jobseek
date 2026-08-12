import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  cached: vi.fn((_key: string, fetcher: () => unknown) => fetcher()),
  search: vi.fn(),
}));

vi.mock("server-only", () => ({}));
vi.mock("next/cache", () => ({ cacheLife: vi.fn(), cacheTag: vi.fn() }));
vi.mock("@/lib/cache", () => ({ cached: mocks.cached }));
vi.mock("@/lib/cache-tags", () => ({
  typeaheadOccupationsCacheTag: () => "occupations",
  typeaheadSenioritiesCacheTag: () => "seniorities",
  typeaheadTechnologiesCacheTag: () => "technologies",
}));
vi.mock("@/lib/search/typesense-client", () => ({
  getTypesenseClient: () => ({
    collections: () => ({ documents: () => ({ search: mocks.search }) }),
  }),
}));
vi.mock("@/lib/search/typesense-taxonomy", () => ({
  fetchOccupationDocuments: () => [],
  fetchSeniorityDocuments: () => [
    { id: "1-en", seniority_id: 1, slug: "senior", name: "Senior", locale: "en" },
  ],
  fetchTechnologyDocuments: () => [],
}));
vi.mock("@/lib/search/typesense-filters", () => ({
  buildFilterString: () => "",
  POSTING_BASE_FILTER: "is_active:true",
}));
vi.mock("@/lib/search/typeahead-boost", () => ({
  boostByFilterMatches: (values: unknown) => values,
}));

import { getAllSeniorities } from "../taxonomy";

beforeEach(() => {
  vi.clearAllMocks();
  vi.spyOn(console, "error").mockImplementation(() => {});
});

describe("taxonomy outage policy", () => {
  it("degrades an optional taxonomy surface only for availability failures", async () => {
    mocks.search.mockRejectedValue(
      Object.assign(new Error("read ECONNRESET"), { code: "ECONNRESET" }),
    );

    await expect(getAllSeniorities("en")).resolves.toEqual([]);
    expect(mocks.cached).toHaveBeenCalledTimes(1);
  });

  it("propagates rate limits and other unexpected errors", async () => {
    mocks.search.mockRejectedValue(
      Object.assign(new Error("Request failed with HTTP code 429"), {
        httpStatus: 429,
      }),
    );

    const rejection = getAllSeniorities("en");
    await expect(rejection).rejects.toMatchObject({
      message: "Typesense request failed",
      httpStatus: 429,
    });
  });
});
