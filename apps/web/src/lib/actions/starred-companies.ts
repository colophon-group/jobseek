"use server";

import { observeCompanySelection } from "@/lib/company-selection-telemetry";

import { eq, and } from "drizzle-orm";
import { db } from "@/db";
import { followedCompany } from "@/db/schema";
import { getSessionUserId } from "@/lib/sessionCache";
import { normalizeWatchlistUuid } from "@/lib/services/watchlist-input";
import {
  companyReferenceErrorResult,
  lockCompanyStar,
  prepareCompanyReferences,
  persistCompanyReferences,
} from "@/lib/services/company-references";

export type ToggleResult = {
  starred: boolean;
} | { error: string };

/**
 * Delete first, so removing an existing selection never needs search. An
 * absent selection is prepared outside database locks, then rechecked under
 * the same advisory lock before applying exactly one toggle. Concurrent
 * requests therefore preserve toggle parity without network I/O in a lock.
 */
async function toggleStarredCompanyObserved(
  companyId: string,
): Promise<ToggleResult> {
  const userId = await getSessionUserId();
  if (!userId) throw new Error("Not authenticated");
  const normalized = normalizeWatchlistUuid(companyId);
  if (!normalized) return { error: "invalid_company" };
  companyId = normalized;

  const removeExisting = async (tx: Parameters<Parameters<typeof db.transaction>[0]>[0]) => {
    await lockCompanyStar(tx, userId, companyId);
    const removed = await tx.delete(followedCompany).where(and(
      eq(followedCompany.userId, userId), eq(followedCompany.companyId, companyId),
    )).returning({ companyId: followedCompany.companyId });
    return removed.length > 0;
  };

  if (await db.transaction(removeExisting)) return { starred: false };

  try {
    const references = await prepareCompanyReferences([companyId]);
    return await db.transaction(async (tx) => {
      if (await removeExisting(tx)) return { starred: false };
      await persistCompanyReferences(tx, references);
      await tx.insert(followedCompany).values({ userId, companyId });
      return { starred: true };
    });
  } catch (err) {
    return companyReferenceErrorResult(err);
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

export async function toggleStarredCompany(companyId: string): Promise<ToggleResult> {
  return observeCompanySelection("star", () => toggleStarredCompanyObserved(companyId));
}
