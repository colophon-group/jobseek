"use server";

import { cacheLife } from "next/cache";
import { getSearchClient } from "@/lib/search/typesense-client";
import { withTypesenseRetry } from "@/lib/search/typesense-retry";

// Per-region in-memory `'use cache'` (cacheLife('hours')). Build ID is
// included in the key automatically — every deploy re-fetches. Migrated
// from Redis-backed `cached(..., { ttl: 21600 })` in #2884 (bucket 5).
// Returns a plain serializable object (or null for the existing placeholder
// UI); Number coercions stay inside the cache boundary so the cached value is
// the final shape consumers see.
type SiteStats = {
  companyCount: number;
  jobPostingCount: number;
};

async function getCachedSiteStats(): Promise<SiteStats | null> {
  "use cache";
  cacheLife("hours");
  try {
    const client = getSearchClient();
    const [companies, postings] = await Promise.all([
      withTypesenseRetry(
        () =>
          client.collections("company").documents().search({
            q: "*",
            per_page: 0,
          }),
        { label: "siteStats.company" },
      ),
      withTypesenseRetry(
        () =>
          client.collections("job_posting").documents().search({
            q: "*",
            filter_by: "is_active:true",
            per_page: 0,
          }),
        { label: "siteStats.jobPosting" },
      ),
    ]);
    return {
      companyCount: Number(companies.found ?? 0),
      jobPostingCount: Number(postings.found ?? 0),
    };
  } catch {
    return null;
  }
}

export async function getSiteStats(): Promise<SiteStats | null> {
  // A secretless build must not enter the cache boundary: Next surfaces
  // missing-environment errors from cached functions as prerender failures
  // before a page-level catch can provide the existing placeholder UI.
  if (!process.env.TYPESENSE_SEARCH_KEY) return null;
  return getCachedSiteStats();
}
