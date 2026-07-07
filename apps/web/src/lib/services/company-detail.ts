import "server-only";

import { sql } from "drizzle-orm";
import { db } from "@/db";
import { CACHE_TTL_DETAIL } from "@/lib/cache-ttl";
import { cached } from "@/lib/cache";
import { withDbRetry } from "@/lib/db-retry";
import { getSearchClient } from "@/lib/search/typesense-client";
import {
  isRetryableError as isRetryableTypesenseError,
  isTypesenseRateLimitError,
  isTypesenseUnavailableError,
  withTypesenseRetry,
} from "@/lib/search/typesense-retry";
import {
  canResolveCompanyBySlugFromEnv,
  isSafeCompanySlug,
  mapPostgresCompanyRowToDetail,
  mapTypesenseCompanyHitToDetail,
  resolveCompanyBySlug,
  type CompanyDetail,
  type PostgresCompanyRow,
} from "@/lib/services/company-detail-lookup";

export type { CompanyDetail } from "@/lib/services/company-detail-lookup";

export async function getCompanyBySlug(
  slug: string,
  locale: string,
): Promise<CompanyDetail | null> {
  if (!canResolveCompanyBySlugFromEnv(process.env)) {
    console.warn("[company] lookup skipped because Typesense and DATABASE_URL are not configured");
    return null;
  }
  const key = `company-slug:${slug}:${locale}`;
  // Empty-result skipping is load-bearing here: a not-yet-indexed company
  // should be retried on the next request, but throwing a sentinel from a
  // `'use cache'` boundary leaks that error into the RSC payload (#3603).
  return cached(key, () => fetchCompanyBySlug(slug, locale), {
    ttl: CACHE_TTL_DETAIL,
    skipIf: (data) => data === null,
  });
}

async function fetchCompanyBySlug(slug: string, locale: string): Promise<CompanyDetail | null> {
  return resolveCompanyBySlug(slug, locale, {
    fetchFromTypesense: fetchCompanyBySlugFromTypesense,
    fetchFromPostgres: fetchCompanyBySlugFromPostgres,
    hasPostgresConfig: () => Boolean(process.env.DATABASE_URL),
    isTypesenseUnavailableError,
    logger: console,
  });
}

function shouldRetryCompanyTypesenseRead(err: unknown): boolean {
  return isRetryableTypesenseError(err) || isTypesenseRateLimitError(err);
}

async function fetchCompanyBySlugFromTypesense(
  slug: string,
  locale: string,
): Promise<CompanyDetail | null> {
  if (!isSafeCompanySlug(slug)) return null;
  const client = getSearchClient();
  const result = await withTypesenseRetry(
    () =>
      client.collections("company").documents().search({
        q: "*",
        filter_by: `slug:=${slug}`,
        per_page: 1,
      }),
    {
      attempts: 5,
      baseDelaysMs: [250, 500, 1000, 2000],
      isRetryable: shouldRetryCompanyTypesenseRead,
      label: `companyBySlug[${slug}]`,
    },
  );
  const hit = result.hits?.[0]?.document as Record<string, unknown> | undefined;
  return hit ? mapTypesenseCompanyHitToDetail(hit, slug, locale) : null;
}

async function fetchCompanyBySlugFromPostgres(
  slug: string,
  locale: string,
): Promise<CompanyDetail | null> {
  // Retry on transient connection-class errors (#2918): the build that
  // killed prerender at 2026-05-09T15:41:49Z hit `read ECONNRESET` from
  // the Supabase pooler on this exact query. The next build 2 min later
  // succeeded, a flake, not structural break. `withDbRetry` only retries
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

  const row = (rows as unknown as PostgresCompanyRow[])[0];
  return row ? mapPostgresCompanyRowToDetail(row) : null;
}
