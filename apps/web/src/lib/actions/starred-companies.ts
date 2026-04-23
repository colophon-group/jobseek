"use server";

import { eq, and } from "drizzle-orm";
import { db } from "@/db";
import { followedCompany } from "@/db/schema";
import { getSessionUserId } from "@/lib/sessionCache";

export type ToggleResult = {
  starred: boolean;
};

export async function toggleStarredCompany(
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
    return { starred: false };
  }

  await db.insert(followedCompany).values({ userId, companyId });
  return { starred: true };
}

export async function getStarredCompanyIds(): Promise<string[]> {
  const userId = await getSessionUserId();
  if (!userId) return [];

  try {
    const rows = await db
      .select({ companyId: followedCompany.companyId })
      .from(followedCompany)
      .where(eq(followedCompany.userId, userId));
    return rows.map((r) => r.companyId);
  } catch {
    return [];
  }
}
