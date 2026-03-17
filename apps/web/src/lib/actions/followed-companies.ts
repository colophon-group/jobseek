"use server";

import { eq, and } from "drizzle-orm";
import { db } from "@/db";
import { followedCompany } from "@/db/schema";
import { getSessionUserId } from "@/lib/sessionCache";
import { canFollowMore } from "@/lib/plans";

export type ToggleResult = {
  followed: boolean;
  limitReached?: boolean;
  current?: number;
  max?: number;
};

export async function toggleFollowedCompany(
  companyId: string,
): Promise<ToggleResult> {
  const userId = await getSessionUserId();
  if (!userId) throw new Error("Not authenticated");

  const [existing] = await db
    .select({ id: followedCompany.id })
    .from(followedCompany)
    .where(
      and(
        eq(followedCompany.userId, userId),
        eq(followedCompany.companyId, companyId),
      ),
    )
    .limit(1);

  if (existing) {
    await db.delete(followedCompany).where(eq(followedCompany.id, existing.id));
    return { followed: false };
  }

  const { allowed, current, max } = await canFollowMore(userId);
  if (!allowed) {
    return { followed: false, limitReached: true, current, max };
  }

  await db.insert(followedCompany).values({ userId, companyId });
  return { followed: true };
}

export async function getFollowedCompanyIds(): Promise<string[]> {
  const userId = await getSessionUserId();
  if (!userId) return [];

  const rows = await db
    .select({ companyId: followedCompany.companyId })
    .from(followedCompany)
    .where(eq(followedCompany.userId, userId));

  return rows.map((r) => r.companyId);
}
