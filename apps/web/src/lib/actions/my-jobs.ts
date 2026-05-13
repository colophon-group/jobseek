"use server";

import { eq, and, desc, asc, count, sql } from "drizzle-orm";
import { db } from "@/db";
import { savedJob, jobPosting, company, applicationInterview } from "@/db/schema";
import { getSessionUserId } from "@/lib/sessionCache";
import {
  type ApplicationStatus,
  type InterviewType,
  type MyJobEntry,
  type MyJobDetail,
  type InterviewEntry,
} from "./my-jobs-types";

// Re-export types (types are erased at runtime, safe in "use server" files)
export type {
  ApplicationStatus,
  InterviewType,
  MyJobEntry,
  MyJobDetail,
  InterviewEntry,
} from "./my-jobs-types";

const LEGAL_TRANSITIONS: Record<ApplicationStatus, ApplicationStatus[]> = {
  saved: ["applied", "interviewing", "offered", "rejected"],
  applied: ["saved", "interviewing", "offered", "rejected"],
  interviewing: ["saved", "applied", "offered", "rejected"],
  offered: ["saved", "applied", "interviewing", "rejected"],
  rejected: ["saved", "applied", "interviewing", "offered"],
};

type SortBy = "status_changed_at" | "saved_at" | "status" | "company_name";

// ── getMyJobs ────────────────────────────────────────────────────────

export async function getMyJobs(params: {
  offset: number;
  limit: number;
  sortBy?: SortBy;
  sortDir?: "asc" | "desc";
  statusFilter?: ApplicationStatus[];
  groupByCompany?: boolean;
}): Promise<{ jobs: MyJobEntry[]; total: number }> {
  const userId = await getSessionUserId();
  if (!userId) return { jobs: [], total: 0 };

  const {
    offset,
    limit,
    sortBy = "status_changed_at",
    sortDir = "desc",
    statusFilter,
    groupByCompany = false,
  } = params;

  // Build WHERE conditions
  const conditions = [eq(savedJob.userId, userId)];
  if (statusFilter && statusFilter.length > 0) {
    conditions.push(
      sql`${savedJob.status} IN (${sql.join(
        statusFilter.map((s) => sql`${s}`),
        sql`, `,
      )})`,
    );
  }

  const where = and(...conditions);

  // Count
  const [totalRow] = await db
    .select({ count: count() })
    .from(savedJob)
    .where(where);

  const total = totalRow?.count ?? 0;
  if (total === 0) return { jobs: [], total: 0 };

  // Build ORDER BY
  const sortCol = {
    status_changed_at: savedJob.statusChangedAt,
    saved_at: savedJob.savedAt,
    status: savedJob.status,
    company_name: company.name,
  }[sortBy];

  const dirFn = sortDir === "asc" ? asc : desc;

  const orderClauses = [];
  if (groupByCompany) {
    orderClauses.push(asc(company.name));
  }
  orderClauses.push(dirFn(sortCol));

  // Fetch
  const rows = await db
    .select({
      id: savedJob.id,
      savedAt: savedJob.savedAt,
      status: savedJob.status,
      statusChangedAt: savedJob.statusChangedAt,
      appliedAt: savedJob.appliedAt,
      salaryMinOverride: savedJob.salaryMinOverride,
      salaryMaxOverride: savedJob.salaryMaxOverride,
      salaryCurrencyOverride: savedJob.salaryCurrencyOverride,
      salaryPeriodOverride: savedJob.salaryPeriodOverride,
      postingId: jobPosting.id,
      postingTitle: sql<string | null>`${jobPosting.titles}[1]`,
      postingSourceUrl: jobPosting.sourceUrl,
      postingFirstSeenAt: jobPosting.firstSeenAt,
      postingIsActive: jobPosting.isActive,
      postingSalaryMin: jobPosting.salaryMin,
      postingSalaryMax: jobPosting.salaryMax,
      postingSalaryCurrency: jobPosting.salaryCurrency,
      postingSalaryPeriod: jobPosting.salaryPeriod,
      companyId: company.id,
      companyName: company.name,
      companySlug: company.slug,
      companyIcon: company.icon,
      interviewCount: sql<number>`(
        SELECT count(*)::int FROM application_interview ai
        WHERE ai.saved_job_id = ${savedJob.id}
      )`,
    })
    .from(savedJob)
    .innerJoin(jobPosting, eq(savedJob.jobPostingId, jobPosting.id))
    .innerJoin(company, eq(jobPosting.companyId, company.id))
    .where(where)
    .orderBy(...orderClauses)
    .offset(offset)
    .limit(limit);

  const jobs: MyJobEntry[] = rows.map((r) => ({
    id: r.id,
    savedAt: r.savedAt.toISOString(),
    status: r.status as ApplicationStatus,
    statusChangedAt: r.statusChangedAt.toISOString(),
    appliedAt: r.appliedAt?.toISOString() ?? null,
    interviewCount: r.interviewCount,
    posting: {
      id: r.postingId,
      title: r.postingTitle,
      sourceUrl: r.postingSourceUrl,
      firstSeenAt: r.postingFirstSeenAt.toISOString(),
      isActive: r.postingIsActive,
      salaryMin: r.postingSalaryMin,
      salaryMax: r.postingSalaryMax,
      salaryCurrency: r.postingSalaryCurrency,
      salaryPeriod: r.postingSalaryPeriod,
    },
    company: {
      id: r.companyId,
      name: r.companyName,
      slug: r.companySlug,
      icon: r.companyIcon,
    },
    salaryOverride: {
      min: r.salaryMinOverride,
      max: r.salaryMaxOverride,
      currency: r.salaryCurrencyOverride,
      period: r.salaryPeriodOverride,
    },
  }));

  return { jobs, total };
}

// ── getMyJobDetail ───────────────────────────────────────────────────

export async function getMyJobDetail(
  savedJobId: string,
): Promise<MyJobDetail | null> {
  const userId = await getSessionUserId();
  if (!userId) return null;

  const [row] = await db
    .select({
      id: savedJob.id,
      savedAt: savedJob.savedAt,
      status: savedJob.status,
      statusChangedAt: savedJob.statusChangedAt,
      appliedAt: savedJob.appliedAt,
      offeredAt: savedJob.offeredAt,
      rejectedAt: savedJob.rejectedAt,
      salaryMinOverride: savedJob.salaryMinOverride,
      salaryMaxOverride: savedJob.salaryMaxOverride,
      salaryCurrencyOverride: savedJob.salaryCurrencyOverride,
      salaryPeriodOverride: savedJob.salaryPeriodOverride,
      postingId: jobPosting.id,
      postingTitle: sql<string | null>`${jobPosting.titles}[1]`,
      postingSourceUrl: jobPosting.sourceUrl,
      postingFirstSeenAt: jobPosting.firstSeenAt,
      postingIsActive: jobPosting.isActive,
      postingSalaryMin: jobPosting.salaryMin,
      postingSalaryMax: jobPosting.salaryMax,
      postingSalaryCurrency: jobPosting.salaryCurrency,
      postingSalaryPeriod: jobPosting.salaryPeriod,
      companyId: company.id,
      companyName: company.name,
      companySlug: company.slug,
      companyIcon: company.icon,
    })
    .from(savedJob)
    .innerJoin(jobPosting, eq(savedJob.jobPostingId, jobPosting.id))
    .innerJoin(company, eq(jobPosting.companyId, company.id))
    .where(and(eq(savedJob.id, savedJobId), eq(savedJob.userId, userId)))
    .limit(1);

  if (!row) return null;

  const interviewRows = await db
    .select()
    .from(applicationInterview)
    .where(eq(applicationInterview.savedJobId, savedJobId))
    .orderBy(asc(applicationInterview.round));

  const interviews: InterviewEntry[] = interviewRows.map((r) => ({
    id: r.id,
    round: r.round,
    type: r.type as InterviewType,
    scheduledAt: r.scheduledAt?.toISOString() ?? null,
    createdAt: r.createdAt.toISOString(),
  }));

  return {
    id: row.id,
    savedAt: row.savedAt.toISOString(),
    status: row.status as ApplicationStatus,
    statusChangedAt: row.statusChangedAt.toISOString(),
    appliedAt: row.appliedAt?.toISOString() ?? null,
    offeredAt: row.offeredAt?.toISOString() ?? null,
    rejectedAt: row.rejectedAt?.toISOString() ?? null,
    interviewCount: interviews.length,
    posting: {
      id: row.postingId,
      title: row.postingTitle,
      sourceUrl: row.postingSourceUrl,
      firstSeenAt: row.postingFirstSeenAt.toISOString(),
      isActive: row.postingIsActive,
      salaryMin: row.postingSalaryMin,
      salaryMax: row.postingSalaryMax,
      salaryCurrency: row.postingSalaryCurrency,
      salaryPeriod: row.postingSalaryPeriod,
    },
    company: {
      id: row.companyId,
      name: row.companyName,
      slug: row.companySlug,
      icon: row.companyIcon,
    },
    salaryOverride: {
      min: row.salaryMinOverride,
      max: row.salaryMaxOverride,
      currency: row.salaryCurrencyOverride,
      period: row.salaryPeriodOverride,
    },
    interviews,
  };
}

// ── updateJobStatus ──────────────────────────────────────────────────

export async function updateJobStatus(
  savedJobId: string,
  newStatus: ApplicationStatus,
): Promise<{ ok: boolean; error?: string }> {
  const userId = await getSessionUserId();
  if (!userId) return { ok: false, error: "Not authenticated" };

  const [row] = await db
    .select({ id: savedJob.id, status: savedJob.status })
    .from(savedJob)
    .where(and(eq(savedJob.id, savedJobId), eq(savedJob.userId, userId)))
    .limit(1);

  if (!row) return { ok: false, error: "Not found" };

  const currentStatus = row.status as ApplicationStatus;
  const allowed = LEGAL_TRANSITIONS[currentStatus];
  if (!allowed.includes(newStatus)) {
    return {
      ok: false,
      error: `Cannot transition from ${currentStatus} to ${newStatus}`,
    };
  }

  const now = new Date();
  const updates: Record<string, unknown> = {
    status: newStatus,
    statusChangedAt: now,
  };

  if (newStatus === "applied") updates.appliedAt = now;
  if (newStatus === "offered") updates.offeredAt = now;
  if (newStatus === "rejected") updates.rejectedAt = now;

  await db
    .update(savedJob)
    .set(updates)
    .where(eq(savedJob.id, savedJobId));

  // Auto-create first interview if transitioning to interviewing with none.
  //
  // Race with `addInterview` (#3160): two tabs running
  // `updateJobStatus('interviewing')` and `addInterview(...)` for the same
  // saved_job both observe "no interviews yet" and both insert round=1.
  // Fix: rely on the UNIQUE (saved_job_id, round) constraint added in
  // migration 0078 and use INSERT … ON CONFLICT DO NOTHING. If the other
  // call already inserted round=1, this is a silent no-op (caller didn't
  // ask for a specific interview row, only that one exists).
  if (newStatus === "interviewing") {
    await db
      .insert(applicationInterview)
      .values({
        savedJobId,
        round: 1,
        type: "phone_screen",
      })
      .onConflictDoNothing({
        target: [applicationInterview.savedJobId, applicationInterview.round],
      });
  }

  return { ok: true };
}

// ── addInterview ─────────────────────────────────────────────────────

export async function addInterview(
  savedJobId: string,
  type: InterviewType,
): Promise<{ ok: boolean; interview?: InterviewEntry; error?: string }> {
  const userId = await getSessionUserId();
  if (!userId) return { ok: false, error: "Not authenticated" };

  // Verify ownership
  const [row] = await db
    .select({ id: savedJob.id, status: savedJob.status })
    .from(savedJob)
    .where(and(eq(savedJob.id, savedJobId), eq(savedJob.userId, userId)))
    .limit(1);

  if (!row) return { ok: false, error: "Not found" };

  // Atomic round-number assignment (#3160 / #3114).
  //
  // Previous shape was two statements — `SELECT coalesce(max(round), 0)`
  // followed by an `INSERT` with the read value — which races between
  // any two concurrent callers (double-tap, two tabs, addInterview vs
  // updateJobStatus auto-create). Both observe the same max and both
  // INSERT the same round.
  //
  // New shape: a single `INSERT … SELECT coalesce(max(round), 0) + 1 …`
  // is atomic in postgres — the inner SELECT and the row write happen
  // under the same statement, and the UNIQUE (saved_job_id, round)
  // constraint (migration 0078) is the backstop. If two such statements
  // serialize and the second observes the first's row, MAX returns the
  // new value and the round increments cleanly. If they truly tie under
  // the same snapshot (a window narrowed but not eliminated by SELECT's
  // read consistency), the UNIQUE constraint trips the loser with a
  // 23505 unique_violation and the retry loop computes a fresh round.
  //
  // The retry budget is small (4 attempts) — the only way to exhaust it
  // is a many-way concurrent burst, which the application surface (a
  // single "Add interview" button) cannot generate.
  const MAX_ATTEMPTS = 4;
  let inserted:
    | {
        id: string;
        round: number;
        type: string;
        scheduledAt: Date | null;
        createdAt: Date;
      }
    | undefined;
  let lastErr: unknown;

  for (let attempt = 0; attempt < MAX_ATTEMPTS; attempt++) {
    try {
      const rows = await db.execute<{
        id: string;
        round: number;
        type: string;
        scheduled_at: Date | null;
        created_at: Date;
      }>(sql`
        INSERT INTO application_interview (saved_job_id, round, type)
        SELECT
          ${savedJobId}::uuid,
          coalesce(max(round), 0) + 1,
          ${type}
        FROM application_interview
        WHERE saved_job_id = ${savedJobId}::uuid
        RETURNING id, round, type, scheduled_at, created_at
      `);

      const r = (rows as unknown as Array<{
        id: string;
        round: number;
        type: string;
        scheduled_at: Date | null;
        created_at: Date;
      }>)[0];

      if (!r) {
        // Defensive: postgres should always return the inserted row.
        // Treat as a transient miss and retry.
        lastErr = new Error("addInterview: INSERT…SELECT returned no row");
        continue;
      }

      inserted = {
        id: r.id,
        round: r.round,
        type: r.type,
        scheduledAt: r.scheduled_at,
        createdAt: r.created_at,
      };
      break;
    } catch (err) {
      lastErr = err;
      // Postgres unique_violation: another concurrent caller won the
      // race for this round number. Recompute via another INSERT…SELECT
      // pass (which will see the conflicting row and pick the next round).
      const code = (err as { code?: unknown }).code;
      if (code === "23505") continue;
      // Non-conflict errors propagate.
      throw err;
    }
  }

  if (!inserted) {
    // Exhausted the retry budget without a successful insert. Surface as
    // a soft error so the caller can decide (the UI shows a toast). The
    // server log path picks up the underlying postgres error.
    console.warn(
      "[addInterview] failed to assign unique round after retries",
      { savedJobId, type, lastErr },
    );
    return { ok: false, error: "Could not assign interview round" };
  }

  // Auto-transition to interviewing if currently applied
  if (row.status === "applied") {
    await db
      .update(savedJob)
      .set({ status: "interviewing", statusChangedAt: new Date() })
      .where(eq(savedJob.id, savedJobId));
  }

  return {
    ok: true,
    interview: {
      id: inserted.id,
      round: inserted.round,
      type: inserted.type as InterviewType,
      scheduledAt: inserted.scheduledAt?.toISOString() ?? null,
      createdAt: inserted.createdAt.toISOString(),
    },
  };
}

// ── updateInterview ──────────────────────────────────────────────────

export async function updateInterview(
  interviewId: string,
  updates: { type?: InterviewType; scheduledAt?: string | null },
): Promise<{ ok: boolean; error?: string }> {
  const userId = await getSessionUserId();
  if (!userId) return { ok: false, error: "Not authenticated" };

  // Verify ownership through saved_job
  const [row] = await db
    .select({ userId: savedJob.userId })
    .from(applicationInterview)
    .innerJoin(savedJob, eq(applicationInterview.savedJobId, savedJob.id))
    .where(eq(applicationInterview.id, interviewId))
    .limit(1);

  if (!row || row.userId !== userId) return { ok: false, error: "Not found" };

  const setObj: Record<string, unknown> = {};
  if (updates.type !== undefined) setObj.type = updates.type;
  if (updates.scheduledAt !== undefined) {
    setObj.scheduledAt = updates.scheduledAt
      ? new Date(updates.scheduledAt)
      : null;
  }

  if (Object.keys(setObj).length > 0) {
    await db
      .update(applicationInterview)
      .set(setObj)
      .where(eq(applicationInterview.id, interviewId));
  }

  return { ok: true };
}

// ── deleteInterview ──────────────────────────────────────────────────

export async function deleteInterview(
  interviewId: string,
): Promise<{ ok: boolean; error?: string }> {
  const userId = await getSessionUserId();
  if (!userId) return { ok: false, error: "Not authenticated" };

  // Get interview + saved_job info
  const [row] = await db
    .select({
      savedJobId: applicationInterview.savedJobId,
      round: applicationInterview.round,
      sjUserId: savedJob.userId,
      sjStatus: savedJob.status,
    })
    .from(applicationInterview)
    .innerJoin(savedJob, eq(applicationInterview.savedJobId, savedJob.id))
    .where(eq(applicationInterview.id, interviewId))
    .limit(1);

  if (!row || row.sjUserId !== userId) return { ok: false, error: "Not found" };

  // Delete the interview
  await db
    .delete(applicationInterview)
    .where(eq(applicationInterview.id, interviewId));

  // Renumber remaining rounds
  const remaining = await db
    .select({ id: applicationInterview.id })
    .from(applicationInterview)
    .where(eq(applicationInterview.savedJobId, row.savedJobId))
    .orderBy(asc(applicationInterview.round));

  for (let i = 0; i < remaining.length; i++) {
    await db
      .update(applicationInterview)
      .set({ round: i + 1 })
      .where(eq(applicationInterview.id, remaining[i].id));
  }

  // If no interviews remain and status is interviewing, transition back
  if (remaining.length === 0 && row.sjStatus === "interviewing") {
    await db
      .update(savedJob)
      .set({ status: "applied", statusChangedAt: new Date() })
      .where(eq(savedJob.id, row.savedJobId));
  }

  return { ok: true };
}

// ── updateSalaryOverride ─────────────────────────────────────────────

export async function updateSalaryOverride(
  savedJobId: string,
  data: {
    salaryMin?: number | null;
    salaryMax?: number | null;
    currency?: string | null;
    period?: string | null;
  },
): Promise<{ ok: boolean; error?: string }> {
  const userId = await getSessionUserId();
  if (!userId) return { ok: false, error: "Not authenticated" };

  const [row] = await db
    .select({ id: savedJob.id })
    .from(savedJob)
    .where(and(eq(savedJob.id, savedJobId), eq(savedJob.userId, userId)))
    .limit(1);

  if (!row) return { ok: false, error: "Not found" };

  await db
    .update(savedJob)
    .set({
      salaryMinOverride: data.salaryMin ?? null,
      salaryMaxOverride: data.salaryMax ?? null,
      salaryCurrencyOverride: data.currency ?? null,
      salaryPeriodOverride: data.period ?? null,
    })
    .where(eq(savedJob.id, savedJobId));

  return { ok: true };
}
