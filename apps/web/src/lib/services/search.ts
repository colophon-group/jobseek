import "server-only";

import { sql } from "drizzle-orm";
import { cacheLife } from "next/cache";
import { db } from "@/db";
import { getSearchProvider } from "@/lib/search";
import type { SearchResponse, SearchResultPosting, HistogramFilters, WorkMode } from "@/lib/search";
import { cached } from "@/lib/cache";
import { CACHE_TTL_SHORT, CACHE_TTL_MEDIUM } from "@/lib/cache-ttl";
import { withDbRetry } from "@/lib/db-retry";
import { getSessionUserId } from "@/lib/sessionCache";
import { ANON_MAX_COMPANIES, ANON_MAX_CARD_POSTINGS } from "@/lib/search/constants";
import { canonicalStringCompare } from "@/lib/sort";
import { normalizeHistogramFilters, type NormalizedHistogramFilters } from "@/lib/search/histogram-filters";

// ── Posting detail ──────────────────────────────────────────────────

export interface PostingDetail {
  id: string;
  title: string | null;
  company: { id: string; name: string; slug: string; logo: string | null; icon: string | null };
  locations: { id: number; name: string; type: string; geoType?: string; parentName?: string }[];
  employmentType: string | null;
  experienceMin: number | null;
  experienceMax: number | null;
  technologies: { id: number; name: string }[];
  salaryMin: number | null;
  salaryMax: number | null;
  salaryCurrency: string | null;
  salaryPeriod: string | null;
  seniority: { id: number; slug: string; name: string } | null;
  sourceUrl: string;
  firstSeenAt: string;
  descriptionHtml: string | null;
  descriptionUrl: string | null;
}

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export async function getPostingDetail(params: {
  postingId: string;
  locale: string;
}): Promise<PostingDetail | null> {
  const { postingId, locale } = params;
  if (!UUID_RE.test(postingId)) return null;
  return _fetchPostingDetail(postingId, locale);
}

async function resolvePostingLocations(
  locationIds: number[] | null,
  locationTypes: string[] | null,
  locale: string,
): Promise<PostingDetail["locations"]> {
  if (!locationIds || locationIds.length === 0) return [];
  const pgArray = `{${locationIds.join(",")}}`;
  const locRows = await withDbRetry(
    () =>
      db.execute<{
        [key: string]: unknown;
        location_id: number;
        name: string;
        type: string;
        parent_name: string | null;
      }>(sql`
        SELECT DISTINCT ON (ln.location_id) ln.location_id, ln.name, l.type::text,
          (SELECT pn.name FROM location_name pn
           WHERE pn.location_id = l.parent_id
             AND pn.locale IN (${locale}, 'en')
             AND pn.is_display = true
           ORDER BY (pn.locale = ${locale})::int DESC
           LIMIT 1) AS parent_name
        FROM location_name ln
        JOIN location l ON l.id = ln.location_id
        WHERE ln.location_id = ANY(${pgArray}::integer[])
          AND ln.locale IN (${locale}, 'en')
          AND ln.is_display = true
        ORDER BY ln.location_id, (ln.locale = ${locale})::int DESC
      `),
    { label: "postingLocations" },
  );
  const nameMap = new Map<number, { name: string; geoType: string; parentName?: string }>();
  for (const r of locRows as unknown as { location_id: number; name: string; type: string; parent_name: string | null }[]) {
    nameMap.set(r.location_id, { name: r.name, geoType: r.type, parentName: r.parent_name ?? undefined });
  }
  return locationIds
    .map((id, i) => {
      const resolved = nameMap.get(id);
      return {
        id,
        name: resolved?.name ?? "",
        type: locationTypes?.[i] ?? "onsite",
        geoType: resolved?.geoType,
        parentName: resolved?.parentName,
      };
    })
    .filter((l) => l.name !== "");
}

async function resolvePostingTechnologies(
  technologyIds: number[] | null,
): Promise<{ id: number; name: string }[]> {
  if (!technologyIds || technologyIds.length === 0) return [];
  const techArray = `{${technologyIds.join(",")}}`;
  const techRows = await withDbRetry(
    () =>
      db.execute<{ [key: string]: unknown; id: number; name: string | null }>(
        sql`SELECT id, name FROM technology WHERE id = ANY(${techArray}::integer[]) ORDER BY name`,
      ),
    { label: "postingTechnologies" },
  );
  return (techRows as unknown as { id: number; name: string | null }[])
    .filter((t) => t.name)
    .map((t) => ({ id: t.id, name: t.name! }));
}

// Per-region in-memory `'use cache'` (cacheLife({ revalidate: 300 })).
// Build ID is part of the key, so each deploy re-fetches. Migrated from
// Redis-backed `cached(..., { ttl: 300 })` in #2884 (bucket 5). Args
// `(postingId, locale)` derive the cache key automatically; no manual
// key construction. `firstSeenAt` is converted to ISO string before the
// return, so the cached value is fully serializable (no `Date`).
async function _fetchPostingDetail(
  postingId: string,
  locale: string,
): Promise<PostingDetail | null> {
  "use cache";
  cacheLife({ revalidate: CACHE_TTL_MEDIUM });
  const rows = await withDbRetry(
    () =>
      db.execute<{
        [key: string]: unknown;
        id: string;
        title: string | null;
        company_id: string;
        company_name: string;
        company_slug: string;
        company_logo: string | null;
        company_icon: string | null;
        location_ids: number[] | null;
        location_types: string[] | null;
        employment_type: string | null;
        source_url: string;
        first_seen_at: Date;
        locales: string[];
      }>(sql`
        SELECT jp.id, jp.titles[1] AS title,
          c.id AS company_id, c.name AS company_name, c.slug AS company_slug,
          c.logo AS company_logo, c.icon AS company_icon,
          jp.location_ids, jp.location_types,
          jp.employment_type, jp.source_url, jp.first_seen_at,
          jp.locales,
          jp.experience_min, jp.experience_max, jp.technology_ids,
          jp.salary_min, jp.salary_max, jp.salary_currency, jp.salary_period,
          jp.seniority_id, s.slug AS seniority_slug, sn.name AS seniority_name
        FROM job_posting jp
        JOIN company c ON c.id = jp.company_id
        LEFT JOIN seniority s ON s.id = jp.seniority_id
        LEFT JOIN LATERAL (
          SELECT name FROM seniority_name
          WHERE seniority_id = jp.seniority_id AND locale IN (${locale}, 'en') AND is_display = true
          ORDER BY (locale = ${locale})::int DESC LIMIT 1
        ) sn ON true
        WHERE jp.id = ${postingId}
      `),
    { label: `postingDetail[${postingId}]` },
  );

  type Row = {
    id: string; title: string | null;
    company_id: string; company_name: string; company_slug: string;
    company_logo: string | null; company_icon: string | null;
    location_ids: number[] | null; location_types: string[] | null;
    employment_type: string | null; source_url: string;
    first_seen_at: Date; locales: string[];
    experience_min: number | null; experience_max: number | null;
    technology_ids: number[] | null;
    salary_min: number | null; salary_max: number | null;
    salary_currency: string | null; salary_period: string | null;
    seniority_id: number | null; seniority_slug: string | null; seniority_name: string | null;
  };
  const row = (rows as unknown as Row[])[0];
  if (!row) return null;

  // Resolve location and technology names in parallel
  const [locations, technologies] = await Promise.all([
    resolvePostingLocations(row.location_ids, row.location_types, locale),
    resolvePostingTechnologies(row.technology_ids),
  ]);

  // Build R2 description URL for client-side fetch
  const r2Domain = process.env.R2_DOMAIN_URL;
  let descriptionUrl: string | null = null;
  if (r2Domain) {
    const descLocale = row.locales?.[0] ?? "en";
    descriptionUrl = `${r2Domain.replace(/\/$/, "")}/job/${postingId}/${descLocale}/latest.html`;
  }

  return {
    id: row.id,
    title: row.title,
    company: {
      id: row.company_id,
      name: row.company_name,
      slug: row.company_slug,
      logo: row.company_logo,
      icon: row.company_icon,
    },
    locations,
    employmentType: row.employment_type,
    experienceMin: row.experience_min,
    experienceMax: row.experience_max,
    technologies,
    salaryMin: row.salary_min,
    salaryMax: row.salary_max,
    salaryCurrency: row.salary_currency,
    salaryPeriod: row.salary_period,
    seniority: row.seniority_id && row.seniority_slug && row.seniority_name
      ? { id: row.seniority_id, slug: row.seniority_slug, name: row.seniority_name }
      : null,
    sourceUrl: row.source_url,
    firstSeenAt: new Date(row.first_seen_at).toISOString(),
    descriptionHtml: null,
    descriptionUrl,
  };
}

export async function searchJobs(params: {
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
}): Promise<SearchResponse> {
  const userId = await getSessionUserId();

  // Enforce truncation for unauthenticated users
  if (!userId && params.offset >= ANON_MAX_COMPANIES) {
    return { companies: [], totalCompanies: 0, truncated: true };
  }

  // No expansion needed — ancestor IDs are stored on each Typesense document
  const result = await getSearchProvider().search(params);

  // Mark as truncated if this is the last allowed page for anon
  if (!userId && params.offset + result.companies.length >= ANON_MAX_COMPANIES) {
    return { ...result, truncated: true };
  }

  return result;
}

type TopCompaniesParams = {
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
};

// Bump when the default anonymous ordering semantics change. The explore
// prerender embeds this payload, so old Redis values must be unreachable
// after ranking fixes even if a page cache revalidates before Redis expires.
const TOP_COMPANIES_DEFAULT_CACHE_VERSION = "v2";

/**
 * Session-free implementation shared by :func:`listTopCompanies` (which
 * reads ``getSessionUserId`` to enforce the anonymous truncation cap)
 * and :func:`listTopCompaniesAnonymous` (which skips the session read
 * for ISR-eligible call sites). Reading ``headers()`` / ``cookies()``
 * inside an ISR page render path silently downgrades the route to
 * dynamic — see #2640 + #2243.
 */
async function _listTopCompaniesImpl(
  params: TopCompaniesParams,
  userId: string | null,
): Promise<SearchResponse> {
  if (!userId && params.offset >= ANON_MAX_COMPANIES) {
    return { companies: [], totalCompanies: 0, truncated: true };
  }

  // Determine if this is a default/unfiltered request (cacheable).
  // The default case has languages=[locale] (single locale from resolveJobLanguages).
  // We consider that "unfiltered" since it's the automatic default, not a user choice.
  const hasNoExplicitFilters =
    !params.locationIds?.length &&
    !params.occupationIds?.length &&
    !params.seniorityIds?.length &&
    !params.technologyIds?.length &&
    !params.employmentTypes?.length &&
    !params.workMode?.length &&
    params.salaryMinEur == null &&
    params.salaryMaxEur == null &&
    params.experienceMin == null &&
    params.experienceMax == null;

  const fetch = () => getSearchProvider().listTopCompanies(params);

  // Cache the default homepage (no user-specified filters). Include languages
  // in the key since different locales produce different language filters.
  // `canonicalStringCompare` (locale-independent `Intl.Collator("en", {
  // sensitivity: "base" })`) avoids the UTF-16-code-unit-order bug class
  // from #3221 — for ASCII locale codes the orders coincide today, but the
  // canonical comparator is forwards-compatible if future locale codes
  // carry accented chars and keeps every cache-key call-site on one rule.
  const langKey = [...(params.languages ?? [])].sort(canonicalStringCompare).join(",");
  let result: SearchResponse;
  if (hasNoExplicitFilters) {
    result = await cached(
      `top-companies:${TOP_COMPANIES_DEFAULT_CACHE_VERSION}:${params.locale}:${langKey}:${params.offset}:${params.limit}`,
      fetch,
      { ttl: CACHE_TTL_SHORT, skipIf: (r: SearchResponse) => !!r.degraded },
    );
  } else {
    result = await fetch();
  }

  if (!userId && params.offset + result.companies.length >= ANON_MAX_COMPANIES) {
    return { ...result, truncated: true };
  }

  return result;
}

export async function listTopCompanies(params: TopCompaniesParams): Promise<SearchResponse> {
  const userId = await getSessionUserId();
  return _listTopCompaniesImpl(params, userId);
}

/**
 * Anonymous variant of :func:`listTopCompanies` for ISR-eligible
 * server-render paths (#2640). Does NOT read the session — calling
 * ``getSessionUserId`` would await ``headers()`` and silently
 * downgrade the route to dynamic rendering. Always treats the
 * caller as anonymous, so the truncation cap is enforced at
 * ``ANON_MAX_COMPANIES``. Safe for use from a page render with
 * ``revalidate = N``.
 */
export async function listTopCompaniesAnonymous(
  params: TopCompaniesParams,
): Promise<SearchResponse> {
  return _listTopCompaniesImpl(params, null);
}

// ── Currency rates for salary filter ────────────────────────────────

export interface CurrencyRate {
  currency: string;
  toEur: number;
}

// Per-region in-memory `'use cache'` (cacheLife('hours')). Migrated from
// Redis-backed `cached(..., { ttl: 3600 })` in #2884 (bucket 5). The
// fallback try/catch stays *outside* the cache boundary so that the
// `currency_rate` table-missing path returns the EUR fallback uncached
// (preserving the original behaviour: don't cache the fallback shape).
async function _fetchCurrencyRates(): Promise<CurrencyRate[]> {
  "use cache";
  cacheLife("hours");
  const rows = await withDbRetry(
    () =>
      db.execute<{ [key: string]: unknown; currency: string; to_eur: string }>(
        sql`SELECT currency, to_eur FROM currency_rate ORDER BY currency`,
      ),
    { label: "currencyRates" },
  );
  return (rows as unknown as { currency: string; to_eur: string }[]).map((r) => ({
    currency: r.currency,
    toEur: parseFloat(r.to_eur),
  }));
}

export async function getCurrencyRates(): Promise<CurrencyRate[]> {
  try {
    return await _fetchCurrencyRates();
  } catch {
    // Table may not exist yet — return fallback without caching
    return [{ currency: "EUR", toEur: 1 }];
  }
}

export type { SalaryBucket, ExperienceBucket } from "@/lib/search/types";
import type { SalaryBucket, ExperienceBucket } from "@/lib/search/types";

// Histograms are cached on a *normalized* filter object (sorted arrays,
// stable shape) so the auto-derived `'use cache'` key matches the legacy
// concatenated string-key behaviour. Migrated from Redis-backed
// `cached(..., { ttl: 3600 })` in #2884 (bucket 5). The try/catch stays
// *inside* the cache boundary so a Typesense blip caches `[]` for the
// TTL window (preserving the original behaviour — avoids hammering on
// failure). Returns plain arrays of `{ bucket, count }` shapes (already
// serializable; no Maps/Sets/Dates).

// `normalizeHistogramFilters` + `NormalizedHistogramFilters` extracted to
// `@/lib/search/histogram-filters` so the sync helper is unit-testable
// without booting the `"use server"` module surface. See #3276.

async function _fetchSalaryHistogram(f: NormalizedHistogramFilters): Promise<SalaryBucket[]> {
  "use cache";
  cacheLife("hours");
  try {
    // No expansion needed — ancestor IDs are stored on each Typesense document
    return await getSearchProvider().getSalaryHistogram(f);
  } catch {
    return [];
  }
}

async function _fetchExperienceHistogram(f: NormalizedHistogramFilters): Promise<ExperienceBucket[]> {
  "use cache";
  cacheLife("hours");
  try {
    return await getSearchProvider().getExperienceHistogram(f);
  } catch {
    return [];
  }
}

/**
 * Returns salary_eur distribution across fixed EUR buckets for the histogram.
 * Accepts optional filters to scope to a company / keyword / location context.
 */
export async function getSalaryHistogram(filters?: HistogramFilters): Promise<SalaryBucket[]> {
  return _fetchSalaryHistogram(normalizeHistogramFilters(filters));
}

/**
 * Returns experience_min distribution for the histogram.
 * Accepts optional filters to scope to a company / keyword / location context.
 */
export async function getExperienceHistogram(filters?: HistogramFilters): Promise<ExperienceBucket[]> {
  return _fetchExperienceHistogram(normalizeHistogramFilters(filters));
}

// ── Load more postings ─────────────────────────────────────────────

export async function loadMorePostings(params: {
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
}): Promise<{ postings: SearchResultPosting[]; truncated?: boolean }> {
  const userId = await getSessionUserId();

  if (!userId && params.offset >= ANON_MAX_CARD_POSTINGS) {
    return { postings: [], truncated: true };
  }

  // No expansion needed — ancestor IDs are stored on each Typesense document
  const postings = await getSearchProvider().loadPostings(params);

  if (!userId && params.offset + postings.length >= ANON_MAX_CARD_POSTINGS) {
    return { postings, truncated: true };
  }

  return { postings };
}
