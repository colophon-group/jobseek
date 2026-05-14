import { beforeEach, describe, expect, it, vi } from "vitest";

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

import { fetchExploreData } from "../explore-data";

beforeEach(() => {
  vi.clearAllMocks();
  mocks.searchJobs.mockResolvedValue({
    companies: [],
    totalCompanies: 0,
  });
  mocks.listTopCompanies.mockResolvedValue({
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
  });
  mocks.parseRangeParam.mockReturnValue({ min: undefined, max: undefined });
  mocks.getCurrencyRates.mockResolvedValue([
    { currency: "USD", toEur: 0.92 },
    { currency: "CHF", toEur: 0.95 },
    { currency: "JPY", toEur: 0.006 },
  ]);
});

describe("fetchExploreData — salary EUR conversion (#3178)", () => {
  it("converts USD 100K filter to ~92000 EUR before calling Typesense (was 100000 pre-fix)", async () => {
    // The headline #3178 scenario: a US user with salcur=USD enters "$100K+".
    // Pre-fix, salaryMinEur was passed through as 100000, so the filter
    // `salary_eur:[100000..]` excluded $100K US roles whose salary_eur ≈ 92,000.
    // Post-fix, the converted EUR value is what reaches the Typesense query.
    mocks.parseRangeParam.mockReturnValueOnce({ min: 100000, max: undefined });

    await fetchExploreData({
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

    await fetchExploreData({
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

    await fetchExploreData({
      searchParams: { sal: "10000000-", salcur: "JPY" },
      locale: "en",
    });

    const callArgs = mocks.listTopCompanies.mock.calls[0][0];
    expect(callArgs.salaryMinEur).toBe(60000);
  });

  it("leaves EUR 100K unchanged (identity branch — no rate lookup needed)", async () => {
    mocks.parseRangeParam.mockReturnValueOnce({ min: 100000, max: undefined });

    await fetchExploreData({
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

    await fetchExploreData({
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

    await fetchExploreData({
      searchParams: { sal: "100000-" },
      locale: "en",
    });

    const callArgs = mocks.listTopCompanies.mock.calls[0][0];
    expect(callArgs.salaryMinEur).toBe(92000);
  });

  it("preserves both min and max conversion (range filter)", async () => {
    mocks.parseRangeParam.mockReturnValueOnce({ min: 50000, max: 150000 });

    await fetchExploreData({
      searchParams: { sal: "50000-150000", salcur: "USD" },
      locale: "en",
    });

    const callArgs = mocks.listTopCompanies.mock.calls[0][0];
    expect(callArgs.salaryMinEur).toBe(46000); // 50000 * 0.92
    expect(callArgs.salaryMaxEur).toBe(138000); // 150000 * 0.92
  });
});
