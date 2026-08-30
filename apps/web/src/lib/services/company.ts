import "server-only";

import { cacheLife, cacheTag } from "next/cache";
import { CACHE_TTL_MEDIUM, CACHE_TTL_LONG } from "@/lib/cache-ttl";
import { getSearchProvider } from "@/lib/search";
import type { SearchResultPosting, WorkMode } from "@/lib/search";
import {
  companyByIdCacheTag,
  companyCsvDataCacheTag,
  typeaheadCompaniesCacheTag,
} from "@/lib/cache-tags";
import { getSessionUserId } from "@/lib/sessionCache";
import { ANON_MAX_COMPANIES, ANON_MAX_POSTINGS } from "@/lib/search/constants";
import { getSearchClient } from "@/lib/search/typesense-client";
import { buildFilterString, POSTING_BASE_FILTER } from "@/lib/search/typesense-filters";
import { isTypesenseUnavailableError } from "@/lib/search/typesense-retry";
import {
  fetchLocationDocumentsByIds,
  fetchLocationDocumentsWithAncestors,
  fetchLocationMacroDocuments,
  type TypesenseLocationDocument,
} from "@/lib/search/typesense-taxonomy";
import { parseSearchFilters } from "@/lib/services/search-input";
import { getCurrencyRates } from "@/lib/services/search";
import { firstOf, idsOrUndefined, parseRangeParam } from "@/lib/search/params";
import { convertToEur } from "@/lib/salary";
import { canonicalStringCompare, makeDisplayStringCompare } from "@/lib/sort";
import { logExternalError } from "@/lib/safe-external-error";

export { getCompanyBySlug } from "@/lib/services/company-detail";
export type { CompanyDetail } from "@/lib/services/company-detail";

// ── Company suggestions (search bar autocomplete) ───────────────────

export interface CompanySuggestion {
  id: string;
  name: string;
  slug: string;
  icon: string | null;
}

export async function suggestCompanies(params: {
  query: string;
  failOnUnavailable?: boolean;
}): Promise<CompanySuggestion[]> {
  const q = params.query.trim().toLowerCase();
  if (q.length < 2) return [];

  // Let Typesense failures escape the cache boundary so an outage-shaped
  // empty list is not cached for an hour. The public surface degrades to no
  // suggestions until the search service recovers.
  try {
    return await _queryCompanySuggestionsCached(q);
  } catch (err) {
    if (!isTypesenseUnavailableError(err)) throw err;
    if (params.failOnUnavailable) throw err;
    return [];
  }
}

async function _queryCompanySuggestionsCached(
  q: string,
): Promise<CompanySuggestion[]> {
  "use cache";
  cacheLife({ revalidate: CACHE_TTL_LONG });
  // Tag the slot so `revalidateTag(typeaheadCompaniesCacheTag())` from
  // /api/internal/invalidate-typeahead drops it after `crawler sync`,
  // instead of waiting up to 3600s for the TTL. See #2907 follow-up.
  cacheTag(typeaheadCompaniesCacheTag());

  const result = await getSearchClient().collections("company").documents().search({
    q,
    query_by: "name",
    filter_by: "active_posting_count:>0",
    sort_by: "_text_match:desc,active_posting_count:desc",
    per_page: 5,
    prefix: true,
    num_typos: 1,
  });

  return (result.hits ?? []).map((hit) => {
    const doc = hit.document as Record<string, unknown>;
    return {
      id: String(doc.id),
      name: String(doc.name ?? ""),
      slug: String(doc.slug ?? ""),
      icon: typeof doc.icon === "string" ? doc.icon : null,
    };
  });
}

// ── Paginated company search with filter-aware match counts ─────────

export interface CompanyListEntry {
  id: string;
  name: string;
  slug: string;
  icon: string | null;
  description: string | null;
  activeMatches: number;
  yearMatches: number;
}

export async function searchCompaniesForWatchlist(params: {
  query?: string;
  industryId?: number;
  locale: string;
  offset: number;
  limit: number;
  // Current watchlist filters — used to compute match counts
  keywords?: string[];
  locationIds?: number[];
  occupationIds?: number[];
  seniorityIds?: number[];
  technologyIds?: number[];
  salaryMin?: number;
  salaryMax?: number;
  experienceMin?: number;
  experienceMax?: number;
  languages?: string[];
  starredCompanyIds?: string[];
}): Promise<{ companies: CompanyListEntry[]; total: number }> {
  try {
    return await _searchCompaniesForWatchlistTypesense(params);
  } catch (err) {
    if (!isTypesenseUnavailableError(err)) throw err;
    logExternalError(
      "error",
      { service: "typesense", operation: "search_companies_watchlist" },
      err,
    );
    return { companies: [], total: 0 };
  }
}

async function _searchCompaniesForWatchlistTypesense(params: {
  query?: string;
  industryId?: number;
  locale: string;
  offset: number;
  limit: number;
  keywords?: string[];
  locationIds?: number[];
  occupationIds?: number[];
  seniorityIds?: number[];
  technologyIds?: number[];
  salaryMin?: number;
  salaryMax?: number;
  experienceMin?: number;
  experienceMax?: number;
  languages?: string[];
  starredCompanyIds?: string[];
}): Promise<{ companies: CompanyListEntry[]; total: number }> {
  const client = getSearchClient();
  const q = params.query?.trim();
  const hasQuery = q && q.length >= 2;

  // No expansion needed — ancestor IDs are stored on each Typesense document
  // Build watchlist context filter for job_posting queries.
  // Map salaryMin/salaryMax to salaryMinEur/salaryMaxEur for buildFilterString.
  const watchlistFilterStr = buildFilterString({
    locationIds: params.locationIds,
    occupationIds: params.occupationIds,
    seniorityIds: params.seniorityIds,
    technologyIds: params.technologyIds,
    salaryMinEur: params.salaryMin,
    salaryMaxEur: params.salaryMax,
    experienceMin: params.experienceMin,
    experienceMax: params.experienceMax,
    languages: params.languages,
  });

  const hasWatchlistFilters = watchlistFilterStr.length > 0 || (params.keywords && params.keywords.length > 0);

  // Starred company handling
  const starredIds = params.starredCompanyIds;
  const wantStarredBoost = !hasQuery && starredIds && starredIds.length > 0;

  if (hasWatchlistFilters) {
    // FACET APPROACH: Get companies ranked by filtered match count.
    // Facets only return companies with >0 matching postings — zero-match filtering is implicit.
    const activeFilter = `${POSTING_BASE_FILTER}${watchlistFilterStr ? " && " + watchlistFilterStr : ""}`;
    const keywordsQ = params.keywords?.length ? params.keywords.join(" ") : "*";

    const facetResult = await client.collections("job_posting").documents().search({
      q: keywordsQ,
      query_by: "title",
      filter_by: activeFilter,
      facet_by: "company_id",
      facet_strategy: "exhaustive",
      max_facet_values: params.offset + params.limit + (wantStarredBoost ? starredIds!.length : 0),
      per_page: 0, // counts only
    });

    const facetCounts = facetResult.facet_counts?.[0]?.counts ?? [];
    const totalFromFacet = facetResult.facet_counts?.[0]?.stats?.total_values ?? 0;

    // Build a map of companyId -> active match count from facets
    const activeMatchMap = new Map<string, number>();
    for (const fc of facetCounts) {
      activeMatchMap.set(fc.value, fc.count);
    }

    // If we need text filtering on company name, filter the facet results
    let filteredCompanyIds: string[];
    let total: number;

    if (hasQuery || params.industryId != null) {
      // Query company collection to get matching company IDs, then intersect
      const companyFilterParts: string[] = [];
      if (params.industryId != null) companyFilterParts.push(`industry_id:=${params.industryId}`);

      const companyResult = await client.collections("company").documents().search({
        q: hasQuery ? q! : "*",
        query_by: "name",
        ...(companyFilterParts.length > 0
          ? { filter_by: companyFilterParts.join(" && ") }
          : {}),
        per_page: 250, // generous limit to intersect with facets
        prefix: true,
        num_typos: 1,
      });

      const companyHits = companyResult.hits ?? [];
      const companyHitDocs = companyHits.map((hit) => hit.document as Record<string, unknown>);
      const companyHitIds = companyHitDocs.map((doc) => doc.id as string);

      if (companyHitIds.length > 0) {
        const candidateFacetResult = await client.collections("job_posting").documents().search({
          q: keywordsQ,
          query_by: "title",
          filter_by: `${activeFilter} && company_id:[${companyHitIds.join(",")}]`,
          facet_by: "company_id",
          facet_strategy: "exhaustive",
          max_facet_values: companyHitIds.length,
          per_page: 0,
        });

        for (const fc of candidateFacetResult.facet_counts?.[0]?.counts ?? []) {
          activeMatchMap.set(fc.value, fc.count);
        }
      }

      // Keep all company-name hits selectable. The first posting facet query
      // only returns the top filtered companies, so exact company searches
      // like "Google" or "Salesforce" could disappear when they had zero
      // matches for the current watchlist filters, or when they simply were
      // not in that first facet window.
      const positiveMatchIds = companyHitIds
        .filter((id) => (activeMatchMap.get(id) ?? 0) > 0)
        .sort((a, b) => (activeMatchMap.get(b) ?? 0) - (activeMatchMap.get(a) ?? 0));
      const zeroMatchIds = companyHitIds.filter((id) => !positiveMatchIds.includes(id));
      filteredCompanyIds = [...positiveMatchIds, ...zeroMatchIds];
      total = filteredCompanyIds.length;
    } else {
      filteredCompanyIds = facetCounts.map((fc) => fc.value);
      total = totalFromFacet;
    }

    // Apply starred boost ordering
    let orderedIds: string[];
    if (wantStarredBoost) {
      const starredSet = new Set(starredIds!);
      const starred = filteredCompanyIds.filter((id) => starredSet.has(id));
      const rest = filteredCompanyIds.filter((id) => !starredSet.has(id));
      orderedIds = [...starred, ...rest];
    } else {
      orderedIds = filteredCompanyIds;
    }

    // Paginate
    const pageIds = orderedIds.slice(params.offset, params.offset + params.limit);
    if (pageIds.length === 0) return { companies: [], total };

    // Fetch company details + year counts from company collection
    const companyDocs = await client.collections("company").documents().search({
      q: "*",
      filter_by: `id:[${pageIds.join(",")}]`,
      per_page: pageIds.length,
    });

    const companyMap = new Map<string, Record<string, unknown>>();
    for (const hit of companyDocs.hits ?? []) {
      const doc = hit.document as Record<string, unknown>;
      companyMap.set(doc.id as string, doc);
    }

    return {
      companies: pageIds.map((id) => {
        const doc = companyMap.get(id);
        return {
          id,
          name: (doc?.name as string) ?? "",
          slug: (doc?.slug as string) ?? "",
          icon: (doc?.icon as string) ?? null,
          description: (doc?.description as string) ?? null,
          activeMatches: activeMatchMap.get(id) ?? 0,
          yearMatches: (doc?.year_posting_count as number) ?? 0,
        };
      }),
      total,
    };
  }

  // NO WATCHLIST FILTERS: query the full company collection. Companies with
  // no active postings are still valid watchlist targets (#3383).

  if (wantStarredBoost) {
    // Two queries: starred first, then remaining
    const companyFilterParts: string[] = [];
    if (params.industryId != null) companyFilterParts.push(`industry_id:=${params.industryId}`);
    const baseFilter = companyFilterParts.join(" && ");

    const starredFilter = [baseFilter, `id:[${starredIds!.join(",")}]`]
      .filter(Boolean)
      .join(" && ");
    const remainingFilter = [baseFilter, `id:!=[${starredIds!.join(",")}]`]
      .filter(Boolean)
      .join(" && ");

    const [starredResult, remainingResult] = await Promise.all([
      client.collections("company").documents().search({
        q: "*",
        query_by: "name",
        filter_by: starredFilter,
        sort_by: "active_posting_count:desc",
        per_page: starredIds!.length,
        page: 1,
      }),
      client.collections("company").documents().search({
        q: "*",
        query_by: "name",
        filter_by: remainingFilter,
        sort_by: "active_posting_count:desc",
        per_page: params.limit,
        page: 1,
      }),
    ]);

    // Combine: all starred + remaining to fill the page
    const starredHits = starredResult.hits ?? [];
    const remainingHits = remainingResult.hits ?? [];
    const allHits = [...starredHits, ...remainingHits];
    const total = (starredResult.found ?? 0) + (remainingResult.found ?? 0);

    // Paginate across the combined result
    const pageHits = allHits.slice(params.offset, params.offset + params.limit);

    return {
      companies: pageHits.map((hit) => {
        const doc = hit.document as Record<string, unknown>;
        return {
          id: doc.id as string,
          name: doc.name as string,
          slug: doc.slug as string,
          icon: (doc.icon as string) ?? null,
          description: (doc.description as string) ?? null,
          activeMatches: (doc.active_posting_count as number) ?? 0,
          yearMatches: (doc.year_posting_count as number) ?? 0,
        };
      }),
      total,
    };
  }

  // Simple case: no starred, no watchlist filters, maybe text query
  const companyFilterParts: string[] = [];
  if (params.industryId != null) companyFilterParts.push(`industry_id:=${params.industryId}`);

  const result = await client.collections("company").documents().search({
    q: hasQuery ? q! : "*",
    query_by: "name",
    ...(companyFilterParts.length > 0
      ? { filter_by: companyFilterParts.join(" && ") }
      : {}),
    sort_by: hasQuery ? "_text_match:desc,active_posting_count:desc" : "active_posting_count:desc",
    per_page: params.limit,
    page: Math.floor(params.offset / params.limit) + 1,
    prefix: true,
    num_typos: 1,
  });

  return {
    companies: (result.hits ?? []).map((hit) => {
      const doc = hit.document as Record<string, unknown>;
      return {
        id: doc.id as string,
        name: doc.name as string,
        slug: doc.slug as string,
        icon: (doc.icon as string) ?? null,
        description: (doc.description as string) ?? null,
        activeMatches: (doc.active_posting_count as number) ?? 0,
        yearMatches: (doc.year_posting_count as number) ?? 0,
      };
    }),
    total: result.found ?? 0,
  };
}

// ── Industry suggestions ────────────────────────────────────────────

export interface IndustrySuggestion {
  id: number;
  name: string;
}

export async function suggestIndustries(params: {
  query?: string;
  locale: string;
  failOnUnavailable?: boolean;
}): Promise<IndustrySuggestion[]> {
  try {
    return await _suggestIndustries(params);
  } catch (err) {
    if (!isTypesenseUnavailableError(err)) throw err;
    if (params.failOnUnavailable) throw err;
    logExternalError("error", { service: "typesense", operation: "suggest_industries" }, err);
    return [];
  }
}

async function _suggestIndustries(params: {
  query?: string;
  locale: string;
}): Promise<IndustrySuggestion[]> {
  const q = params.query?.trim().toLowerCase();
  const hasQuery = q && q.length >= 1;
  const localeField = ["de", "fr", "it"].includes(params.locale)
    ? `industry_name_${params.locale}`
    : "industry_name";
  const result = await getSearchClient().collections("company").documents().search({
    q: hasQuery ? q : "*",
    query_by: "industry_name,industry_name_de,industry_name_fr,industry_name_it",
    filter_by: "industry_id:>=0",
    group_by: "industry_id",
    group_limit: 1,
    per_page: 100,
    prefix: true,
    num_typos: 0,
    include_fields: [...new Set(["industry_id", "industry_name", localeField])].join(","),
  });

  const suggestions = (result.grouped_hits ?? []).flatMap((group) => {
    const doc = group.hits[0]?.document as Record<string, unknown> | undefined;
    if (!doc || typeof doc.industry_id !== "number") return [];
    const name = String(doc[localeField] ?? doc.industry_name ?? "");
    return name ? [{ id: doc.industry_id, name }] : [];
  });
  return suggestions
    .sort((a, b) => canonicalStringCompare(a.name, b.name));
}

// ── Similar companies (same industry, active, excluding self) ───────

export interface SimilarCompany {
  id: string;
  slug: string;
  name: string;
  icon: string | null;
  activeJobCount: number;
}

export interface SimilarCompaniesPage {
  companies: SimilarCompany[];
  hasMore: boolean;
  /** True when an anonymous user has reached the pagination cap. */
  truncated?: boolean;
}

/**
 * Same-industry peers for the company page strip.
 *
 * Two code paths:
 * - **Unfiltered** — query the `company` collection by `active_posting_count`
 *   desc. Paginated (offset + limit), counts are the precomputed totals.
 * - **Filtered** — the caller passes URL `searchParams` reflecting the
 *   user's active filters. We fetch a pool of same-industry candidates
 *   from `company`, then facet on `job_posting` (filtered + scoped to
 *   those candidate IDs) to get per-company filtered counts. Returns
 *   top-N by filtered count; pagination is disabled because the
 *   filter-ranked order breaks offset semantics.
 *
 * Either path: returns an empty page on any failure so the strip
 * silently hides.
 */
export async function getSimilarCompanies(
  companyId: string,
  industryId: number | null,
  opts: {
    offset?: number;
    limit?: number;
    /** Raw URL search params. When any filter is set, the filtered path runs. */
    searchParams?: Record<string, string | string[] | undefined>;
    locale?: string;
  } = {},
): Promise<SimilarCompaniesPage> {
  const offset = opts.offset ?? 0;
  const limit = opts.limit ?? 10;
  if (industryId == null || !Number.isInteger(industryId)) {
    return { companies: [], hasMore: false };
  }

  const filters = await _parseSimilarFilters(opts.searchParams, opts.locale);
  if (_hasSimilarFilters(filters)) {
    // Filtered path: cacheLife('hours') (was Redis ttl 600s) — semantic
    // similarity within an industry shifts on the same time-scale as
    // posting churn. Migrated from Redis-backed `cached()` in #2884
    // (bucket 4). Filters are normalized (sorted arrays, primitives) so
    // the implicit `'use cache'` argument-hash key matches the legacy
    // `_similarFiltersKey` concat-key behaviour and avoids splitting
    // hits across input-order permutations.
    return _fetchSimilarFilteredCached(
      companyId,
      industryId,
      limit,
      _normalizeSimilarFilters(filters),
    );
  }

  // Anonymous users can scroll up to ANON_MAX_COMPANIES similar peers;
  // after that pagination is capped and the strip renders a sign-in
  // prompt (same pattern as the main companies list — see
  // actions/search.ts::searchCompanies and TruncationPrompt usage).
  // The cache stays shared between logged-in and anon; the cap is
  // applied outside the cached() boundary so cache keys don't multiply.
  //
  // The session lookup (which reads request headers and would force
  // dynamic rendering on any caller) is gated to the load-more path.
  // First-page calls never approach the cap, so callers rendered from
  // a static page can fetch page 0 without tainting the server render.
  // See issue #2243.
  const wouldHitCap = offset + limit > ANON_MAX_COMPANIES;
  const userId = wouldHitCap ? await getSessionUserId() : null;
  if (wouldHitCap && !userId && offset >= ANON_MAX_COMPANIES) {
    return { companies: [], hasMore: false, truncated: true };
  }

  // Unfiltered path: cacheLife({ revalidate: 3600 }) preserves the
  // legacy 1h TTL for the unfiltered ranked-peers slot. Migrated from
  // Redis-backed `cached()` in #2884 (bucket 4).
  const page = await _fetchSimilarUnfilteredCached(
    companyId,
    industryId,
    offset,
    limit,
  );

  if (wouldHitCap && !userId && offset + page.companies.length >= ANON_MAX_COMPANIES) {
    return { ...page, hasMore: false, truncated: true };
  }
  return page;
}

// Cached wrapper for `_fetchSimilarFiltered`. Splits the public
// `getSimilarCompanies` from the cache boundary so the session-tainted
// branch (the cap check) stays outside the slot — and so the unfiltered
// vs filtered branches each get their own implicit cache key based on
// their distinct argument lists. See #2884 bucket 4.
async function _fetchSimilarFilteredCached(
  companyId: string,
  industryId: number,
  limit: number,
  filters: SimilarFilters,
): Promise<SimilarCompaniesPage> {
  "use cache";
  cacheLife("hours");
  cacheTag(companyByIdCacheTag(companyId));
  // CSV-driven sweep — an industry move (changing `industry_id` on a
  // company row) changes the candidate pool for every other company in
  // the source AND target industry. Conservative: drop on every CSV
  // sync. Mirrors the legacy company-similar Redis namespace from the
  // cache invalidation registry (#2715). See #2884.
  cacheTag(companyCsvDataCacheTag());
  return _fetchSimilarFiltered(companyId, industryId, limit, filters);
}

async function _fetchSimilarUnfilteredCached(
  companyId: string,
  industryId: number,
  offset: number,
  limit: number,
): Promise<SimilarCompaniesPage> {
  "use cache";
  cacheLife({ revalidate: CACHE_TTL_LONG });
  cacheTag(companyByIdCacheTag(companyId));
  cacheTag(companyCsvDataCacheTag());
  return _fetchSimilarUnfiltered(companyId, industryId, offset, limit);
}

async function _fetchSimilarUnfiltered(
  companyId: string,
  industryId: number,
  offset: number,
  limit: number,
): Promise<SimilarCompaniesPage> {
  try {
    const client = getSearchClient();
    // Typesense paginates via 1-based `page`. Convert offset → page with
    // `per_page = limit`; on mixed offsets the client picks the right batch.
    const page = Math.floor(offset / limit) + 1;
    const result = await client.collections("company").documents().search({
      q: "*",
      query_by: "name",
      filter_by: `industry_id:=${industryId} && active_posting_count:>0 && id:!=${companyId}`,
      sort_by: "active_posting_count:desc",
      per_page: limit,
      page,
      include_fields: "id,slug,name,icon,active_posting_count",
    });
    const companies = (result.hits ?? []).map((hit) => _toSimilarCompany(hit.document as Record<string, unknown>));
    const found = typeof result.found === "number" ? result.found : companies.length;
    const hasMore = offset + companies.length < found;
    return { companies, hasMore };
  } catch (err) {
    logExternalError("error", { service: "typesense", operation: "similar_companies" }, err);
    return { companies: [], hasMore: false };
  }
}

type SimilarFilters = {
  keywords: string[];
  locationIds: number[];
  occupationIds: number[];
  seniorityIds: number[];
  technologyIds: number[];
  employmentTypes: string[];
  salaryMinEur?: number;
  salaryMaxEur?: number;
  experienceMin?: number;
  experienceMax?: number;
};

async function _parseSimilarFilters(
  searchParams: Record<string, string | string[] | undefined> | undefined,
  locale: string | undefined,
): Promise<SimilarFilters> {
  const empty: SimilarFilters = {
    keywords: [],
    locationIds: [],
    occupationIds: [],
    seniorityIds: [],
    technologyIds: [],
    employmentTypes: [],
  };
  if (!searchParams || !locale) return empty;

  const q = firstOf(searchParams.q);
  const loc = firstOf(searchParams.loc);
  const occ = firstOf(searchParams.occ);
  const sen = firstOf(searchParams.sen);
  const tech = firstOf(searchParams.tech);
  const sal = firstOf(searchParams.sal);
  const salcur = firstOf(searchParams.salcur);
  const exp = firstOf(searchParams.exp);
  const etype = firstOf(searchParams.etype);

  const parsed = await parseSearchFilters({ q, loc, occ, sen, tech, locale });
  const { min: salaryMinDisplay, max: salaryMaxDisplay } = parseRangeParam(sal);
  // Convert user-currency filter amount to EUR — the `salary_eur` field on
  // every job_posting Typesense document is in EUR (see
  // apps/crawler/src/processing/cpu.py::_extract_salary_fields), so the filter
  // threshold MUST be in EUR-equivalent units. Without this, "100K USD" was
  // compared against EUR-indexed values, silently excluding US roles paying
  // $100K (their `salary_eur` ≈ 92,000 < 100,000). Mirrors the fix in
  // `explore-page-data.ts` / `company-page-data.ts` (issue #3178).
  //
  // The strip is a client component that calls this server action with the
  // URL search params from `useSearchParams()`. The toolbar omits `salcur`
  // from the URL only when it equals "EUR" (see `company-page.tsx::updateUrl`
  // and `search-page.tsx::updateUrl`), so `salcur ?? "EUR"` is the URL's
  // own source of truth — no need to read user preferences here, which would
  // taint the `'use cache'` boundary and break the static company-page shell.
  //
  // `getCurrencyRates` is cache-backed (`cacheLife("hours")`) and is only
  // called when a salary filter is actually active.
  const salaryCurrencyParam = salcur ?? "EUR";
  const rates =
    salaryMinDisplay != null || salaryMaxDisplay != null
      ? await getCurrencyRates()
      : [];
  const salaryMinEur = convertToEur(salaryMinDisplay, salaryCurrencyParam, rates);
  const salaryMaxEur = convertToEur(salaryMaxDisplay, salaryCurrencyParam, rates);
  const { min: experienceMin, max: experienceMax } = parseRangeParam(exp);

  return {
    keywords: parsed.keywords,
    locationIds: idsOrUndefined(parsed.locations) ?? [],
    occupationIds: idsOrUndefined(parsed.occupations) ?? [],
    seniorityIds: idsOrUndefined(parsed.seniorities) ?? [],
    technologyIds: idsOrUndefined(parsed.technologies) ?? [],
    employmentTypes: etype ? etype.split(",").filter(Boolean) : [],
    salaryMinEur,
    salaryMaxEur,
    experienceMin,
    experienceMax,
  };
}

function _hasSimilarFilters(f: SimilarFilters): boolean {
  return (
    f.keywords.length > 0 ||
    f.locationIds.length > 0 ||
    f.occupationIds.length > 0 ||
    f.seniorityIds.length > 0 ||
    f.technologyIds.length > 0 ||
    f.employmentTypes.length > 0 ||
    f.salaryMinEur != null ||
    f.salaryMaxEur != null ||
    f.experienceMin != null ||
    f.experienceMax != null
  );
}

/**
 * Sort all array fields for stable `'use cache'` key derivation. The
 * legacy `cached()` helper hashed a concat-key string built from
 * pre-sorted arrays — under `'use cache'`, the implicit key derives
 * from the argument structure, so the arrays themselves must be sorted
 * for `[A,B]` and `[B,A]` inputs to share a slot. Pure function; the
 * input is not mutated. See #2884 bucket 4.
 *
 * String fields use `canonicalStringCompare` (locale-independent
 * `Intl.Collator("en", { sensitivity: "base" })`) — the raw
 * `Array#sort()` uses UTF-16 code unit order, where `"ü"` (U+00FC)
 * sorts after `"z"` (U+007A). That produces different cache keys for
 * `["python","übung","zoom"]` depending on the caller's input
 * permutation. See #3221.
 */
function _normalizeSimilarFilters(f: SimilarFilters): SimilarFilters {
  return {
    keywords: [...f.keywords].sort(canonicalStringCompare),
    locationIds: [...f.locationIds].sort((a, b) => a - b),
    occupationIds: [...f.occupationIds].sort((a, b) => a - b),
    seniorityIds: [...f.seniorityIds].sort((a, b) => a - b),
    technologyIds: [...f.technologyIds].sort((a, b) => a - b),
    employmentTypes: [...f.employmentTypes].sort(canonicalStringCompare),
    salaryMinEur: f.salaryMinEur,
    salaryMaxEur: f.salaryMaxEur,
    experienceMin: f.experienceMin,
    experienceMax: f.experienceMax,
  };
}

async function _fetchSimilarFiltered(
  companyId: string,
  industryId: number,
  limit: number,
  filters: SimilarFilters,
): Promise<SimilarCompaniesPage> {
  try {
    const client = getSearchClient();

    // Step 1: candidate pool of same-industry companies ordered by raw
    // active count. Fetch a wider pool than `limit` so thinning by the
    // filter still leaves enough results to rank. 100 covers typical
    // industries without materially growing the query cost.
    const pool = await client.collections("company").documents().search({
      q: "*",
      query_by: "name",
      filter_by: `industry_id:=${industryId} && active_posting_count:>0 && id:!=${companyId}`,
      sort_by: "active_posting_count:desc",
      per_page: 100,
      include_fields: "id,slug,name,icon",
    });
    const candidates = new Map<string, { slug: string; name: string; icon: string | null }>();
    for (const hit of pool.hits ?? []) {
      const doc = hit.document as Record<string, unknown>;
      const id = doc.id as string;
      if (!id) continue;
      candidates.set(id, {
        slug: (doc.slug as string) ?? "",
        name: (doc.name as string) ?? "",
        icon: (doc.icon as string) ?? null,
      });
    }
    if (candidates.size === 0) return { companies: [], hasMore: false };

    // Step 2: facet on job_posting with user filters scoped to the pool.
    const filterStr = buildFilterString({
      locationIds: filters.locationIds.length ? filters.locationIds : undefined,
      occupationIds: filters.occupationIds.length ? filters.occupationIds : undefined,
      seniorityIds: filters.seniorityIds.length ? filters.seniorityIds : undefined,
      technologyIds: filters.technologyIds.length ? filters.technologyIds : undefined,
      employmentTypes: filters.employmentTypes.length ? filters.employmentTypes : undefined,
      salaryMinEur: filters.salaryMinEur,
      salaryMaxEur: filters.salaryMaxEur,
      experienceMin: filters.experienceMin,
      experienceMax: filters.experienceMax,
    });
    const candidateIds = [...candidates.keys()];
    const activeFilter = `${POSTING_BASE_FILTER} && company_id:[${candidateIds.join(",")}]${filterStr ? ` && ${filterStr}` : ""}`;
    const q = filters.keywords.length ? filters.keywords.join(" ") : "*";

    const facet = await client.collections("job_posting").documents().search({
      q,
      query_by: "title",
      filter_by: activeFilter,
      facet_by: "company_id",
      max_facet_values: candidateIds.length,
      per_page: 0,
    });
    const counts = new Map<string, number>();
    for (const entry of facet.facet_counts?.[0]?.counts ?? []) {
      counts.set(entry.value, entry.count);
    }

    // Step 3: rank candidates by filtered count, drop zeros, slice top-N.
    const companies: SimilarCompany[] = [...candidates.entries()]
      .map(([id, meta]) => ({
        id,
        slug: meta.slug,
        name: meta.name,
        icon: meta.icon,
        activeJobCount: counts.get(id) ?? 0,
      }))
      .filter((c) => c.activeJobCount > 0)
      .sort((a, b) => b.activeJobCount - a.activeJobCount)
      .slice(0, limit);

    return { companies, hasMore: false };
  } catch (err) {
    logExternalError("error", { service: "typesense", operation: "similar_companies_filtered" }, err);
    return { companies: [], hasMore: false };
  }
}

function _toSimilarCompany(doc: Record<string, unknown>): SimilarCompany {
  // Coerce numeric fields defensively — a missing/string count would
  // propagate into the ICU plural as `NaN` and render "NaN open positions".
  const raw = doc.active_posting_count;
  const count = typeof raw === "number" ? raw : Number(raw);
  return {
    id: (doc.id as string) ?? "",
    slug: (doc.slug as string) ?? "",
    name: (doc.name as string) ?? "",
    icon: (doc.icon as string) ?? null,
    activeJobCount: Number.isFinite(count) ? count : 0,
  };
}

// ── Company postings with counts ────────────────────────────────────

/**
 * Normalised parameter shape for `_fetchCompanyPostingsCached`. Sorted
 * arrays + primitive-only fields make the implicit `'use cache'`
 * argument-hash key match the legacy concat-key behaviour and avoid
 * splitting hits across input-order permutations.
 */
interface NormalizedCompanyPostingsParams {
  companyId: string;
  keywords: string[];
  locationIds: number[];
  occupationIds: number[];
  seniorityIds: number[];
  technologyIds: number[];
  employmentTypes: string[];
  workMode: WorkMode[];
  languages: string[];
  salaryMinEur: number | null;
  salaryMaxEur: number | null;
  experienceMin: number | null;
  experienceMax: number | null;
  locale: string;
  offset: number;
  limit: number;
}

export interface CompanyPostingsParams {
  companyId: string;
  keywords: string[];
  locationIds?: number[];
  occupationIds?: number[];
  seniorityIds?: number[];
  technologyIds?: number[];
  employmentTypes?: string[];
  workMode?: WorkMode[];
  salaryMinEur?: number;
  salaryMaxEur?: number;
  experienceMin?: number;
  experienceMax?: number;
  languages: string[];
  locale: string;
  offset: number;
  limit: number;
}

/**
 * Session-free implementation shared by :func:`getCompanyPostings`
 * (which reads ``getSessionUserId`` to enforce the anonymous truncation
 * cap) and :func:`getCompanyPostingsAnonymous` (which skips the session
 * read for ISR-eligible call sites). Reading ``headers()`` /
 * ``cookies()`` inside an ISR page render path silently downgrades the
 * route to dynamic — see #3203 + #2640 + #2243.
 */
async function _getCompanyPostingsImpl(
  params: CompanyPostingsParams,
  userId: string | null,
): Promise<{ postings: SearchResultPosting[]; activeCount: number; yearCount: number; truncated?: boolean }> {
  if (!userId && params.offset >= ANON_MAX_POSTINGS) {
    return { postings: [], activeCount: 0, yearCount: 0, truncated: true };
  }

  // Pre-sort all array fields + collapse `undefined` to `null` so the
  // implicit `'use cache'` key derivation hashes the same value for
  // equivalent caller intents. The sorted+nulled shape mirrors the
  // legacy concat-key string built by the old `cached()` call.
  //
  // String arrays use `canonicalStringCompare` — locale-independent so
  // a `de-DE` viewer and an `en-US` viewer with the same filter set
  // share a cache slot (raw `.sort()` uses UTF-16 order and splits
  // them, see #3221).
  const normalized: NormalizedCompanyPostingsParams = {
    companyId: params.companyId,
    keywords: [...params.keywords].sort(canonicalStringCompare),
    locationIds: [...(params.locationIds ?? [])].sort((a, b) => a - b),
    occupationIds: [...(params.occupationIds ?? [])].sort((a, b) => a - b),
    seniorityIds: [...(params.seniorityIds ?? [])].sort((a, b) => a - b),
    technologyIds: [...(params.technologyIds ?? [])].sort((a, b) => a - b),
    employmentTypes: [...(params.employmentTypes ?? [])].sort(canonicalStringCompare),
    workMode: [...(params.workMode ?? [])].sort(canonicalStringCompare),
    languages: [...params.languages].sort(canonicalStringCompare),
    salaryMinEur: params.salaryMinEur ?? null,
    salaryMaxEur: params.salaryMaxEur ?? null,
    experienceMin: params.experienceMin ?? null,
    experienceMax: params.experienceMax ?? null,
    locale: params.locale,
    offset: params.offset,
    limit: params.limit,
  };

  const result = await _fetchCompanyPostingsCached(normalized);

  if (!userId && params.offset + result.postings.length >= ANON_MAX_POSTINGS) {
    return { ...result, truncated: true };
  }

  return result;
}

export async function getCompanyPostings(
  params: CompanyPostingsParams,
): Promise<{ postings: SearchResultPosting[]; activeCount: number; yearCount: number; truncated?: boolean }> {
  const userId = await getSessionUserId();
  return _getCompanyPostingsImpl(params, userId);
}

/**
 * Anonymous variant of :func:`getCompanyPostings` for ISR-eligible
 * server-render paths (#3203, mirrors :func:`listTopCompaniesAnonymous`
 * from #2640). Does NOT read the session — calling
 * ``getSessionUserId`` would await ``headers()`` and silently downgrade
 * the route to dynamic rendering. Always treats the caller as
 * anonymous, so the truncation cap is enforced at ``ANON_MAX_POSTINGS``.
 * Safe for use from a page render with ``revalidate = N``.
 */
export async function getCompanyPostingsAnonymous(
  params: CompanyPostingsParams,
): Promise<{ postings: SearchResultPosting[]; activeCount: number; yearCount: number; truncated?: boolean }> {
  return _getCompanyPostingsImpl(params, null);
}

/**
 * Cached inner for {@link getCompanyPostings}. cacheLife({ revalidate:
 * 300 }) preserves the legacy 5-minute TTL — postings churn faster than
 * top-locations / similar-companies, and the frequent revalidation is
 * the dominant cost driver this slot is solving for. Migrated from
 * Redis-backed `cached()` in #2884 (bucket 4).
 *
 * `firstSeenAt` is normalised to an ISO string before return — Date is
 * not part of the project's `'use cache'` serializable subset. The
 * caller-side type already accepts `Date | string`. (Same convention as
 * the bucket-5 PR's `_fetchPostingDetail`.)
 */
async function _fetchCompanyPostingsCached(
  params: NormalizedCompanyPostingsParams,
): Promise<{ postings: SearchResultPosting[]; activeCount: number; yearCount: number }> {
  "use cache";
  cacheLife({ revalidate: CACHE_TTL_MEDIUM });
  cacheTag(companyByIdCacheTag(params.companyId));

  // Re-shape to the SearchProvider param contract. Drop the cache-only
  // null-vs-undefined distinction back to undefined for the optional
  // numeric fields.
  const result = await getSearchProvider().loadPostingsWithCounts({
    companyId: params.companyId,
    keywords: params.keywords,
    locationIds: params.locationIds,
    occupationIds: params.occupationIds,
    seniorityIds: params.seniorityIds,
    technologyIds: params.technologyIds,
    employmentTypes: params.employmentTypes,
    workMode: params.workMode.length > 0 ? params.workMode : undefined,
    languages: params.languages,
    salaryMinEur: params.salaryMinEur ?? undefined,
    salaryMaxEur: params.salaryMaxEur ?? undefined,
    experienceMin: params.experienceMin ?? undefined,
    experienceMax: params.experienceMax ?? undefined,
    locale: params.locale,
    offset: params.offset,
    limit: params.limit,
  });

  return {
    ...result,
    postings: result.postings.map((p) => ({
      ...p,
      firstSeenAt:
        p.firstSeenAt instanceof Date
          ? p.firstSeenAt.toISOString()
          : p.firstSeenAt,
    })),
  };
}

// ── Top locations for a company ─────────────────────────────────────

export interface CompanyLocation {
  id: number;
  slug: string;
  name: string;
  type: string;
  count: number;
}

// Per-region in-memory `'use cache'` (cacheLife('hours')). Migrated from
// Redis-backed `cached(..., { ttl: 600 })` in #2884 (bucket 4). Ticked
// up from 600s to the 'hours' built-in profile because top-locations
// derive from posting churn, which the page-level `cacheTag` invalidator
// can drop on demand if the operator wants fresher data sooner. Build
// ID is part of the key, so each deploy re-fetches anyway.
//
// Cache key is `(companyId, locale)` — both load-bearing for the
// ranked list (locale picks the localised display name).
export async function getCompanyTopLocations(
  companyId: string,
  locale: string,
): Promise<{ locations: CompanyLocation[]; totalCount: number }> {
  try {
    return await _getCompanyTopLocationsCached(companyId, locale);
  } catch (err) {
    if (!isTypesenseUnavailableError(err)) throw err;
    logExternalError(
      "error",
      { service: "typesense", operation: "company_top_locations" },
      err,
    );
    return { locations: [], totalCount: 0 };
  }
}

async function _getCompanyTopLocationsCached(
  companyId: string,
  locale: string,
): Promise<{ locations: CompanyLocation[]; totalCount: number }> {
  "use cache";
  cacheLife("hours");
  cacheTag(companyByIdCacheTag(companyId));
  return _fetchTopLocations(companyId, locale);
}

async function _fetchTopLocations(
  companyId: string,
  locale: string,
): Promise<{ locations: CompanyLocation[]; totalCount: number }> {
  const facet = await _fetchCompanyLocationFacet(companyId, "location_direct_ids", 15);
  const documents = await fetchLocationDocumentsByIds(facet.counts.map((entry) => entry.id));
  const byId = new Map(documents.map((document) => [document.location_id, document]));
  return {
    locations: facet.counts.flatMap(({ id, count }) => {
      const document = byId.get(id);
      if (!document) return [];
      return [{
        id,
        slug: document.slug,
        name: _companyLocationName(document, locale),
        type: document.type,
        count,
      }];
    }),
    totalCount: facet.totalValues,
  };
}

// ── All locations grouped by country / region ─────────────────────

export interface CompanyLocationWithAliases extends CompanyLocation {
  aliases: string[];
}

export interface CompanyRegionGroup {
  regionId: number;
  regionSlug: string;
  regionName: string;
  regionCount: number;
  regionAliases: string[];
  locations: CompanyLocationWithAliases[];
}

export interface GroupedCompanyLocations {
  countryId: number;
  countrySlug: string;
  countryName: string;
  countryCount: number;
  countryAliases: string[];
  regions: CompanyRegionGroup[];
}

/**
 * Macro-region cluster on the company-page location modal. Mirrors the
 * shape used by the global modal (`GlobalMacroRegion` in
 * `apps/web/src/lib/actions/locations.ts`) — kept structurally identical
 * so the rendering code in `LocationModal` can mirror what
 * `LocationSearchModal` does without re-keying. Per #2940 the cluster is
 * gated behind `≥2 macro-member countries with postings for that company`,
 * which is computed at fetch time and surfaced as `eligibleMacros[]`.
 */
export interface CompanyMacroRegion {
  id: number;
  slug: string;
  name: string;
  abbreviation: string;
  count: number;
  memberCountryNames: string[];
  /**
   * Member country IDs — used by the hierarchical-disable hook so
   * selecting a macro in {@link LocationModal} disables its member
   * countries (and transitively their regions/cities) without a second
   * round-trip. Mirrors {@link GlobalMacroRegion.memberCountryIds}. See
   * #2978.
   */
  memberCountryIds: number[];
}

/**
 * Wrapper shape returned by {@link getCompanyLocationsGroupedWithMacros}.
 * The existing array-shape function {@link getCompanyLocationsGrouped} is
 * left untouched for callers that don't need the Regions cluster (search
 * input typeahead, server-side filter resolution, etc.).
 */
export interface CompanyLocationsResponse {
  countries: GroupedCompanyLocations[];
  macros: CompanyMacroRegion[];
}

// Per-region in-memory `'use cache'` (cacheLife('hours')). Migrated from
// Redis-backed `cached(..., { ttl: 600 })` in #2884 (bucket 4). Same
// rationale as `getCompanyTopLocations` above — derives from posting
// churn, page-level invalidator can drop on demand. Build ID is part
// of the key, so each deploy re-fetches.
//
// Cache key is `(companyId, locale)`. The result is plain JSON-shaped
// data (no Maps/Dates) — `_fetchLocationsGrouped` returns nested arrays
// of primitives.
export async function getCompanyLocationsGrouped(
  companyId: string,
  locale: string,
): Promise<GroupedCompanyLocations[]> {
  try {
    return await _getCompanyLocationsGroupedCached(companyId, locale);
  } catch (err) {
    if (!isTypesenseUnavailableError(err)) throw err;
    logExternalError(
      "error",
      { service: "typesense", operation: "company_locations_grouped" },
      err,
    );
    return [];
  }
}

async function _getCompanyLocationsGroupedCached(
  companyId: string,
  locale: string,
): Promise<GroupedCompanyLocations[]> {
  "use cache";
  cacheLife("hours");
  cacheTag(companyByIdCacheTag(companyId));
  return _fetchLocationsGrouped(companyId, locale);
}

/**
 * Same as {@link getCompanyLocationsGrouped} plus the macro-region cluster
 * (e.g. EU/EMEA/DACH) — but only the macros where THIS company has
 * postings spanning ≥2 of the macro's member countries (#2940 step 5
 * gate). Companies that hire only from one country never see the Regions
 * cluster (it would be noise).
 */
export async function getCompanyLocationsGroupedWithMacros(
  companyId: string,
  locale: string,
): Promise<CompanyLocationsResponse> {
  try {
    return await _getCompanyLocationsGroupedWithMacrosCached(companyId, locale);
  } catch (err) {
    if (!isTypesenseUnavailableError(err)) throw err;
    logExternalError(
      "error",
      { service: "typesense", operation: "company_locations_grouped_with_macros" },
      err,
    );
    return { countries: [], macros: [] };
  }
}

async function _getCompanyLocationsGroupedWithMacrosCached(
  companyId: string,
  locale: string,
): Promise<CompanyLocationsResponse> {
  "use cache";
  cacheLife("hours");
  cacheTag(companyByIdCacheTag(companyId));
  const [countries, macros] = await Promise.all([
    _fetchLocationsGrouped(companyId, locale),
    _fetchCompanyMacroCluster(companyId, locale),
  ]);
  return { countries, macros };
}

/**
 * Fetch macro regions that have ≥2 member countries with postings for
 * `companyId`. Returns the macro `count` (total active postings whose
 * `location_ids` ancestor includes this macro) and the localized member
 * country names. The ≥2-member gate is evaluated from the macro document's
 * member-country IDs and the company facet counts, not the posting count.
 * A company with 50 postings in only Germany doesn't see DACH; a company
 * with one posting in Germany and one in Austria does.
 */
async function _fetchCompanyMacroCluster(
  companyId: string,
  locale: string,
): Promise<CompanyMacroRegion[]> {
  // Hardcoded canonical display labels — kept in sync with
  // `MACRO_DISPLAY_NAMES` in `apps/web/src/lib/actions/locations.ts`. When
  // #2939 lands proper aliases on the location collection, both can move
  // to a shared source.
  const MACRO_DISPLAY_NAMES: Record<string, string> = {
    eu: "European Union",
    emea: "Europe, Middle East & Africa",
    dach: "DACH (Germany, Austria, Switzerland)",
    apac: "Asia-Pacific",
    americas: "Americas",
    latam: "Latin America",
    nordics: "Nordics",
    mena: "Middle East & North Africa",
    worldwide: "Worldwide",
  };

  const [facet, macros] = await Promise.all([
    _fetchCompanyLocationFacet(companyId, "location_ids", 5000),
    fetchLocationMacroDocuments(),
  ]);
  const counts = new Map(facet.counts.map((entry) => [entry.id, entry.count]));
  const eligible = macros.filter((macro) =>
    (macro.member_country_ids ?? []).filter((id) => (counts.get(id) ?? 0) > 0).length >= 2,
  );
  const memberIds = eligible.flatMap((macro) => macro.member_country_ids ?? []);
  const members = await fetchLocationDocumentsByIds(memberIds);
  const membersById = new Map(members.map((member) => [member.location_id, member]));
  const compareNames = makeDisplayStringCompare(locale);

  return eligible.map((macro) => {
    const abbreviation = _companyLocationName(macro, locale);
    const slugKey = macro.slug.toLowerCase()
      || abbreviation.toLowerCase().replace(/\s+/g, "-");
    const canonical = MACRO_DISPLAY_NAMES[slugKey];
    const memberRows = (macro.member_country_ids ?? [])
      .flatMap((id) => {
        const member = membersById.get(id);
        return member ? [{ id, name: _companyLocationName(member, locale) }] : [];
      })
      .sort((a, b) => compareNames(a.name, b.name));
    return {
      id: macro.location_id,
      slug: macro.slug || slugKey,
      name: canonical ?? abbreviation,
      abbreviation,
      count: counts.get(macro.location_id) ?? 0,
      memberCountryNames: memberRows.map((member) => member.name),
      memberCountryIds: memberRows.map((member) => member.id),
    };
  }).sort((a, b) => b.count - a.count);
}

async function _fetchLocationsGrouped(
  companyId: string,
  locale: string,
): Promise<GroupedCompanyLocations[]> {
  const facet = await _fetchCompanyLocationFacet(companyId, "location_direct_ids", 5000);
  const documents = await fetchLocationDocumentsWithAncestors(
    facet.counts.map((entry) => entry.id),
  );
  const byId = new Map(documents.map((document) => [document.location_id, document]));

  // Build country → region → city hierarchy
  const countries = new Map<number, GroupedCompanyLocations>();
  // Track direct counts for country/region entries
  const directCountryCount = new Map<number, number>();
  const directRegionCount = new Map<number, number>();

  for (const { id, count } of facet.counts) {
    const location = byId.get(id);
    if (!location || location.type === "macro") continue;
    const parent = location.parent_id == null ? undefined : byId.get(location.parent_id);
    const grandparent = parent?.parent_id == null ? undefined : byId.get(parent.parent_id);
    const region = location.type === "region"
      ? location
      : location.type === "city" && parent?.type === "region"
        ? parent
        : undefined;
    const countryMeta = location.type === "country"
      ? location
      : parent?.type === "country"
        ? parent
        : grandparent?.type === "country"
          ? grandparent
          : undefined;
    const cid = countryMeta?.location_id ?? 0;
    let country = countries.get(cid);
    if (!country) {
      country = {
        countryId: cid,
        countrySlug: countryMeta?.slug ?? "",
        countryName: countryMeta ? _companyLocationName(countryMeta, locale) : "Other",
        countryCount: 0,
        countryAliases: countryMeta ? _companyLocationAliases(countryMeta, locale) : [],
        regions: [],
      };
      countries.set(cid, country);
    }

    if (location.type === "country") {
      directCountryCount.set(cid, count);
      continue;
    }
    if (location.type === "region") {
      directRegionCount.set(location.location_id, count);
      continue;
    }

    // City: find or create region group
    const rid = region?.location_id ?? 0;
    let regionGroup = country.regions.find((candidate) => candidate.regionId === rid);
    if (!regionGroup) {
      regionGroup = {
        regionId: rid,
        regionSlug: region?.slug ?? "",
        regionName: region ? _companyLocationName(region, locale) : "",
        regionCount: 0,
        regionAliases: region ? _companyLocationAliases(region, locale) : [],
        locations: [],
      };
      country.regions.push(regionGroup);
    }

    regionGroup.locations.push({
      id: location.location_id,
      slug: location.slug,
      name: _companyLocationName(location, locale),
      type: location.type,
      count,
      aliases: _companyLocationAliases(location, locale),
    });
  }

  // Aggregate counts bottom-up
  for (const country of countries.values()) {
    let countryTotal = directCountryCount.get(country.countryId) ?? 0;
    for (const region of country.regions) {
      const cityTotal = region.locations.reduce((sum, l) => sum + l.count, 0);
      region.regionCount = cityTotal + (directRegionCount.get(region.regionId) ?? 0);
      countryTotal += region.regionCount;
      region.locations.sort((a, b) => b.count - a.count);
    }
    country.countryCount = countryTotal;
    // Sort regions by count desc
    country.regions.sort((a, b) => b.regionCount - a.regionCount);
  }

  const compareNames = makeDisplayStringCompare(locale);
  return [...countries.values()]
    .filter((group) => group.regions.some((region) => region.locations.length > 0))
    .sort((a, b) => compareNames(a.countryName, b.countryName));
}

async function _fetchCompanyLocationFacet(
  companyId: string,
  field: "location_ids" | "location_direct_ids",
  maxFacetValues: number,
): Promise<{
  counts: Array<{ id: number; count: number }>;
  totalValues: number;
}> {
  const result = await getSearchClient().collections("job_posting").documents().search({
    q: "*",
    query_by: "title",
    filter_by: `${POSTING_BASE_FILTER} && company_id:=${companyId}`,
    facet_by: field,
    facet_strategy: "exhaustive",
    max_facet_values: maxFacetValues,
    per_page: 0,
  });
  const facet = result.facet_counts?.find((entry) => entry.field_name === field);
  return {
    counts: (facet?.counts ?? []).map((entry) => ({
      id: Number(entry.value),
      count: entry.count,
    })),
    totalValues: facet?.stats?.total_values ?? facet?.counts.length ?? 0,
  };
}

function _companyLocationName(
  document: TypesenseLocationDocument,
  locale: string,
): string {
  const localized = document[`name_${locale}` as "name_de" | "name_fr" | "name_it"];
  return localized ?? document.name_en ?? document.slug;
}

function _companyLocationAliases(
  document: TypesenseLocationDocument,
  locale: string,
): string[] {
  return [...new Set([
    document.name_en,
    _companyLocationName(document, locale),
    ...(document.aliases ?? []),
  ].filter(Boolean).map((name) => name.toLowerCase()))];
}

// ── Helpers ─────────────────────────────────────────────────────────
