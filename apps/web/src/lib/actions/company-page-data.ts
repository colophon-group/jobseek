"use server";

import {
  getCompanyBySlug,
  getCompanyPostings,
  getCompanyPostingsAnonymous,
  getSimilarCompanies,
  type CompanyDetail,
  type SimilarCompaniesPage,
} from "@/lib/actions/company";
import { getCurrencyRates } from "@/lib/actions/search";
import { parseSearchFilters, type ParsedSearchFilters } from "@/lib/actions/search-input";
import { getPreferences } from "@/lib/actions/preferences";
import { readAnonJobLanguagesCookie } from "@/lib/anon-preferences";
import { getSession } from "@/lib/sessionCache";
import { resolveCompanyPageJobLanguages } from "@/lib/company-job-languages";
import { firstOf, idsOrUndefined, parseRangeParam, getGeoFromHeaders } from "@/lib/search/params";
import { convertToEur } from "@/lib/salary";
import type { SearchResultPosting } from "@/lib/search";

const PAGE_SIZE = 20;

const DEFAULT_DISPLAY_CURRENCY = "EUR";

const EMPTY_PARSED_FILTERS: ParsedSearchFilters = {
  keywords: [],
  locations: [],
  occupations: [],
  seniorities: [],
  technologies: [],
  workMode: [],
  employmentTypes: [],
};

export interface CompanyPageData {
  company: CompanyDetail;
  /** The cached shell has no results yet; load them browser-direct before rendering. */
  postingsDeferred?: boolean;
  /**
   * Global peer totals for the server-rendered fallback when browser-direct
   * search is disabled. Otherwise the strip loads directly after hydration.
   */
  similarCompanies?: SimilarCompaniesPage;
  postings: SearchResultPosting[];
  activeCount: number;
  yearCount: number;
  truncated?: boolean;
  parsed: ParsedSearchFilters;
  displayCurrency: string;
  jobLanguages: string[];
  languages: string[];
  userLat: number | undefined;
  userLng: number | undefined;
  salaryCurrencyParam: string;
  salaryMinDisplay: number | undefined;
  salaryMaxDisplay: number | undefined;
  experienceMin: number | undefined;
  experienceMax: number | undefined;
  showPostingId: string | null;
}

export async function fetchCompanyPageData(params: {
  slug: string;
  searchParams: Record<string, string | string[] | undefined>;
  locale: string;
}): Promise<CompanyPageData | null> {
  const { slug, searchParams, locale } = params;

  const company = await getCompanyBySlug(slug, locale);
  if (!company) return null;

  const q = firstOf(searchParams.q);
  const qmode = firstOf(searchParams.qmode) === "literal" ? "literal" as const : undefined;
  const loc = firstOf(searchParams.loc);
  const occ = firstOf(searchParams.occ);
  const sen = firstOf(searchParams.sen);
  const tech = firstOf(searchParams.tech);
  const wm = firstOf(searchParams.wm);
  const etype = firstOf(searchParams.etype);
  const show = firstOf(searchParams.show);
  const sal = firstOf(searchParams.sal);
  const salcur = firstOf(searchParams.salcur);
  const exp = firstOf(searchParams.exp);

  const { userLat, userLng } = await getGeoFromHeaders();

  // Auth users persist `jobLanguages` in `user_preferences`; anon users
  // mirror it into a cookie (issue #2850 + `anon-preferences.ts`).
  const session = await getSession();
  const [parsed, prefs, anonJobLangs] = await Promise.all([
    parseSearchFilters({ q, qmode, loc, occ, sen, tech, wm, etype, locale, userLat, userLng }),
    session ? getPreferences() : Promise.resolve(null),
    session ? Promise.resolve(null) : readAnonJobLanguagesCookie(),
  ]);

  const storedJobLanguages = prefs?.jobLanguages ?? anonJobLangs ?? [];
  const displayCurrency = prefs?.displayCurrency ?? "EUR";
  const { jobLanguages, languages } = resolveCompanyPageJobLanguages(
    storedJobLanguages,
    locale,
  );

  const locationIds = idsOrUndefined(parsed.locations);
  const occupationIds = idsOrUndefined(parsed.occupations);
  const seniorityIds = idsOrUndefined(parsed.seniorities);
  const technologyIds = idsOrUndefined(parsed.technologies);
  const workMode = parsed.workMode.length > 0 ? parsed.workMode : undefined;
  const employmentTypes =
    parsed.employmentTypes.length > 0 ? parsed.employmentTypes : undefined;

  const salaryCurrencyParam = salcur ?? displayCurrency;
  const { min: salaryMinDisplay, max: salaryMaxDisplay } = parseRangeParam(sal);
  // Convert user-currency filter amount to EUR — see explore-page-data.ts for the
  // full rationale (issue #3178). `getCurrencyRates` is cache-backed
  // (`cacheLife("hours")`), so this is not an extra DB round-trip in the
  // steady state.
  const rates =
    salaryMinDisplay != null || salaryMaxDisplay != null
      ? await getCurrencyRates()
      : [];
  const salaryMinEur = convertToEur(salaryMinDisplay, salaryCurrencyParam, rates);
  const salaryMaxEur = convertToEur(salaryMaxDisplay, salaryCurrencyParam, rates);
  const { min: experienceMin, max: experienceMax } = parseRangeParam(exp);

  const postingsResult = await getCompanyPostings({
    companyId: company.id,
    keywords: parsed.keywords,
    locationIds,
    occupationIds,
    seniorityIds,
    technologyIds,
    employmentTypes,
    workMode,
    salaryMinEur,
    salaryMaxEur,
    experienceMin,
    experienceMax,
    languages,
    locale,
    offset: 0,
    limit: PAGE_SIZE,
  });

  return {
    company,
    postings: postingsResult.postings,
    activeCount: postingsResult.activeCount,
    yearCount: postingsResult.yearCount,
    truncated: postingsResult.truncated,
    parsed,
    displayCurrency,
    jobLanguages,
    languages,
    userLat,
    userLng,
    salaryCurrencyParam,
    salaryMinDisplay,
    salaryMaxDisplay,
    experienceMin,
    experienceMax,
    showPostingId: show ?? null,
  };
}

/**
 * Server-side prerender variant of :func:`fetchCompanyPageData` for the
 * anonymous, no-filter company-detail page case (#3203).
 *
 * Mirrors :func:`fetchExplorePageDefaults` (#2640). Critically does NOT
 * call :func:`getPreferences`/:func:`getSession`/:func:`readAnonJobLanguagesCookie`
 * (read ``cookies()``) or :func:`getGeoFromHeaders` (reads ``headers()``)
 * — those force dynamic rendering and would silently break the page's
 * ISR eligibility (`revalidate = CACHE_TTL_DETAIL`). Returns the same
 * ``CompanyPageData`` shape with anonymous defaults: EUR currency, no
 * job-language filter, no geo proximity bias, no active filters,
 * ``showPostingId: null``. The client component reuses app-bootstrap
 * preferences and loads deferred or filtered results directly through the
 * scoped browser Typesense key. With browser-direct search enabled, only
 * company facts are fetched on the server; posting counts stay loading until
 * that first browser read completes.
 *
 * Returns ``null`` when the slug is unknown — caller renders the
 * not-found shell. The cache layer in `getCompanyBySlug` ensures repeat
 * unknown-slug hits don't churn Typesense/Postgres.
 */
export async function fetchCompanyPageDefaults(params: {
  slug: string;
  locale: string;
}): Promise<CompanyPageData | null> {
  const { slug, locale } = params;

  const company = await getCompanyBySlug(slug, locale);
  if (!company) return null;

  const displayCurrency = DEFAULT_DISPLAY_CURRENCY;
  const { jobLanguages, languages } = resolveCompanyPageJobLanguages([], locale);

  // Browser-direct mode already reads fresh postings and peers after hydration.
  // Keep those lists out of the cached shell to avoid fetching, serializing and
  // rendering them a second time on every cold company/locale path. Retain the
  // anonymous server snapshot for environments where direct search is disabled.
  const postingsDeferred = process.env.NEXT_PUBLIC_TYPESENSE_DIRECT === "1";
  const [postingsResult, similarCompanies] = postingsDeferred
    ? [null, undefined]
    : await Promise.all([
        getCompanyPostingsAnonymous({
          companyId: company.id,
          keywords: [],
          languages,
          locale,
          offset: 0,
          limit: PAGE_SIZE,
        }),
        getSimilarCompanies(company.id, company.industryId, {
          offset: 0,
          limit: 10,
          locale,
        }),
      ]);

  return {
    company,
    postingsDeferred,
    similarCompanies,
    postings: postingsResult?.postings ?? [],
    activeCount: postingsResult?.activeCount ?? 0,
    yearCount: postingsResult?.yearCount ?? 0,
    truncated: postingsResult?.truncated,
    parsed: EMPTY_PARSED_FILTERS,
    displayCurrency,
    jobLanguages,
    languages,
    userLat: undefined,
    userLng: undefined,
    salaryCurrencyParam: displayCurrency,
    salaryMinDisplay: undefined,
    salaryMaxDisplay: undefined,
    experienceMin: undefined,
    experienceMax: undefined,
    showPostingId: null,
  };
}
