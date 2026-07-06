import { beforeEach, describe, expect, it, vi } from "vitest";
import type { CompanyDetail } from "@/lib/actions/company";

// Hoisted mocks so they apply before module imports below.
const mocks = vi.hoisted(() => ({
  getCompanyBySlug: vi.fn(),
  getCompanyPostings: vi.fn(),
  getCompanyPostingsAnonymous: vi.fn(),
  getSession: vi.fn(),
  getPreferences: vi.fn(),
  readAnonJobLanguagesCookie: vi.fn(),
  getGeoFromHeaders: vi.fn(),
  parseSearchFilters: vi.fn(),
  getCurrencyRates: vi.fn(),
  parseRangeParam: vi.fn(),
}));

// `server-only` throws when loaded outside a Next.js runtime; neutralise.
vi.mock("server-only", () => ({}));

vi.mock("@/lib/actions/company", () => ({
  getCompanyBySlug: mocks.getCompanyBySlug,
  getCompanyPostings: mocks.getCompanyPostings,
  getCompanyPostingsAnonymous: mocks.getCompanyPostingsAnonymous,
}));
vi.mock("@/lib/actions/search", () => ({
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

import {
  fetchCompanyPageDefaults,
  fetchCompanyPageData,
} from "../company-page-data";

function makeCompany(): CompanyDetail {
  return {
    id: "company-1",
    name: "Test Company",
    slug: "test-company",
    icon: null,
    logo: null,
    website: null,
    description: null,
    industryId: null,
    industryName: null,
    employeeCountRange: null,
    foundedYear: null,
    activeJobCount: 7,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.getCompanyBySlug.mockResolvedValue(makeCompany());
  mocks.getCompanyPostingsAnonymous.mockResolvedValue({
    postings: [],
    activeCount: 0,
    yearCount: 0,
  });
  mocks.getCompanyPostings.mockResolvedValue({
    postings: [],
    activeCount: 0,
    yearCount: 0,
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
  ]);
});

describe("fetchCompanyPageDefaults — ISR-safe prerender variant (#3203)", () => {
  it("returns null when the company is unknown", async () => {
    mocks.getCompanyBySlug.mockResolvedValueOnce(null);

    const result = await fetchCompanyPageDefaults({
      slug: "ghost",
      locale: "en",
    });

    expect(result).toBeNull();
  });

  it("does NOT call session-/cookie-/header-reading helpers (would force dynamic render)", async () => {
    await fetchCompanyPageDefaults({ slug: "test-company", locale: "en" });

    // These three are the dynamic-rendering hazards that force the
    // page off the ISR / `'use cache'` path. The whole point of this
    // function is to NOT call them. Same defence as
    // `listTopCompaniesAnonymous` from #2640.
    expect(mocks.getSession).not.toHaveBeenCalled();
    expect(mocks.getPreferences).not.toHaveBeenCalled();
    expect(mocks.readAnonJobLanguagesCookie).not.toHaveBeenCalled();
    expect(mocks.getGeoFromHeaders).not.toHaveBeenCalled();
  });

  it("uses the anonymous postings variant (no session read)", async () => {
    await fetchCompanyPageDefaults({ slug: "test-company", locale: "en" });

    expect(mocks.getCompanyPostingsAnonymous).toHaveBeenCalledTimes(1);
    expect(mocks.getCompanyPostings).not.toHaveBeenCalled();
  });

  it("returns anonymous defaults (EUR, no filters, locale-only language)", async () => {
    const result = await fetchCompanyPageDefaults({
      slug: "test-company",
      locale: "de",
    });

    expect(result).not.toBeNull();
    expect(result?.displayCurrency).toBe("EUR");
    expect(result?.salaryCurrencyParam).toBe("EUR");
    expect(result?.jobLanguages).toEqual([]);
    // resolveJobLanguages: [] + "de" -> ["de"]
    expect(result?.languages).toEqual(["de"]);
    expect(result?.userLat).toBeUndefined();
    expect(result?.userLng).toBeUndefined();
    expect(result?.parsed.keywords).toEqual([]);
    expect(result?.parsed.locations).toEqual([]);
    expect(result?.parsed.occupations).toEqual([]);
    expect(result?.parsed.seniorities).toEqual([]);
    expect(result?.parsed.technologies).toEqual([]);
    expect(result?.parsed.workMode).toEqual([]);
    expect(result?.salaryMinDisplay).toBeUndefined();
    expect(result?.salaryMaxDisplay).toBeUndefined();
    expect(result?.experienceMin).toBeUndefined();
    expect(result?.experienceMax).toBeUndefined();
    expect(result?.showPostingId).toBeNull();
  });

  it("fetches the company exactly once (single Typesense round-trip on the cold path)", async () => {
    // Regression for the core #3203 perf bug: the company should not
    // be fetched twice during a single page render. The page route
    // calls `fetchCompanyPageDefaults` which calls `getCompanyBySlug`
    // exactly once. The page's `generateMetadata` separately calls
    // `getCompanyBySlug` (different render context, separate cache
    // hit), but within a single render pass the data action only
    // triggers a single fetch — `'use cache'` deduplicates the
    // metadata + page reads server-side.
    await fetchCompanyPageDefaults({ slug: "test-company", locale: "en" });

    expect(mocks.getCompanyBySlug).toHaveBeenCalledTimes(1);
    expect(mocks.getCompanyBySlug).toHaveBeenCalledWith("test-company", "en");
  });
});

describe("fetchCompanyPageData — personalized server action (control)", () => {
  it("DOES read session/preferences/geo (in contrast to fetchCompanyPageDefaults)", async () => {
    await fetchCompanyPageData({
      slug: "test-company",
      searchParams: {},
      locale: "en",
    });

    // Belt-and-braces guard: if a future refactor accidentally turns
    // fetchCompanyPageData into the same shape as
    // fetchCompanyPageDefaults, this test fails so the two paths stay
    // distinguishable.
    expect(mocks.getGeoFromHeaders).toHaveBeenCalled();
    expect(mocks.getSession).toHaveBeenCalled();
  });
});

describe("fetchCompanyPageData — salary EUR conversion (#3178)", () => {
  it("converts USD 100K filter to ~92000 EUR before calling Typesense (was 100000 pre-fix)", async () => {
    // Simulate the bug scenario from #3178: a US user with `salcur=USD`
    // sets "$100K+". Pre-fix, salaryMinEur was passed through as 100000,
    // which excluded $100K US roles (their salary_eur ≈ 92,000).
    mocks.parseRangeParam.mockReturnValueOnce({ min: 100000, max: undefined });

    await fetchCompanyPageData({
      slug: "test-company",
      searchParams: { sal: "100000-", salcur: "USD" },
      locale: "en",
    });

    expect(mocks.getCurrencyRates).toHaveBeenCalled();
    expect(mocks.getCompanyPostings).toHaveBeenCalledTimes(1);
    const callArgs = mocks.getCompanyPostings.mock.calls[0][0];
    expect(callArgs.salaryMinEur).toBe(92000);
    expect(callArgs.salaryMaxEur).toBeUndefined();
  });

  it("leaves EUR 100K unchanged (identity branch — no rates needed)", async () => {
    mocks.parseRangeParam.mockReturnValueOnce({ min: 100000, max: undefined });

    await fetchCompanyPageData({
      slug: "test-company",
      searchParams: { sal: "100000-", salcur: "EUR" },
      locale: "en",
    });

    const callArgs = mocks.getCompanyPostings.mock.calls[0][0];
    expect(callArgs.salaryMinEur).toBe(100000);
  });

  it("does NOT fetch currency rates when no salary filter is active (no extra DB round-trip)", async () => {
    mocks.parseRangeParam.mockReturnValueOnce({ min: undefined, max: undefined });

    await fetchCompanyPageData({
      slug: "test-company",
      searchParams: {},
      locale: "en",
    });

    expect(mocks.getCurrencyRates).not.toHaveBeenCalled();
  });
});
