import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  getGeo: vi.fn(),
  parse: vi.fn(),
}));

vi.mock("@/lib/search/params", () => ({
  getGeoFromHeaders: mocks.getGeo,
}));
vi.mock("@/lib/services/search-input", () => ({
  parseSearchFilters: mocks.parse,
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
    const tooManyTerms = Array.from({ length: 21 }, (_, index) => `q${index}`).join(" ");

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
});
