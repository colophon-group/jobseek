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
import type { SearchResponse } from "@/lib/search";

const PAGE_SIZE = 10;

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

export interface ExploreData {
  result: SearchResponse;
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
}

export async function fetchExploreData(params: {
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

  const { userLat, userLng } = await getGeoFromHeaders();

  // For authenticated users, `getPreferences` returns the DB row.
  // For anon users, we mirror `jobLanguages` into a cookie (see
  // issue #2850 + `anon-preferences.ts`) — read it here so anon
  // toggles in /settings actually flow through to the server-side
  // search. Other prefs (display currency etc.) stay anon-defaults.
  const session = await getSession();
  const [parsed, prefs, anonJobLangs] = await Promise.all([
    parseSearchFilters({ q, loc, occ, sen, tech, wm, etype, locale, userLat, userLng }),
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

  const result =
    parsed.keywords.length > 0
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
  };
}

/**
 * Server-side prerender variant of :func:`fetchExploreData` for the
 * unauthenticated, no-filter homepage case (#2640).
 *
 * Critically does NOT call :func:`getPreferences` (reads
 * ``cookies()``) or :func:`getGeoFromHeaders` (reads ``headers()``) —
 * both forces dynamic rendering and would silently break the page's
 * ISR eligibility. Returns the same ``ExploreData`` shape with
 * anonymous defaults: EUR currency, no job-language filter, no geo
 * proximity bias. The client component conditionally re-fetches the
 * personalized variant via :func:`fetchExploreData` when the
 * ``logged_in`` hint cookie or any filter searchParams are present.
 *
 * Net effect: anonymous visitors hitting ``/explore`` with no
 * filters get a CDN-cached prerendered page with embedded
 * ``initialData``, no Vercel function invocation. Logged-in users
 * still pay the function call (one fetch on mount, same as today).
 * Filter changes always go through ``fetchExploreData`` because the
 * defaults can't reflect them.
 */
export async function fetchExploreDefaults(params: {
  locale: string;
}): Promise<ExploreData> {
  const { locale } = params;

  const displayCurrency = DEFAULT_DISPLAY_CURRENCY;
  const jobLanguages: string[] = [];
  const languages = resolveJobLanguages(jobLanguages, locale);

  // ``listTopCompaniesAnonymous`` (not ``listTopCompanies``) — the
  // ``listTopCompanies`` variant calls ``getSessionUserId`` which awaits
  // ``headers()`` and would silently downgrade the page to dynamic
  // rendering, defeating the ISR optimisation this whole module is for.
  const result = await listTopCompaniesAnonymous({
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
  });

  return {
    result,
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
  };
}
