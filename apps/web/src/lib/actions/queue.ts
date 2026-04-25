"use server";

import { eq, and, desc, count, sql } from "drizzle-orm";
import { db } from "@/db";
import { jobQueue, jobPosting, company, userResume } from "@/db/schema";
import { getSessionUserId } from "@/lib/sessionCache";
import { extractKeywords } from "@/lib/resume/extract-keywords";

export { scoreColor, formatScore } from "@/lib/queue-utils";

export type QueueEntry = {
  id: string;
  addedAt: string;
  overlapScore: number | null;
  matchedKeywords: string[];
  missingKeywords: string[];
  fitExplanation: string | null;
  analyzedAt: string | null;
  posting: {
    id: string;
    title: string | null;
    sourceUrl: string;
  };
  company: {
    id: string;
    name: string;
    slug: string;
    icon: string | null;
  };
};

export async function addToQueue(
  jobPostingId: string,
): Promise<{ queued: boolean; queueId?: string }> {
  const userId = await getSessionUserId();
  if (!userId) throw new Error("Not authenticated");

  const [existing] = await db
    .select({ id: jobQueue.id })
    .from(jobQueue)
    .where(
      and(
        eq(jobQueue.userId, userId),
        eq(jobQueue.postingId, jobPostingId),
      ),
    )
    .limit(1);

  if (existing) {
    return { queued: true, queueId: existing.id };
  }

  const [row] = await db
    .insert(jobQueue)
    .values({ userId, postingId: jobPostingId })
    .returning({ id: jobQueue.id });

  return { queued: true, queueId: row.id };
}

export async function removeFromQueue(
  queueId: string,
): Promise<{ removed: boolean }> {
  const userId = await getSessionUserId();
  if (!userId) throw new Error("Not authenticated");

  const [item] = await db
    .select({ id: jobQueue.id })
    .from(jobQueue)
    .where(
      and(
        eq(jobQueue.id, queueId),
        eq(jobQueue.userId, userId),
      ),
    )
    .limit(1);

  if (!item) return { removed: false };

  await db.delete(jobQueue).where(eq(jobQueue.id, queueId));
  return { removed: true };
}

export async function checkQueueStatus(
  jobPostingId: string,
): Promise<{ queued: boolean; queueId?: string; analyzed: boolean }> {
  const userId = await getSessionUserId();
  if (!userId) return { queued: false, analyzed: false };

  const [item] = await db
    .select({
      id: jobQueue.id,
      analyzedAt: jobQueue.analyzedAt,
    })
    .from(jobQueue)
    .where(
      and(
        eq(jobQueue.userId, userId),
        eq(jobQueue.postingId, jobPostingId),
      ),
    )
    .limit(1);

  if (!item) return { queued: false, analyzed: false };

  return {
    queued: true,
    queueId: item.id,
    analyzed: item.analyzedAt !== null,
  };
}

export async function getQueueItems(params: {
  offset: number;
  limit: number;
}): Promise<{ items: QueueEntry[]; total: number }> {
  const userId = await getSessionUserId();
  if (!userId) return { items: [], total: 0 };

  const [totalRow] = await db
    .select({ count: count() })
    .from(jobQueue)
    .where(eq(jobQueue.userId, userId));

  const total = totalRow?.count ?? 0;
  if (total === 0) return { items: [], total: 0 };

  const rows = await db
    .select({
      id: jobQueue.id,
      addedAt: jobQueue.addedAt,
      overlapScore: jobQueue.overlapScore,
      matchedKeywords: jobQueue.matchedKeywords,
      missingKeywords: jobQueue.missingKeywords,
      fitExplanation: jobQueue.fitExplanation,
      analyzedAt: jobQueue.analyzedAt,
      postingId: jobPosting.id,
      postingTitle: sql<string | null>`${jobPosting.titles}[1]`,
      postingSourceUrl: jobPosting.sourceUrl,
      companyId: company.id,
      companyName: company.name,
      companySlug: company.slug,
      companyIcon: company.icon,
    })
    .from(jobQueue)
    .innerJoin(jobPosting, eq(jobQueue.postingId, jobPosting.id))
    .innerJoin(company, eq(jobPosting.companyId, company.id))
    .where(eq(jobQueue.userId, userId))
    .orderBy(desc(jobQueue.addedAt))
    .offset(params.offset)
    .limit(params.limit);

  const items: QueueEntry[] = rows.map((r) => ({
    id: r.id,
    addedAt: r.addedAt.toISOString(),
    overlapScore: r.overlapScore,
    matchedKeywords: r.matchedKeywords ?? [],
    missingKeywords: r.missingKeywords ?? [],
    fitExplanation: r.fitExplanation,
    analyzedAt: r.analyzedAt?.toISOString() ?? null,
    posting: {
      id: r.postingId,
      title: r.postingTitle,
      sourceUrl: r.postingSourceUrl,
    },
    company: {
      id: r.companyId,
      name: r.companyName,
      slug: r.companySlug,
      icon: r.companyIcon,
    },
  }));

  return { items, total };
}

export async function analyzeQueueItem(
  queueId: string,
  postingId: string,
): Promise<void> {
  const userId = await getSessionUserId();
  if (!userId) throw new Error("Not authenticated");

  // Get user's resume keywords
  const [resume] = await db
    .select({ keywords: userResume.keywords })
    .from(userResume)
    .where(eq(userResume.userId, userId))
    .limit(1);

  if (!resume || !resume.keywords || resume.keywords.length === 0) {
    throw new Error("Resume not uploaded or keywords not extracted");
  }

  // Get job posting titles
  const [posting] = await db
    .select({
      titles: jobPosting.titles,
    })
    .from(jobPosting)
    .where(eq(jobPosting.id, postingId))
    .limit(1);

  if (!posting || !posting.titles) {
    throw new Error("Job posting not found");
  }

  // Extract keywords from job titles (for now, without full description from R2)
  const jobTitleText = posting.titles.join(" ");
  const jobKeywords = await extractKeywords(jobTitleText);

  // Calculate overlap
  const resumeKeywordSet = new Set(resume.keywords.map((k) => k.toLowerCase()));
  const jobKeywordSet = new Set(jobKeywords.map((k) => k.toLowerCase()));

  const matched = jobKeywords.filter((k) =>
    resumeKeywordSet.has(k.toLowerCase()),
  );

  const missing = jobKeywords.filter(
    (k) => !resumeKeywordSet.has(k.toLowerCase()),
  );

  const overlapScore =
    jobKeywordSet.size > 0 ? matched.length / jobKeywordSet.size : 0;

  // Generate fit explanation
  const matchPercentage = Math.round(overlapScore * 100);
  let fitExplanation = `${matchPercentage}% skill overlap. `;
  if (matched.length > 0) {
    fitExplanation += `Matched: ${matched.slice(0, 3).join(", ")}${matched.length > 3 ? `, +${matched.length - 3} more` : ""}. `;
  }
  if (missing.length > 0) {
    fitExplanation += `Missing: ${missing.slice(0, 3).join(", ")}${missing.length > 3 ? `, +${missing.length - 3} more` : ""}.`;
  }

  // Update queue item
  await db
    .update(jobQueue)
    .set({
      overlapScore: overlapScore,
      matchedKeywords: matched,
      missingKeywords: missing,
      fitExplanation: fitExplanation,
      analyzedAt: new Date(),
    })
    .where(
      and(
        eq(jobQueue.id, queueId),
        eq(jobQueue.userId, userId),
      ),
    );
}
