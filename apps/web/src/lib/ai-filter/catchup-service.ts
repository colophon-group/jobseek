import "server-only";

import { randomUUID } from "node:crypto";
import { and, desc, eq, gt, isNull, or, sql } from "drizzle-orm";

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
  AI_FILTER_USER_MONTHLY_BUDGET_NANODOLLARS,
  readAiFilterRuntimePolicy,
} from "./policy";

const DAY_MS = 24 * 60 * 60 * 1_000;
const SEGMENT_LEASE_MS = 5 * 60 * 1_000;

export type AiFilterCatchupStepResult = Readonly<{
  status:
    | "continue"
    | "caught_up"
    | "busy"
    | "disabled"
    | "paused_entitlement"
    | "paused_budget"
    | "paused_provider"
    | "paused_kill";
  segmentId: string | null;
}>;

function roundedHorizonEnd(now: Date): Date {
  return new Date(Math.floor(now.getTime() / 1_000) * 1_000 + 1_000);
}

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
      const [previous] = await tx
        .select()
        .from(aiFilterSegment)
        .where(and(
          eq(aiFilterSegment.watchlistId, input.watchlistId),
          eq(aiFilterSegment.queryVersionId, current.queryVersionId),
        ))
        .orderBy(desc(aiFilterSegment.createdAt))
        .limit(1);
      const continuePrevious =
        previous?.status === "completed" &&
        previous.scannedCount > 0;
      const windowEnd = continuePrevious
        ? previous.windowEnd
        : roundedHorizonEnd(input.now);
      const windowStart = continuePrevious
        ? previous.windowStart
        : new Date(windowEnd.getTime() - 30 * DAY_MS);
      const selectionOffset = continuePrevious
        ? previous.selectionOffset + previous.scannedCount
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
        idempotencyKey:
          `${current.queryVersionId}:${windowStart.toISOString()}:${windowEnd.toISOString()}:${selectionOffset}`,
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
  now?: Date;
  signal?: AbortSignal;
}): Promise<AiFilterCatchupStepResult> {
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
      userMonthlyBudgetNanodollars: AI_FILTER_USER_MONTHLY_BUDGET_NANODOLLARS,
      projectMonthlyBudgetNanodollars: 1,
      maxSegmentsPerUser: 2,
      maxSegmentsPerProject: 20,
    };
  }
  const executionEnabled = policyValid && policy.enabled && policy.routeEnabled;
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
  try {
    page = await loadAiFilterCandidatePage({
      ownerId: input.ownerId,
      watchlistId: input.watchlistId,
      offset: claim.segment.selectionOffset,
      windowStart: claim.segment.windowStart,
      windowEnd: claim.segment.windowEnd,
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

  const repository = new PostgresAiFilterExecutionRepository(
    policy.projectMonthlyBudgetNanodollars,
  );
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

  if (
    page.scannedCount > 0 &&
    claim.segment.selectionOffset + page.scannedCount < page.total
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
      .set({ lastCaughtUpAt: claim.segment.windowEnd, updatedAt: completedAt })
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
