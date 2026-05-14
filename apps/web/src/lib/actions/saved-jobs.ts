"use server";

import { eq, and, desc, count, sql } from "drizzle-orm";
import { db } from "@/db";
import { savedJob, jobPosting, company } from "@/db/schema";
import { getSessionUserId } from "@/lib/sessionCache";
import { isUniqueViolation } from "@/lib/db-conflict";

export type SavedJobEntry = {
  id: string;
  savedAt: string;
  posting: {
    id: string;
    title: string | null;
    sourceUrl: string;
    firstSeenAt: string;
    isActive: boolean;
  };
  company: {
    id: string;
    name: string;
    slug: string;
    icon: string | null;
  };
};

/**
 * UNIQUE index name behind `(user_id, job_posting_id)` on `saved_job`
 * (see `apps/web/src/db/schema.ts`). Used to scope the race-recovery
 * branch in `toggleSavedJob` to only the conflict it knows how to
 * handle — any other unique violation propagates so the real bug
 * surfaces.
 */
const SAVED_JOB_UNIQUE_CONSTRAINT = "idx_sj_user_posting";

/**
 * Toggle a (user, posting) save row.
 *
 * #3179 — the legacy SELECT-then-INSERT-OR-DELETE shape raced under
 * a double-click on flaky network: two parallel calls with no row
 * present both observed an empty SELECT, both ran INSERT, and the
 * loser crashed with an un-handled `23505` from `idx_sj_user_posting`.
 *
 * Fix matches the #3268 retry-on-conflict shape: optimistically try
 * INSERT first; if it succeeds the toggle was OFF→ON. If the INSERT
 * trips the UNIQUE index, the row already exists (either pre-call or
 * landed by a racing winner), so the toggle is ON→OFF — DELETE and
 * return `saved=false`. The UNIQUE index is the source of truth; the
 * exception path serialises contention without leaking 500s.
 *
 * The conflict catch is scoped narrowly to `code === "23505"` on
 * `idx_sj_user_posting`. Other unique violations propagate.
 */
export async function toggleSavedJob(
  jobPostingId: string,
): Promise<{ saved: boolean; savedJobId?: string }> {
  const userId = await getSessionUserId();
  if (!userId) throw new Error("Not authenticated");

  try {
    const [row] = await db
      .insert(savedJob)
      .values({ userId, jobPostingId })
      .returning({ id: savedJob.id });
    // INSERT succeeded — the row didn't exist before this call.
    return { saved: true, savedJobId: row.id };
  } catch (err) {
    if (!isUniqueViolation(err, SAVED_JOB_UNIQUE_CONSTRAINT)) throw err;
    // Row already exists (either pre-call or just inserted by a racing
    // winner). Toggle semantics: transition ON→OFF. DELETE matching
    // the (user_id, job_posting_id) pair — using the columns rather
    // than re-SELECTing avoids another round-trip and another race
    // window.
    await db
      .delete(savedJob)
      .where(
        and(
          eq(savedJob.userId, userId),
          eq(savedJob.jobPostingId, jobPostingId),
        ),
      );
    return { saved: false };
  }
}

export type SavedJobStatus = { postingId: string; savedJobId: string; status: string };

export async function getSavedJobStatuses(): Promise<SavedJobStatus[]> {
  const userId = await getSessionUserId();
  if (!userId) return [];

  const rows = await db
    .select({ id: savedJob.id, jobPostingId: savedJob.jobPostingId, status: savedJob.status })
    .from(savedJob)
    .where(eq(savedJob.userId, userId));

  return rows.map((r) => ({ postingId: r.jobPostingId, savedJobId: r.id, status: r.status }));
}

export async function getSavedJobs(params: {
  offset: number;
  limit: number;
}): Promise<{ jobs: SavedJobEntry[]; total: number }> {
  const userId = await getSessionUserId();
  if (!userId) return { jobs: [], total: 0 };

  const [totalRow] = await db
    .select({ count: count() })
    .from(savedJob)
    .where(eq(savedJob.userId, userId));

  const total = totalRow?.count ?? 0;
  if (total === 0) return { jobs: [], total: 0 };

  const rows = await db
    .select({
      id: savedJob.id,
      savedAt: savedJob.savedAt,
      postingId: jobPosting.id,
      postingTitle: sql<string | null>`${jobPosting.titles}[1]`,
      postingSourceUrl: jobPosting.sourceUrl,
      postingFirstSeenAt: jobPosting.firstSeenAt,
      postingIsActive: jobPosting.isActive,
      companyId: company.id,
      companyName: company.name,
      companySlug: company.slug,
      companyIcon: company.icon,
    })
    .from(savedJob)
    .innerJoin(jobPosting, eq(savedJob.jobPostingId, jobPosting.id))
    .innerJoin(company, eq(jobPosting.companyId, company.id))
    .where(eq(savedJob.userId, userId))
    .orderBy(desc(savedJob.savedAt))
    .offset(params.offset)
    .limit(params.limit);

  const jobs: SavedJobEntry[] = rows.map((r) => ({
    id: r.id,
    savedAt: r.savedAt.toISOString(),
    posting: {
      id: r.postingId,
      title: r.postingTitle,
      sourceUrl: r.postingSourceUrl,
      firstSeenAt: r.postingFirstSeenAt.toISOString(),
      isActive: r.postingIsActive,
    },
    company: {
      id: r.companyId,
      name: r.companyName,
      slug: r.companySlug,
      icon: r.companyIcon,
    },
  }));

  return { jobs, total };
}
