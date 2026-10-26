import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ExploreData } from "@/lib/actions/explore-page-data";
import type { ParsedSearchFilters } from "@/lib/services/search-input";

const mocks = vi.hoisted(() => ({
  parseOffline: vi.fn(),
  resolveFilters: vi.fn(),
  semanticFilters: vi.fn(),
  directList: vi.fn(),
  directSearch: vi.fn(),
  serverPage: vi.fn(),
}));

vi.mock("@/lib/actions/explore-page-data", () => ({
  fetchExplorePageData: mocks.serverPage,
}));
vi.mock("@/lib/search/typesense-browser-filter-state", () => ({
  parseCompanyFilterStateOffline: mocks.parseOffline,
  resolveCompanyFilterStateDirect: mocks.resolveFilters,
}));
vi.mock("@/lib/actions/company-filter-state", () => ({
  resolveCompanySemanticFilters: mocks.semanticFilters,
}));
vi.mock("@/lib/search/search-runner", () => ({
  tryListTopCompaniesDirect: mocks.directList,
  trySearchJobsDirect: mocks.directSearch,
}));

import { loadExploreBrowserData } from "../explore-browser-data";

const emptyParsed: ParsedSearchFilters = {
  keywords: [],
  locations: [],
  occupations: [],
  seniorities: [],
  technologies: [],
  workMode: [],
  employmentTypes: [],
};

function makeData(overrides: Partial<ExploreData> = {}): ExploreData {
  return {
    result: {
      companies: [{ company: { id: "broad" } } as never],
      totalCompanies: 1,
    },
    parsed: emptyParsed,
    displayCurrency: "EUR",
    jobLanguages: [],
    languages: ["en"],
    languageOverride: null,
    userLat: undefined,
    userLng: undefined,
    salaryCurrencyParam: "EUR",
    salaryMinDisplay: undefined,
    salaryMaxDisplay: undefined,
    experienceMin: undefined,
    experienceMax: undefined,
    ...overrides,
  };
}

beforeEach(() => {
  mocks.parseOffline.mockReset();
  mocks.parseOffline.mockReturnValue({ parsed: emptyParsed, complete: true });
  mocks.resolveFilters.mockReset();
  mocks.resolveFilters.mockResolvedValue({ parsed: emptyParsed, complete: true });
  mocks.semanticFilters.mockReset();
  mocks.semanticFilters.mockImplementation(({ q }: { q: string }) => ({
    parsed: { ...emptyParsed, keywords: [q] },
    userLat: 47.37,
    userLng: 8.54,
  }));
  mocks.directList.mockReset();
  mocks.directList.mockResolvedValue({ companies: [], totalCompanies: 0 });
  mocks.directSearch.mockReset();
  mocks.directSearch.mockResolvedValue({ companies: [], totalCompanies: 0 });
  mocks.serverPage.mockReset();
  mocks.serverPage.mockResolvedValue(makeData({
    result: { companies: [], totalCompanies: 0, degraded: true },
  }));
});

describe("loadExploreBrowserData", () => {
  it("resolves explicit filters and searches directly with preferences and EUR bounds", async () => {
    const parsed: ParsedSearchFilters = {
      ...emptyParsed,
      locations: [{
        id: 10,
        slug: "zurich",
        name: "Zürich",
        type: "city",
        parentName: "Switzerland",
      }],
      occupations: [{ id: 20, slug: "engineer", name: "Engineer" }],
      seniorities: [{ id: 30, slug: "senior", name: "Senior" }],
      technologies: [{ id: 40, slug: "react", name: "React" }],
      workMode: ["remote"],
      employmentTypes: ["full_time"],
    };
    mocks.resolveFilters.mockResolvedValue({ parsed, complete: true });

    const result = await loadExploreBrowserData({
      initialData: makeData(),
      searchParams: new URLSearchParams(
        "loc=zurich&occ=engineer&sen=senior&tech=react&wm=remote&etype=full_time&sal=100-200&salcur=CHF&exp=2-5",
      ),
      locale: "de",
      displayCurrency: "CHF",
      jobLanguages: ["de", "en"],
      rates: [{ currency: "CHF", toEur: 1.04 }],
      isLoggedIn: true,
    });

    expect(mocks.directList).toHaveBeenCalledWith(
      {
        locationIds: [10],
        occupationIds: [20],
        seniorityIds: [30],
        technologyIds: [40],
        employmentTypes: ["full_time"],
        workMode: ["remote"],
        salaryMinEur: 104,
        salaryMaxEur: 208,
        experienceMin: 2,
        experienceMax: 5,
        languages: ["de", "en"],
        locale: "de",
        offset: 0,
        limit: 10,
      },
      true,
    );
    expect(mocks.directSearch).not.toHaveBeenCalled();
    expect(mocks.serverPage).not.toHaveBeenCalled();
    expect(result).toMatchObject({
      unavailable: false,
      directAttempted: true,
      data: {
        displayCurrency: "CHF",
        jobLanguages: ["de", "en"],
        salaryCurrencyParam: "CHF",
      },
    });
  });

  it("keeps canonical semantic q parsing and geolocation but reads results directly", async () => {
    const parsed: ParsedSearchFilters = {
      ...emptyParsed,
      keywords: ["python"],
      locations: [{
        id: 10,
        slug: "zurich",
        name: "Zurich",
        type: "city",
        parentName: "Switzerland",
      }],
      workMode: ["remote"],
    };
    mocks.semanticFilters.mockResolvedValue({
      parsed,
      userLat: 47.37,
      userLng: 8.54,
    });

    const result = await loadExploreBrowserData({
      initialData: makeData(),
      searchParams: new URLSearchParams("q=remote%20Zurich%20python"),
      locale: "en",
      displayCurrency: "EUR",
      jobLanguages: [],
      rates: [],
      isLoggedIn: false,
    });

    expect(mocks.semanticFilters).toHaveBeenCalledWith({
      q: "remote Zurich python",
      loc: undefined,
      occ: undefined,
      sen: undefined,
      tech: undefined,
      wm: undefined,
      etype: undefined,
      locale: "en",
    });
    expect(mocks.resolveFilters).not.toHaveBeenCalled();
    expect(mocks.directSearch).toHaveBeenCalledWith(
      expect.objectContaining({
        keywords: ["python"],
        locationIds: [10],
        workMode: ["remote"],
        languages: [],
      }),
      false,
    );
    expect(result.data).toMatchObject({ userLat: 47.37, userLng: 8.54 });
  });

  it("honors a public language override independently of preferences", async () => {
    await loadExploreBrowserData({
      initialData: makeData(),
      searchParams: new URLSearchParams("lang=fr,it"),
      locale: "de",
      displayCurrency: "EUR",
      jobLanguages: ["de", "en"],
      rates: [],
      isLoggedIn: false,
    });

    expect(mocks.directList).toHaveBeenCalledWith(
      expect.objectContaining({ languages: ["fr", "it"] }),
      false,
    );
  });

  it("fails closed when both direct and server search are unavailable", async () => {
    mocks.semanticFilters.mockResolvedValue({
      parsed: { ...emptyParsed, keywords: ["python"] },
      userLat: undefined,
      userLng: undefined,
    });
    mocks.directSearch.mockResolvedValue(null);

    const result = await loadExploreBrowserData({
      initialData: makeData(),
      searchParams: new URLSearchParams("q=python"),
      locale: "en",
      displayCurrency: "EUR",
      jobLanguages: [],
      rates: [],
      isLoggedIn: false,
    });

    expect(result.unavailable).toBe(true);
    expect(result.directAttempted).toBe(true);
    expect(result.data.result).toEqual({
      companies: [],
      totalCompanies: 0,
      degraded: true,
    });
    expect(result.data.parsed.keywords).toEqual(["python"]);
    expect(mocks.serverPage).toHaveBeenCalledOnce();
  });

  it.each(["taxonomy", "listing", "keywords"])(
    "recovers a failed browser %s read through one filtered server action",
    async (failure) => {
      const searchParams = new URLSearchParams(
        "loc=india&sal=100000-&salcur=EUR&lang=fr,it&utm_source=ignored&show=ignored",
      );
      if (failure === "taxonomy") {
        mocks.resolveFilters.mockRejectedValue(new TypeError("Failed to fetch"));
      } else if (failure === "listing") {
        mocks.directList.mockResolvedValue(null);
      } else {
        searchParams.set("q", "python");
        mocks.directSearch.mockResolvedValue(null);
      }
      const recovered = makeData({
        result: { companies: [{ company: { id: "filtered" } } as never], totalCompanies: 1 },
        displayCurrency: "INR",
        salaryCurrencyParam: "EUR",
        languages: ["fr", "it"],
      });
      mocks.serverPage.mockResolvedValue(recovered);

      const result = await loadExploreBrowserData({
        initialData: makeData(), searchParams, locale: "en",
        displayCurrency: "INR", jobLanguages: [], rates: [], isLoggedIn: true,
      });

      expect(mocks.serverPage).toHaveBeenCalledExactlyOnceWith({
        searchParams: {
          loc: "india", sal: "100000-", salcur: "EUR", lang: "fr,it",
          ...(failure === "keywords" ? { q: "python" } : {}),
        },
        locale: "en",
      });
      expect(result).toEqual({ data: recovered, unavailable: false, directAttempted: true });
    },
  );

  it("keeps the requested filters unavailable when the recovery action rejects", async () => {
    const parsed = { ...emptyParsed, unresolvedExplicitSlugs: { loc: ["india"] } };
    mocks.parseOffline.mockReturnValue({ parsed, complete: false });
    mocks.resolveFilters.mockRejectedValue(new TypeError("Failed to fetch"));
    mocks.serverPage.mockRejectedValue(new Error("Server unavailable"));
    const result = await loadExploreBrowserData({
      initialData: makeData(), searchParams: new URLSearchParams("loc=india&salcur=EUR"),
      locale: "en", displayCurrency: "INR", jobLanguages: [], rates: [], isLoggedIn: true,
    });
    expect(mocks.serverPage).toHaveBeenCalledOnce();
    expect(result.unavailable).toBe(true);
    expect(result.data.result).toEqual({ companies: [], totalCompanies: 0, degraded: true });
    expect(result.data.parsed.unresolvedExplicitSlugs).toEqual({ loc: ["india"] });
  });

  it("does not broaden unresolved explicit slugs", async () => {
    const parsed: ParsedSearchFilters = {
      ...emptyParsed,
      unresolvedExplicitSlugs: { loc: ["missing-place"] },
    };
    mocks.resolveFilters.mockResolvedValue({ parsed, complete: false });

    const result = await loadExploreBrowserData({
      initialData: makeData(),
      searchParams: new URLSearchParams("loc=missing-place"),
      locale: "en",
      displayCurrency: "EUR",
      jobLanguages: [],
      rates: [],
      isLoggedIn: false,
    });

    expect(mocks.directList).not.toHaveBeenCalled();
    expect(mocks.directSearch).not.toHaveBeenCalled();
    expect(result.unavailable).toBe(true);
    expect(mocks.serverPage).not.toHaveBeenCalled();
    expect(result.data.parsed.unresolvedExplicitSlugs).toEqual({
      loc: ["missing-place"],
    });
  });

  it("rejects malformed numeric ranges before a direct query", async () => {
    const result = await loadExploreBrowserData({
      initialData: makeData(),
      searchParams: new URLSearchParams("sal=not-a-range"),
      locale: "en",
      displayCurrency: "EUR",
      jobLanguages: [],
      rates: [],
      isLoggedIn: false,
    });

    expect(mocks.resolveFilters).not.toHaveBeenCalled();
    expect(mocks.directList).not.toHaveBeenCalled();
    expect(result.unavailable).toBe(true);
    expect(mocks.serverPage).not.toHaveBeenCalled();
  });
});
