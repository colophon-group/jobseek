"use client";

import {
  searchJobs as serverSearchJobs,
  listTopCompanies as serverListTopCompanies,
} from "@/lib/actions/search";
import { getCompanyPostings as serverGetCompanyPostings } from "@/lib/actions/company";
import {
  getWatchlistPostings as serverGetWatchlistPostings,
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
      console.error("[search-runner] browser searchJobs failed, falling back", err);
    }
  }
  return serverSearchJobs(params);
}

export async function runListTopCompanies(
  params: ListInput,
  isLoggedIn: boolean,
): Promise<SearchResponse> {
  if (directEnabled) {
    if (!isLoggedIn && params.offset >= ANON_MAX_COMPANIES) {
      return { companies: [], totalCompanies: 0, truncated: true };
    }
    try {
      const provider = await tryBrowserProvider();
      const result = await provider.listTopCompanies(params);
      if (!result.degraded) return applyAnonCap(result, params.offset, isLoggedIn);
    } catch (err) {
      console.error("[search-runner] browser listTopCompanies failed, falling back", err);
    }
  }
  return serverListTopCompanies(params);
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
      console.error("[search-runner] browser getWatchlistPostings failed, falling back", err);
    }
  }
  return serverGetWatchlistPostings(params);
}

export async function runGetCompanyPostings(
  params: CompanyPostingsInput,
  isLoggedIn: boolean,
): Promise<CompanyPostingsResult> {
  if (directEnabled) {
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
      console.error("[search-runner] browser getCompanyPostings failed, falling back", err);
    }
  }
  return serverGetCompanyPostings(params);
}
