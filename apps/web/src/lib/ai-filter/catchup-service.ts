import "server-only";

import { randomUUID } from "node:crypto";
import { and, desc, eq, gt, isNull, lte, or, sql } from "drizzle-orm";

import { db } from "@/db";
import {
  aiFilterConfiguration,
  aiFilterEvent,
  aiFilterQueryVersion,
  aiFilterSegment,
  subscription,
} from "@/db/schema";
import { loadAiFilterCandidatePage, AiFilterCandidateLoadError } from "./candidate-loader";
import { JevClient } from "./jev-client";
import { executeAiFilterSegment } from "./orchestrator";
import { PostgresAiFilterExecutionRepository } from "./postgres-repository";
import {
  canRunAiFilter,
  readAiFilterRuntimePolicy,
} from "./policy";
import { aiFilterHistoricalHorizonStart } from "./horizon";

const SEGMENT_LEASE_MS = 5 * 60 * 1_000;

export type AiFilterCatchupStepResult = Readonly<{
  status:
    | "continue"
    | "demand_satisfied"
    | "caught_up"
    | "busy"
    | "disabled"
    | "paused_entitlement"
    | "paused_budget"
    | "paused_provider"
    | "paused_kill";
  segmentId: string | null;
}>;

async function entitled(ownerId: string, now: Date): Promise<boolean> {
  const [row] = await db
    .select({ id: subscription.id })
    .from(subscription)
    .where(and(
      eq(subscription.userId, ownerId),
      eq(subscription.status, "active"),
      eq(subscription.plan, "unlimited"),
      or(isNull(subscription.endsAt), gt(subscription.endsAt, now)),
    ))
    .limit(1);
  return Boolean(row);
}

async function pauseSegment(input: {
  segmentId: string;
  ownerId: string;
  watchlistId: string;
  queryVersionId: string;
  leaseOwner: string;
  status: "paused_entitlement" | "paused_provider" | "paused_kill";
  reason: string;
  now: Date;
}): Promise<void> {
  await db.transaction(async (tx) => {
    const updated = await tx
      .update(aiFilterSegment)
      .set({
        status: input.status,
        stopReason: input.reason,
        leaseOwner: null,
        leaseExpiresAt: null,
        updatedAt: input.now,
      })
      .where(and(
        eq(aiFilterSegment.id, input.segmentId),
        eq(aiFilterSegment.ownerId, input.ownerId),
        eq(aiFilterSegment.status, "processing"),
        eq(aiFilterSegment.leaseOwner, input.leaseOwner),
      ))
      .returning({ id: aiFilterSegment.id });
    if (updated.length !== 1) return;
    await tx.insert(aiFilterEvent).values({
      watchlistId: input.watchlistId,
      ownerId: input.ownerId,
      queryVersionId: input.queryVersionId,
      type: input.status,
      payload: { stopReason: input.reason },
      idempotencyKey: `segment:${input.segmentId}:${input.status}:${input.reason}`,
      createdAt: input.now,
    }).onConflictDoNothing();
  });
}

async function claimSegment(input: {
  ownerId: string;
  watchlistId: string;
  leaseOwner: string;
  executionEnabled: boolean;
  hasEntitlement: boolean;
  maxSegmentsPerUser: number;
  maxSegmentsPerProject: number;
  demandTargetOffset: number;
  now: Date;
}) {
  return db.transaction(async (tx) => {
    await tx.execute(
      sql`SELECT pg_advisory_xact_lock(hashtextextended(${`ai-filter-catchup:${input.watchlistId}`}, 619_907))`,
    );
    const [current] = await tx
      .select({
        configurationId: aiFilterConfiguration.id,
        configurationStatus: aiFilterConfiguration.status,
        ownerId: aiFilterConfiguration.ownerId,
        queryVersionId: aiFilterQueryVersion.id,
        queryText: aiFilterQueryVersion.queryText,
        horizonStartedAt: aiFilterQueryVersion.horizonStartedAt,
        horizonEndsAt: aiFilterQueryVersion.horizonEndsAt,
        lastCaughtUpAt: aiFilterConfiguration.lastCaughtUpAt,
        lastSweepAt: aiFilterConfiguration.lastSweepAt,
      })
      .from(aiFilterConfiguration)
      .innerJoin(
        aiFilterQueryVersion,
        and(
          eq(aiFilterQueryVersion.configurationId, aiFilterConfiguration.id),
          eq(aiFilterQueryVersion.revision, aiFilterConfiguration.currentRevision),
        ),
      )
      .where(and(
        eq(aiFilterConfiguration.watchlistId, input.watchlistId),
        eq(aiFilterConfiguration.ownerId, input.ownerId),
      ))
      .limit(1);
    if (!current) return { kind: "disabled" as const, segment: null };
    if (current.configurationStatus !== "enabled") {
      return { kind: "disabled" as const, segment: null };
    }

    const [active] = await tx
      .select()
      .from(aiFilterSegment)
      .where(and(
        eq(aiFilterSegment.watchlistId, input.watchlistId),
        eq(aiFilterSegment.queryVersionId, current.queryVersionId),
        sql`${aiFilterSegment.status} IN ('pending', 'processing', 'paused_entitlement', 'paused_budget', 'paused_provider', 'paused_kill')`,
      ))
      .orderBy(desc(aiFilterSegment.createdAt))
      .limit(1);
    if (
      active?.status === "processing" &&
      active.leaseExpiresAt &&
      active.leaseExpiresAt.getTime() > input.now.getTime()
    ) {
      return { kind: "busy" as const, segment: active };
    }

    const [previous] = active
      ? [undefined]
      : await tx
          .select()
          .from(aiFilterSegment)
          .where(and(
            eq(aiFilterSegment.watchlistId, input.watchlistId),
            eq(aiFilterSegment.queryVersionId, current.queryVersionId),
          ))
          .orderBy(desc(aiFilterSegment.createdAt))
          .limit(1);
    const [historicalFoundation] = await tx
      .select({ id: aiFilterSegment.id })
      .from(aiFilterSegment)
      .where(and(
        eq(aiFilterSegment.watchlistId, input.watchlistId),
        eq(aiFilterSegment.queryVersionId, current.queryVersionId),
        eq(aiFilterSegment.status, "caught_up"),
        lte(aiFilterSegment.windowStart, current.horizonStartedAt),
      ))
      .limit(1);
    const previousCoversQueryHorizon = Boolean(
      previous?.status === "caught_up" &&
      historicalFoundation &&
      current.lastCaughtUpAt &&
      current.lastCaughtUpAt.getTime() >= current.horizonEndsAt.getTime() &&
      previous.windowEnd.getTime() >= current.horizonEndsAt.getTime(),
    );
    if (
      !active &&
      previous?.status === "caught_up" &&
      previousCoversQueryHorizon &&
      current.lastSweepAt
    ) {
      return { kind: "caught_up" as const, segment: previous };
    }
    if (
      !active &&
      previous?.status === "completed" &&
      !previous.idempotencyKey.startsWith("repair:") &&
      previous.selectionOffset + previous.scannedCount >= input.demandTargetOffset
    ) {
      return { kind: "demand_satisfied" as const, segment: previous };
    }

    await tx.execute(
      sql`SELECT pg_advisory_xact_lock(hashtextextended(${`ai-filter-user-capacity:${input.ownerId}`}, 619_907))`,
    );
    await tx.execute(
      sql`SELECT pg_advisory_xact_lock(hashtextextended('ai-filter-project-capacity', 619_907))`,
    );
    const [[userCapacity], [projectCapacity]] = await Promise.all([
      tx
        .select({ count: sql<number>`count(*)::integer` })
        .from(aiFilterSegment)
        .where(and(
          eq(aiFilterSegment.ownerId, input.ownerId),
          eq(aiFilterSegment.status, "processing"),
          gt(aiFilterSegment.leaseExpiresAt, input.now),
        )),
      tx
        .select({ count: sql<number>`count(*)::integer` })
        .from(aiFilterSegment)
        .where(and(
          eq(aiFilterSegment.status, "processing"),
          gt(aiFilterSegment.leaseExpiresAt, input.now),
        )),
    ]);
    if (
      (userCapacity?.count ?? 0) >= input.maxSegmentsPerUser ||
      (projectCapacity?.count ?? 0) >= input.maxSegmentsPerProject
    ) {
      return { kind: "busy" as const, segment: active ?? null };
    }

    let segment = active;
    if (!segment) {
      // Older deployments created 30-day segment windows even when the
      // immutable query revision already described the full historical
      // horizon. Expanding the window backwards is cursor-safe under the
      // newest-first order: the previously scanned prefix is unchanged and
      // the older jobs are appended after it. Do not let that stale segment
      // mark the larger query horizon caught up.
      const continuesOpenWindow = Boolean(
        previous?.status === "completed" && previous.scannedCount > 0,
      );
      const repairsMissingDecisions = Boolean(
        previous?.status === "caught_up" &&
        previousCoversQueryHorizon &&
        !current.lastSweepAt,
      );
      const continuesRepair = Boolean(
        continuesOpenWindow && previous?.idempotencyKey.startsWith("repair:"),
      );
      const advancesFreshWindow = Boolean(
        previous?.status === "caught_up" &&
        historicalFoundation &&
        current.lastCaughtUpAt &&
        current.lastCaughtUpAt.getTime() < current.horizonEndsAt.getTime(),
      );
      const continuesHistoricalBackfill = Boolean(
        previous?.status === "caught_up" &&
        !historicalFoundation &&
        previous.scannedCount > 0,
      );
      const windowEnd = continuesOpenWindow
        ? previous!.windowEnd
        : current.horizonEndsAt;
      const windowStart = continuesOpenWindow
        ? previous!.windowStart
        : repairsMissingDecisions
          ? current.horizonStartedAt
        : advancesFreshWindow
          ? current.lastCaughtUpAt!
          : current.horizonStartedAt;
      const selectionOffset = continuesOpenWindow || continuesHistoricalBackfill
        ? previous!.selectionOffset + previous!.scannedCount
        : 0;
      const segmentId = randomUUID();
      const initialStatus = !input.hasEntitlement
        ? "paused_entitlement"
        : !input.executionEnabled
          ? "paused_kill"
          : "processing";
      const [inserted] = await tx.insert(aiFilterSegment).values({
        id: segmentId,
        watchlistId: input.watchlistId,
        ownerId: input.ownerId,
        queryVersionId: current.queryVersionId,
        status: initialStatus,
        selectionOffset,
        scannedCount: 0,
        selectionSnapshot: [],
        windowStart,
        windowEnd,
        cursor: 0,
        attempt: initialStatus === "processing" ? 1 : 0,
        leaseOwner: initialStatus === "processing" ? input.leaseOwner : null,
        leaseExpiresAt: initialStatus === "processing"
          ? new Date(input.now.getTime() + SEGMENT_LEASE_MS)
          : null,
        stopReason: initialStatus === "paused_entitlement"
          ? "entitlement_unavailable"
          : initialStatus === "paused_kill"
            ? "kill_switch_active"
            : null,
        idempotencyKey: repairsMissingDecisions || continuesRepair
          ? `repair:${current.queryVersionId}:${windowStart.toISOString()}:${windowEnd.toISOString()}:${selectionOffset}`
          : `${current.queryVersionId}:${windowStart.toISOString()}:${windowEnd.toISOString()}:${selectionOffset}`,
        startedAt: initialStatus === "processing" ? input.now : null,
        createdAt: input.now,
        updatedAt: input.now,
      }).returning();
      segment = inserted;
    } else if (!input.hasEntitlement) {
      await tx
        .update(aiFilterSegment)
        .set({
          status: "paused_entitlement",
          stopReason: "entitlement_unavailable",
          leaseOwner: null,
          leaseExpiresAt: null,
          updatedAt: input.now,
        })
        .where(eq(aiFilterSegment.id, segment.id));
      return { kind: "paused_entitlement" as const, segment };
    } else if (!input.executionEnabled) {
      await tx
        .update(aiFilterSegment)
        .set({
          status: "paused_kill",
          stopReason: "kill_switch_active",
          leaseOwner: null,
          leaseExpiresAt: null,
          updatedAt: input.now,
        })
        .where(eq(aiFilterSegment.id, segment.id));
      return { kind: "paused_kill" as const, segment };
    } else {
      const [claimed] = await tx
        .update(aiFilterSegment)
        .set({
          status: "processing",
          stopReason: null,
          leaseOwner: input.leaseOwner,
          leaseExpiresAt: new Date(input.now.getTime() + SEGMENT_LEASE_MS),
          attempt: sql`${aiFilterSegment.attempt} + 1`,
          startedAt: segment.startedAt ?? input.now,
          updatedAt: input.now,
        })
        .where(eq(aiFilterSegment.id, segment.id))
        .returning();
      segment = claimed;
    }
    if (!segment) throw new Error("AI filter segment claim failed");
    if (segment.status === "paused_entitlement") {
      return { kind: "paused_entitlement" as const, segment };
    }
    if (segment.status === "paused_kill") {
      return { kind: "paused_kill" as const, segment };
    }
    return {
      kind: "claimed" as const,
      segment,
      queryText: current.queryText,
      configurationId: current.configurationId,
    };
  });
}

/** One durable workflow step; the workflow loops only when this returns continue. */
export async function runAiFilterCatchupStep(input: {
  ownerId: string;
  watchlistId: string;
  leaseOwner: string;
  demandTargetOffset: number;
  now?: Date;
  signal?: AbortSignal;
}): Promise<AiFilterCatchupStepResult> {
  if (
    !Number.isSafeInteger(input.demandTargetOffset) ||
    input.demandTargetOffset < 1 ||
    input.demandTargetOffset > 10_000
  ) {
    throw new TypeError("AI filter demand target is invalid");
  }
  const now = input.now ?? new Date();
  let policy: ReturnType<typeof readAiFilterRuntimePolicy>;
  let policyValid = true;
  try {
    policy = readAiFilterRuntimePolicy();
  } catch {
    policyValid = false;
    policy = {
      enabled: false,
      routeEnabled: false,
      userMonthlyBudgetNanodollars: null,
      projectMonthlyBudgetNanodollars: null,
      maxSegmentsPerUser: 2,
      maxSegmentsPerProject: 20,
    };
  }
  const executionEnabled = policyValid && canRunAiFilter(policy);
  const hasEntitlement = await entitled(input.ownerId, now);
  const claim = await claimSegment({
    ...input,
    executionEnabled,
    hasEntitlement,
    maxSegmentsPerUser: policy.maxSegmentsPerUser,
    maxSegmentsPerProject: policy.maxSegmentsPerProject,
    now,
  });
  if (claim.kind === "disabled") return { status: "disabled", segmentId: null };
  if (claim.kind === "busy") return { status: "busy", segmentId: claim.segment?.id ?? null };
  if (claim.kind === "caught_up") {
    return { status: "caught_up", segmentId: claim.segment.id };
  }
  if (claim.kind === "demand_satisfied") {
    return { status: "demand_satisfied", segmentId: claim.segment.id };
  }
  if (claim.kind === "paused_entitlement") {
    return { status: "paused_entitlement", segmentId: claim.segment?.id ?? null };
  }
  if (claim.kind === "paused_kill") {
    return { status: "paused_kill", segmentId: claim.segment?.id ?? null };
  }

  const hmacSecret = process.env.AI_FILTER_CACHE_HMAC_SECRET;
  if (
    !policyValid ||
    !hmacSecret ||
    Buffer.byteLength(hmacSecret, "utf8") < 32 ||
    !process.env.TYPESAFE_AI_TOKEN?.trim()
  ) {
    await pauseSegment({
      segmentId: claim.segment.id,
      ownerId: input.ownerId,
      watchlistId: input.watchlistId,
      queryVersionId: claim.segment.queryVersionId,
      leaseOwner: input.leaseOwner,
      status: "paused_kill",
      reason: "policy_unavailable",
      now,
    });
    return { status: "paused_kill", segmentId: claim.segment.id };
  }

  let page: Awaited<ReturnType<typeof loadAiFilterCandidatePage>>;
  const repairsMissingDecisions = claim.segment.idempotencyKey.startsWith("repair:");
  try {
    page = await loadAiFilterCandidatePage({
      ownerId: input.ownerId,
      watchlistId: input.watchlistId,
      offset: claim.segment.selectionOffset,
      windowStart: claim.segment.windowStart,
      windowEnd: claim.segment.windowEnd,
      excludeDecidedForQueryVersionId: repairsMissingDecisions
        ? claim.segment.queryVersionId
        : undefined,
      signal: input.signal,
    });
  } catch (error) {
    await pauseSegment({
      segmentId: claim.segment.id,
      ownerId: input.ownerId,
      watchlistId: input.watchlistId,
      queryVersionId: claim.segment.queryVersionId,
      leaseOwner: input.leaseOwner,
      status: "paused_provider",
      reason: error instanceof AiFilterCandidateLoadError
        ? error.code
        : "candidate_load_unavailable",
      now,
    });
    return { status: "paused_provider", segmentId: claim.segment.id };
  }

  const checkpointAt = new Date();
  const checkpoint = await db
    .update(aiFilterSegment)
    .set({
      selectionSnapshot: page.candidates.map((candidate) => ({
        candidateId: candidate.candidateId,
        postingFirstSeenAt: candidate.postingFirstSeenAt,
        expiresAt: candidate.expiresAt,
        contentIdentity: candidate.classifierInput.contentIdentity,
      })),
      scannedCount: page.scannedCount,
      leaseExpiresAt: new Date(checkpointAt.getTime() + SEGMENT_LEASE_MS),
      updatedAt: checkpointAt,
    })
    .where(and(
      eq(aiFilterSegment.id, claim.segment.id),
      eq(aiFilterSegment.leaseOwner, input.leaseOwner),
      eq(aiFilterSegment.status, "processing"),
    ))
    .returning({ id: aiFilterSegment.id });
  if (checkpoint.length !== 1) {
    return { status: "busy", segmentId: claim.segment.id };
  }

  const repository = new PostgresAiFilterExecutionRepository({
    user: policy.userMonthlyBudgetNanodollars,
    project: policy.projectMonthlyBudgetNanodollars,
  });
  const outcome = await executeAiFilterSegment({
    context: {
      ownerId: input.ownerId,
      watchlistId: input.watchlistId,
      queryVersionId: claim.segment.queryVersionId,
      segmentId: claim.segment.id,
      queryText: claim.queryText,
      leaseOwner: input.leaseOwner,
    },
    candidates: page.candidates,
    hmacSecret,
    repository,
    classifier: new JevClient(),
    executionEnabled,
    now: checkpointAt,
    signal: input.signal,
  });
  if (outcome.status !== "completed") {
    return { status: outcome.status, segmentId: claim.segment.id };
  }

  const coveredOffset = claim.segment.selectionOffset + page.scannedCount;
  if (coveredOffset >= input.demandTargetOffset) {
    return { status: "demand_satisfied", segmentId: claim.segment.id };
  }
  if (
    page.scannedCount > 0 &&
    coveredOffset < page.total
  ) {
    return { status: "continue", segmentId: claim.segment.id };
  }
  const completedAt = new Date();
  const caughtUp = await db.transaction(async (tx) => {
    const updated = await tx
      .update(aiFilterSegment)
      .set({ status: "caught_up", completedAt, updatedAt: completedAt })
      .where(and(
        eq(aiFilterSegment.id, claim.segment.id),
        eq(aiFilterSegment.status, "completed"),
      ))
      .returning({ id: aiFilterSegment.id });
    if (updated.length !== 1) return false;
    await tx
      .update(aiFilterConfiguration)
      .set({
        lastCaughtUpAt: claim.segment.windowEnd,
        ...(claim.segment.windowStart.getTime() <=
          aiFilterHistoricalHorizonStart().getTime()
          ? { lastSweepAt: completedAt }
          : {}),
        updatedAt: completedAt,
      })
      .where(eq(aiFilterConfiguration.id, claim.configurationId));
    await tx.insert(aiFilterEvent).values({
      watchlistId: input.watchlistId,
      ownerId: input.ownerId,
      queryVersionId: claim.segment.queryVersionId,
      type: "caught_up",
      payload: { through: claim.segment.windowEnd.toISOString() },
      idempotencyKey: `query:${claim.segment.queryVersionId}:caught-up:${claim.segment.windowEnd.toISOString()}`,
      createdAt: completedAt,
    }).onConflictDoNothing();
    return true;
  });
  if (!caughtUp) return { status: "busy", segmentId: claim.segment.id };
  return { status: "caught_up", segmentId: claim.segment.id };
}
