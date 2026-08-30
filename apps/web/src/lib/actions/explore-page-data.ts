"use server";

import {
  searchJobs,
  listTopCompanies,
  listTopCompaniesAnonymous,
  getCurrencyRates,
} from "@/lib/actions/search";
import { parseSearchFilters, type ParsedSearchFilters } from "@/lib/actions/search-input";
import { getPreferences } from "@/lib/actions/preferences";
import { resolveJobLanguages } from "@/lib/job-languages";
import { readAnonJobLanguagesCookie } from "@/lib/anon-preferences";
import { getSession } from "@/lib/sessionCache";
import { firstOf, idsOrUndefined, parseRangeParam, getGeoFromHeaders } from "@/lib/search/params";
import { convertToEur } from "@/lib/salary";
import { parseExploreSearchLanguages } from "@/lib/search/language-param";
import type { SearchResponse } from "@/lib/search";
import { isTypesenseUnavailableError } from "@/lib/search/typesense-retry";
import {
  EMPTY_PARSED_FILTERS,
  parseOfflineSearchFilters,
} from "@/lib/search/offline-filters";
import { logExternalError } from "@/lib/safe-external-error";
import {
  getExploreRepositoryFallbackCompanies,
  hasTypesenseSearchConfiguration,
  type ExploreRepositoryCompany,
} from "@/lib/explore-repository-fallback";

const PAGE_SIZE = 10;

const DEFAULT_DISPLAY_CURRENCY = "EUR";

export interface ExploreData {
  result: SearchResponse;
  /**
   * Real company profile identities used only when Typesense is not
   * configured (for example, deterministic secretless CI builds). This is a
   * separate shape so the UI cannot mistake it for live job results.
   */
  repositoryFallbackCompanies?: ExploreRepositoryCompany[];
  parsed: ParsedSearchFilters;
  displayCurrency: string;
  jobLanguages: string[];
  languages: string[];
  /** Explicit public-API language override carried by a `moreAt` URL. */
  languageOverride?: string[] | null;
  userLat: number | undefined;
  userLng: number | undefined;
  salaryCurrencyParam: string;
  salaryMinDisplay: number | undefined;
  salaryMaxDisplay: number | undefined;
  experienceMin: number | undefined;
  experienceMax: number | undefined;
}

function repositoryFallbackFor(
  result: SearchResponse,
  typesenseConfigured: boolean,
): ExploreRepositoryCompany[] | undefined {
  if (typesenseConfigured || result.companies.length > 0) {
    return undefined;
  }
  return getExploreRepositoryFallbackCompanies();
}

function unavailableSearchResponse(): SearchResponse {
  return { companies: [], totalCompanies: 0, degraded: true };
}

function hasUnresolvedExplicitSlugs(parsed: ParsedSearchFilters): boolean {
  return (["loc", "occ", "sen", "tech"] as const).some(
    (kind) => (parsed.unresolvedExplicitSlugs?.[kind]?.length ?? 0) > 0,
  );
}

type SearchFilterParams = Parameters<typeof parseSearchFilters>[0];

/**
 * A failed cache-component lookup is not retained by Next, so a burst of
 * identical Explore actions can otherwise multiply the same slow taxonomy
 * request inside one warm function instance. Keep only live promises and
 * always remove them after settlement; raw filter values never reach logs or
 * a persistent cache through this registry.
 */
const inflightFilterParses = new Map<string, Promise<ParsedSearchFilters>>();

function filterParseKey(params: SearchFilterParams): string {
  return JSON.stringify([
    params.locale,
    params.q,
    params.loc,
    params.occ,
    params.sen,
    params.tech,
    params.wm,
    params.etype,
    params.userLat,
    params.userLng,
  ]);
}

function parseSearchFiltersSingleFlight(
  params: SearchFilterParams,
): Promise<ParsedSearchFilters> {
  const key = filterParseKey(params);
  const existing = inflightFilterParses.get(key);
  if (existing) return existing;

  const promise = parseSearchFilters(params).finally(() => {
    if (inflightFilterParses.get(key) === promise) {
      inflightFilterParses.delete(key);
    }
  });
  inflightFilterParses.set(key, promise);
  return promise;
}

export async function fetchExploreFilterPageData(
  params: SearchFilterParams,
): Promise<{ parsed: ParsedSearchFilters; degraded: boolean }> {
  try {
    return {
      parsed: await parseSearchFiltersSingleFlight(params),
      degraded: false,
    };
  } catch (err) {
    if (!isTypesenseUnavailableError(err)) {
      // SDK/Axios errors can retain credential-bearing request configuration.
      // Next logs rejected Server Action values, so never rethrow the original
      // object across that boundary—even for deterministic 4xx responses.
      logExternalError(
        "error",
        { service: "typesense", operation: "explore_filter_resolution" },
        err,
      );
      throw new Error("Explore filter resolution failed");
    }
    logExternalError(
      "warn",
      { service: "typesense", operation: "explore_filter_resolution" },
      err,
    );
    return {
      parsed: parseOfflineSearchFilters(params),
      degraded: true,
    };
  }
}

export async function fetchExplorePageData(params: {
  searchParams: Record<string, string | string[] | undefined>;
  locale: string;
}): Promise<ExploreData> {
  const { searchParams, locale } = params;

  const q = firstOf(searchParams.q);
  const loc = firstOf(searchParams.loc);
  const occ = firstOf(searchParams.occ);
  const sen = firstOf(searchParams.sen);
  const tech = firstOf(searchParams.tech);
  const wm = firstOf(searchParams.wm);
  const etype = firstOf(searchParams.etype);
  const sal = firstOf(searchParams.sal);
  const salcur = firstOf(searchParams.salcur);
  const exp = firstOf(searchParams.exp);
  const typesenseConfigured = hasTypesenseSearchConfiguration();
  const languageParam = parseExploreSearchLanguages(firstOf(searchParams.lang));
  const languageOverride = languageParam.ok ? languageParam.languages : null;

  const { userLat, userLng } = await getGeoFromHeaders();

  // For authenticated users, `getPreferences` returns the DB row.
  // For anon users, we mirror `jobLanguages` into a cookie (see
  // issue #2850 + `anon-preferences.ts`) — read it here so anon
  // toggles in /settings actually flow through to the server-side
  // search. Other prefs (display currency etc.) stay anon-defaults.
  const session = await getSession();
  const [filterResolution, prefs, anonJobLangs] = await Promise.all([
    typesenseConfigured
      ? fetchExploreFilterPageData({ q, loc, occ, sen, tech, wm, etype, locale, userLat, userLng })
      : Promise.resolve({
          parsed: parseOfflineSearchFilters({ q, loc, occ, sen, tech, wm, etype }),
          degraded: true,
        }),
    session ? getPreferences() : Promise.resolve(null),
    session ? Promise.resolve(null) : readAnonJobLanguagesCookie(),
  ]);
  const { parsed } = filterResolution;

  const jobLanguages = prefs?.jobLanguages ?? anonJobLangs ?? [];
  const displayCurrency = prefs?.displayCurrency ?? "EUR";
  const languages = languageOverride ?? resolveJobLanguages(jobLanguages, locale);

  const locationIds = idsOrUndefined(parsed.locations);
  const occupationIds = idsOrUndefined(parsed.occupations);
  const seniorityIds = idsOrUndefined(parsed.seniorities);
  const technologyIds = idsOrUndefined(parsed.technologies);
  const workMode = parsed.workMode.length > 0 ? parsed.workMode : undefined;
  const employmentTypes =
    parsed.employmentTypes.length > 0 ? parsed.employmentTypes : undefined;

  const salaryCurrencyParam = salcur ?? displayCurrency;
  const { min: salaryMinDisplay, max: salaryMaxDisplay } = parseRangeParam(sal);
  // The `salary_eur` field on every job_posting Typesense document is in EUR
  // (see apps/crawler/src/processing/cpu.py::_extract_salary_fields). Convert
  // the user-currency filter amount to EUR before passing it to the filter
  // builder; otherwise "100K USD" would exclude US roles paying $100K
  // because their `salary_eur` ≈ 92,000 < 100,000 (issue #3178).
  // `getCurrencyRates` is cache-backed (`cacheLife("hours")`), so this is
  // not an extra DB round-trip in the steady state.
  const rates =
    salaryMinDisplay != null || salaryMaxDisplay != null
      ? await getCurrencyRates()
      : [];
  const salaryMinEur = convertToEur(salaryMinDisplay, salaryCurrencyParam, rates);
  const salaryMaxEur = convertToEur(salaryMaxDisplay, salaryCurrencyParam, rates);
  const { min: experienceMin, max: experienceMax } = parseRangeParam(exp);

  // If taxonomy resolution failed, searching without the unresolved IDs would
  // silently broaden the request and present a false zero/success state. Keep
  // the user's URL/filter payload and return the explicit unavailable state.
  const result =
    !typesenseConfigured ||
    filterResolution.degraded ||
    hasUnresolvedExplicitSlugs(parsed)
    ? unavailableSearchResponse()
    : parsed.keywords.length > 0
      ? await searchJobs({
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
        })
      : await listTopCompanies({
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
    result,
    repositoryFallbackCompanies: repositoryFallbackFor(result, typesenseConfigured),
    parsed,
    displayCurrency,
    jobLanguages,
    languages,
    languageOverride,
    userLat,
    userLng,
    salaryCurrencyParam,
    salaryMinDisplay,
    salaryMaxDisplay,
    experienceMin,
    experienceMax,
  };
}

/**
 * Server-side prerender variant of :func:`fetchExplorePageData` for the
 * unauthenticated, no-filter homepage case (#2640).
 *
 * Critically does NOT call :func:`getPreferences` (reads
 * ``cookies()``) or :func:`getGeoFromHeaders` (reads ``headers()``) —
 * both force dynamic rendering and would silently break the page's
 * ISR eligibility. Returns the same ``ExploreData`` shape with
 * anonymous defaults: EUR currency, no job-language filter, no geo
 * proximity bias. The client component resolves preference- or filter-bearing
 * views through the browser loader and scoped Typesense key. Semantic free
 * text retains only the narrow geo-aware parser action.
 *
 * Net effect: anonymous visitors hitting ``/explore`` with no
 * filters get a CDN-cached prerendered page with embedded
 * ``initialData``, no Vercel function invocation. Logged-in users reuse the
 * layout bootstrap they already need; Explore no longer invokes this full
 * page-data action on mount.
 */
export async function fetchExplorePageDefaults(params: {
  locale: string;
}): Promise<ExploreData> {
  const { locale } = params;

  const displayCurrency = DEFAULT_DISPLAY_CURRENCY;
  const jobLanguages: string[] = [];
  const languages = resolveJobLanguages(jobLanguages, locale);
  const typesenseConfigured = hasTypesenseSearchConfiguration();

  // ``listTopCompaniesAnonymous`` (not ``listTopCompanies``) — the
  // ``listTopCompanies`` variant calls ``getSessionUserId`` which awaits
  // ``headers()`` and would silently downgrade the page to dynamic
  // rendering, defeating the ISR optimisation this whole module is for.
  const result = typesenseConfigured
    ? await listTopCompaniesAnonymous({
        locationIds: undefined,
        occupationIds: undefined,
        seniorityIds: undefined,
        technologyIds: undefined,
        salaryMinEur: undefined,
        salaryMaxEur: undefined,
        experienceMin: undefined,
        experienceMax: undefined,
        languages,
        locale,
        offset: 0,
        limit: PAGE_SIZE,
      })
    : unavailableSearchResponse();

  return {
    result,
    repositoryFallbackCompanies: repositoryFallbackFor(result, typesenseConfigured),
    parsed: EMPTY_PARSED_FILTERS,
    displayCurrency,
    jobLanguages,
    languages,
    languageOverride: null,
    userLat: undefined,
    userLng: undefined,
    salaryCurrencyParam: displayCurrency,
    salaryMinDisplay: undefined,
    salaryMaxDisplay: undefined,
    experienceMin: undefined,
    experienceMax: undefined,
  };
}
