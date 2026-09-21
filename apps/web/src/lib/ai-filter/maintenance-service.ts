import "server-only";

import { and, asc, eq, gt, inArray, isNull, lte, notExists, or } from "drizzle-orm";

import { db } from "@/db";
import {
  aiFilterConfiguration,
  aiFilterDecision,
  aiFilterGlobalCache,
  subscription,
} from "@/db/schema";
import { startAiFilterCatchup } from "./workflow-trigger";

const CLEANUP_BATCH = 500;
const SWEEP_BATCH = 100;

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

async function mapWithConcurrency<T, R>(
  values: readonly T[],
  concurrency: number,
  mapper: (value: T) => Promise<R>,
): Promise<R[]> {
  const results = new Array<R>(values.length);
  let next = 0;
  await Promise.all(Array.from(
    { length: Math.min(concurrency, values.length) },
    async () => {
      while (next < values.length) {
        const index = next++;
        results[index] = await mapper(values[index]!);
      }
    },
  ));
  return results;
}

async function claimSweepBatch(now: Date) {
  return db.transaction(async (tx) => {
    const configurations = await tx
      .select({
        id: aiFilterConfiguration.id,
        ownerId: aiFilterConfiguration.ownerId,
        watchlistId: aiFilterConfiguration.watchlistId,
      })
      .from(aiFilterConfiguration)
      .innerJoin(
        subscription,
        and(
          eq(subscription.userId, aiFilterConfiguration.ownerId),
          eq(subscription.status, "active"),
          eq(subscription.plan, "unlimited"),
          or(isNull(subscription.endsAt), gt(subscription.endsAt, now)),
        ),
      )
      .where(eq(aiFilterConfiguration.status, "enabled"))
      .orderBy(
        asc(aiFilterConfiguration.lastSweepAt),
        aiFilterConfiguration.id,
      )
      .limit(SWEEP_BATCH)
      .for("update", { of: aiFilterConfiguration, skipLocked: true });

    if (configurations.length > 0) {
      await tx
        .update(aiFilterConfiguration)
        .set({ lastSweepAt: now })
        .where(inArray(
          aiFilterConfiguration.id,
          configurations.map((configuration) => configuration.id),
        ));
    }
    return configurations;
  });
}

/** Bounded hourly sweep; database/workflow idempotency coalesces other triggers. */
export async function sweepAiFilterCatchup(input: { now?: Date } = {}) {
  const now = input.now ?? new Date();
  const configurations = await claimSweepBatch(now);

  const starts = await mapWithConcurrency(configurations, 10, async (configuration) => {
    try {
      const workflow = await startAiFilterCatchup(configuration);
      return { status: "started" as const, runId: workflow.runId };
    } catch {
      // Keep sweep output bounded and content-free. A later sweep retries the
      // same durable configuration; DB singleflight prevents paid duplicates.
      return { status: "failed" as const };
    }
  });
  return {
    selected: configurations.length,
    started: starts.filter((result) => result.status === "started").length,
    failed: starts.filter((result) => result.status === "failed").length,
    hasMore: configurations.length === SWEEP_BATCH,
  };
}
