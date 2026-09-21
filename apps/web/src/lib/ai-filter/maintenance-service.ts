import "server-only";

import { and, eq, inArray, lte, notExists } from "drizzle-orm";

import { db } from "@/db";
import {
  aiFilterDecision,
  aiFilterGlobalCache,
} from "@/db/schema";

const CLEANUP_BATCH = 500;

export async function cleanupAiFilterRetention(input: { now?: Date } = {}) {
  const now = input.now ?? new Date();
  return db.transaction(async (tx) => {
    const expiredDecisions = await tx
      .select({ id: aiFilterDecision.id })
      .from(aiFilterDecision)
      .where(lte(aiFilterDecision.expiresAt, now))
      .orderBy(aiFilterDecision.expiresAt, aiFilterDecision.id)
      .limit(CLEANUP_BATCH);
    if (expiredDecisions.length > 0) {
      await tx
        .delete(aiFilterDecision)
        .where(inArray(
          aiFilterDecision.id,
          expiredDecisions.map((row) => row.id),
        ));
    }

    const expiredCache = await tx
      .select({ cacheKey: aiFilterGlobalCache.cacheKey })
      .from(aiFilterGlobalCache)
      .where(and(
        lte(aiFilterGlobalCache.expiresAt, now),
        notExists(
          tx
            .select({ id: aiFilterDecision.id })
            .from(aiFilterDecision)
            .where(eq(aiFilterDecision.cacheKey, aiFilterGlobalCache.cacheKey)),
        ),
      ))
      .orderBy(aiFilterGlobalCache.expiresAt, aiFilterGlobalCache.cacheKey)
      .limit(CLEANUP_BATCH);
    if (expiredCache.length > 0) {
      await tx
        .delete(aiFilterGlobalCache)
        .where(inArray(
          aiFilterGlobalCache.cacheKey,
          expiredCache.map((row) => row.cacheKey),
        ));
    }
    return {
      decisionsDeleted: expiredDecisions.length,
      cacheEntriesDeleted: expiredCache.length,
      hasMore:
        expiredDecisions.length === CLEANUP_BATCH ||
        expiredCache.length === CLEANUP_BATCH,
    };
  });
}
