import { describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ perform: vi.fn() }));
vi.mock("server-only", () => ({}));
vi.mock("@/lib/search/typesense-client", () => ({
  getTypesenseClient: () => ({ multiSearch: { perform: mocks.perform } }),
}));
vi.mock("@/lib/services/company", () => ({ suggestCompanies: vi.fn() }));
vi.mock("@/lib/services/locations", () => ({ suggestLocations: vi.fn() }));
vi.mock("@/lib/services/taxonomy", () => ({
  suggestOccupations: vi.fn(), suggestSeniorities: vi.fn(), suggestTechnologies: vi.fn(),
}));

import { suggestSearchBarTermTypeahead } from "../typeahead";

describe("prior-term suggestion batch", () => {
  it("uses one Typesense request and keeps hits attached to their source term", async () => {
    mocks.perform.mockImplementation(async ({ searches }: { searches: Array<{ collection: string; q: string }> }) => ({
      results: searches.map(({ collection, q }) => ({ hits: collection === "occupation" && q === "accountant"
        ? [{ document: { occupation_id: 9, slug: "accountant", name: "Accountant" } }] : [] })),
    }));
    const result = await suggestSearchBarTermTypeahead({
      terms: ["junior", "accountant", "Zurich"], locale: "en",
    });
    expect(mocks.perform).toHaveBeenCalledTimes(1);
    expect(mocks.perform.mock.calls[0][0].searches).toHaveLength(12);
    expect(result.map((item) => item.term)).toEqual(["junior", "accountant", "Zurich"]);
    expect(result[0].occupations).toEqual([]);
    expect(result[1].occupations).toEqual([{ id: 9, slug: "accountant", name: "Accountant" }]);
    expect(result[2].occupations).toEqual([]);
  });
});
