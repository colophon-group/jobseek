import "server-only";
import { and, asc, desc, eq, gt, gte, inArray, lt, sql } from "drizzle-orm";
import { db } from "@/db";
import { hasPaidEntitlement } from "@/lib/paid-entitlement";
import { aiFilterConfiguration, aiFilterDecision, aiFilterQueryVersion } from "@/db/schema";
import { matchCompiledWatchlistsInWindow, type WatchlistWindowMatchResult } from "./watchlist-matcher";
import type { CompiledWatchlistMatcher } from "@/lib/watchlist-matcher-contract";
import { getNotificationSearchWindow, NOTIFICATION_MATCH_LIMIT_PER_WATCHLIST } from "@/lib/notifications/scheduler-policy";

type Transaction = Parameters<Parameters<typeof db.transaction>[0]>[0];
type Reader = Pick<Transaction, "select" | "selectDistinctOn" | "execute">;

/** Never invoke the classifier or spend AI budget while preparing an email. */
export async function getNotificationNarrowing(ownerId: string, watchlistId: string, reader: Reader = db) {
  const now = new Date();
  const [resource] = await reader.select({
    queryVersionId: aiFilterQueryVersion.id,
    prompt: aiFilterQueryVersion.queryText,
    updatedAt: aiFilterConfiguration.updatedAt,
    lastCaughtUpAt: aiFilterConfiguration.lastCaughtUpAt,
  }).from(aiFilterConfiguration).innerJoin(aiFilterQueryVersion, and(
    eq(aiFilterQueryVersion.configurationId, aiFilterConfiguration.id),
    eq(aiFilterQueryVersion.revision, aiFilterConfiguration.currentRevision),
  )).where(and(
    eq(aiFilterConfiguration.ownerId, ownerId), eq(aiFilterConfiguration.watchlistId, watchlistId),
    eq(aiFilterConfiguration.status, "enabled"),
  )).for("share", { of: aiFilterConfiguration }).limit(1);
  if (!resource) return null;
  return await hasPaidEntitlement(reader, ownerId, now) ? resource : null;
}

/** Latest decision wins even when a candidate has several content revisions. */
function latestDecisions(reader: Reader, ownerId: string, watchlistId: string, queryVersionId: string) {
  return reader.selectDistinctOn([aiFilterDecision.candidateId], {
    candidateId: aiFilterDecision.candidateId,
    effective: sql<string>`COALESCE(${aiFilterDecision.userOverride}, ${aiFilterDecision.modelDecision})`.as("effective"),
    firstSeenAt: aiFilterDecision.postingFirstSeenAt,
    expiresAt: aiFilterDecision.expiresAt,
  }).from(aiFilterDecision).where(and(
    eq(aiFilterDecision.ownerId, ownerId), eq(aiFilterDecision.watchlistId, watchlistId),
    eq(aiFilterDecision.queryVersionId, queryVersionId),
  )).orderBy(asc(aiFilterDecision.candidateId), desc(aiFilterDecision.decidedAt), desc(aiFilterDecision.id)).as("latest_decisions");
}

export async function matchNarrowedNotificationWatchlist(input: {
  ownerId: string; compiled: CompiledWatchlistMatcher; windowStart: Date; windowEnd: Date;
}): Promise<WatchlistWindowMatchResult> {
  const { compiled, ownerId, windowStart, windowEnd } = input;
  if (!ownerId) throw new Error("Narrowed notifications require an owner");
  const resource = await getNotificationNarrowing(ownerId, compiled.watchlistId);
  const result: WatchlistWindowMatchResult = {
    window: { windowStart: windowStart.toISOString(), windowEnd: windowEnd.toISOString(), boundary: "[windowStart, windowEnd)" },
    postings: [], watchlists: [{ id: compiled.watchlistId, label: compiled.watchlistLabel, total: 0, returned: 0, truncated: false }],
  };
  const searchWindow = getNotificationSearchWindow({ windowStart, windowEnd });
  if (!searchWindow) return result;
  // Disabled/deleted prompts or lost entitlement never broaden the email.
  if (!resource) return result;
  // Do not close a weekly window before evaluation has finished: late accepted
  // decisions must still be eligible on the next runner invocation.
  if (!resource.lastCaughtUpAt || resource.lastCaughtUpAt < windowEnd) {
    throw new Error("Narrowed notification results are still being evaluated");
  }
  const latest = latestDecisions(db, ownerId, compiled.watchlistId, resource.queryVersionId);
  const cap = NOTIFICATION_MATCH_LIMIT_PER_WATCHLIST;
  const rows = await db.select({ id: latest.candidateId }).from(latest).where(and(
    eq(latest.effective, "accepted"), gt(latest.expiresAt, new Date()),
    gte(latest.firstSeenAt, windowStart), lt(latest.firstSeenAt, windowEnd),
  )).orderBy(desc(latest.firstSeenAt), asc(latest.candidateId)).limit(cap + 1);
  // Intersect with current structured filters and active postings BEFORE the
  // search result cap, so rejected jobs cannot crowd out narrowed matches.
  for (let offset = 0; offset < Math.min(rows.length, cap); offset += 50) {
    const ids = rows.slice(offset, Math.min(offset + 50, cap)).map(row => row.id);
    const page = await matchCompiledWatchlistsInWindow({
      watchlists: [{ ...compiled, candidateFilters: { ...compiled.candidateFilters, postingIds: ids } }],
      ...searchWindow, limitPerWatchlist: 50,
    });
    result.postings.push(...page.postings.map(posting => ({ ...posting,
      matchedWatchlists: posting.matchedWatchlists.map(label => ({ ...label, narrowedQueryVersionId: resource.queryVersionId })),
    })));
  }
  result.watchlists[0] = { ...result.watchlists[0]!, total: result.postings.length, returned: result.postings.length, truncated: rows.length > cap };
  return result;
}

/** Revalidate prompt and decisions under row locks held through submission. */
export async function validateNarrowedNotificationPostings(tx: Transaction, input: {
  ownerId: string; watchlistId: string; queryVersionId: string; postingIds: string[]; plannedAt: Date;
}): Promise<boolean> {
  const resource = await getNotificationNarrowing(input.ownerId, input.watchlistId, tx);
  if (!resource || resource.queryVersionId !== input.queryVersionId || resource.updatedAt > input.plannedAt) return false;
  // Lock all selected content revisions, including rejected ones. This also
  // serializes with feedback that would remove a job while an email submits.
  const rows = await tx.select({ id: aiFilterDecision.candidateId,
    decision: aiFilterDecision.modelDecision, override: aiFilterDecision.userOverride,
    expiresAt: aiFilterDecision.expiresAt,
  }).from(aiFilterDecision).where(and(
    eq(aiFilterDecision.ownerId, input.ownerId), eq(aiFilterDecision.watchlistId, input.watchlistId),
    eq(aiFilterDecision.queryVersionId, input.queryVersionId), inArray(aiFilterDecision.candidateId, input.postingIds),
  )).orderBy(desc(aiFilterDecision.decidedAt), desc(aiFilterDecision.id)).for("share");
  const seen = new Map<string, (typeof rows)[number]>();
  for (const row of rows) if (!seen.has(row.id)) seen.set(row.id, row);
  return input.postingIds.every(id => {
    const row = seen.get(id);
    return row && (row.override ?? row.decision) === "accepted" && row.expiresAt > new Date();
  });
}
