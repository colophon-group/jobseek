"use client";

import {
  searchJobs as serverSearchJobs,
  listTopCompanies as serverListTopCompanies,
} from "@/lib/actions/search";
import { getCompanyPostings as serverGetCompanyPostings } from "@/lib/actions/company";
import {
  getWatchlistPostings as serverGetWatchlistPostings,
  getWatchlistPostingYearCount as serverGetWatchlistPostingYearCount,
  type WatchlistPostingEntry,
} from "@/lib/actions/watchlists";
import type {
  SearchFilters,
  SearchResponse,
  SearchResultPosting,
} from "./types";
import {
  ANON_MAX_COMPANIES,
  ANON_MAX_POSTINGS,
  ANON_MAX_WATCHLIST_POSTINGS,
} from "./constants";
import { logExternalError } from "@/lib/safe-external-error";

type SearchInput = SearchFilters & { keywords: string[]; offset: number; limit: number };
type ListInput = SearchFilters & { offset: number; limit: number };
type CompanyPostingsInput = SearchFilters & {
  companyId: string;
  keywords: string[];
  offset: number;
  limit: number;
};
type CompanyPostingsResult = {
  postings: SearchResultPosting[];
  activeCount: number;
  yearCount: number;
  truncated?: boolean;
};
type SimilarCompaniesResult = {
  companies: Array<{
    id: string;
    slug: string;
    name: string;
    icon: string | null;
    activeJobCount: number;
  }>;
  hasMore: boolean;
};

const directEnabled = process.env.NEXT_PUBLIC_TYPESENSE_DIRECT === "1";

async function tryBrowserProvider() {
  const { getBrowserSearchProvider } = await import("./typesense-browser");
  return getBrowserSearchProvider();
}

function applyAnonCap(
  result: SearchResponse,
  offset: number,
  isLoggedIn: boolean,
): SearchResponse {
  if (isLoggedIn) return result;
  if (offset >= ANON_MAX_COMPANIES) {
    return { companies: [], totalCompanies: 0, truncated: true };
  }
  if (offset + result.companies.length >= ANON_MAX_COMPANIES) {
    return { ...result, truncated: true };
  }
  return result;
}

export async function runSearchJobs(
  params: SearchInput,
  isLoggedIn: boolean,
): Promise<SearchResponse> {
  if (directEnabled) {
    if (!isLoggedIn && params.offset >= ANON_MAX_COMPANIES) {
      return { companies: [], totalCompanies: 0, truncated: true };
    }
    try {
      const provider = await tryBrowserProvider();
      const result = await provider.search(params);
      if (!result.degraded) return applyAnonCap(result, params.offset, isLoggedIn);
    } catch (err) {
      logExternalError("error", { service: "typesense", operation: "browser_search_jobs" }, err);
    }
  }
  return serverSearchJobs(params);
}

export async function runListTopCompanies(
  params: ListInput,
  isLoggedIn: boolean,
): Promise<SearchResponse> {
  const direct = await tryListTopCompaniesDirect(params, isLoggedIn);
  if (direct) return direct;
  return serverListTopCompanies(params);
}

/**
 * Revalidate a server-prerendered explore shell directly against Typesense.
 * Returns null when browser-direct search is disabled or degraded so mount-time
 * refreshes never create a duplicate Vercel Server Action invocation.
 */
export async function tryListTopCompaniesDirect(
  params: ListInput,
  isLoggedIn: boolean,
): Promise<SearchResponse | null> {
  if (!directEnabled) return null;
  if (!isLoggedIn && params.offset >= ANON_MAX_COMPANIES) {
    return { companies: [], totalCompanies: 0, truncated: true };
  }
  try {
    const provider = await tryBrowserProvider();
    const result = await provider.listTopCompanies(params);
    if (!result.degraded) return applyAnonCap(result, params.offset, isLoggedIn);
  } catch (err) {
    logExternalError(
      "error",
      { service: "typesense", operation: "browser_list_top_companies" },
      err,
    );
  }
  return null;
}

type WatchlistPostingsInput = {
  companyIds: string[];
  anyCompany?: boolean;
  offset: number;
  limit: number;
  keywords?: string[];
  locationIds?: number[];
  occupationIds?: number[];
  seniorityIds?: number[];
  technologyIds?: number[];
  /** Work-mode filter — `onsite | hybrid | remote` (issue #3037). */
  workMode?: ("onsite" | "hybrid" | "remote")[];
  /** Employment-type filter (issue #3037). */
  employmentType?: string[];
  salaryMin?: number;
  salaryMax?: number;
  experienceMin?: number;
  experienceMax?: number;
  languages?: string[];
};

type WatchlistRefreshResult = {
  postings: WatchlistPostingEntry[];
  total: number;
  yearTotal: number;
};

/**
 * Refresh an anonymous public watchlist shell directly from Typesense.
 * A failure returns null so callers preserve SSR data without consuming a
 * mount-time Server Action invocation.
 */
export async function tryGetWatchlistSnapshotDirect(
  params: Omit<WatchlistPostingsInput, "offset" | "limit">,
): Promise<WatchlistRefreshResult | null> {
  if (!directEnabled) return null;
  try {
    const browser = await import("./typesense-browser-watchlist");
    const [{ postings, total }, yearTotal] = await Promise.all([
      browser.getWatchlistPostingsBrowser({ ...params, offset: 0, limit: 20 }),
      browser.getWatchlistPostingYearCountBrowser(params),
    ]);
    return { postings, total, yearTotal };
  } catch (err) {
    logExternalError(
      "error",
      { service: "typesense", operation: "browser_watchlist_snapshot" },
      err,
    );
    return null;
  }
}

export async function runGetWatchlistPostings(
  params: WatchlistPostingsInput,
  isLoggedIn: boolean,
): Promise<{ postings: WatchlistPostingEntry[]; total: number; truncated?: boolean }> {
  if (directEnabled) {
    if (!isLoggedIn && params.offset >= ANON_MAX_WATCHLIST_POSTINGS) {
      return { postings: [], total: 0, truncated: true };
    }
    try {
      const m = await import("./typesense-browser-watchlist");
      const result = await m.getWatchlistPostingsBrowser(params);
      const truncated =
        !isLoggedIn && params.offset + params.limit >= ANON_MAX_WATCHLIST_POSTINGS
          ? true
          : undefined;
      return truncated ? { ...result, truncated } : result;
    } catch (err) {
      logExternalError(
        "error",
        { service: "typesense", operation: "browser_watchlist_postings" },
        err,
      );
    }
  }
  return serverGetWatchlistPostings(params);
}

/**
 * Client runner for the watchlist "in the last year" count. Mirrors
 * the active-count fetch shape so the two badges on the watchlist
 * detail page can refetch in lockstep when filters change.
 *
 * Issue #3344 — before this, `yearTotal` was rendered as a static SSR
 * prop and went stale until the next full page reload. The active
 * count next to it kept updating via `runGetWatchlistPostings`, which
 * produced visible "active changes but year doesn't" divergence.
 */
export async function runGetWatchlistPostingYearCount(
  params: Omit<WatchlistPostingsInput, "offset" | "limit">,
): Promise<number> {
  return serverGetWatchlistPostingYearCount(params);
}

export async function runGetCompanyPostings(
  params: CompanyPostingsInput,
  isLoggedIn: boolean,
): Promise<CompanyPostingsResult> {
  const direct = await tryGetCompanyPostingsDirect(params, isLoggedIn);
  if (direct) return direct;
  return serverGetCompanyPostings(params);
}

/**
 * Revalidate a server-prerendered company shell directly against Typesense.
 * The null result is deliberate: callers that are merely refreshing hydrated
 * data must keep the rendered snapshot instead of falling back to Fluid CPU.
 */
export async function tryGetCompanyPostingsDirect(
  params: CompanyPostingsInput,
  isLoggedIn: boolean,
): Promise<CompanyPostingsResult | null> {
  if (!directEnabled) return null;
  if (!isLoggedIn && params.offset >= ANON_MAX_POSTINGS) {
    return { postings: [], activeCount: 0, yearCount: 0, truncated: true };
  }
  try {
    const provider = await tryBrowserProvider();
    const result = await provider.loadPostingsWithCounts(params);
    if (!isLoggedIn && params.offset + result.postings.length >= ANON_MAX_POSTINGS) {
      return { ...result, truncated: true };
    }
    return result;
  } catch (err) {
    logExternalError(
      "error",
      { service: "typesense", operation: "browser_company_postings" },
      err,
    );
    return null;
  }
}

/**
 * Revalidate the unfiltered peer strip embedded in a company shell directly
 * against Typesense. A failed refresh keeps the rendered snapshot and never
 * falls through to a mount-time Server Action.
 */
export async function tryGetSimilarCompaniesDirect(params: {
  companyId: string;
  industryId: number;
  limit: number;
}): Promise<SimilarCompaniesResult | null> {
  if (!directEnabled) return null;
  try {
    const provider = await tryBrowserProvider();
    return await provider.loadSimilarCompanies(
      params.companyId,
      params.industryId,
      params.limit,
    );
  } catch (err) {
    logExternalError(
      "error",
      { service: "typesense", operation: "browser_similar_companies" },
      err,
    );
    return null;
  }
}
