import "server-only";

import { and, asc, eq, inArray, sql } from "drizzle-orm";
import { db } from "@/db";
import { aiFilterConfiguration, aiFilterQueryVersion, aiFilterSegment } from "@/db/schema";
import { paidEntitlementCondition } from "@/lib/paid-entitlement";
import { assertAiFilterCandidateScope } from "./candidate-loader";
import { AiFilterEntitlementError, AiFilterNotFoundError, putAiFilterConfiguration } from "./configuration-service";
import { AI_FILTER_MAX_FRESHNESS_SEGMENTS_PROJECT, canRunAiFilter, readAiFilterRuntimePolicy } from "./policy";
import { startAiFilterCatchup } from "./workflow-trigger";

const REFRESH_PAGE_SIZE = 20;
const DISPATCH_INTERVAL_MS = 45_000;
const SWEEP_WORK_BUDGET_MS = 45_000;

type RefreshTarget = {
  configurationId: string;
  ownerId: string;
  watchlistId: string;
  queryVersionId: string;
  query: string;
  candidateLanguages: string[];
};

/** Claim oldest dispatches first; a crash before Workflow start retries next minute. */
export async function claimAiFilterRefreshTargets(now: Date, limit = REFRESH_PAGE_SIZE): Promise<RefreshTarget[]> {
  return db.transaction(async tx => {
    const targets = await tx.select({
      configurationId: aiFilterConfiguration.id,
      ownerId: aiFilterConfiguration.ownerId,
      watchlistId: aiFilterConfiguration.watchlistId,
      queryVersionId: aiFilterQueryVersion.id,
      query: aiFilterQueryVersion.queryText,
      candidateLanguages: aiFilterQueryVersion.candidateLanguages,
    }).from(aiFilterConfiguration).innerJoin(aiFilterQueryVersion, and(
      eq(aiFilterQueryVersion.configurationId, aiFilterConfiguration.id),
      eq(aiFilterQueryVersion.revision, aiFilterConfiguration.currentRevision),
    )).where(and(
      eq(aiFilterConfiguration.status, "enabled"),
      sql`(SELECT count(*) FROM ${aiFilterSegment} WHERE ${aiFilterSegment.kind} = 'freshness' AND ${aiFilterSegment.status} = 'processing' AND ${aiFilterSegment.leaseExpiresAt} > ${now.toISOString()}::timestamptz) < ${AI_FILTER_MAX_FRESHNESS_SEGMENTS_PROJECT}`,
      paidEntitlementCondition(aiFilterConfiguration.ownerId, now),
      sql`(${aiFilterConfiguration.refreshRequestedAt} IS NULL OR ${aiFilterConfiguration.refreshRequestedAt} < ${new Date(now.getTime() - DISPATCH_INTERVAL_MS).toISOString()}::timestamptz)`,
      sql`NOT EXISTS (SELECT 1 FROM ${aiFilterSegment} WHERE ${aiFilterSegment.watchlistId} = ${aiFilterConfiguration.watchlistId} AND ${aiFilterSegment.kind} = 'freshness' AND ${aiFilterSegment.status} = 'processing' AND ${aiFilterSegment.leaseExpiresAt} > ${now.toISOString()}::timestamptz)`,
    )).orderBy(
      sql`${aiFilterConfiguration.refreshRequestedAt} ASC NULLS FIRST`,
      asc(aiFilterConfiguration.id),
    ).limit(Math.min(limit, REFRESH_PAGE_SIZE)).for("update", { of: aiFilterConfiguration, skipLocked: true });
    if (targets.length) {
      await tx.update(aiFilterConfiguration).set({ refreshRequestedAt: now })
        .where(inArray(aiFilterConfiguration.id, targets.map(target => target.configurationId)));
    }
    return targets;
  });
}

const dependencies = {
  claim: claimAiFilterRefreshTargets,
  assertScope: assertAiFilterCandidateScope,
  configure: putAiFilterConfiguration,
  start: startAiFilterCatchup,
};

/** Scheduled matching is authorized by the saved, enabled narrowing request. */
export async function runAiFilterRefreshSweep(input: {
  now?: Date;
  dependencies?: typeof dependencies;
} = {}) {
  const counters = { claimed: 0, started: 0, deferred: 0, failed: 0 };
  const policy = readAiFilterRuntimePolicy();
  if (process.env.VERCEL_ENV !== "production" || !canRunAiFilter(policy) ||
    !process.env.TYPESAFE_AI_TOKEN?.trim() ||
    Buffer.byteLength(process.env.AI_FILTER_CACHE_HMAC_SECRET ?? "", "utf8") < 32) {
    return { status: "off" as const, ...counters };
  }
  const now = input.now ?? new Date();
  const deps = input.dependencies ?? dependencies;
  const deadline = Date.now() + SWEEP_WORK_BUDGET_MS;
  // Prepare one target at a time. A timed-out invocation must not mark a
  // whole page dispatched and repeatedly starve its later watchlists.
  while (counters.claimed < REFRESH_PAGE_SIZE && Date.now() < deadline) {
    const targets = await deps.claim(now, 1);
    if (!targets.length) break;
    counters.claimed += targets.length;
    for (const target of targets) {
      try {
        await deps.assertScope({
          ownerId: target.ownerId, watchlistId: target.watchlistId,
          candidateLanguages: target.candidateLanguages,
          signal: AbortSignal.timeout(Math.max(1, deadline - Date.now())),
        });
        const state = await deps.configure({
          ownerId: target.ownerId, watchlistId: target.watchlistId,
          query: target.query, candidateLanguages: target.candidateLanguages,
          expectedEnabledQueryVersionId: target.queryVersionId, now,
        });
        await deps.start({
          ownerId: target.ownerId, watchlistId: target.watchlistId,
          queryVersionId: state.queryVersionId, kind: "freshness",
          demandTargetOffset: 10_000,
        });
        counters.started += 1;
      } catch (error) {
        if (error instanceof AiFilterNotFoundError || error instanceof AiFilterEntitlementError || error instanceof TypeError) {
          counters.deferred += 1;
        } else {
          counters.failed += 1;
        }
      }
    }
  }
  // Aggregate evidence only: queries and owner IDs never reach cron logs.
  return { status: "completed" as const, ...counters };
}
