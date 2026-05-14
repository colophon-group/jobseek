"use server";

import { eq, and } from "drizzle-orm";
import { db } from "@/db";
import { followedCompany } from "@/db/schema";
import { getSessionUserId } from "@/lib/sessionCache";
import { isUniqueViolation } from "@/lib/db-conflict";

export type ToggleResult = {
  starred: boolean;
};

/**
 * UNIQUE index name behind `(user_id, company_id)` on
 * `followed_company` (see `apps/web/src/db/schema.ts`). Used to scope
 * the race-recovery branch in `toggleStarredCompany`.
 */
const FOLLOWED_COMPANY_UNIQUE_CONSTRAINT = "idx_fc_user_company";

/**
 * Toggle a (user, company) follow row.
 *
 * #3179 — same SELECT-then-INSERT-OR-DELETE race as `toggleSavedJob`,
 * fixed with the same retry-on-conflict shape (matches #3268). The
 * UNIQUE constraint `idx_fc_user_company` is the source of truth.
 *
 * See `toggleSavedJob` for the full reasoning; this is the parallel
 * implementation on `followed_company`.
 */
export async function toggleStarredCompany(
  companyId: string,
): Promise<ToggleResult> {
  const userId = await getSessionUserId();
  if (!userId) throw new Error("Not authenticated");

  try {
    await db.insert(followedCompany).values({ userId, companyId });
    return { starred: true };
  } catch (err) {
    if (!isUniqueViolation(err, FOLLOWED_COMPANY_UNIQUE_CONSTRAINT)) throw err;
    await db
      .delete(followedCompany)
      .where(
        and(
          eq(followedCompany.userId, userId),
          eq(followedCompany.companyId, companyId),
        ),
      );
    return { starred: false };
  }
}

export async function getStarredCompanyIds(): Promise<string[]> {
  const userId = await getSessionUserId();
  if (!userId) return [];

  const rows = await db
    .select({ companyId: followedCompany.companyId })
    .from(followedCompany)
    .where(eq(followedCompany.userId, userId));

  return rows.map((r) => r.companyId);
}
