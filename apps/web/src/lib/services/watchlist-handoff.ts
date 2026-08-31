import "server-only";

import { isSafeCompanySlug } from "@/lib/services/company-detail-lookup";
import type { WatchlistFilters } from "@/lib/services/watchlists";

const MAX_HANDOFF_COMPANIES = 25;

/**
 * Resolve the public API's company-slug contract before writing UUID foreign
 * keys. Unknown/invalid slugs fail the whole handoff so a constrained request
 * can never silently broaden into an any-company watchlist.
 */
export async function createWatchlistFromHandoffWithDeps(params: {
  title: string;
  description?: string;
  companySlugs: string[];
  filters?: WatchlistFilters;
}, deps: {
  getCompanyIdsBySlugs: (slugs: readonly string[]) => Promise<Map<string, string>>;
  createWatchlist: (params: {
    title: string;
    description?: string;
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

  const companyIdsBySlug = await deps.getCompanyIdsBySlugs(companySlugs);
  if (companyIdsBySlug.size !== companySlugs.length) {
    return { error: "invalid_companies" };
  }

  const companyIds = [
    ...new Set(companySlugs.map((slug) => companyIdsBySlug.get(slug)!)),
  ];
  const filters = companyIds.length > 0
    ? { ...params.filters, anyCompany: false }
    : params.filters;

  return deps.createWatchlist({
    title: params.title,
    description: params.description,
    companyIds,
    filters,
  });
}
