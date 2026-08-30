import type { ExploreData } from "@/lib/actions/explore-page-data";
import { resolveCompanySemanticFilters } from "@/lib/actions/company-filter-state";
import type { CurrencyRate } from "@/lib/actions/search";
import { resolveJobLanguages } from "@/lib/job-languages";
import { convertToEur } from "@/lib/salary";
import { logExternalError } from "@/lib/safe-external-error";
import { parseExploreSearchLanguages } from "@/lib/search/language-param";
import {
  tryListTopCompaniesDirect,
  trySearchJobsDirect,
} from "@/lib/search/search-runner";
import {
  parseCompanyFilterStateOffline,
  resolveCompanyFilterStateDirect,
} from "@/lib/search/typesense-browser-filter-state";
import type { ParsedSearchFilters } from "@/lib/services/search-input";

const PAGE_SIZE = 10;
const MAX_RANGE_PARAM_LENGTH = 64;
const MAX_LANGUAGE_PARAM_LENGTH = 128;

type RangeResult = {
  min: number | undefined;
  max: number | undefined;
  valid: boolean;
};

export type ExploreBrowserDataResult = {
  data: ExploreData;
  unavailable: boolean;
  /** Prevents SearchPage from repeating this mount-time direct read. */
  directAttempted: boolean;
};

function parseRange(raw: string | null): RangeResult {
  if (!raw) return { min: undefined, max: undefined, valid: true };
  if (raw.length > MAX_RANGE_PARAM_LENGTH) {
    return { min: undefined, max: undefined, valid: false };
  }
  const parts = raw.split("-");
  if (parts.length !== 2) {
    return { min: undefined, max: undefined, valid: false };
  }
  const parse = (value: string): number | undefined => {
    if (!value) return undefined;
    if (!/^\d+$/.test(value)) return Number.NaN;
    const parsed = Number.parseInt(value, 10);
    return Number.isSafeInteger(parsed) && parsed >= 0
      ? parsed
      : Number.NaN;
  };
  const min = parse(parts[0]);
  const max = parse(parts[1]);
  return {
    min: Number.isNaN(min) ? undefined : min,
    max: Number.isNaN(max) ? undefined : max,
    valid: !Number.isNaN(min) && !Number.isNaN(max),
  };
}

function validCurrency(value: string | null | undefined): string | null {
  return value && /^[A-Z]{3}$/.test(value) ? value : null;
}

function unavailableData(params: {
  initialData: ExploreData;
  parsed: ParsedSearchFilters;
  displayCurrency: string;
  jobLanguages: string[];
  languages: string[];
  languageOverride: string[] | null;
  salaryCurrencyParam: string;
  salary: RangeResult;
  experience: RangeResult;
  userLat?: number;
  userLng?: number;
}): ExploreData {
  return {
    ...params.initialData,
    result: { companies: [], totalCompanies: 0, degraded: true },
    repositoryFallbackCompanies: undefined,
    parsed: params.parsed,
    displayCurrency: params.displayCurrency,
    jobLanguages: params.jobLanguages,
    languages: params.languages,
    languageOverride: params.languageOverride,
    userLat: params.userLat,
    userLng: params.userLng,
    salaryCurrencyParam: params.salaryCurrencyParam,
    salaryMinDisplay: params.salary.min,
    salaryMaxDisplay: params.salary.max,
    experienceMin: params.experience.min,
    experienceMax: params.experience.max,
  };
}

/**
 * Initialize a preference- or filter-bearing Explore shell from the browser.
 * Explicit taxonomy slugs and results use the scoped Typesense key. Semantic
 * free text retains the narrow canonical parser action because it needs
 * request geolocation; it never fetches the result set server-side.
 */
export async function loadExploreBrowserData(params: {
  initialData: ExploreData;
  searchParams: URLSearchParams;
  locale: string;
  displayCurrency?: string | null;
  jobLanguages: string[];
  rates: CurrencyRate[];
  isLoggedIn: boolean;
}): Promise<ExploreBrowserDataResult> {
  const displayCurrency = validCurrency(params.displayCurrency) ?? "EUR";
  const salaryCurrencyParam =
    validCurrency(params.searchParams.get("salcur")) ?? displayCurrency;
  const salary = parseRange(params.searchParams.get("sal"));
  const experience = parseRange(params.searchParams.get("exp"));
  const rawLanguage = params.searchParams.get("lang");
  const languageParam =
    (rawLanguage?.length ?? 0) <= MAX_LANGUAGE_PARAM_LENGTH
      ? parseExploreSearchLanguages(rawLanguage)
      : { ok: false as const, error: "language param is too long" };
  const languageOverride = languageParam.ok
    ? languageParam.languages
    : null;
  const languages =
    languageOverride ?? resolveJobLanguages(params.jobLanguages, params.locale);
  const offline = parseCompanyFilterStateOffline(params.searchParams);
  const unavailable = (
    parsed = offline.parsed,
    geo?: { userLat?: number; userLng?: number },
  ): ExploreBrowserDataResult => ({
    data: unavailableData({
      initialData: params.initialData,
      parsed,
      displayCurrency,
      jobLanguages: params.jobLanguages,
      languages,
      languageOverride,
      salaryCurrencyParam,
      salary,
      experience,
      userLat: geo?.userLat,
      userLng: geo?.userLng,
    }),
    unavailable: true,
    directAttempted: true,
  });

  if (!salary.valid || !experience.valid) return unavailable();

  let parsed: ParsedSearchFilters;
  let userLat: number | undefined;
  let userLng: number | undefined;
  const rawQuery = params.searchParams.get("q")?.trim();
  if (rawQuery) {
    try {
      const semantic = await resolveCompanySemanticFilters({
        q: rawQuery,
        loc: params.searchParams.get("loc") ?? undefined,
        occ: params.searchParams.get("occ") ?? undefined,
        sen: params.searchParams.get("sen") ?? undefined,
        tech: params.searchParams.get("tech") ?? undefined,
        wm: params.searchParams.get("wm") ?? undefined,
        etype: params.searchParams.get("etype") ?? undefined,
        locale: params.locale,
      });
      if (!semantic) return unavailable();
      parsed = semantic.parsed;
      userLat = semantic.userLat;
      userLng = semantic.userLng;
    } catch (error) {
      logExternalError(
        "error",
        { service: "typesense", operation: "explore_semantic_filters" },
        error,
      );
      return unavailable();
    }
  } else {
    let filterState;
    try {
      filterState = await resolveCompanyFilterStateDirect(
        params.searchParams,
        params.locale,
      );
    } catch (error) {
      logExternalError(
        "error",
        { service: "typesense", operation: "browser_explore_filter_state" },
        error,
      );
      return unavailable();
    }
    if (!filterState.complete) return unavailable(filterState.parsed);
    parsed = filterState.parsed;
  }

  if (parsed.unresolvedExplicitSlugs) {
    return unavailable(parsed, { userLat, userLng });
  }

  const searchInput = {
    locationIds:
      parsed.locations.length > 0
        ? parsed.locations.map((location) => location.id)
        : undefined,
    occupationIds:
      parsed.occupations.length > 0
        ? parsed.occupations.map((occupation) => occupation.id)
        : undefined,
    seniorityIds:
      parsed.seniorities.length > 0
        ? parsed.seniorities.map((seniority) => seniority.id)
        : undefined,
    technologyIds:
      parsed.technologies.length > 0
        ? parsed.technologies.map((technology) => technology.id)
        : undefined,
    employmentTypes:
      parsed.employmentTypes.length > 0
        ? parsed.employmentTypes
        : undefined,
    workMode: parsed.workMode.length > 0 ? parsed.workMode : undefined,
    salaryMinEur: convertToEur(salary.min, salaryCurrencyParam, params.rates),
    salaryMaxEur: convertToEur(salary.max, salaryCurrencyParam, params.rates),
    experienceMin: experience.min,
    experienceMax: experience.max,
    languages,
    locale: params.locale,
    offset: 0,
    limit: PAGE_SIZE,
  };
  const result =
    parsed.keywords.length > 0
      ? await trySearchJobsDirect(
          { ...searchInput, keywords: parsed.keywords },
          params.isLoggedIn,
        )
      : await tryListTopCompaniesDirect(searchInput, params.isLoggedIn);
  if (!result) return unavailable(parsed, { userLat, userLng });

  return {
    data: {
      ...params.initialData,
      result,
      repositoryFallbackCompanies: undefined,
      parsed,
      displayCurrency,
      jobLanguages: params.jobLanguages,
      languages,
      languageOverride,
      userLat,
      userLng,
      salaryCurrencyParam,
      salaryMinDisplay: salary.min,
      salaryMaxDisplay: salary.max,
      experienceMin: experience.min,
      experienceMax: experience.max,
    },
    unavailable: false,
    directAttempted: true,
  };
}
