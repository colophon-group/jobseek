// @vitest-environment node
import { beforeEach, describe, expect, it, vi } from "vitest";
vi.mock("server-only", () => ({}));
const mocks = vi.hoisted(() => ({ locations: vi.fn(), parents: vi.fn(), occupations: vi.fn(), seniorities: vi.fn(), technologies: vi.fn(), detail: vi.fn() }));
vi.mock("../typesense-taxonomy", () => ({
  fetchLocationDocumentsBySlugs: mocks.locations, fetchLocationDocumentsByIds: mocks.parents,
  fetchOccupationDocuments: mocks.occupations, fetchSeniorityDocuments: mocks.seniorities, fetchTechnologyDocuments: mocks.technologies,
}));
vi.mock("../typesense-posting-detail", () => ({ fetchIndexedPostingDetail: mocks.detail }));
import { resolveLocationSlugs, resolveOccupationSlugs, resolveSenioritySlugs, resolveTechnologySlugs, withMatchingDataReads } from "../matching-data";

beforeEach(() => vi.resetAllMocks());
describe("standalone matching data", () => {
  it("preserves localized names and parent fallback without the web cache runtime", async () => {
    mocks.locations.mockResolvedValue([{ location_id: 2, slug: "zurich", name_en: "Zurich", name_de: "Zürich", parent_id: 1, type: "city" }]);
    mocks.parents.mockResolvedValue([{ location_id: 1, slug: "switzerland", name_en: "Switzerland", name_de: "Schweiz" }]);
    const result = await resolveLocationSlugs(["zurich"], "de");
    expect(result.get("zurich")).toMatchObject({ id: 2, name: "Zürich", parentName: "Schweiz" });
    mocks.occupations.mockResolvedValue([{ occupation_id: 3, slug: "engineer", name: "Engineer" }, { occupation_id: 4, slug: "other", name: "Other" }]);
    mocks.seniorities.mockResolvedValue([{ seniority_id: 5, slug: "senior", name: "Senior" }]);
    mocks.technologies.mockResolvedValue([{ technology_id: 6, slug: "rust", name: null }]);
    expect([...(await resolveOccupationSlugs(["engineer"], "en")).values()]).toEqual([{ id: 3, slug: "engineer", name: "Engineer" }]);
    expect((await resolveSenioritySlugs(["senior"], "en")).get("senior")?.id).toBe(5);
    expect((await resolveTechnologySlugs(["rust"])).get("rust")?.name).toBe("rust");
  });

  it("deduplicates concurrent lookups within a slice and reads changed taxonomy on the next slice", async () => {
    mocks.occupations.mockResolvedValueOnce([{ occupation_id: 3, slug: "engineer", name: "Before" }])
      .mockResolvedValueOnce([{ occupation_id: 3, slug: "engineer", name: "After" }]);
    await withMatchingDataReads(async () => {
      const [a,b] = await Promise.all([resolveOccupationSlugs(["engineer"], "en"), resolveOccupationSlugs(["engineer"], "en")]);
      expect(a.get("engineer")?.name).toBe("Before");
      expect(b.get("engineer")?.name).toBe("Before");
      expect(mocks.occupations).toHaveBeenCalledOnce();
    });
    const next = await withMatchingDataReads(() => resolveOccupationSlugs(["engineer"], "en"));
    expect(next.get("engineer")?.name).toBe("After");
    expect(mocks.occupations).toHaveBeenCalledTimes(2);
  });
});
