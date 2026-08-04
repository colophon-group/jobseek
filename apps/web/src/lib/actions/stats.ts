"use server";

import { cacheLife } from "next/cache";
import { getSearchClient } from "@/lib/search/typesense-client";
import { withTypesenseRetry } from "@/lib/search/typesense-retry";

// Per-region in-memory `'use cache'` (cacheLife('hours')). Build ID is
// included in the key automatically — every deploy re-fetches. Migrated
// from Redis-backed `cached(..., { ttl: 21600 })` in #2884 (bucket 5).
// Returns a plain serializable object; Number coercions stay inside the
// cache boundary so the cached value is the final shape consumers see.
export async function getSiteStats() {
  "use cache";
  cacheLife("hours");
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
}
