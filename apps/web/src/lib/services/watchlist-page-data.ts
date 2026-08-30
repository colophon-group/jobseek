import "server-only";

import {
  getPublicWatchlistPostings,
  getWatchlistPostings,
  getWatchlistPostingYearCount,
  type WatchlistDetail,
  type WatchlistPostingEntry,
} from "@/lib/services/watchlists";
import { getCurrencyRates } from "@/lib/services/search";
import { resolveLocationSlugs } from "@/lib/services/locations";
import {
  resolveOccupationSlugs,
  resolveSenioritySlugs,
  resolveTechnologySlugs,
} from "@/lib/services/taxonomy";
import { resolveJobLanguages } from "@/lib/job-languages";
import { convertToEur } from "@/lib/salary";
import { isTypesenseUnavailableError } from "@/lib/search/typesense-retry";
import { logExternalError } from "@/lib/safe-external-error";
import type { WatchlistPostingsParams } from "@/lib/search/typesense-browser-watchlist";

/**
 * Existing watchlist routes must return a usable degraded response before a
 * chain of Typesense SDK retries can consume the Function's runtime budget.
 * The authoritative Postgres lookup happens before this search-only budget.
 */
export const WATCHLIST_SEARCH_BUDGET_MS = 6_000;

export interface WatchlistPageData {
  detail: WatchlistDetail;
  isOwner: boolean;
  isPaidPlan: boolean;
  limitReached: boolean;
  postings: WatchlistPostingEntry[];
  total: number;
  /** Count of postings first seen in the last year matching the same filters. */
  yearTotal: number;
  resolvedLocations: {
    id: number;
    slug: string;
    name: string;
    type: "macro" | "country" | "region" | "city";
    parentName: string | null;
  }[];
  resolvedOccupations: { id: number; slug: string; name: string }[];
  resolvedSeniorities: { id: number; slug: string; name: string }[];
  resolvedTechnologies: { id: number; slug: string; name: string }[];
  jobLanguages: string[];
  languages: string[];
  /** Resolved query base used for a no-fallback anonymous browser refresh. */
  browserPostingFilters?: Omit<WatchlistPostingsParams, "offset" | "limit"> | null;
}

export type BuildWatchlistPageDataParams = {
  detail: WatchlistDetail;
  locale: string;
  isOwner: boolean;
  isPaidPlan: boolean;
  limitReached: boolean;
  jobLanguages: string[];
  publicSnapshot: boolean;
};

function degradedWatchlistPageData(
  params: BuildWatchlistPageDataParams,
): WatchlistPageData {
  return {
    detail: params.detail,
    isOwner: params.isOwner,
    isPaidPlan: params.isPaidPlan,
    limitReached: params.limitReached,
    postings: [],
    total: 0,
    yearTotal: 0,
    resolvedLocations: [],
    resolvedOccupations: [],
    resolvedSeniorities: [],
    resolvedTechnologies: [],
    jobLanguages: params.jobLanguages,
    languages: resolveJobLanguages(params.jobLanguages, params.locale),
    browserPostingFilters: null,
  };
}

async function buildWatchlistPageDataUnbounded(
  params: BuildWatchlistPageDataParams,
  abortSignal: AbortSignal,
): Promise<WatchlistPageData> {
  const {
    detail,
    locale,
    isOwner,
    isPaidPlan,
    limitReached,
    jobLanguages,
    publicSnapshot,
  } = params;
  const languages = resolveJobLanguages(jobLanguages, locale);
  const filters = detail.filters;

  const [locMap, occMap, senMap, techMap] = await Promise.all([
    filters.locationSlugs?.length
      ? resolveLocationSlugs(filters.locationSlugs, locale)
      : Promise.resolve(new Map()),
    filters.occupationSlugs?.length
      ? resolveOccupationSlugs(filters.occupationSlugs, locale)
      : Promise.resolve(new Map()),
    filters.senioritySlugs?.length
      ? resolveSenioritySlugs(filters.senioritySlugs, locale)
      : Promise.resolve(new Map()),
    filters.technologySlugs?.length
      ? resolveTechnologySlugs(filters.technologySlugs)
      : Promise.resolve(new Map()),
  ]);

  const resolvedLocations = (filters.locationSlugs ?? [])
    .map((slug) => locMap.get(slug))
    .filter((location): location is NonNullable<typeof location> => location != null)
    .map((location) => ({
      id: location.id,
      slug: location.slug,
      name: location.name,
      type: location.type as "macro" | "country" | "region" | "city",
      parentName: location.parentName ?? null,
    }));
  const resolvedOccupations = (filters.occupationSlugs ?? [])
    .map((slug) => occMap.get(slug))
    .filter((occupation): occupation is NonNullable<typeof occupation> => occupation != null);
  const resolvedSeniorities = (filters.senioritySlugs ?? [])
    .map((slug) => senMap.get(slug))
    .filter((seniority): seniority is NonNullable<typeof seniority> => seniority != null);
  const resolvedTechnologies = (filters.technologySlugs ?? [])
    .map((slug) => techMap.get(slug))
    .filter((technology): technology is NonNullable<typeof technology> => technology != null);

  const workModes = new Set(["onsite", "hybrid", "remote"] as const);
  const validatedWorkMode = (filters.workMode ?? []).filter(
    (mode): mode is "onsite" | "hybrid" | "remote" => workModes.has(mode),
  );
  const salaryCurrency = filters.salaryCurrency ?? "EUR";
  const rates =
    filters.salaryMin != null || filters.salaryMax != null
      ? await getCurrencyRates()
      : [];
  const salaryMinEur = convertToEur(filters.salaryMin, salaryCurrency, rates);
  const salaryMaxEur = convertToEur(filters.salaryMax, salaryCurrency, rates);

  const browserPostingFilters = {
    companyIds: filters.anyCompany ? [] : detail.companies.map((company) => company.id),
    anyCompany: filters.anyCompany,
    keywords: filters.keywords,
    locationIds: resolvedLocations.map((location) => location.id),
    occupationIds: resolvedOccupations.map((occupation) => occupation.id),
    seniorityIds: resolvedSeniorities.map((seniority) => seniority.id),
    technologyIds: resolvedTechnologies.map((technology) => technology.id),
    workMode: validatedWorkMode.length > 0 ? validatedWorkMode : undefined,
    employmentType: filters.employmentType?.length ? filters.employmentType : undefined,
    salaryMin: salaryMinEur,
    salaryMax: salaryMaxEur,
    experienceMin: filters.experienceMin,
    experienceMax: filters.experienceMax,
    languages,
  } satisfies Omit<WatchlistPostingsParams, "offset" | "limit">;
  const sharedCountsParams = {
    ...browserPostingFilters,
    abortSignal,
  };
  const [{ postings, total }, yearTotal] = await Promise.all([
    (publicSnapshot ? getPublicWatchlistPostings : getWatchlistPostings)({
      ...sharedCountsParams,
      offset: 0,
      limit: 20,
    }),
    getWatchlistPostingYearCount(sharedCountsParams),
  ]);

  return {
    detail,
    isOwner,
    isPaidPlan,
    limitReached,
    postings,
    total,
    yearTotal,
    resolvedLocations,
    resolvedOccupations,
    resolvedSeniorities,
    resolvedTechnologies,
    jobLanguages,
    languages,
    browserPostingFilters,
  };
}

function searchDeadlineError(): Error {
  return Object.assign(new Error("Watchlist search budget exceeded"), {
    typesenseUnavailable: true as const,
  });
}

export async function buildWatchlistPageData(
  params: BuildWatchlistPageDataParams,
): Promise<WatchlistPageData> {
  const controller = new AbortController();
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([
      buildWatchlistPageDataUnbounded(params, controller.signal),
      new Promise<never>((_resolve, reject) => {
        timer = setTimeout(() => {
          const deadlineError = searchDeadlineError();
          reject(deadlineError);
          controller.abort(deadlineError);
        }, WATCHLIST_SEARCH_BUDGET_MS);
      }),
    ]);
  } catch (err) {
    if (!isTypesenseUnavailableError(err)) throw err;
    logExternalError(
      "error",
      { service: "typesense", operation: "watchlist_page_data" },
      err,
    );
    return degradedWatchlistPageData(params);
  } finally {
    if (timer) clearTimeout(timer);
    if (!controller.signal.aborted) controller.abort(searchDeadlineError());
  }
}

/** Anonymous, cache-safe initial data for an authoritative public row. */
export function fetchPublicWatchlistPageData(params: {
  detail: WatchlistDetail;
  locale: string;
}): Promise<WatchlistPageData> {
  return buildWatchlistPageData({
    detail: params.detail,
    locale: params.locale,
    isOwner: false,
    isPaidPlan: false,
    limitReached: true,
    jobLanguages: [],
    publicSnapshot: true,
  });
}
