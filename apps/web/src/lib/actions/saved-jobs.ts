"use server";

import { eq, and, desc, count, sql } from "drizzle-orm";
import { db } from "@/db";
import { savedJob, jobPosting, company } from "@/db/schema";
import { getSessionUserId } from "@/lib/sessionCache";

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

export async function toggleSavedJob(
  jobPostingId: string,
): Promise<{ saved: boolean }> {
  const userId = await getSessionUserId();
  if (!userId) throw new Error("Not authenticated");

  const [existing] = await db
    .select({ id: savedJob.id })
    .from(savedJob)
    .where(
      and(
        eq(savedJob.userId, userId),
        eq(savedJob.jobPostingId, jobPostingId),
      ),
    )
    .limit(1);

  if (existing) {
    await db.delete(savedJob).where(eq(savedJob.id, existing.id));
    return { saved: false };
  }

  await db.insert(savedJob).values({ userId, jobPostingId });
  return { saved: true };
}

export async function getSavedJobIds(): Promise<string[]> {
  const userId = await getSessionUserId();
  if (!userId) return [];

  const rows = await db
    .select({ jobPostingId: savedJob.jobPostingId })
    .from(savedJob)
    .where(eq(savedJob.userId, userId));

  return rows.map((r) => r.jobPostingId);
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
