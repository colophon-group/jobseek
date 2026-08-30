import type { CompanyPageData } from "@/lib/actions/company-page-data";
import { resolveCompanySemanticFilters } from "@/lib/actions/company-filter-state";
import type { CurrencyRate } from "@/lib/actions/search";
import { resolveJobLanguages } from "@/lib/job-languages";
import { convertToEur } from "@/lib/salary";
import { logExternalError } from "@/lib/safe-external-error";
import { tryGetCompanyPostingsDirect } from "@/lib/search/search-runner";
import {
  parseCompanyFilterStateOffline,
  resolveCompanyFilterStateDirect,
} from "@/lib/search/typesense-browser-filter-state";
import type { ParsedSearchFilters } from "@/lib/services/search-input";

const PAGE_SIZE = 20;
const MAX_RANGE_PARAM_LENGTH = 64;

type RangeResult = {
  min: number | undefined;
  max: number | undefined;
  valid: boolean;
};

export type CompanyBrowserDataResult = {
  data: CompanyPageData;
  unavailable: boolean;
  /** Prevents CompanyPage from immediately repeating this mount-time read. */
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
    return Number.isSafeInteger(parsed) && parsed >= 0 ? parsed : Number.NaN;
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

function buildUnavailableData(params: {
  initialData: CompanyPageData;
  parsed: ParsedSearchFilters;
  displayCurrency: string;
  jobLanguages: string[];
  languages: string[];
  salaryCurrencyParam: string;
  salary: RangeResult;
  experience: RangeResult;
  userLat?: number;
  userLng?: number;
}): CompanyPageData {
  return {
    ...params.initialData,
    postings: [],
    activeCount: 0,
    yearCount: 0,
    truncated: false,
    parsed: params.parsed,
    displayCurrency: params.displayCurrency,
    jobLanguages: params.jobLanguages,
    languages: params.languages,
    salaryCurrencyParam: params.salaryCurrencyParam,
    salaryMinDisplay: params.salary.min,
    salaryMaxDisplay: params.salary.max,
    experienceMin: params.experience.min,
    experienceMax: params.experience.max,
    userLat: params.userLat ?? params.initialData.userLat,
    userLng: params.userLng ?? params.initialData.userLng,
  };
}

/**
 * Initialize a personalized/filter-bearing company view without invoking a
 * Server Action. URL vocabulary is resolved through the scoped browser key;
 * any unsafe, unresolved, or degraded input fails closed to an unavailable
 * result rather than restoring the broader prerendered posting list.
 */
export async function loadCompanyBrowserData(params: {
  initialData: CompanyPageData;
  searchParams: URLSearchParams;
  locale: string;
  displayCurrency?: string | null;
  jobLanguages: string[];
  rates: CurrencyRate[];
  isLoggedIn: boolean;
}): Promise<CompanyBrowserDataResult> {
  const displayCurrency = validCurrency(params.displayCurrency) ?? "EUR";
  const salaryCurrencyParam =
    validCurrency(params.searchParams.get("salcur")) ?? displayCurrency;
  const salary = parseRange(params.searchParams.get("sal"));
  const experience = parseRange(params.searchParams.get("exp"));
  const languages = resolveJobLanguages(params.jobLanguages, params.locale);
  const offline = parseCompanyFilterStateOffline(params.searchParams);
  const unavailable = (
    parsed = offline.parsed,
    geo?: { userLat?: number; userLng?: number },
  ) => ({
    data: buildUnavailableData({
      initialData: params.initialData,
      parsed,
      displayCurrency,
      jobLanguages: params.jobLanguages,
      languages,
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
  let userLat = params.initialData.userLat;
  let userLng = params.initialData.userLng;
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
        { service: "typesense", operation: "company_semantic_filters" },
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
        { service: "typesense", operation: "browser_company_filter_state" },
        error,
      );
      return unavailable();
    }
    if (!filterState.complete) {
      return unavailable(filterState.parsed);
    }
    parsed = filterState.parsed;
  }
  if (parsed.unresolvedExplicitSlugs) {
    return unavailable(parsed, { userLat, userLng });
  }
  const hasResultFilter =
    parsed.keywords.length > 0 ||
    parsed.locations.length > 0 ||
    parsed.occupations.length > 0 ||
    parsed.seniorities.length > 0 ||
    parsed.technologies.length > 0 ||
    parsed.employmentTypes.length > 0 ||
    parsed.workMode.length > 0 ||
    salary.min != null ||
    salary.max != null ||
    experience.min != null ||
    experience.max != null ||
    languages.join(",") !== params.initialData.languages.join(",");

  const baseData: CompanyPageData = {
    ...params.initialData,
    parsed,
    displayCurrency,
    jobLanguages: params.jobLanguages,
    languages,
    salaryCurrencyParam,
    salaryMinDisplay: salary.min,
    salaryMaxDisplay: salary.max,
    experienceMin: experience.min,
    experienceMax: experience.max,
    userLat,
    userLng,
  };
  if (!hasResultFilter) {
    return { data: baseData, unavailable: false, directAttempted: false };
  }

  const result = await tryGetCompanyPostingsDirect(
    {
      companyId: params.initialData.company.id,
      keywords: parsed.keywords,
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
      salaryMinEur: convertToEur(
        salary.min,
        salaryCurrencyParam,
        params.rates,
      ),
      salaryMaxEur: convertToEur(
        salary.max,
        salaryCurrencyParam,
        params.rates,
      ),
      experienceMin: experience.min,
      experienceMax: experience.max,
      languages,
      locale: params.locale,
      offset: 0,
      limit: PAGE_SIZE,
    },
    params.isLoggedIn,
  );
  if (!result) return unavailable(parsed, { userLat, userLng });

  return {
    data: {
      ...baseData,
      postings: result.postings,
      activeCount: result.activeCount,
      yearCount: result.yearCount,
      truncated: result.truncated,
    },
    unavailable: false,
    directAttempted: true,
  };
}
