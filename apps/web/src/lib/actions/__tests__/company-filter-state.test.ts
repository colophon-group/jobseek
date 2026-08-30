import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  getGeo: vi.fn(),
  parse: vi.fn(),
  complexity: vi.fn(),
}));

vi.mock("@/lib/search/params", () => ({
  getGeoFromHeaders: mocks.getGeo,
}));
vi.mock("@/lib/services/search-input", () => ({
  parseSearchFilters: mocks.parse,
  getSemanticSearchQueryComplexity: mocks.complexity,
}));

import { resolveCompanySemanticFilters } from "../company-filter-state";

const parsed = {
  keywords: [],
  locations: [],
  occupations: [],
  seniorities: [],
  technologies: [],
  workMode: ["remote"],
  employmentTypes: [],
};

beforeEach(() => {
  mocks.getGeo.mockReset();
  mocks.getGeo.mockResolvedValue({ userLat: 47.37, userLng: 8.54 });
  mocks.parse.mockReset();
  mocks.parse.mockResolvedValue(parsed);
  mocks.complexity.mockReset();
  mocks.complexity.mockReturnValue({
    uniqueTerms: 3,
    occupationCandidates: 6,
    maxTermLength: 9,
  });
});

describe("resolveCompanySemanticFilters", () => {
  it("delegates free text to the canonical parser with request geolocation", async () => {
    const result = await resolveCompanySemanticFilters({
      q: "remote backend developer",
      loc: "zurich",
      occ: "software-engineer",
      locale: "de",
    });

    expect(mocks.parse).toHaveBeenCalledWith({
      q: "remote backend developer",
      loc: "zurich",
      occ: "software-engineer",
      sen: undefined,
      tech: undefined,
      wm: undefined,
      etype: undefined,
      locale: "de",
      userLat: 47.37,
      userLng: 8.54,
    });
    expect(result).toEqual({
      parsed,
      userLat: 47.37,
      userLng: 8.54,
    });
  });

  it("normalizes unsupported route locales", async () => {
    await resolveCompanySemanticFilters({ q: "Zurich", locale: "xx" });
    expect(mocks.parse.mock.calls[0]?.[0]).toMatchObject({ locale: "en" });
  });

  it("rejects query fan-out and unsafe explicit slugs before server work", async () => {
    const tooManyTerms = Array.from({ length: 13 }, (_, index) => `q${index}`).join("/");

    mocks.complexity.mockReturnValueOnce({
      uniqueTerms: 13,
      occupationCandidates: 13,
      maxTermLength: 3,
    });

    await expect(
      resolveCompanySemanticFilters({ q: tooManyTerms, locale: "en" }),
    ).resolves.toBeNull();
    await expect(
      resolveCompanySemanticFilters({
        q: "remote",
        loc: "zurich`,company_id:!=[]",
        locale: "en",
      }),
    ).resolves.toBeNull();
    expect(mocks.getGeo).not.toHaveBeenCalled();
    expect(mocks.parse).not.toHaveBeenCalled();
  });

  it("caps canonical slash/pipe/hyphen candidate fan-out", async () => {
    mocks.complexity.mockReturnValue({
      uniqueTerms: 12,
      occupationCandidates: 37,
      maxTermLength: 3,
    });

    await expect(
      resolveCompanySemanticFilters({
        q: "a/b|c-d/e|f-g/h|i-j/k|l-m",
        locale: "en",
      }),
    ).resolves.toBeNull();
    expect(mocks.complexity).toHaveBeenCalledWith(
      "a/b|c-d/e|f-g/h|i-j/k|l-m",
    );
    expect(mocks.getGeo).not.toHaveBeenCalled();
    expect(mocks.parse).not.toHaveBeenCalled();
  });
});
