"use server";

import {
  getCompanyBySlug,
  getCompanyPostings,
  getCompanyPostingsAnonymous,
  type CompanyDetail,
} from "@/lib/actions/company";
import { parseSearchFilters, type ParsedSearchFilters } from "@/lib/actions/search-input";
import { getPreferences } from "@/lib/actions/preferences";
import { readAnonJobLanguagesCookie } from "@/lib/anon-preferences";
import { getSession } from "@/lib/sessionCache";
import { resolveJobLanguages } from "@/lib/job-languages";
import { firstOf, idsOrUndefined, parseRangeParam, getGeoFromHeaders } from "@/lib/search/params";
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
};

export interface CompanyPageData {
  company: CompanyDetail;
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
  const loc = firstOf(searchParams.loc);
  const occ = firstOf(searchParams.occ);
  const sen = firstOf(searchParams.sen);
  const tech = firstOf(searchParams.tech);
  const wm = firstOf(searchParams.wm);
  const show = firstOf(searchParams.show);
  const sal = firstOf(searchParams.sal);
  const salcur = firstOf(searchParams.salcur);
  const exp = firstOf(searchParams.exp);

  const { userLat, userLng } = await getGeoFromHeaders();

  // Auth users persist `jobLanguages` in `user_preferences`; anon users
  // mirror it into a cookie (issue #2850 + `anon-preferences.ts`).
  const session = await getSession();
  const [parsed, prefs, anonJobLangs] = await Promise.all([
    parseSearchFilters({ q, loc, occ, sen, tech, wm, locale, userLat, userLng }),
    session ? getPreferences() : Promise.resolve(null),
    session ? Promise.resolve(null) : readAnonJobLanguagesCookie(),
  ]);

  const jobLanguages = prefs?.jobLanguages ?? anonJobLangs ?? [];
  const displayCurrency = prefs?.displayCurrency ?? "EUR";
  const languages = resolveJobLanguages(jobLanguages, locale);

  const locationIds = idsOrUndefined(parsed.locations);
  const occupationIds = idsOrUndefined(parsed.occupations);
  const seniorityIds = idsOrUndefined(parsed.seniorities);
  const technologyIds = idsOrUndefined(parsed.technologies);
  const workMode = parsed.workMode.length > 0 ? parsed.workMode : undefined;

  const salaryCurrencyParam = salcur ?? displayCurrency;
  const { min: salaryMinDisplay, max: salaryMaxDisplay } = parseRangeParam(sal);
  const salaryMinEur = salaryMinDisplay;
  const salaryMaxEur = salaryMaxDisplay;
  const { min: experienceMin, max: experienceMax } = parseRangeParam(exp);

  const postingsResult = await getCompanyPostings({
    companyId: company.id,
    keywords: parsed.keywords,
    locationIds,
    occupationIds,
    seniorityIds,
    technologyIds,
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
 * Mirrors :func:`fetchExploreDefaults` (#2640). Critically does NOT
 * call :func:`getPreferences`/:func:`getSession`/:func:`readAnonJobLanguagesCookie`
 * (read ``cookies()``) or :func:`getGeoFromHeaders` (reads ``headers()``)
 * — those force dynamic rendering and would silently break the page's
 * ISR eligibility (`revalidate = CACHE_TTL_DETAIL`). Returns the same
 * ``CompanyPageData`` shape with anonymous defaults: EUR currency, no
 * job-language filter, no geo proximity bias, no active filters,
 * ``showPostingId: null``. The client component conditionally re-fetches
 * the personalised variant via :func:`fetchCompanyPageData` when the
 * ``logged_in`` hint cookie, the anonymous-job-languages hint cookie,
 * or any filter searchParams are present.
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
  const jobLanguages: string[] = [];
  const languages = resolveJobLanguages(jobLanguages, locale);

  // ``getCompanyPostingsAnonymous`` (not ``getCompanyPostings``) — the
  // latter calls ``getSessionUserId`` which awaits ``headers()`` and
  // would silently downgrade the page to dynamic rendering, defeating
  // the ISR optimisation this function exists for. See the parallel
  // pattern in `explore-data.ts::fetchExploreDefaults` (#2640).
  const postingsResult = await getCompanyPostingsAnonymous({
    companyId: company.id,
    keywords: [],
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
