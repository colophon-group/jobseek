import { beforeEach, describe, expect, it, vi } from "vitest";
import type { CompanyPageData } from "@/lib/actions/company-page-data";
import type { ParsedSearchFilters } from "@/lib/services/search-input";

const mocks = vi.hoisted(() => ({
  resolveFilters: vi.fn(),
  directPostings: vi.fn(),
  parseOffline: vi.fn(),
}));

vi.mock("@/lib/search/typesense-browser-filter-state", () => ({
  resolveCompanyFilterStateDirect: mocks.resolveFilters,
  parseCompanyFilterStateOffline: mocks.parseOffline,
}));
vi.mock("@/lib/search/search-runner", () => ({
  tryGetCompanyPostingsDirect: mocks.directPostings,
}));

import { loadCompanyBrowserData } from "../company-browser-data";

const emptyParsed: ParsedSearchFilters = {
  keywords: [],
  locations: [],
  occupations: [],
  seniorities: [],
  technologies: [],
  workMode: [],
  employmentTypes: [],
};

function makeData(overrides: Partial<CompanyPageData> = {}): CompanyPageData {
  return {
    company: {
      id: "company-1",
      name: "Example",
      slug: "example",
      icon: null,
      logo: null,
      website: null,
      description: null,
      industryId: null,
      industryName: null,
      employeeCountRange: null,
      foundedYear: null,
      activeJobCount: 50,
    },
    postings: [{ id: "broad-posting" } as CompanyPageData["postings"][number]],
    activeCount: 50,
    yearCount: 80,
    parsed: emptyParsed,
    displayCurrency: "EUR",
    jobLanguages: [],
    languages: ["en"],
    userLat: undefined,
    userLng: undefined,
    salaryCurrencyParam: "EUR",
    salaryMinDisplay: undefined,
    salaryMaxDisplay: undefined,
    experienceMin: undefined,
    experienceMax: undefined,
    showPostingId: null,
    ...overrides,
  };
}

beforeEach(() => {
  mocks.parseOffline.mockReset();
  mocks.parseOffline.mockImplementation((params: URLSearchParams) => {
    const q = params.get("q");
    const loc = params.get("loc");
    return {
      complete: !loc,
      parsed: {
        ...emptyParsed,
        keywords: q ? q.split(",") : [],
        ...(loc ? { unresolvedExplicitSlugs: { loc: loc.split(",") } } : {}),
      },
    };
  });
  mocks.resolveFilters.mockReset();
  mocks.resolveFilters.mockResolvedValue({ parsed: emptyParsed, complete: true });
  mocks.directPostings.mockReset();
  mocks.directPostings.mockResolvedValue({
    postings: [],
    activeCount: 0,
    yearCount: 0,
    truncated: false,
  });
});

describe("loadCompanyBrowserData", () => {
  it("uses resolved URL filters, preferences, bounds, and the authenticated cap", async () => {
    const parsed: ParsedSearchFilters = {
      ...emptyParsed,
      keywords: ["python"],
      locations: [
        {
          id: 10,
          slug: "zurich",
          name: "Zürich",
          type: "city",
          parentName: "Switzerland",
        },
      ],
      occupations: [{ id: 20, slug: "engineer", name: "Engineer" }],
      seniorities: [{ id: 30, slug: "senior", name: "Senior" }],
      technologies: [{ id: 40, slug: "react", name: "React" }],
      workMode: ["remote"],
      employmentTypes: ["full_time"],
    };
    mocks.resolveFilters.mockResolvedValue({ parsed, complete: true });
    mocks.directPostings.mockResolvedValue({
      postings: [{ id: "filtered" }],
      activeCount: 1,
      yearCount: 2,
      truncated: false,
    });

    const result = await loadCompanyBrowserData({
      initialData: makeData(),
      searchParams: new URLSearchParams(
        "q=python&loc=zurich&sal=100-200&salcur=CHF&exp=2-5",
      ),
      locale: "de",
      displayCurrency: "CHF",
      jobLanguages: ["de", "en"],
      rates: [{ currency: "CHF", toEur: 1.04 }],
      isLoggedIn: true,
    });

    expect(mocks.directPostings).toHaveBeenCalledWith(
      {
        companyId: "company-1",
        keywords: ["python"],
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
        limit: 20,
      },
      true,
    );
    expect(result).toMatchObject({
      unavailable: false,
      directAttempted: true,
      data: {
        activeCount: 1,
        displayCurrency: "CHF",
        jobLanguages: ["de", "en"],
        salaryCurrencyParam: "CHF",
      },
    });
  });

  it("passes anonymous status to preserve posting caps", async () => {
    mocks.resolveFilters.mockResolvedValue({
      parsed: { ...emptyParsed, keywords: ["python"] },
      complete: true,
    });

    await loadCompanyBrowserData({
      initialData: makeData(),
      searchParams: new URLSearchParams("q=python"),
      locale: "en",
      displayCurrency: null,
      jobLanguages: [],
      rates: [],
      isLoggedIn: false,
    });

    expect(mocks.directPostings.mock.calls[0]?.[1]).toBe(false);
  });

  it("fails closed when direct posting search is degraded", async () => {
    mocks.resolveFilters.mockResolvedValue({
      parsed: { ...emptyParsed, keywords: ["python"] },
      complete: true,
    });
    mocks.directPostings.mockResolvedValue(null);

    const result = await loadCompanyBrowserData({
      initialData: makeData(),
      searchParams: new URLSearchParams("q=python"),
      locale: "en",
      displayCurrency: "EUR",
      jobLanguages: [],
      rates: [],
      isLoggedIn: false,
    });

    expect(result.unavailable).toBe(true);
    expect(result.data.postings).toEqual([]);
    expect(result.data.activeCount).toBe(0);
    expect(result.data.parsed.keywords).toEqual(["python"]);
  });

  it("does not query postings when an explicit slug is unresolved", async () => {
    const parsed: ParsedSearchFilters = {
      ...emptyParsed,
      unresolvedExplicitSlugs: { loc: ["missing-place"] },
    };
    mocks.resolveFilters.mockResolvedValue({ parsed, complete: false });

    const result = await loadCompanyBrowserData({
      initialData: makeData(),
      searchParams: new URLSearchParams("loc=missing-place"),
      locale: "en",
      displayCurrency: "EUR",
      jobLanguages: [],
      rates: [],
      isLoggedIn: false,
    });

    expect(mocks.directPostings).not.toHaveBeenCalled();
    expect(result.unavailable).toBe(true);
    expect(result.data.parsed.unresolvedExplicitSlugs).toEqual({
      loc: ["missing-place"],
    });
  });

  it("reuses the shell when browser state does not change results", async () => {
    const initialData = makeData();
    const result = await loadCompanyBrowserData({
      initialData,
      searchParams: new URLSearchParams("salcur=EUR"),
      locale: "en",
      displayCurrency: "EUR",
      jobLanguages: [],
      rates: [],
      isLoggedIn: false,
    });

    expect(mocks.directPostings).not.toHaveBeenCalled();
    expect(result).toMatchObject({
      unavailable: false,
      directAttempted: false,
      data: { postings: initialData.postings },
    });
  });

  it("rejects malformed numeric ranges without searching", async () => {
    const result = await loadCompanyBrowserData({
      initialData: makeData(),
      searchParams: new URLSearchParams("sal=100-not-a-number"),
      locale: "en",
      displayCurrency: "EUR",
      jobLanguages: [],
      rates: [],
      isLoggedIn: false,
    });

    expect(mocks.resolveFilters).not.toHaveBeenCalled();
    expect(mocks.directPostings).not.toHaveBeenCalled();
    expect(result.unavailable).toBe(true);
    expect(result.data.postings).toEqual([]);
  });
});
