import "server-only";

import {
  getPublicWatchlistPostings,
  getWatchlistPostings,
  getWatchlistPostingYearCount,
  type WatchlistDetail,
  type WatchlistPostingEntry,
} from "@/lib/services/watchlists";
import { resolveJobLanguages } from "@/lib/job-languages";
import { isTypesenseUnavailableError } from "@/lib/search/typesense-retry";
import { logExternalError } from "@/lib/safe-external-error";
import type { WatchlistPostingsParams } from "@/lib/search/typesense-browser-watchlist";
import { compileWatchlistMatcherSources } from "@/lib/services/watchlist-matcher";

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
  const [compiled] = await compileWatchlistMatcherSources([{
    watchlistId: detail.id,
    watchlistLabel: detail.title,
    filters: detail.filters,
    companyIds: detail.companies.map((company) => company.id),
    locale,
    jobLanguages,
  }]);
  const browserPostingFilters = compiled.candidateFilters;
  const languages = browserPostingFilters.languages ?? resolveJobLanguages(jobLanguages, locale);
  const resolvedLocations = compiled.resolvedLocations.map((location) => ({
      id: location.id,
      slug: location.slug,
      name: location.name,
      type: location.type as "macro" | "country" | "region" | "city",
      parentName: location.parentName ?? null,
    }));
  const resolvedOccupations = compiled.resolvedOccupations;
  const resolvedSeniorities = compiled.resolvedSeniorities;
  const resolvedTechnologies = compiled.resolvedTechnologies;
  browserPostingFilters satisfies Omit<WatchlistPostingsParams, "offset" | "limit">;
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
