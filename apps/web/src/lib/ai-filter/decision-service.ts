import "server-only";

import { and, desc, eq, gt, inArray, sql } from "drizzle-orm";

import { db } from "@/db";
import {
  aiFilterConfiguration,
  aiFilterDecision,
  aiFilterFeedback,
  aiFilterQueryVersion,
  watchlist,
} from "@/db/schema";
import type { AiFilterDecisionValue } from "./contract";
import { loadAiFilterDecisionCandidates } from "./candidate-loader";
import { AiFilterNotFoundError } from "./configuration-service";

type Transaction = Parameters<Parameters<typeof db.transaction>[0]>[0];

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

export async function listAiFilterDecisions(input: {
  ownerId: string;
  watchlistId: string;
  bucket: AiFilterDecisionValue;
  offset?: number;
  limit?: number;
  now?: Date;
  signal?: AbortSignal;
}) {
  const offset = input.offset ?? 0;
  const limit = input.limit ?? 25;
  if (!Number.isSafeInteger(offset) || offset < 0) throw new TypeError("Invalid offset");
  if (!Number.isSafeInteger(limit) || limit < 1 || limit > 100) {
    throw new TypeError("Invalid limit");
  }
  const now = input.now ?? new Date();
  const candidatePage = await loadAiFilterDecisionCandidates({
    ownerId: input.ownerId,
    watchlistId: input.watchlistId,
    offset,
    limit: Math.min(100, limit * 4),
    now,
    signal: input.signal,
  });
  const candidateIds = candidatePage.postings.map((posting) => posting.id);
  if (candidateIds.length === 0) {
    return { decisions: [], nextOffset: offset, hasMore: false };
  }
  const rows = await db
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
      eq(watchlist.id, input.watchlistId),
      eq(watchlist.userId, input.ownerId),
      inArray(aiFilterDecision.candidateId, candidateIds),
      gt(aiFilterDecision.expiresAt, now),
      sql`COALESCE(${aiFilterDecision.userOverride}, ${aiFilterDecision.modelDecision}) = ${input.bucket}`,
    ))
    .limit(candidateIds.length);
  const rowByCandidate = new Map(rows.map((row) => [row.candidateId, row]));
  const decisions: Array<{
    effectiveDecision: AiFilterDecisionValue;
    postingFirstSeenAt: string;
    decidedAt: string;
    expiresAt: string;
    posting: (typeof candidatePage.postings)[number];
    decisionId: string;
    candidateId: string;
    modelDecision: AiFilterDecisionValue;
    userOverride: AiFilterDecisionValue | null;
  }> = [];
  let scannedCount = 0;
  for (const posting of candidatePage.postings) {
    scannedCount += 1;
    const row = rowByCandidate.get(posting.id);
    if (row) decisions.push({
      ...row,
      effectiveDecision: row.userOverride ?? row.modelDecision,
      postingFirstSeenAt: row.postingFirstSeenAt.toISOString(),
      decidedAt: row.decidedAt.toISOString(),
      expiresAt: row.expiresAt.toISOString(),
      posting,
    });
    if (decisions.length === limit) break;
  }
  const nextOffset = offset + scannedCount;
  return {
    decisions,
    nextOffset,
    hasMore: nextOffset < candidatePage.total,
  };
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
