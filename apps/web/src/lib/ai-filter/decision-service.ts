import "server-only";

import { and, asc, desc, eq, gt, lte, sql } from "drizzle-orm";

import { db } from "@/db";
import {
  aiFilterConfiguration,
  aiFilterDecision,
  aiFilterFeedback,
  aiFilterQueryVersion,
  aiFilterSegment,
  watchlist,
} from "@/db/schema";
import type { AiFilterDecisionValue } from "./contract";
import { readWatchlistCandidatesByIds } from "@/lib/services/watchlist-matcher";
import {
  AiFilterNotFoundError,
  getSharedAiFilterOwnerId,
} from "./configuration-service";

type Transaction = Parameters<Parameters<typeof db.transaction>[0]>[0];
type AiFilterDecisionPosting = Awaited<
  ReturnType<typeof readWatchlistCandidatesByIds>
>[number];

function requireIdempotencyKey(value: unknown): string {
  if (
    typeof value !== "string" ||
    value.length < 8 ||
    value.length > 128 ||
    !/^[A-Za-z0-9._:-]+$/.test(value)
  ) {
    throw new TypeError("AI filter idempotency key is invalid");
  }
  return value;
}

function persistedIdempotencyKey(decisionId: string, value: unknown): string {
  return `decision:${decisionId}:${requireIdempotencyKey(value)}`;
}

async function lockOwnedCurrentDecision(
  tx: Transaction,
  input: { ownerId: string; watchlistId: string; decisionId: string },
) {
  const [decision] = await tx
    .select({
      id: aiFilterDecision.id,
      modelDecision: aiFilterDecision.modelDecision,
      userOverride: aiFilterDecision.userOverride,
    })
    .from(aiFilterDecision)
    .innerJoin(
      watchlist,
      and(
        eq(watchlist.id, aiFilterDecision.watchlistId),
        eq(watchlist.userId, aiFilterDecision.ownerId),
      ),
    )
    .innerJoin(
      aiFilterConfiguration,
      and(
        eq(aiFilterConfiguration.watchlistId, watchlist.id),
        eq(aiFilterConfiguration.ownerId, watchlist.userId),
      ),
    )
    .innerJoin(
      aiFilterQueryVersion,
      and(
        eq(aiFilterQueryVersion.configurationId, aiFilterConfiguration.id),
        eq(aiFilterQueryVersion.revision, aiFilterConfiguration.currentRevision),
        eq(aiFilterQueryVersion.id, aiFilterDecision.queryVersionId),
      ),
    )
    .where(and(
      eq(aiFilterDecision.id, input.decisionId),
      eq(aiFilterDecision.watchlistId, input.watchlistId),
      eq(aiFilterDecision.ownerId, input.ownerId),
    ))
    .for("update", { of: aiFilterDecision })
    .limit(1);
  if (!decision) throw new AiFilterNotFoundError();
  return decision;
}

function effectiveDecision(decision: {
  modelDecision: AiFilterDecisionValue;
  userOverride: AiFilterDecisionValue | null;
}): AiFilterDecisionValue {
  return decision.userOverride ?? decision.modelDecision;
}

async function hydrateCurrentDecisionPostings(
  candidateIds: readonly string[],
  signal?: AbortSignal,
): Promise<AiFilterDecisionPosting[]> {
  return readWatchlistCandidatesByIds(candidateIds, signal);
}

export async function listAiFilterDecisions(input: {
  ownerId: string;
  watchlistId: string;
  bucket: AiFilterDecisionValue;
  offset?: number;
  limit?: number;
  now?: Date;
  signal?: AbortSignal;
  /** Shared viewers page durable decisions without extending evaluation. */
  persistedOnly?: boolean;
}) {
  const offset = input.offset ?? 0;
  const limit = input.limit ?? 25;
  if (!Number.isSafeInteger(offset) || offset < 0) throw new TypeError("Invalid offset");
  if (!Number.isSafeInteger(limit) || limit < 1 || limit > 100) {
    throw new TypeError("Invalid limit");
  }
  const now = input.now ?? new Date();
  const [resource] = await db
    .select({
      queryVersionId: aiFilterQueryVersion.id,
      horizonStartedAt: aiFilterQueryVersion.horizonStartedAt,
      horizonEndsAt: aiFilterQueryVersion.horizonEndsAt,
      lastCaughtUpAt: aiFilterConfiguration.lastCaughtUpAt,
      lastSweepAt: aiFilterConfiguration.lastSweepAt,
    })
    .from(aiFilterConfiguration)
    .innerJoin(
      watchlist,
      and(
        eq(watchlist.id, aiFilterConfiguration.watchlistId),
        eq(watchlist.userId, aiFilterConfiguration.ownerId),
      ),
    )
    .innerJoin(
      aiFilterQueryVersion,
      and(
        eq(aiFilterQueryVersion.configurationId, aiFilterConfiguration.id),
        eq(aiFilterQueryVersion.revision, aiFilterConfiguration.currentRevision),
      ),
    )
    .where(and(
      eq(watchlist.id, input.watchlistId),
      eq(watchlist.userId, input.ownerId),
    ))
    .limit(1);
  if (!resource) throw new AiFilterNotFoundError();

  const [rows, [bucketCount], [latestSegment], [historicalFoundation]] = await Promise.all([
    db
      .select({
        decisionId: aiFilterDecision.id,
        candidateId: aiFilterDecision.candidateId,
        modelDecision: aiFilterDecision.modelDecision,
        userOverride: aiFilterDecision.userOverride,
        postingFirstSeenAt: aiFilterDecision.postingFirstSeenAt,
        decidedAt: aiFilterDecision.decidedAt,
        expiresAt: aiFilterDecision.expiresAt,
      })
      .from(aiFilterDecision)
      .where(and(
        eq(aiFilterDecision.watchlistId, input.watchlistId),
        eq(aiFilterDecision.ownerId, input.ownerId),
        eq(aiFilterDecision.queryVersionId, resource.queryVersionId),
        gt(aiFilterDecision.expiresAt, now),
        sql`COALESCE(${aiFilterDecision.userOverride}, ${aiFilterDecision.modelDecision}) = ${input.bucket}`,
      ))
      .orderBy(
        desc(aiFilterDecision.postingFirstSeenAt),
        asc(aiFilterDecision.candidateId),
      )
      .offset(offset)
      .limit(limit + 1),
    offset === 0
      ? db
          .select({ total: sql<number>`count(*)::integer` })
          .from(aiFilterDecision)
          .where(and(
            eq(aiFilterDecision.watchlistId, input.watchlistId),
            eq(aiFilterDecision.ownerId, input.ownerId),
            eq(aiFilterDecision.queryVersionId, resource.queryVersionId),
            gt(aiFilterDecision.expiresAt, now),
            sql`COALESCE(${aiFilterDecision.userOverride}, ${aiFilterDecision.modelDecision}) = ${input.bucket}`,
          ))
      : Promise.resolve([{ total: 0 }]),
    db
      .select({
        status: aiFilterSegment.status,
        windowStart: aiFilterSegment.windowStart,
        windowEnd: aiFilterSegment.windowEnd,
      })
      .from(aiFilterSegment)
      .where(and(
        eq(aiFilterSegment.watchlistId, input.watchlistId),
        eq(aiFilterSegment.queryVersionId, resource.queryVersionId),
      ))
      .orderBy(desc(aiFilterSegment.createdAt))
      .limit(1),
    db
      .select({ id: aiFilterSegment.id })
      .from(aiFilterSegment)
      .where(and(
        eq(aiFilterSegment.watchlistId, input.watchlistId),
        eq(aiFilterSegment.queryVersionId, resource.queryVersionId),
        eq(aiFilterSegment.status, "caught_up"),
        lte(aiFilterSegment.windowStart, resource.horizonStartedAt),
      ))
      .limit(1),
  ]);
  const pageRows = rows.slice(0, limit);
  const hydrationIds = pageRows.map((row) => row.candidateId);
  const postings = await hydrateCurrentDecisionPostings(hydrationIds, input.signal);
  const postingById = new Map(postings.map((posting) => [posting.id, posting]));
  const decisions: Array<{
    effectiveDecision: AiFilterDecisionValue;
    postingFirstSeenAt: string;
    decidedAt: string;
    expiresAt: string;
    posting: AiFilterDecisionPosting;
    decisionId: string;
    candidateId: string;
    modelDecision: AiFilterDecisionValue;
    userOverride: AiFilterDecisionValue | null;
  }> = [];
  for (const row of pageRows) {
    const posting = postingById.get(row.candidateId);
    if (!posting) continue;
    decisions.push({
      ...row,
      effectiveDecision: row.userOverride ?? row.modelDecision,
      postingFirstSeenAt: row.postingFirstSeenAt.toISOString(),
      decidedAt: row.decidedAt.toISOString(),
      expiresAt: row.expiresAt.toISOString(),
      posting,
    });
  }

  const caughtUpCoversQueryHorizon = Boolean(
    latestSegment?.status === "caught_up" &&
    historicalFoundation &&
    resource.lastCaughtUpAt &&
    resource.lastSweepAt &&
    resource.lastCaughtUpAt.getTime() >= resource.horizonEndsAt.getTime() &&
    latestSegment.windowEnd.getTime() >= resource.horizonEndsAt.getTime(),
  );

  return {
    decisions,
    total: offset === 0 ? bucketCount?.total ?? 0 : undefined,
    nextOffset: offset + pageRows.length,
    hasMore: rows.length > limit || (!input.persistedOnly && !caughtUpCoversQueryHorizon),
  };
}

/** Read accepted decisions from an entitled owner's unlisted shared watchlist. */
export async function listSharedAiFilterDecisions(input: {
  watchlistId: string;
  offset?: number;
  limit?: number;
  now?: Date;
  signal?: AbortSignal;
}) {
  const ownerId = await getSharedAiFilterOwnerId({
    watchlistId: input.watchlistId,
    now: input.now,
  });
  return listAiFilterDecisions({
    ...input,
    ownerId,
    bucket: "accepted",
    persistedOnly: true,
  });
}

export async function moveAiFilterDecision(input: {
  ownerId: string;
  watchlistId: string;
  decisionId: string;
  to: AiFilterDecisionValue;
  idempotencyKey: unknown;
  now?: Date;
}) {
  const idempotencyKey = persistedIdempotencyKey(
    input.decisionId,
    input.idempotencyKey,
  );
  const now = input.now ?? new Date();
  return db.transaction(async (tx) => {
    const decision = await lockOwnedCurrentDecision(tx, input);
    const [existing] = await tx
      .select({ id: aiFilterFeedback.id })
      .from(aiFilterFeedback)
      .where(and(
        eq(aiFilterFeedback.ownerId, input.ownerId),
        eq(aiFilterFeedback.idempotencyKey, idempotencyKey),
      ))
      .limit(1);
    if (!existing) {
      const from = effectiveDecision(decision);
      await tx.insert(aiFilterFeedback).values({
        decisionId: decision.id,
        ownerId: input.ownerId,
        kind: "move",
        fromDecision: from,
        toDecision: input.to,
        idempotencyKey,
        createdAt: now,
      });
      await tx
        .update(aiFilterDecision)
        .set({
          userOverride: input.to === decision.modelDecision ? null : input.to,
          updatedAt: now,
        })
        .where(eq(aiFilterDecision.id, decision.id));
      return { decision: input.to, changed: from !== input.to };
    }
    return { decision: effectiveDecision(decision), changed: false };
  });
}

export async function undoAiFilterDecisionMove(input: {
  ownerId: string;
  watchlistId: string;
  decisionId: string;
  moveIdempotencyKey: unknown;
  idempotencyKey: unknown;
  now?: Date;
}) {
  const moveKey = persistedIdempotencyKey(
    input.decisionId,
    input.moveIdempotencyKey,
  );
  const undoKey = persistedIdempotencyKey(input.decisionId, input.idempotencyKey);
  const now = input.now ?? new Date();
  return db.transaction(async (tx) => {
    const decision = await lockOwnedCurrentDecision(tx, input);
    const [existingUndo] = await tx
      .select({ id: aiFilterFeedback.id })
      .from(aiFilterFeedback)
      .where(and(
        eq(aiFilterFeedback.ownerId, input.ownerId),
        eq(aiFilterFeedback.idempotencyKey, undoKey),
      ))
      .limit(1);
    if (existingUndo) return { decision: effectiveDecision(decision), changed: false };

    const [latest] = await tx
      .select({
        kind: aiFilterFeedback.kind,
        idempotencyKey: aiFilterFeedback.idempotencyKey,
        fromDecision: aiFilterFeedback.fromDecision,
      })
      .from(aiFilterFeedback)
      .where(and(
        eq(aiFilterFeedback.decisionId, decision.id),
        eq(aiFilterFeedback.ownerId, input.ownerId),
      ))
      .orderBy(desc(aiFilterFeedback.createdAt), desc(aiFilterFeedback.id))
      .limit(1);
    if (
      !latest ||
      latest.kind !== "move" ||
      latest.idempotencyKey !== moveKey ||
      !latest.fromDecision
    ) {
      throw new TypeError("AI filter move is no longer undoable");
    }
    await tx.insert(aiFilterFeedback).values({
      decisionId: decision.id,
      ownerId: input.ownerId,
      kind: "undo",
      fromDecision: effectiveDecision(decision),
      toDecision: latest.fromDecision,
      idempotencyKey: undoKey,
      createdAt: now,
    });
    await tx
      .update(aiFilterDecision)
      .set({
        userOverride:
          latest.fromDecision === decision.modelDecision ? null : latest.fromDecision,
        updatedAt: now,
      })
      .where(eq(aiFilterDecision.id, decision.id));
    return { decision: latest.fromDecision, changed: true };
  });
}

export async function reportAiFilterMistake(input: {
  ownerId: string;
  watchlistId: string;
  decisionId: string;
  idempotencyKey: unknown;
  now?: Date;
}) {
  const idempotencyKey = persistedIdempotencyKey(
    input.decisionId,
    input.idempotencyKey,
  );
  const now = input.now ?? new Date();
  return db.transaction(async (tx) => {
    const decision = await lockOwnedCurrentDecision(tx, input);
    await tx.insert(aiFilterFeedback).values({
      decisionId: decision.id,
      ownerId: input.ownerId,
      kind: "mistake",
      fromDecision: effectiveDecision(decision),
      toDecision: null,
      idempotencyKey,
      createdAt: now,
    }).onConflictDoNothing();
    return { reported: true };
  });
}
