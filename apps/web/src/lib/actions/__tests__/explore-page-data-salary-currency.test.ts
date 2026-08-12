import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Hoisted mocks so they apply before module imports below. Mirrors the
// pattern in `company-page-data-defaults.test.ts`.
const mocks = vi.hoisted(() => ({
  searchJobs: vi.fn(),
  listTopCompanies: vi.fn(),
  listTopCompaniesAnonymous: vi.fn(),
  getCurrencyRates: vi.fn(),
  getSession: vi.fn(),
  getPreferences: vi.fn(),
  readAnonJobLanguagesCookie: vi.fn(),
  getGeoFromHeaders: vi.fn(),
  parseSearchFilters: vi.fn(),
  parseRangeParam: vi.fn(),
  logExternalError: vi.fn(),
}));

vi.mock("server-only", () => ({}));

vi.mock("@/lib/actions/search", () => ({
  searchJobs: mocks.searchJobs,
  listTopCompanies: mocks.listTopCompanies,
  listTopCompaniesAnonymous: mocks.listTopCompaniesAnonymous,
  getCurrencyRates: mocks.getCurrencyRates,
}));
vi.mock("@/lib/sessionCache", () => ({ getSession: mocks.getSession }));
vi.mock("@/lib/actions/preferences", () => ({
  getPreferences: mocks.getPreferences,
}));
vi.mock("@/lib/anon-preferences", () => ({
  readAnonJobLanguagesCookie: mocks.readAnonJobLanguagesCookie,
}));
vi.mock("@/lib/search/params", () => ({
  firstOf: (v: unknown) => (Array.isArray(v) ? v[0] : v),
  idsOrUndefined: (items: { id: number }[]) =>
    items.length > 0 ? items.map((i) => i.id) : undefined,
  parseRangeParam: (v: string | undefined) => mocks.parseRangeParam(v),
  getGeoFromHeaders: mocks.getGeoFromHeaders,
}));
vi.mock("@/lib/actions/search-input", () => ({
  parseSearchFilters: mocks.parseSearchFilters,
}));
vi.mock("@/lib/safe-external-error", () => ({
  logExternalError: mocks.logExternalError,
}));

import { fetchExplorePageData, fetchExplorePageDefaults } from "../explore-page-data";

beforeEach(() => {
  vi.resetAllMocks();
  stubTypesenseConfiguration(true);
  mocks.searchJobs.mockResolvedValue({
    companies: [],
    totalCompanies: 0,
  });
  mocks.listTopCompanies.mockResolvedValue({
    companies: [],
    totalCompanies: 0,
  });
  mocks.listTopCompaniesAnonymous.mockResolvedValue({
    companies: [],
    totalCompanies: 0,
  });
  mocks.getSession.mockResolvedValue(null);
  mocks.getPreferences.mockResolvedValue(null);
  mocks.readAnonJobLanguagesCookie.mockResolvedValue(null);
  mocks.getGeoFromHeaders.mockResolvedValue({
    userLat: undefined,
    userLng: undefined,
  });
  mocks.parseSearchFilters.mockResolvedValue({
    keywords: [],
    locations: [],
    occupations: [],
    seniorities: [],
    technologies: [],
    workMode: [],
    employmentTypes: [],
  });
  mocks.parseRangeParam.mockReturnValue({ min: undefined, max: undefined });
  mocks.getCurrencyRates.mockResolvedValue([
    { currency: "USD", toEur: 0.92 },
    { currency: "CHF", toEur: 0.95 },
    { currency: "JPY", toEur: 0.006 },
  ]);
});

afterEach(() => {
  vi.unstubAllEnvs();
});

function stubTypesenseConfiguration(configured: boolean): void {
  vi.stubEnv("TYPESENSE_HOST", configured ? "typesense.example.test" : "");
  vi.stubEnv("TYPESENSE_PORT", configured ? "443" : "");
  vi.stubEnv("TYPESENSE_PROTOCOL", configured ? "https" : "");
  vi.stubEnv("TYPESENSE_SEARCH_KEY", configured ? "search-key" : "");
}

describe("fetchExplorePageData — salary EUR conversion (#3178)", () => {
  it("converts USD 100K filter to ~92000 EUR before calling Typesense (was 100000 pre-fix)", async () => {
    // The headline #3178 scenario: a US user with salcur=USD enters "$100K+".
    // Pre-fix, salaryMinEur was passed through as 100000, so the filter
    // `salary_eur:[100000..]` excluded $100K US roles whose salary_eur ≈ 92,000.
    // Post-fix, the converted EUR value is what reaches the Typesense query.
    mocks.parseRangeParam.mockReturnValueOnce({ min: 100000, max: undefined });

    await fetchExplorePageData({
      searchParams: { sal: "100000-", salcur: "USD" },
      locale: "en",
    });

    expect(mocks.getCurrencyRates).toHaveBeenCalled();
    expect(mocks.listTopCompanies).toHaveBeenCalledTimes(1);
    const callArgs = mocks.listTopCompanies.mock.calls[0][0];
    expect(callArgs.salaryMinEur).toBe(92000);
    expect(callArgs.salaryMaxEur).toBeUndefined();
  });

  it("converts CHF 100K filter to ~95000 EUR", async () => {
    mocks.parseRangeParam.mockReturnValueOnce({ min: 100000, max: undefined });

    await fetchExplorePageData({
      searchParams: { sal: "100000-", salcur: "CHF" },
      locale: "en",
    });

    const callArgs = mocks.listTopCompanies.mock.calls[0][0];
    expect(callArgs.salaryMinEur).toBe(95000);
  });

  it("converts JPY 10M filter to ~60000 EUR (extreme weak-currency case)", async () => {
    // From the #3178 issue body: "JPY 10M" (≈ EUR 60K, a low-end senior
    // Japan salary) used to become `salary_eur:[10000000..]` — EUR 10M,
    // which excludes every posting on the platform. Now: ~60000 EUR.
    mocks.parseRangeParam.mockReturnValueOnce({ min: 10_000_000, max: undefined });

    await fetchExplorePageData({
      searchParams: { sal: "10000000-", salcur: "JPY" },
      locale: "en",
    });

    const callArgs = mocks.listTopCompanies.mock.calls[0][0];
    expect(callArgs.salaryMinEur).toBe(60000);
  });

  it("leaves EUR 100K unchanged (identity branch — no rate lookup needed)", async () => {
    mocks.parseRangeParam.mockReturnValueOnce({ min: 100000, max: undefined });

    await fetchExplorePageData({
      searchParams: { sal: "100000-", salcur: "EUR" },
      locale: "en",
    });

    const callArgs = mocks.listTopCompanies.mock.calls[0][0];
    expect(callArgs.salaryMinEur).toBe(100000);
  });

  it("does NOT fetch currency rates when no salary filter is active", async () => {
    // Defensive guard: don't hit the rates cache on every render — only on
    // renders where the user actually has a salary filter applied.
    mocks.parseRangeParam.mockReturnValueOnce({ min: undefined, max: undefined });

    await fetchExplorePageData({
      searchParams: {},
      locale: "en",
    });

    expect(mocks.getCurrencyRates).not.toHaveBeenCalled();
  });

  it("falls back to displayCurrency when salcur is absent (USD-display user)", async () => {
    // Verifies the salcur fallback chain: `salcur ?? displayCurrency`.
    // A USD-display user without an explicit salcur should still get
    // the EUR conversion applied based on their display preference.
    mocks.getSession.mockResolvedValueOnce({ user: { id: "u1" } });
    mocks.getPreferences.mockResolvedValueOnce({
      displayCurrency: "USD",
      jobLanguages: [],
    });
    mocks.parseRangeParam.mockReturnValueOnce({ min: 100000, max: undefined });

    await fetchExplorePageData({
      searchParams: { sal: "100000-" },
      locale: "en",
    });

    const callArgs = mocks.listTopCompanies.mock.calls[0][0];
    expect(callArgs.salaryMinEur).toBe(92000);
  });

  it("preserves both min and max conversion (range filter)", async () => {
    mocks.parseRangeParam.mockReturnValueOnce({ min: 50000, max: 150000 });

    await fetchExplorePageData({
      searchParams: { sal: "50000-150000", salcur: "USD" },
      locale: "en",
    });

    const callArgs = mocks.listTopCompanies.mock.calls[0][0];
    expect(callArgs.salaryMinEur).toBe(46000); // 50000 * 0.92
    expect(callArgs.salaryMaxEur).toBe(138000); // 150000 * 0.92
  });
});

describe("Explore repository fallback — configuration boundary (#2640)", () => {
  it("adds profile-only fallback identities to a secretless default build", async () => {
    stubTypesenseConfiguration(false);
    mocks.listTopCompaniesAnonymous.mockResolvedValueOnce({
      companies: [],
      totalCompanies: 0,
      degraded: true,
    });

    const data = await fetchExplorePageDefaults({ locale: "en" });

    expect(data.result).toEqual({
      companies: [],
      totalCompanies: 0,
      degraded: true,
    });
    expect(data.repositoryFallbackCompanies).toHaveLength(10);
    expect(data.repositoryFallbackCompanies?.[0]).toEqual({
      name: "Accenture",
      slug: "accenture",
    });
    expect(mocks.listTopCompaniesAnonymous).not.toHaveBeenCalled();
  });

  it("does not replace an empty degraded response when Typesense is configured", async () => {
    stubTypesenseConfiguration(true);
    mocks.listTopCompaniesAnonymous.mockResolvedValueOnce({
      companies: [],
      totalCompanies: 0,
      degraded: true,
    });

    const data = await fetchExplorePageDefaults({ locale: "en" });

    expect(data.result.degraded).toBe(true);
    expect(data.repositoryFallbackCompanies).toBeUndefined();
  });

  it("keeps a legitimate configured zero-result filtered search distinct", async () => {
    stubTypesenseConfiguration(true);
    mocks.parseSearchFilters.mockResolvedValueOnce({
      keywords: ["no-match"],
      locations: [],
      occupations: [],
      seniorities: [],
      technologies: [],
      workMode: [],
      employmentTypes: [],
    });
    mocks.searchJobs.mockResolvedValueOnce({
      companies: [],
      totalCompanies: 0,
    });

    const data = await fetchExplorePageData({
      searchParams: { q: "no-match" },
      locale: "en",
    });

    expect(data.result).toEqual({ companies: [], totalCompanies: 0 });
    expect(data.repositoryFallbackCompanies).toBeUndefined();
  });

  it("carries the offline profile fallback through a filtered secretless request", async () => {
    stubTypesenseConfiguration(false);
    mocks.parseSearchFilters.mockResolvedValueOnce({
      keywords: ["python"],
      locations: [],
      occupations: [],
      seniorities: [],
      technologies: [],
      workMode: [],
      employmentTypes: [],
    });
    mocks.searchJobs.mockResolvedValueOnce({
      companies: [],
      totalCompanies: 0,
      degraded: true,
    });

    const data = await fetchExplorePageData({
      searchParams: { q: "python" },
      locale: "de",
    });

    expect(data.result.companies).toEqual([]);
    expect(data.repositoryFallbackCompanies).toHaveLength(10);
    expect(data.parsed.keywords).toEqual(["python"]);
    expect(mocks.parseSearchFilters).not.toHaveBeenCalled();
    expect(mocks.searchJobs).not.toHaveBeenCalled();
  });

  it("preserves an all-language override through a secretless fallback", async () => {
    stubTypesenseConfiguration(false);

    const data = await fetchExplorePageData({
      searchParams: { lang: "*" },
      locale: "en",
    });

    expect(data.repositoryFallbackCompanies).toHaveLength(10);
    expect(data.languages).toEqual([]);
    expect(data.languageOverride).toEqual([]);
    expect(mocks.parseSearchFilters).not.toHaveBeenCalled();
    expect(mocks.searchJobs).not.toHaveBeenCalled();
    expect(mocks.listTopCompanies).not.toHaveBeenCalled();
  });
});

describe("fetchExplorePageData — public language override (#6132)", () => {
  it("uses `lang` instead of locale/preferences for the linked result set", async () => {
    const data = await fetchExplorePageData({
      searchParams: { lang: "de,fr" },
      locale: "en",
    });

    const callArgs = mocks.listTopCompanies.mock.calls[0][0];
    expect(callArgs.languages).toEqual(["de", "fr"]);
    expect(data.languages).toEqual(["de", "fr"]);
    expect(data.languageOverride).toEqual(["de", "fr"]);
    expect(data.jobLanguages).toEqual([]);
  });

  it("maps the Explore `*` sentinel to REST's all-language semantics", async () => {
    const data = await fetchExplorePageData({
      searchParams: { lang: "*" },
      locale: "en",
    });

    expect(mocks.listTopCompanies.mock.calls[0][0].languages).toEqual([]);
    expect(data.languages).toEqual([]);
    expect(data.languageOverride).toEqual([]);
  });

  it("falls back to the normal preference semantics without `lang`", async () => {
    const data = await fetchExplorePageData({
      searchParams: {},
      locale: "it",
    });

    expect(mocks.listTopCompanies.mock.calls[0][0].languages).toEqual(["it"]);
    expect(data.languageOverride).toBeNull();
  });
});

describe("fetchExplorePageData — unavailable taxonomy resolution (#7218)", () => {
  it("returns a degraded response and preserves an explicit location slug on timeout", async () => {
    const timeout = Object.assign(new Error("request timed out"), {
      code: "ETIMEDOUT",
    });
    mocks.parseSearchFilters.mockRejectedValueOnce(timeout);

    const data = await fetchExplorePageData({
      searchParams: {
        q: "backend developer",
        loc: "india,india",
        wm: "remote",
        etype: "full_time",
      },
      locale: "en",
    });

    expect(data.result).toEqual({
      companies: [],
      totalCompanies: 0,
      degraded: true,
    });
    expect(data.parsed).toMatchObject({
      keywords: ["backend developer"],
      workMode: ["remote"],
      employmentTypes: ["full_time"],
      unresolvedExplicitSlugs: { loc: ["india"] },
    });
    expect(mocks.searchJobs).not.toHaveBeenCalled();
    expect(mocks.listTopCompanies).not.toHaveBeenCalled();
    expect(mocks.logExternalError).toHaveBeenCalledWith(
      "warn",
      { service: "typesense", operation: "explore_filter_resolution" },
      timeout,
    );
  });

  it("single-flights concurrent identical failed filter parses", async () => {
    let rejectParse!: (reason?: unknown) => void;
    mocks.parseSearchFilters.mockImplementationOnce(
      () =>
        new Promise((_resolve, reject) => {
          rejectParse = reject;
        }),
    );

    const request = {
      searchParams: { loc: "india" },
      locale: "en",
    };
    const first = fetchExplorePageData(request);
    const second = fetchExplorePageData(request);
    await vi.waitFor(() => expect(mocks.parseSearchFilters).toHaveBeenCalledTimes(1));
    rejectParse(Object.assign(new Error("request timed out"), { code: "ETIMEDOUT" }));

    const results = await Promise.all([first, second]);
    expect(results.map((result) => result.result.degraded)).toEqual([true, true]);
    expect(results.map((result) => result.parsed.unresolvedExplicitSlugs)).toEqual([
      { loc: ["india"] },
      { loc: ["india"] },
    ]);
    expect(mocks.parseSearchFilters).toHaveBeenCalledTimes(1);
  });

  it("does not broaden a search when a configured resolver returns unresolved explicit slugs", async () => {
    mocks.parseSearchFilters.mockResolvedValueOnce({
      keywords: [],
      locations: [],
      occupations: [],
      seniorities: [],
      technologies: [],
      workMode: [],
      employmentTypes: [],
      unresolvedExplicitSlugs: { loc: ["india"] },
    });

    const data = await fetchExplorePageData({
      searchParams: { loc: "india" },
      locale: "en",
    });

    expect(data.result).toEqual({
      companies: [],
      totalCompanies: 0,
      degraded: true,
    });
    expect(mocks.searchJobs).not.toHaveBeenCalled();
    expect(mocks.listTopCompanies).not.toHaveBeenCalled();
  });

  it("fails loud with a sanitized error for a non-availability parsing defect", async () => {
    const defect = Object.assign(new Error("invalid taxonomy document shape SECRET_CANARY"), {
      httpStatus: 429,
      config: { headers: { "X-TYPESENSE-API-KEY": "SECRET_CANARY" } },
    });
    mocks.parseSearchFilters.mockRejectedValueOnce(defect);

    const rejection = fetchExplorePageData({
      searchParams: { loc: "india" },
      locale: "en",
    });
    await expect(rejection).rejects.toThrow("Explore filter resolution failed");
    await rejection.catch((error) => {
      expect(error).not.toBe(defect);
      expect(error).not.toHaveProperty("config");
      expect(JSON.stringify(error)).not.toContain("SECRET_CANARY");
    });
    expect(mocks.logExternalError).toHaveBeenCalledWith(
      "error",
      { service: "typesense", operation: "explore_filter_resolution" },
      defect,
    );
  });
});
