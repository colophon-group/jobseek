import "server-only";

import { isSafeCompanySlug } from "@/lib/services/company-detail-lookup";
import { CompanyReferenceError } from "@/lib/services/company-references";
import type { WatchlistFilters } from "@/lib/services/watchlists";

const MAX_HANDOFF_COMPANIES = 25;

/**
 * Resolve the public API's company-slug contract before writing UUID foreign
 * keys. Unknown/invalid slugs fail the whole handoff so a constrained request
 * can never silently broaden into an any-company watchlist.
 */
export async function createWatchlistFromHandoffWithDeps(params: {
  title: string;
  companySlugs: string[];
  filters?: WatchlistFilters;
}, deps: {
  getCompanyIdsBySlugs: (slugs: readonly string[]) => Promise<Map<string, string>>;
  createWatchlist: (params: {
    title: string;
    companyIds: string[];
    filters?: WatchlistFilters;
  }) => Promise<{ id: string; slug: string } | { error: string }>;
}): Promise<{ id: string; slug: string } | { error: string }> {
  if (params.companySlugs.length > MAX_HANDOFF_COMPANIES) {
    return { error: "invalid_companies" };
  }
  const companySlugs = [
    ...new Set(
      params.companySlugs
        .map((slug) => slug.trim().toLowerCase())
        .filter(Boolean),
    ),
  ];
  if (companySlugs.some((slug) => !isSafeCompanySlug(slug))) {
    return { error: "invalid_companies" };
  }

  let companyIdsBySlug: Map<string, string>;
  try {
    companyIdsBySlug = await deps.getCompanyIdsBySlugs(companySlugs);
  } catch (error) {
    // Resolver errors may contain provider connection details. Expose only
    // a stable retryable domain code and never save a partially resolved list.
    return { error: error instanceof CompanyReferenceError ? error.code : "company_lookup_unavailable" };
  }
  if (companyIdsBySlug.size !== companySlugs.length) {
    return { error: "unknown_company" };
  }

  const companyIds = [
    ...new Set(companySlugs.map((slug) => companyIdsBySlug.get(slug)!)),
  ];
  const filters = companyIds.length > 0
    ? { ...params.filters, anyCompany: false }
    : params.filters;

  return deps.createWatchlist({
    title: params.title,
    companyIds,
    filters,
  });
}
