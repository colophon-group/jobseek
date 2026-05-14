"use server";

import { sql } from "drizzle-orm";
import { cacheLife, cacheTag } from "next/cache";
import { db } from "@/db";
import { CACHE_TTL_MEDIUM, CACHE_TTL_LONG } from "@/lib/cache-ttl";
import { withDbRetry } from "@/lib/db-retry";
import { getSearchProvider } from "@/lib/search";
import type { SearchResultPosting, WorkMode } from "@/lib/search";
import {
  companyByIdCacheTag,
  companyCacheTag,
  companyCsvDataCacheTag,
  typeaheadCompaniesCacheTag,
} from "@/lib/cache-tags";
import { getSessionUserId } from "@/lib/sessionCache";
import { expandLocationIds } from "@/lib/actions/locations";
import { expandOccupationIds } from "@/lib/actions/taxonomy";
import { ANON_MAX_COMPANIES, ANON_MAX_POSTINGS } from "@/lib/search/constants";
import { getSearchClient } from "@/lib/search/typesense-client";
import { buildFilterString, POSTING_BASE_FILTER } from "@/lib/search/typesense-filters";
import { localesOrNoneClause } from "@/lib/search/pg-filters";
import { parseSearchFilters } from "@/lib/actions/search-input";
import { getCurrencyRates } from "@/lib/actions/search";
import { firstOf, idsOrUndefined, parseRangeParam } from "@/lib/search/params";
import { convertToEur } from "@/lib/salary";
import { canonicalStringCompare } from "@/lib/sort";

// ── Company suggestions (search bar autocomplete) ───────────────────

export interface CompanySuggestion {
  id: string;
  name: string;
  slug: string;
  icon: string | null;
}

export async function suggestCompanies(params: {
  query: string;
}): Promise<CompanySuggestion[]> {
  const q = params.query.trim().toLowerCase();
  if (q.length < 2) return [];

  // Per-region in-memory `'use cache'` (revalidate 3600s). Migrated from
  // Redis-backed `cached()` in #2884 (typeaheads slice). The previous TTL
  // was 600s; bumped to 3600s to match the other 4 typeahead sites
  // (issue prescription). The inner fetcher returns `CompanySuggestion[]`
  // (plain serializable objects, never null), so no throw-and-catch
  // wrapper is needed here.
  return _queryCompanySuggestionsCached(q);
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

  const rows = await withDbRetry(
    () =>
      db.execute<{
        [key: string]: unknown;
        id: string;
        name: string;
        slug: string;
        icon: string | null;
        match_rank: number;
      }>(sql`
        WITH prefix_matches AS (
          SELECT c.id, c.name, c.slug, c.icon, 1 AS match_rank
          FROM company c
          WHERE lower(c.name) LIKE ${q + "%"}
            AND EXISTS (SELECT 1 FROM job_posting jp WHERE jp.company_id = c.id AND jp.is_active = true)
          LIMIT 5
        ),
        fuzzy_matches AS (
          SELECT c.id, c.name, c.slug, c.icon, 2 AS match_rank
          FROM company c
          WHERE length(${q}) >= 3
            AND similarity(lower(c.name), ${q}) > 0.3
            AND c.id NOT IN (SELECT id FROM prefix_matches)
            AND EXISTS (SELECT 1 FROM job_posting jp WHERE jp.company_id = c.id AND jp.is_active = true)
          ORDER BY similarity(lower(c.name), ${q}) DESC
          LIMIT 5
        )
        SELECT * FROM prefix_matches
        UNION ALL
        SELECT * FROM fuzzy_matches
        LIMIT 5
      `),
    { label: "companySuggestions" },
  );

  type Row = { id: string; name: string; slug: string; icon: string | null; match_rank: number };
  return (rows as unknown as Row[]).map((r) => ({
    id: r.id,
    name: r.name,
    slug: r.slug,
    icon: r.icon,
  }));
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
    console.error("[searchCompaniesForWatchlist] Typesense failed, falling back to Postgres", err);
    return _searchCompaniesForWatchlistPostgres(params);
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
      const companyFilterParts: string[] = ["active_posting_count:>0"];
      if (params.industryId != null) companyFilterParts.push(`industry_id:=${params.industryId}`);

      const companyResult = await client.collections("company").documents().search({
        q: hasQuery ? q! : "*",
        query_by: "name",
        filter_by: companyFilterParts.join(" && "),
        per_page: 250, // generous limit to intersect with facets
        prefix: true,
        num_typos: 1,
      });

      const companyNameSet = new Set(
        (companyResult.hits ?? []).map((h) => (h.document as Record<string, unknown>).id as string),
      );

      // Intersect: only companies that appear in both name search and facet results
      filteredCompanyIds = facetCounts
        .filter((fc) => companyNameSet.has(fc.value))
        .map((fc) => fc.value);
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

  // NO WATCHLIST FILTERS: query company collection directly by active_posting_count.
  // Much simpler — every active company is relevant.

  if (wantStarredBoost) {
    // Two queries: starred first, then remaining
    const companyFilterParts: string[] = ["active_posting_count:>0"];
    if (params.industryId != null) companyFilterParts.push(`industry_id:=${params.industryId}`);
    const baseFilter = companyFilterParts.join(" && ");

    const starredFilter = `${baseFilter} && id:[${starredIds!.join(",")}]`;
    const remainingFilter = `${baseFilter} && id:!=[${starredIds!.join(",")}]`;

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
  const companyFilterParts: string[] = ["active_posting_count:>0"];
  if (params.industryId != null) companyFilterParts.push(`industry_id:=${params.industryId}`);

  const result = await client.collections("company").documents().search({
    q: hasQuery ? q! : "*",
    query_by: "name",
    filter_by: companyFilterParts.join(" && "),
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

/** Postgres fallback for searchCompaniesForWatchlist (graceful degradation). */
async function _searchCompaniesForWatchlistPostgres(params: {
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
  const q = params.query?.trim().toLowerCase();
  const hasQuery = q && q.length >= 2;

  const companyClauses = [sql`true`];
  if (hasQuery) {
    companyClauses.push(
      sql`(lower(c.name) LIKE ${q + "%"} OR (length(${q}) >= 3 AND similarity(lower(c.name), ${q}) > 0.3))`,
    );
  }
  if (params.industryId != null) {
    companyClauses.push(sql`c.industry = ${params.industryId}`);
  }
  const companyWhere = sql.join(companyClauses, sql` AND `);

  const [expandedLocIds, expandedOccIds] = await Promise.all([
    params.locationIds?.length
      ? Promise.all(params.locationIds.map(expandLocationIds)).then((a) => [...new Set(a.flat())])
      : undefined,
    params.occupationIds?.length
      ? Promise.all(params.occupationIds.map(expandOccupationIds)).then((a) => [...new Set(a.flat())])
      : undefined,
  ]);

  const jobClauses = [sql`jp.is_active = true`];
  if (params.keywords && params.keywords.length > 0) {
    const kwParts = params.keywords.map((k) => {
      const escaped = k.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      const s = /^\w/.test(k) ? "\\m" : "";
      const e = /\w$/.test(k) ? "\\M" : "";
      return sql`jp.titles[1] ~* ${s + escaped + e}`;
    });
    jobClauses.push(sql`(${sql.join(kwParts, sql` OR `)})`);
  }
  if (expandedLocIds?.length) {
    const a = `{${expandedLocIds.join(",")}}`;
    jobClauses.push(sql`jp.location_ids && ${a}::integer[]`);
  }
  if (expandedOccIds?.length) {
    const a = `{${expandedOccIds.join(",")}}`;
    jobClauses.push(sql`jp.occupation_id = ANY(${a}::integer[])`);
  }
  if (params.seniorityIds?.length) {
    const a = `{${params.seniorityIds.join(",")}}`;
    jobClauses.push(sql`jp.seniority_id = ANY(${a}::integer[])`);
  }
  if (params.technologyIds?.length) {
    const a = `{${params.technologyIds.join(",")}}`;
    jobClauses.push(sql`jp.technology_ids && ${a}::integer[]`);
  }
  if (params.salaryMin != null && params.salaryMax != null) {
    jobClauses.push(sql`jp.salary_eur BETWEEN ${params.salaryMin} AND ${params.salaryMax}`);
  } else if (params.salaryMin != null) {
    jobClauses.push(sql`jp.salary_eur >= ${params.salaryMin}`);
  } else if (params.salaryMax != null) {
    jobClauses.push(sql`jp.salary_eur <= ${params.salaryMax}`);
  }
  if (params.experienceMin != null || params.experienceMax != null) {
    if (params.experienceMin != null && params.experienceMax != null) {
      jobClauses.push(sql`(jp.experience_min IS NULL OR (jp.experience_min >= ${params.experienceMin} AND jp.experience_min <= ${params.experienceMax}))`);
    } else if (params.experienceMin != null) {
      jobClauses.push(sql`(jp.experience_min IS NULL OR jp.experience_min >= ${params.experienceMin})`);
    } else {
      jobClauses.push(sql`(jp.experience_min IS NULL OR jp.experience_min <= ${params.experienceMax!})`);
    }
  }
  const localesClause = localesOrNoneClause(params.languages);
  if (localesClause) jobClauses.push(localesClause);
  const jobWhere = sql.join(jobClauses, sql` AND `);

  const [totalRow] = await withDbRetry(
    () =>
      db.execute<{ [key: string]: unknown; cnt: number }>(sql`
        SELECT count(*)::int AS cnt FROM company c WHERE ${companyWhere}
      `),
    { label: "searchCompaniesForWatchlistPostgres.count" },
  );
  const total = (totalRow as unknown as { cnt: number })?.cnt ?? 0;
  if (total === 0) return { companies: [], total: 0 };

  const starredIds = params.starredCompanyIds;
  const boostStarred = !hasQuery && starredIds && starredIds.length > 0;
  const starredArray = boostStarred ? `{${starredIds.join(",")}}` : null;

  const orderClause = boostStarred
    ? sql`CASE WHEN c.id = ANY(${starredArray}::uuid[]) THEN 0 ELSE 1 END, active_matches DESC, c.name`
    : sql`active_matches DESC, c.name`;

  const rows = await withDbRetry(
    () =>
      db.execute<{
        [key: string]: unknown;
        id: string;
        name: string;
        slug: string;
        icon: string | null;
        description: string | null;
        active_matches: number;
        year_matches: number;
      }>(sql`
        SELECT c.id, c.name, c.slug, c.icon,
               COALESCE(cd.description, c.description) AS description,
               (SELECT count(*)::int FROM job_posting jp
                WHERE jp.company_id = c.id AND ${jobWhere}) AS active_matches,
               (SELECT count(*)::int FROM job_posting jp
                WHERE jp.company_id = c.id
                  AND jp.first_seen_at >= now() - interval '1 year'
                  AND ${jobWhere}) AS year_matches
        FROM company c
        LEFT JOIN company_description cd ON cd.company_id = c.id AND cd.locale = ${params.locale}
        WHERE ${companyWhere}
        ORDER BY ${orderClause}
        OFFSET ${params.offset}
        LIMIT ${params.limit}
      `),
    { label: "searchCompaniesForWatchlistPostgres.rows" },
  );

  type Row = {
    id: string; name: string; slug: string; icon: string | null;
    description: string | null; active_matches: number; year_matches: number;
  };
  return {
    companies: (rows as unknown as Row[]).map((r) => ({
      id: r.id,
      name: r.name,
      slug: r.slug,
      icon: r.icon,
      description: r.description,
      activeMatches: r.active_matches,
      yearMatches: r.year_matches,
    })),
    total,
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
}): Promise<IndustrySuggestion[]> {
  const q = params.query?.trim().toLowerCase();
  const hasQuery = q && q.length >= 1;

  const rows = await withDbRetry(
    () =>
      db.execute<{
        [key: string]: unknown;
        id: number;
        name: string;
      }>(sql`
        SELECT i.id,
               COALESCE(
                 (SELECT idn.name FROM industry_name idn
                  WHERE idn.industry_id = i.id AND idn.locale = ${params.locale} AND idn.is_display = true
                  LIMIT 1),
                 i.name
               ) AS name
        FROM industry i
        ${hasQuery ? sql`WHERE lower(i.name) LIKE ${q + "%"} OR EXISTS (
          SELECT 1 FROM industry_name idn
          WHERE idn.industry_id = i.id AND lower(idn.name) LIKE ${q + "%"}
        )` : sql``}
        ORDER BY i.name
      `),
    { label: "suggestIndustries" },
  );

  type Row = { id: number; name: string };
  return (rows as unknown as Row[]).map((r) => ({ id: r.id, name: r.name }));
}

// ── Company detail ──────────────────────────────────────────────────

export interface CompanyDetail {
  id: string;
  name: string;
  slug: string;
  icon: string | null;
  logo: string | null;
  website: string | null;
  description: string | null;
  industryId: number | null;
  industryName: string | null;
  employeeCountRange: number | null;
  foundedYear: number | null;
  activeJobCount: number;
}

// Sentinel to signal "no company found" out of `_fetchCompanyBySlugCached`
// without letting `null` reach the `'use cache'` boundary. Returning null
// would pin the slot for the cacheLife window and lock a brand-new slug
// out of being seen for up to that long. Throwing past the cache boundary
// (caught by `getCompanyBySlug`) keeps the slot empty and lets the next
// request retry. See #2884 footgun.
class CompanyNotFoundError extends Error {
  constructor() {
    super("company-not-found");
    this.name = "CompanyNotFoundError";
  }
}

export async function getCompanyBySlug(
  slug: string,
  locale: string,
): Promise<CompanyDetail | null> {
  // Throw-and-catch around the `'use cache'` inner. The previous
  // `skipIf: d === null` semantics aren't available under `'use cache'` —
  // the inner fetcher throws `CompanyNotFoundError` on null so the cache
  // slot stays empty for not-yet-seen slugs (avoids 600s of poisoned
  // null), and the wrapper returns null to the caller. Migrated from
  // Redis-backed `cached()` in #2884 (bucket 4 footgun).
  try {
    return await _fetchCompanyBySlugCached(slug, locale);
  } catch (err) {
    if (err instanceof CompanyNotFoundError) return null;
    // Any other error is unexpected — re-throw so the caller / Suspense
    // boundary handles it (matches the original `cached()` behaviour
    // where unexpected errors propagated past the cache layer).
    throw err;
  }
}

async function _fetchCompanyBySlugCached(
  slug: string,
  locale: string,
): Promise<CompanyDetail> {
  "use cache";
  cacheLife("hours");
  // Tag the slot so the page route's `revalidateTag(companyCacheTag(slug))`
  // (already used in `app/[lang]/(app)/company/[slug]/page.tsx` and
  // `generateMetadata`) drops THIS slot too — keeping the data layer's
  // cached entry in sync with the page-level cache. See #2884.
  cacheTag(companyCacheTag(slug));
  // Also drop on a CSV-driven sweep — covers rename / industry change.
  // Mirrors the legacy `company-slug:` Redis-prefix sweep at the
  // `/api/internal/invalidate-typeahead` route. See #2715 + #2884.
  cacheTag(companyCsvDataCacheTag());

  const data = await _fetchCompanyBySlug(slug, locale);
  if (data === null) throw new CompanyNotFoundError();
  return data;
}

async function _fetchCompanyBySlug(slug: string, locale: string): Promise<CompanyDetail | null> {
  // Primary path: Typesense. Falls back to Postgres on either error or 0 hits
  // so brand-new companies (whose Typesense upsert lagged the latest sync)
  // still render. Bot traffic to nonexistent slugs pays the Postgres cost
  // (a cheap PK lookup on company.slug); cache layer above prevents
  // poisoning by not storing nulls.
  try {
    const fromTypesense = await _fetchCompanyBySlugFromTypesense(slug, locale);
    if (fromTypesense) return fromTypesense;
  } catch (err) {
    // Typesense unreachable — fall through to Postgres. Log at info so the
    // fallback rate is queryable (e.g. Cloudflare-tunnel blip pushing 100%
    // of company-page traffic to Supabase). Matches the precedent set by
    // `searchCompaniesForWatchlist` / `_fetchSimilarUnfiltered` in this
    // same file. See #3175.
    console.info("[company] Typesense failed, falling back to Postgres", err);
  }
  return _fetchCompanyBySlugFromPostgres(slug, locale);
}

// Canonical company-slug shape: lowercase alphanumeric segments separated
// by single hyphens (mirrors apps/crawler SLUG_RE). The slug reaches here
// from a URL path segment, so a hostile caller could craft a string that
// escapes the Typesense filter clause when raw-interpolated. Reject
// non-conforming slugs up front; null falls through to a regular 404.
const SLUG_SHAPE = /^[a-z0-9]+(-[a-z0-9]+)*$/;

async function _fetchCompanyBySlugFromTypesense(
  slug: string,
  locale: string,
): Promise<CompanyDetail | null> {
  if (!SLUG_SHAPE.test(slug)) return null;
  const client = getSearchClient();
  const result = await client.collections("company").documents().search({
    q: "*",
    filter_by: `slug:=${slug}`,
    per_page: 1,
  });
  const hit = result.hits?.[0]?.document as Record<string, unknown> | undefined;
  if (!hit) return null;

  const localeKey = (loc: string, base: string): string =>
    loc === "en" ? base : `${base}_${loc}`;
  const pickLocalized = (base: string): string | null => {
    const localized = hit[localeKey(locale, base)];
    if (typeof localized === "string" && localized.length > 0) return localized;
    const en = hit[base];
    return typeof en === "string" && en.length > 0 ? en : null;
  };

  return {
    id: String(hit.id),
    name: String(hit.name ?? ""),
    slug: String(hit.slug ?? slug),
    icon: typeof hit.icon === "string" ? hit.icon : null,
    logo: typeof hit.logo === "string" ? hit.logo : null,
    website: typeof hit.website === "string" ? hit.website : null,
    description: pickLocalized("description"),
    industryId: typeof hit.industry_id === "number" ? hit.industry_id : null,
    industryName: pickLocalized("industry_name"),
    employeeCountRange:
      typeof hit.employee_count_range === "number" ? hit.employee_count_range : null,
    foundedYear: typeof hit.founded_year === "number" ? hit.founded_year : null,
    activeJobCount: typeof hit.active_posting_count === "number" ? hit.active_posting_count : 0,
  };
}

async function _fetchCompanyBySlugFromPostgres(
  slug: string,
  locale: string,
): Promise<CompanyDetail | null> {
  // Retry on transient connection-class errors (#2918): the build that
  // killed prerender at 2026-05-09T15:41:49Z hit `read ECONNRESET` from
  // the Supabase pooler on this exact query. The next build 2 min later
  // succeeded → flake, not structural break. `withDbRetry` only retries
  // ECONNRESET / ETIMEDOUT / ECONNREFUSED / EPIPE / "Connection
  // terminated"-class messages; syntax / constraint / business errors
  // propagate immediately so the original signal is preserved.
  const rows = await withDbRetry(
    () =>
      db.execute<{
        [key: string]: unknown;
        id: string;
        name: string;
        slug: string;
        icon: string | null;
        logo: string | null;
        website: string | null;
        description: string | null;
        industry_id: number | null;
        industry_name: string | null;
        employee_count_range: number | null;
        founded_year: number | null;
      }>(sql`
        SELECT c.id, c.name, c.slug, c.icon, c.logo, c.website,
          COALESCE(cd.description, c.description) AS description,
          c.industry AS industry_id,
          COALESCE(ind_name.name, i.name) AS industry_name,
          c.employee_count_range,
          c.founded_year
        FROM company c
        LEFT JOIN industry i ON i.id = c.industry
        LEFT JOIN company_description cd
          ON cd.company_id = c.id AND cd.locale = ${locale}
        LEFT JOIN LATERAL (
          SELECT name FROM industry_name
          WHERE industry_id = c.industry AND locale IN (${locale}, 'en') AND is_display = true
          ORDER BY (locale = ${locale})::int DESC LIMIT 1
        ) ind_name ON c.industry IS NOT NULL
        WHERE c.slug = ${slug}
      `),
    { label: `companyBySlug[${slug}]` },
  );

  type Row = {
    id: string; name: string; slug: string; icon: string | null;
    logo: string | null; website: string | null; description: string | null;
    industry_id: number | null; industry_name: string | null;
    employee_count_range: number | null; founded_year: number | null;
  };
  const row = (rows as unknown as Row[])[0];
  if (!row) return null;

  return {
    id: row.id,
    name: row.name,
    slug: row.slug,
    icon: row.icon,
    logo: row.logo,
    website: row.website,
    description: row.description,
    industryId: row.industry_id,
    industryName: row.industry_name,
    employeeCountRange: row.employee_count_range,
    foundedYear: row.founded_year,
    // Postgres fallback skips the active count (the only Typesense-only fact).
    // Effect on the page: header strip shows "0 open positions" until Typesense
    // recovers; the postings list itself comes from a separate Typesense call
    // (getCompanyPostings) so its rendering is unaffected by this path.
    activeJobCount: 0,
  };
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
  // sync. Mirrors the legacy `company-similar:` Redis-prefix sweep
  // (#2715). See #2884.
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
    console.error("[_fetchSimilarUnfiltered] Typesense failed, returning empty page", err);
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
  // `explore-data.ts` / `company-page-data.ts` (issue #3178).
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
    console.error("[_fetchSimilarFiltered] Typesense failed, returning empty page", err);
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
  "use cache";
  cacheLife("hours");
  cacheTag(companyByIdCacheTag(companyId));
  return _fetchTopLocations(companyId, locale);
}

async function _fetchTopLocations(
  companyId: string,
  locale: string,
): Promise<{ locations: CompanyLocation[]; totalCount: number }> {
  const rows = await withDbRetry(
    () =>
      db.execute<{
        [key: string]: unknown;
        location_id: number;
        loc_slug: string;
        loc_type: string;
        loc_name: string;
        cnt: number;
        total_locations: number;
      }>(sql`
        WITH active_locs AS (
          SELECT unnest(jp.location_ids) AS location_id
          FROM job_posting jp
          WHERE jp.company_id = ${companyId}
            AND jp.is_active = true
            AND jp.location_ids IS NOT NULL
        ),
        grouped AS (
          SELECT
            al.location_id,
            l.slug AS loc_slug,
            l.type::text AS loc_type,
            ln.name AS loc_name,
            COUNT(*)::int AS cnt
          FROM active_locs al
          JOIN location l ON l.id = al.location_id
          JOIN LATERAL (
            SELECT name FROM location_name
            WHERE location_id = al.location_id AND locale IN (${locale}, 'en') AND is_display = true
            ORDER BY (locale = ${locale})::int DESC LIMIT 1
          ) ln ON true
          GROUP BY al.location_id, l.slug, l.type, ln.name
        )
        SELECT *, COUNT(*) OVER ()::int AS total_locations
        FROM grouped
        ORDER BY cnt DESC
        LIMIT 15
      `),
    { label: `companyTopLocations[${companyId}]` },
  );

  type Row = { location_id: number; loc_slug: string; loc_type: string; loc_name: string; cnt: number; total_locations: number };
  const all = rows as unknown as Row[];
  return {
    locations: all.map((r) => ({
      id: r.location_id,
      slug: r.loc_slug,
      name: r.loc_name,
      type: r.loc_type,
      count: r.cnt,
    })),
    totalCount: all[0]?.total_locations ?? 0,
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
 * country names. The ≥2-member gate is enforced via `HAVING` on the
 * member-country count, not the posting count — a company with 50
 * postings in only Germany doesn't see DACH; a company with one posting
 * in Germany and one in Austria does.
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

  const rows = await db.execute<{
    [key: string]: unknown;
    macro_id: number;
    macro_slug: string | null;
    macro_name: string;
    posting_count: number;
    member_country_count: number;
  }>(sql`
    WITH company_postings AS (
      SELECT id, location_ids
      FROM job_posting
      WHERE company_id = ${companyId}
        AND is_active = true
        AND location_ids IS NOT NULL
    ),
    macro_postings AS (
      -- Each macro that appears as an ancestor on any of this company's postings
      SELECT m.id AS macro_id, m.slug AS macro_slug,
             COUNT(DISTINCT cp.id)::int AS posting_count
      FROM company_postings cp
      JOIN location m ON m.id = ANY(cp.location_ids) AND m.type::text = 'macro'
      GROUP BY m.id, m.slug
    ),
    macro_member_hits AS (
      -- For each macro, count distinct member countries that have at least
      -- one posting for this company. Joins via location_macro_member.
      SELECT lmm.macro_id, COUNT(DISTINCT lmm.country_id)::int AS member_country_count
      FROM location_macro_member lmm
      JOIN company_postings cp ON lmm.country_id = ANY(cp.location_ids)
      GROUP BY lmm.macro_id
    )
    SELECT mp.macro_id, mp.macro_slug,
           ln.name AS macro_name,
           mp.posting_count,
           COALESCE(mmh.member_country_count, 0)::int AS member_country_count
    FROM macro_postings mp
    LEFT JOIN macro_member_hits mmh ON mmh.macro_id = mp.macro_id
    JOIN LATERAL (
      SELECT name FROM location_name
      WHERE location_id = mp.macro_id
        AND locale IN (${locale}, 'en')
        AND is_display = true
      ORDER BY (locale = ${locale})::int DESC LIMIT 1
    ) ln ON true
    WHERE COALESCE(mmh.member_country_count, 0) >= 2
    ORDER BY mp.posting_count DESC
  `);

  type Row = {
    macro_id: number;
    macro_slug: string | null;
    macro_name: string;
    posting_count: number;
    member_country_count: number;
  };
  const macroRows = rows as unknown as Row[];
  if (macroRows.length === 0) return [];

  // Fetch member country names + IDs for each macro. The IDs power the
  // hierarchical-disable hook (#2978) and stay aligned with names because
  // they share the same row order.
  const macroIds = macroRows.map((r) => r.macro_id);
  const pgArray = `{${macroIds.join(",")}}`;
  const memberRows = await db.execute<{
    [key: string]: unknown;
    macro_id: number;
    country_id: number;
    country_name: string;
  }>(sql`
    SELECT lmm.macro_id, lmm.country_id, ln.name AS country_name
    FROM location_macro_member lmm
    JOIN LATERAL (
      SELECT name FROM location_name
      WHERE location_id = lmm.country_id
        AND locale IN (${locale}, 'en')
        AND is_display = true
      ORDER BY (locale = ${locale})::int DESC LIMIT 1
    ) ln ON true
    WHERE lmm.macro_id = ANY(${pgArray}::integer[])
    ORDER BY lmm.macro_id, ln.name
  `);
  const memberMap = new Map<number, { countryNames: string[]; countryIds: number[] }>();
  for (const r of memberRows as unknown as { macro_id: number; country_id: number; country_name: string }[]) {
    let entry = memberMap.get(r.macro_id);
    if (!entry) { entry = { countryNames: [], countryIds: [] }; memberMap.set(r.macro_id, entry); }
    entry.countryNames.push(r.country_name);
    entry.countryIds.push(r.country_id);
  }

  return macroRows.map((r) => {
    const slugKey = (r.macro_slug ?? "").toLowerCase()
      || r.macro_name.toLowerCase().replace(/\s+/g, "-");
    const canonical = MACRO_DISPLAY_NAMES[slugKey];
    const members = memberMap.get(r.macro_id);
    return {
      id: r.macro_id,
      slug: r.macro_slug ?? slugKey,
      name: canonical ?? r.macro_name,
      abbreviation: r.macro_name,
      count: r.posting_count,
      memberCountryNames: members?.countryNames ?? [],
      memberCountryIds: members?.countryIds ?? [],
    };
  });
}

async function _fetchLocationsGrouped(
  companyId: string,
  locale: string,
): Promise<GroupedCompanyLocations[]> {
  const rows = await withDbRetry(
    () =>
      db.execute<{
        [key: string]: unknown;
        location_id: number;
        loc_slug: string;
        loc_type: string;
        loc_name: string;
        cnt: number;
        region_id: number | null;
        region_slug: string | null;
        region_name: string | null;
        country_id: number | null;
        country_slug: string | null;
        country_name: string | null;
      }>(sql`
    WITH active_locs AS (
      SELECT unnest(jp.location_ids) AS location_id
      FROM job_posting jp
      WHERE jp.company_id = ${companyId}
        AND jp.is_active = true
        AND jp.location_ids IS NOT NULL
    ),
    loc_counts AS (
      SELECT al.location_id, COUNT(*)::int AS cnt
      FROM active_locs al GROUP BY al.location_id
    ),
    hierarchy AS (
      SELECT lc.location_id, lc.cnt,
        l.type::text AS loc_type, l.slug AS loc_slug,
        CASE
          WHEN l.type = 'region' THEN l.id
          WHEN l.type = 'city' AND p.type = 'region' THEN p.id
          ELSE NULL
        END AS region_id,
        CASE
          WHEN l.type = 'country' THEN l.id
          WHEN p.type = 'country' THEN p.id
          WHEN gp.type = 'country' THEN gp.id
          ELSE NULL
        END AS country_id
      FROM loc_counts lc
      JOIN location l ON l.id = lc.location_id
      LEFT JOIN location p ON p.id = l.parent_id
      LEFT JOIN location gp ON gp.id = p.parent_id
    )
    SELECT
      h.location_id, h.loc_slug, h.loc_type, h.cnt,
      ln.name AS loc_name,
      h.region_id,
      rl.slug AS region_slug,
      rn.name AS region_name,
      h.country_id,
      cl.slug AS country_slug,
      cn.name AS country_name
    FROM hierarchy h
    JOIN LATERAL (
      SELECT name FROM location_name
      WHERE location_id = h.location_id AND locale IN (${locale}, 'en') AND is_display = true
      ORDER BY (locale = ${locale})::int DESC LIMIT 1
    ) ln ON true
    LEFT JOIN location rl ON rl.id = h.region_id
    LEFT JOIN LATERAL (
      SELECT name FROM location_name
      WHERE location_id = h.region_id AND locale IN (${locale}, 'en') AND is_display = true
      ORDER BY (locale = ${locale})::int DESC LIMIT 1
    ) rn ON true
    LEFT JOIN location cl ON cl.id = h.country_id
    LEFT JOIN LATERAL (
      SELECT name FROM location_name
      WHERE location_id = h.country_id AND locale IN (${locale}, 'en') AND is_display = true
      ORDER BY (locale = ${locale})::int DESC LIMIT 1
    ) cn ON true
    ORDER BY cn.name NULLS LAST, rn.name NULLS LAST, h.cnt DESC
  `),
    { label: `companyLocationsGrouped[${companyId}]` },
  );

  type Row = {
    location_id: number; loc_slug: string; loc_type: string; loc_name: string; cnt: number;
    region_id: number | null; region_slug: string | null; region_name: string | null;
    country_id: number | null; country_slug: string | null; country_name: string | null;
  };

  // Collect all location IDs for alias lookup
  const allLocIds = new Set<number>();
  for (const r of rows as unknown as Row[]) {
    allLocIds.add(r.location_id);
    if (r.region_id) allLocIds.add(r.region_id);
    if (r.country_id) allLocIds.add(r.country_id);
  }

  // Fetch name aliases (user locale + en)
  const aliasMap = new Map<number, string[]>();
  if (allLocIds.size > 0) {
    const pgArray = `{${[...allLocIds].join(",")}}`;
    const aliasRows = await withDbRetry(
      () =>
        db.execute<{
          [key: string]: unknown;
          location_id: number;
          name: string;
        }>(sql`
          SELECT location_id, lower(name) AS name
          FROM location_name
          WHERE location_id = ANY(${pgArray}::integer[])
            AND locale IN (${locale}, 'en')
        `),
      { label: `companyLocationsGrouped.aliases[${companyId}]` },
    );
    for (const a of aliasRows as unknown as { location_id: number; name: string }[]) {
      let arr = aliasMap.get(a.location_id);
      if (!arr) { arr = []; aliasMap.set(a.location_id, arr); }
      if (!arr.includes(a.name)) arr.push(a.name);
    }
  }

  // Build country → region → city hierarchy
  const countries = new Map<number, GroupedCompanyLocations>();
  // Track direct counts for country/region entries
  const directCountryCount = new Map<number, number>();
  const directRegionCount = new Map<number, number>();

  for (const r of rows as unknown as Row[]) {
    const cid = r.country_id ?? 0;
    let country = countries.get(cid);
    if (!country) {
      country = {
        countryId: cid,
        countrySlug: r.country_slug ?? "",
        countryName: r.country_name ?? "Other",
        countryCount: 0,
        countryAliases: aliasMap.get(cid) ?? [],
        regions: [],
      };
      countries.set(cid, country);
    }

    if (r.loc_type === "country") {
      directCountryCount.set(cid, r.cnt);
      continue;
    }
    if (r.loc_type === "region") {
      directRegionCount.set(r.location_id, r.cnt);
      continue;
    }

    // City: find or create region group
    const rid = r.region_id ?? 0;
    let region = country.regions.find((rg) => rg.regionId === rid);
    if (!region) {
      region = {
        regionId: rid,
        regionSlug: r.region_slug ?? "",
        regionName: r.region_name ?? "",
        regionCount: 0,
        regionAliases: rid > 0 ? (aliasMap.get(rid) ?? []) : [],
        locations: [],
      };
      country.regions.push(region);
    }

    region.locations.push({
      id: r.location_id,
      slug: r.loc_slug,
      name: r.loc_name,
      type: r.loc_type,
      count: r.cnt,
      aliases: aliasMap.get(r.location_id) ?? [],
    });
  }

  // Aggregate counts bottom-up
  for (const country of countries.values()) {
    let countryTotal = directCountryCount.get(country.countryId) ?? 0;
    for (const region of country.regions) {
      const cityTotal = region.locations.reduce((sum, l) => sum + l.count, 0);
      region.regionCount = cityTotal + (directRegionCount.get(region.regionId) ?? 0);
      countryTotal += region.regionCount;
    }
    country.countryCount = countryTotal;
    // Sort regions by count desc
    country.regions.sort((a, b) => b.regionCount - a.regionCount);
  }

  return [...countries.values()].filter((g) => g.regions.some((r) => r.locations.length > 0));
}

// ── Helpers ─────────────────────────────────────────────────────────

