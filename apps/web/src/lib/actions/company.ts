"use server";

import { sql } from "drizzle-orm";
import { cacheLife } from "next/cache";
import { db } from "@/db";
import { getSearchProvider } from "@/lib/search";
import type { SearchResultPosting } from "@/lib/search";
import { cached } from "@/lib/cache";
import { getSessionUserId } from "@/lib/sessionCache";
import { expandLocationIds } from "@/lib/actions/locations";
import { expandOccupationIds } from "@/lib/actions/taxonomy";
import { ANON_MAX_COMPANIES, ANON_MAX_POSTINGS } from "@/lib/search/constants";
import { getSearchClient } from "@/lib/search/typesense-client";
import { buildFilterString } from "@/lib/search/typesense-filters";
import { localesOrNoneClause } from "@/lib/search/pg-filters";
import { parseSearchFilters } from "@/lib/actions/search-input";
import { firstOf, idsOrUndefined, parseRangeParam } from "@/lib/search/params";

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
  cacheLife({ revalidate: 3600 });

  const rows = await db.execute<{
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
  `);

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
    const activeFilter = `is_active:true${watchlistFilterStr ? " && " + watchlistFilterStr : ""}`;
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

  const [totalRow] = await db.execute<{ [key: string]: unknown; cnt: number }>(sql`
    SELECT count(*)::int AS cnt FROM company c WHERE ${companyWhere}
  `);
  const total = (totalRow as unknown as { cnt: number })?.cnt ?? 0;
  if (total === 0) return { companies: [], total: 0 };

  const starredIds = params.starredCompanyIds;
  const boostStarred = !hasQuery && starredIds && starredIds.length > 0;
  const starredArray = boostStarred ? `{${starredIds.join(",")}}` : null;

  const orderClause = boostStarred
    ? sql`CASE WHEN c.id = ANY(${starredArray}::uuid[]) THEN 0 ELSE 1 END, active_matches DESC, c.name`
    : sql`active_matches DESC, c.name`;

  const rows = await db.execute<{
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
  `);

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

  const rows = await db.execute<{
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
  `);

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

export async function getCompanyBySlug(
  slug: string,
  locale: string,
): Promise<CompanyDetail | null> {
  const key = `company-slug:${slug}:${locale}`;
  // skipIf null avoids cache-poisoning a brand-new slug that Typesense hasn't
  // yet seen — Postgres fallback can fill the gap on the next request.
  return cached(key, () => _fetchCompanyBySlug(slug, locale), {
    ttl: 600,
    skipIf: (d) => d === null,
  });
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
  } catch {
    // Typesense unreachable — fall through to Postgres.
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
  const rows = await db.execute<{
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
  `);

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
    const filterKey = _similarFiltersKey(filters);
    const key = `company-similar:${companyId}:${industryId}:filtered:${limit}:${filterKey}`;
    return cached(
      key,
      () => _fetchSimilarFiltered(companyId, industryId, limit, filters),
      { ttl: 600 },
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

  const key = `company-similar:${companyId}:${industryId}:${offset}:${limit}`;
  const page = await cached(
    key,
    () => _fetchSimilarUnfiltered(companyId, industryId, offset, limit),
    { ttl: 3600 },
  );

  if (wouldHitCap && !userId && offset + page.companies.length >= ANON_MAX_COMPANIES) {
    return { ...page, hasMore: false, truncated: true };
  }
  return page;
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
  const exp = firstOf(searchParams.exp);
  const etype = firstOf(searchParams.etype);

  const parsed = await parseSearchFilters({ q, loc, occ, sen, tech, locale });
  const { min: salaryMinEur, max: salaryMaxEur } = parseRangeParam(sal);
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

function _similarFiltersKey(f: SimilarFilters): string {
  return [
    [...f.keywords].sort().join(","),
    [...f.locationIds].sort().join(","),
    [...f.occupationIds].sort().join(","),
    [...f.seniorityIds].sort().join(","),
    [...f.technologyIds].sort().join(","),
    [...f.employmentTypes].sort().join(","),
    f.salaryMinEur ?? "",
    f.salaryMaxEur ?? "",
    f.experienceMin ?? "",
    f.experienceMax ?? "",
  ].join("|");
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
    const activeFilter = `is_active:true && company_id:[${candidateIds.join(",")}]${filterStr ? ` && ${filterStr}` : ""}`;
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

export async function getCompanyPostings(params: {
  companyId: string;
  keywords: string[];
  locationIds?: number[];
  occupationIds?: number[];
  seniorityIds?: number[];
  technologyIds?: number[];
  employmentTypes?: string[];
  salaryMinEur?: number;
  salaryMaxEur?: number;
  experienceMin?: number;
  experienceMax?: number;
  languages: string[];
  locale: string;
  offset: number;
  limit: number;
}): Promise<{ postings: SearchResultPosting[]; activeCount: number; yearCount: number; truncated?: boolean }> {
  const userId = await getSessionUserId();

  if (!userId && params.offset >= ANON_MAX_POSTINGS) {
    return { postings: [], activeCount: 0, yearCount: 0, truncated: true };
  }

  const sortedKw = [...params.keywords].sort();
  const sortedLoc = [...(params.locationIds ?? [])].sort();
  const sortedOcc = [...(params.occupationIds ?? [])].sort();
  const sortedSen = [...(params.seniorityIds ?? [])].sort();
  const sortedTech = [...(params.technologyIds ?? [])].sort();
  const sortedEtype = [...(params.employmentTypes ?? [])].sort();
  const sortedLangs = [...params.languages].sort();
  const key = `company-postings:${params.companyId}:${sortedKw.join(",")}:${sortedLoc.join(",")}:${sortedOcc.join(",")}:${sortedSen.join(",")}:${sortedTech.join(",")}:${sortedEtype.join(",")}:${sortedLangs.join(",")}:${params.salaryMinEur ?? ""}:${params.salaryMaxEur ?? ""}:${params.experienceMin ?? ""}:${params.experienceMax ?? ""}:${params.locale}:${params.offset}:${params.limit}`;
  const result = await cached(
    key,
    async () => {
      // No expansion needed — ancestor IDs are stored on each Typesense document
      return getSearchProvider().loadPostingsWithCounts(params);
    },
    { ttl: 300 },
  );

  if (!userId && params.offset + result.postings.length >= ANON_MAX_POSTINGS) {
    return { ...result, truncated: true };
  }

  return result;
}

// ── Top locations for a company ─────────────────────────────────────

export interface CompanyLocation {
  id: number;
  slug: string;
  name: string;
  type: string;
  count: number;
}

export async function getCompanyTopLocations(
  companyId: string,
  locale: string,
): Promise<{ locations: CompanyLocation[]; totalCount: number }> {
  const key = `company-top-locs:${companyId}:${locale}`;
  return cached(key, () => _fetchTopLocations(companyId, locale), { ttl: 600 });
}

async function _fetchTopLocations(
  companyId: string,
  locale: string,
): Promise<{ locations: CompanyLocation[]; totalCount: number }> {
  const rows = await db.execute<{
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
  `);

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

export async function getCompanyLocationsGrouped(
  companyId: string,
  locale: string,
): Promise<GroupedCompanyLocations[]> {
  const key = `company-locs-grouped:${companyId}:${locale}`;
  return cached(key, () => _fetchLocationsGrouped(companyId, locale), { ttl: 600 });
}

async function _fetchLocationsGrouped(
  companyId: string,
  locale: string,
): Promise<GroupedCompanyLocations[]> {
  const rows = await db.execute<{
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
  `);

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
    const aliasRows = await db.execute<{
      [key: string]: unknown;
      location_id: number;
      name: string;
    }>(sql`
      SELECT location_id, lower(name) AS name
      FROM location_name
      WHERE location_id = ANY(${pgArray}::integer[])
        AND locale IN (${locale}, 'en')
    `);
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

