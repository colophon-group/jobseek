"use client";

import {
  searchJobs as serverSearchJobs,
  listTopCompanies as serverListTopCompanies,
} from "@/lib/actions/search";
import type { SearchFilters, SearchResponse } from "./types";
import { ANON_MAX_COMPANIES } from "./constants";

type SearchInput = SearchFilters & { keywords: string[]; offset: number; limit: number };
type ListInput = SearchFilters & { offset: number; limit: number };

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
