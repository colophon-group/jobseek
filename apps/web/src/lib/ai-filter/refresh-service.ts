import "server-only";
import { and, asc, eq, inArray, sql } from "drizzle-orm";
import { db } from "@/db";
import { aiFilterConfiguration, aiFilterQueryVersion } from "@/db/schema";
import { narrowedWorkerPaused } from "@/lib/service-availability";
import { paidEntitlementCondition } from "@/lib/paid-entitlement";
import {
  loadAiFilterCandidatePage,
  countAiFilterCandidates,
} from "./candidate-loader";
import {
  AiFilterNotFoundError,
  AiFilterEntitlementError,
  ensureAiFilterConfiguration,
} from "./configuration-service";
import { readAiFilterRuntimePolicy } from "./policy";
import { runAiFilterCatchupStep } from "./catchup-service";
import { aiFilterHistoricalHorizonStart, aiFilterHorizonEnd } from "./horizon";
import { aiFilterRepairDueCondition } from "./refresh-cadence";
import {
  claimAiFilterWorker,
  claimAiFilterWork,
  enqueueAiFilterWork,
  finishAiFilterWork,
  releaseAiFilterWorker,
  markAiFilterRepairCaughtUp,
} from "./work-queue";

const MAX_WORK_ITEMS = 5;

type RefreshTarget = {
  configurationId: string;
  ownerId: string;
  watchlistId: string;
  queryVersionId: string;
  query: string;
  candidateLanguages: string[];
};

/** Bounded rotating repair covers missed index signals, late indexing and expiry. */
export async function claimAiFilterRefreshTargets(
  now: Date,
  limit = 1,
): Promise<RefreshTarget[]> {
  return db.transaction(async (tx) => {
    const targets = await tx
      .select({
        configurationId: aiFilterConfiguration.id,
        ownerId: aiFilterConfiguration.ownerId,
        watchlistId: aiFilterConfiguration.watchlistId,
        queryVersionId: aiFilterQueryVersion.id,
        query: aiFilterQueryVersion.queryText,
        candidateLanguages: aiFilterQueryVersion.candidateLanguages,
      })
      .from(aiFilterConfiguration)
      .innerJoin(
        aiFilterQueryVersion,
        and(
          eq(aiFilterQueryVersion.configurationId, aiFilterConfiguration.id),
          eq(
            aiFilterQueryVersion.revision,
            aiFilterConfiguration.currentRevision,
          ),
        ),
      )
      .where(
        and(
          eq(aiFilterConfiguration.status, "enabled"),
          paidEntitlementCondition(aiFilterConfiguration.ownerId, now),
          aiFilterRepairDueCondition(aiFilterConfiguration.refreshRequestedAt, now),
        ),
      )
      .orderBy(
        sql`${aiFilterConfiguration.refreshRequestedAt} ASC NULLS FIRST`,
        asc(aiFilterConfiguration.id),
      )
      .limit(Math.min(2, limit))
      .for("update", { of: aiFilterConfiguration, skipLocked: true });
    if (targets.length)
      await tx
        .update(aiFilterConfiguration)
        .set({ refreshRequestedAt: now })
        .where(
          inArray(
            aiFilterConfiguration.id,
            targets.map((target) => target.configurationId),
          ),
        );
    return targets;
  });
}

const dependencies = {
  claimWorker: claimAiFilterWorker,
  releaseWorker: releaseAiFilterWorker,
  repair: claimAiFilterRefreshTargets,
  assertScope: countAiFilterCandidates,
  configure: ensureAiFilterConfiguration,
  load: loadAiFilterCandidatePage,
  caughtUp: markAiFilterRepairCaughtUp,
  enqueue: enqueueAiFilterWork,
  claim: claimAiFilterWork,
  finish: finishAiFilterWork,
  step: runAiFilterCatchupStep,
};

/** One project-wide bounded time slice. No fanout, detached promises or Workflow runs. */
export async function runAiFilterRefreshSweep(
  input: { dependencies?: typeof dependencies } = {},
) {
  const counters = { claimed: 0, started: 0, deferred: 0, failed: 0 };
  const policy = readAiFilterRuntimePolicy();
  if (
    process.env.VERCEL_ENV !== "production" ||
    narrowedWorkerPaused() ||
    !policy.enabled ||
    !policy.routeEnabled ||
    !process.env.TYPESAFE_AI_TOKEN?.trim() ||
    Buffer.byteLength(process.env.AI_FILTER_CACHE_HMAC_SECRET ?? "", "utf8") <
      32
  ) {
    return { status: "off" as const, ...counters };
  }
  const deps = input.dependencies ?? dependencies;
  const leaseOwner = await deps.claimWorker();
  if (!leaseOwner) return { status: "completed" as const, ...counters };
  const deadline = Date.now() + 42_000;
  const signal = AbortSignal.timeout(42_000);
  try {
    const verified = new Set<string>();
    // Five candidates keep each provider call bounded. A time slice may advance
    // several checkpoints but always yields to the next lane/oldest work item.
    while (
      counters.claimed < MAX_WORK_ITEMS &&
      Date.now() + 25_000 < deadline
    ) {
      const target = await deps.claim(leaseOwner);
      if (!target) break;
      counters.claimed += 1;
      try {
        const scopeKey = `${target.queryVersionId}:${target.scopeVersion ?? ""}`;
        if (target.query && !verified.has(scopeKey)) {
          const total = await deps.assertScope({
            ownerId: target.ownerId,
            watchlistId: target.watchlistId,
            candidateLanguages: target.candidateLanguages,
            signal,
          });
          if (total > 10_000) {
            await deps.finish(target, "paused_kill", "scope_too_broad");
            counters.deferred += 1;
            continue;
          }
          const current = await deps.configure({
            ownerId: target.ownerId,
            watchlistId: target.watchlistId,
            query: target.query,
            candidateLanguages: target.candidateLanguages,
            expectedEnabledQueryVersionId: target.queryVersionId,
          });
          verified.add(scopeKey);
          if (current.queryVersionId !== target.queryVersionId) {
            await deps.finish(target, "disabled");
            continue;
          }
        }
        const result = await deps.step({
          ...target,
          candidateLimit: 5,
          signal,
        });
        await deps.finish(target, result.status);
        if (
          result.status.startsWith("paused_") &&
          result.status !== "paused_entitlement" &&
          result.status !== "paused_budget"
        )
          counters.failed += 1;
        counters.started += result.segmentId ? 1 : 0;
      } catch {
        await deps.finish(target, "failed", "worker_unavailable");
        counters.failed += 1;
      }
    }
    if (Date.now() + 25_000 < deadline) {
      // Repair yields to admitted owner demand and never consumes its first-page budget.
      for (const target of await deps.repair(new Date(), 1)) {
        try {
          const total = await deps.assertScope({
            ownerId: target.ownerId,
            watchlistId: target.watchlistId,
            candidateLanguages: target.candidateLanguages,
            signal,
          });
          if (total > 10_000) throw new TypeError("ineligible");
          const state = await deps.configure({
            ...target,
            expectedEnabledQueryVersionId: target.queryVersionId,
          });
          const page = await deps.load({
            ownerId: target.ownerId,
            watchlistId: target.watchlistId,
            offset: 0,
            windowStart: aiFilterHistoricalHorizonStart(),
            windowEnd: aiFilterHorizonEnd(new Date()),
            onlyUndecidedForQueryVersionId: state.queryVersionId,
            candidateLimit: 1,
            signal,
          });
          if (page.scannedCount)
            await deps.enqueue({
              ownerId: target.ownerId,
              watchlistId: target.watchlistId,
              queryVersionId: state.queryVersionId,
              kind: "freshness",
              demandTargetOffset: 10_000,
            });
          else
            await deps.caughtUp({
              watchlistId: target.watchlistId,
              queryVersionId: state.queryVersionId,
            });
        } catch (error) {
          if (
            error instanceof TypeError ||
            error instanceof AiFilterNotFoundError ||
            error instanceof AiFilterEntitlementError
          )
            counters.deferred += 1;
          else counters.failed += 1;
        }
      }
    }
    return { status: "completed" as const, ...counters };
  } catch (error) {
    counters.failed += 1;
    throw error;
  } finally {
    await deps.releaseWorker(leaseOwner, counters.failed > 0);
  }
}
