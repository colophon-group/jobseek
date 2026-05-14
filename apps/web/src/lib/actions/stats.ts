"use server";

import { sql } from "drizzle-orm";
import { cacheLife } from "next/cache";
import { db } from "@/db";
import { company, jobPosting } from "@/db/schema";

// Per-region in-memory `'use cache'` (cacheLife('hours')). Build ID is
// included in the key automatically — every deploy re-fetches. Migrated
// from Redis-backed `cached(..., { ttl: 21600 })` in #2884 (bucket 5).
// Returns a plain serializable object; Number coercions stay inside the
// cache boundary so the cached value is the final shape consumers see.
export async function getStats() {
  "use cache";
  cacheLife("hours");
  const [[companyRow], [jobRow]] = await Promise.all([
    db.select({ count: sql<number>`count(*)` }).from(company),
    db
      .select({ count: sql<number>`count(*)` })
      .from(jobPosting)
      .where(sql`${jobPosting.isActive} = true`),
  ]);
  return {
    companyCount: Number(companyRow.count),
    jobPostingCount: Number(jobRow.count),
  };
}
