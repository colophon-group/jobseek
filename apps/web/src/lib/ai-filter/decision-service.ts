import "server-only";

import { and, asc, desc, eq, gt, lte, sql } from "drizzle-orm";

import { db } from "@/db";
import {
  aiFilterConfiguration,
  aiFilterDecision,
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

type AiFilterDecisionPosting = Awaited<
  ReturnType<typeof readWatchlistCandidatesByIds>
>[number];

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
