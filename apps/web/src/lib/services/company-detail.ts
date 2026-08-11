import "server-only";

import { CACHE_TTL_DETAIL } from "@/lib/cache-ttl";
import { cached } from "@/lib/cache";
import { companyDetailCacheKey } from "@/lib/cache-registry";
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
  mapTypesenseCompanyHitToDetail,
  resolveCompanyBySlug,
  type CompanyDetail,
} from "@/lib/services/company-detail-lookup";

const MAX_COMPANY_SLUG_BATCH = 25;

export type { CompanyDetail } from "@/lib/services/company-detail-lookup";

export async function getCompanyBySlug(
  slug: string,
  locale: string,
): Promise<CompanyDetail | null> {
  if (!canResolveCompanyBySlugFromEnv(process.env)) {
    console.warn("[company] lookup skipped because Typesense is not configured");
    return null;
  }
  const key = companyDetailCacheKey(slug, locale);
  // Empty-result skipping is load-bearing here: a not-yet-indexed company
  // should be retried on the next request, but throwing a sentinel from a
  // `'use cache'` boundary leaks that error into the RSC payload (#3603).
  return cached(key, () => fetchCompanyBySlug(slug, locale), {
    ttl: CACHE_TTL_DETAIL,
    skipIf: (data) => data === null,
  });
}

/** Resolve a bounded set of canonical slugs to UUIDs in one Typesense read. */
export async function getCompanyIdsBySlugs(
  slugs: readonly string[],
): Promise<Map<string, string>> {
  if (slugs.length === 0) return new Map();
  if (slugs.length > MAX_COMPANY_SLUG_BATCH) {
    throw new Error("Company slug batch exceeds the supported limit");
  }
  if (!canResolveCompanyBySlugFromEnv(process.env)) {
    throw new Error("Company lookup is not configured");
  }
  if (slugs.some((slug) => !isSafeCompanySlug(slug))) {
    return new Map();
  }

  const result = await withTypesenseRetry(
    () => getSearchClient()
      .collections<{ id: string; slug: string }>("company")
      .documents()
      .search({
        q: "*",
        filter_by: `slug:[${slugs.join(",")}]`,
        per_page: slugs.length,
      }),
    {
      attempts: 5,
      baseDelaysMs: [250, 500, 1000, 2000],
      isRetryable: shouldRetryCompanyTypesenseRead,
      label: "companyIdsBySlugs",
    },
  );

  return new Map(
    (result.hits ?? []).map((hit) => [
      String(hit.document.slug),
      String(hit.document.id),
    ]),
  );
}

async function fetchCompanyBySlug(slug: string, locale: string): Promise<CompanyDetail | null> {
  return resolveCompanyBySlug(slug, locale, {
    fetchFromTypesense: fetchCompanyBySlugFromTypesense,
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
