import "server-only";

import { createHash, randomUUID } from "node:crypto";
import {
  and,
  desc,
  eq,
  gt,
  isNull,
  or,
  sql,
} from "drizzle-orm";

import { db } from "@/db";
import {
  aiFilterBudgetAccount,
  aiFilterConfiguration,
  aiFilterDecision,
  aiFilterEvent,
  aiFilterQueryVersion,
  aiFilterSegment,
  subscription,
  watchlist,
  watchlistCompany,
} from "@/db/schema";
import type { WatchlistFilters } from "@/lib/watchlist-matcher-contract";
import {
  CLASSIFIER_INPUT_NORMALIZER_VERSION,
  CLASSIFIER_INPUT_SCHEMA_VERSION,
} from "./classifier-input";
import { normalizeAiFilterSoftQueryV1 } from "./contract";
import {
  AI_FILTER_PROMPT_VERSION,
  JEV_MODEL,
} from "./policy";

const DAY_MS = 24 * 60 * 60 * 1_000;

type Transaction = Parameters<Parameters<typeof db.transaction>[0]>[0];

export class AiFilterNotFoundError extends Error {
  constructor() {
    super("AI filter resource was not found");
    this.name = "AiFilterNotFoundError";
  }
}

export class AiFilterEntitlementError extends Error {
  constructor() {
    super("An active subscription is required");
    this.name = "AiFilterEntitlementError";
  }
}

export type AiFilterOwnerState = Readonly<{
  watchlistId: string;
  enabled: boolean;
  entitled: boolean;
  query: string;
  queryRevision: number;
  queryVersionId: string;
  status:
    | "idle"
    | "processing"
    | "caught_up"
    | "paused_entitlement"
    | "paused_budget"
    | "provider_unavailable"
    | "waiting_for_jev"
    | "paused_kill"
    | "cancelled"
    | "failed"
    | "disabled";
  counts: Readonly<{ accepted: number; rejected: number; total: number }>;
  progress: Readonly<{
    selectionOffset: number;
    scannedCount: number;
    completedCount: number;
    stopReason: string | null;
  }>;
  budget: Readonly<{
    actualNanodollars: number;
    reservedNanodollars: number;
  }>;
  lastCaughtUpAt: string | null;
  latestEventSequence: number;
}>;

function monthStartUtc(now: Date): Date {
  return new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 1));
}

function horizonEnd(now: Date): Date {
  return new Date(Math.floor(now.getTime() / 1_000) * 1_000 + 1_000);
}

function stableJson(value: unknown): string {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(stableJson).join(",")}]`;
  const record = value as Record<string, unknown>;
  return `{${Object.keys(record).sort().map((key) =>
    `${JSON.stringify(key)}:${stableJson(record[key])}`).join(",")}}`;
}

async function filterFingerprint(
  tx: Transaction,
  watchlistId: string,
  filters: unknown,
): Promise<string> {
  const companies = await tx
    .select({ companyId: watchlistCompany.companyId })
    .from(watchlistCompany)
    .where(eq(watchlistCompany.watchlistId, watchlistId));
  const canonical = stableJson({
    filters: filters as WatchlistFilters,
    companyIds: companies.map((row) => row.companyId).sort(),
  });
  return createHash("sha256").update(canonical, "utf8").digest("hex");
}

async function ownedWatchlist(
  tx: Transaction,
  ownerId: string,
  watchlistId: string,
) {
  const [row] = await tx
    .select({ id: watchlist.id, filters: watchlist.filters })
    .from(watchlist)
    .where(and(eq(watchlist.id, watchlistId), eq(watchlist.userId, ownerId)))
    .limit(1);
  if (!row) throw new AiFilterNotFoundError();
  return row;
}

async function activeEntitlement(
  tx: Transaction,
  ownerId: string,
  now: Date,
): Promise<boolean> {
  const [row] = await tx
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

async function lockConfiguration(tx: Transaction, watchlistId: string): Promise<void> {
  await tx.execute(
    sql`SELECT pg_advisory_xact_lock(hashtextextended(${`ai-filter-config:${watchlistId}`}, 119_027))`,
  );
}

/** Idempotently enables the filter or creates one immutable query version. */
export async function putAiFilterConfiguration(input: {
  ownerId: string;
  watchlistId: string;
  query: unknown;
  now?: Date;
}): Promise<AiFilterOwnerState> {
  const now = input.now ?? new Date();
  await db.transaction(async (tx) => {
    await lockConfiguration(tx, input.watchlistId);
    const owned = await ownedWatchlist(tx, input.ownerId, input.watchlistId);
    // Authorize before inspecting user-controlled configuration so anonymous
    // and cross-owner callers always receive the same not-found boundary.
    const normalizedQuery = normalizeAiFilterSoftQueryV1(input.query);
    const queryText = normalizedQuery;
    if (!await activeEntitlement(tx, input.ownerId, now)) {
      throw new AiFilterEntitlementError();
    }
    const fingerprint = await filterFingerprint(tx, input.watchlistId, owned.filters);
    const [configuration] = await tx
      .select()
      .from(aiFilterConfiguration)
      .where(eq(aiFilterConfiguration.watchlistId, input.watchlistId))
      .limit(1);

    if (configuration) {
      const [currentQuery] = await tx
        .select()
        .from(aiFilterQueryVersion)
        .where(and(
          eq(aiFilterQueryVersion.configurationId, configuration.id),
          eq(aiFilterQueryVersion.revision, configuration.currentRevision),
        ))
        .limit(1);
      if (!currentQuery) throw new Error("AI filter current query version is missing");
      if (currentQuery.normalizedQuery === normalizedQuery) {
        await tx
          .update(aiFilterConfiguration)
          .set({ status: "enabled", disabledAt: null, updatedAt: now })
          .where(eq(aiFilterConfiguration.id, configuration.id));
        return;
      }

      const revision = configuration.currentRevision + 1;
      const end = horizonEnd(now);
      const queryVersionId = randomUUID();
      await tx.insert(aiFilterQueryVersion).values({
        id: queryVersionId,
        configurationId: configuration.id,
        revision,
        queryText,
        normalizedQuery,
        model: JEV_MODEL,
        promptVersion: AI_FILTER_PROMPT_VERSION,
        schemaVersion: CLASSIFIER_INPUT_SCHEMA_VERSION,
        normalizerVersion: CLASSIFIER_INPUT_NORMALIZER_VERSION,
        filterFingerprint: fingerprint,
        horizonStartedAt: new Date(end.getTime() - 30 * DAY_MS),
        horizonEndsAt: end,
        createdAt: now,
      });
      await tx
        .update(aiFilterConfiguration)
        .set({
          status: "enabled",
          currentRevision: revision,
          lastCaughtUpAt: null,
          disabledAt: null,
          updatedAt: now,
        })
        .where(eq(aiFilterConfiguration.id, configuration.id));
      await tx
        .update(aiFilterSegment)
        .set({
          status: "cancelled",
          stopReason: "configuration_changed",
          leaseOwner: null,
          leaseExpiresAt: null,
          completedAt: now,
          updatedAt: now,
        })
        .where(and(
          eq(aiFilterSegment.watchlistId, input.watchlistId),
          neCurrentQuery(aiFilterSegment.queryVersionId, queryVersionId),
          sql`${aiFilterSegment.status} IN ('pending', 'processing', 'paused_entitlement', 'paused_budget', 'paused_provider', 'paused_kill')`,
        ));
      await tx.insert(aiFilterEvent).values({
        watchlistId: input.watchlistId,
        ownerId: input.ownerId,
        queryVersionId,
        type: "query_changed",
        payload: { revision },
        idempotencyKey: `query:${queryVersionId}:created`,
        createdAt: now,
      });
      return;
    }

    const configurationId = randomUUID();
    const queryVersionId = randomUUID();
    const end = horizonEnd(now);
    await tx.insert(aiFilterConfiguration).values({
      id: configurationId,
      watchlistId: input.watchlistId,
      ownerId: input.ownerId,
      status: "enabled",
      currentRevision: 1,
      createdAt: now,
      updatedAt: now,
    });
    await tx.insert(aiFilterQueryVersion).values({
      id: queryVersionId,
      configurationId,
      revision: 1,
      queryText,
      normalizedQuery,
      model: JEV_MODEL,
      promptVersion: AI_FILTER_PROMPT_VERSION,
      schemaVersion: CLASSIFIER_INPUT_SCHEMA_VERSION,
      normalizerVersion: CLASSIFIER_INPUT_NORMALIZER_VERSION,
      filterFingerprint: fingerprint,
      horizonStartedAt: new Date(end.getTime() - 30 * DAY_MS),
      horizonEndsAt: end,
      createdAt: now,
    });
    await tx.insert(aiFilterEvent).values({
      watchlistId: input.watchlistId,
      ownerId: input.ownerId,
      queryVersionId,
      type: "enabled",
      payload: { revision: 1 },
      idempotencyKey: `query:${queryVersionId}:created`,
      createdAt: now,
    });
  });
  return getAiFilterOwnerState({
    ownerId: input.ownerId,
    watchlistId: input.watchlistId,
    now,
  });
}

// Kept as a helper so the cancellation predicate remains explicit in SQL.
function neCurrentQuery(column: typeof aiFilterSegment.queryVersionId, value: string) {
  return sql`${column} <> ${value}`;
}

export async function disableAiFilterConfiguration(input: {
  ownerId: string;
  watchlistId: string;
  now?: Date;
}): Promise<void> {
  const now = input.now ?? new Date();
  await db.transaction(async (tx) => {
    await lockConfiguration(tx, input.watchlistId);
    await ownedWatchlist(tx, input.ownerId, input.watchlistId);
    const [configuration] = await tx
      .select()
      .from(aiFilterConfiguration)
      .where(and(
        eq(aiFilterConfiguration.watchlistId, input.watchlistId),
        eq(aiFilterConfiguration.ownerId, input.ownerId),
      ))
      .limit(1);
    if (!configuration) throw new AiFilterNotFoundError();
    const [queryVersion] = await tx
      .select({ id: aiFilterQueryVersion.id })
      .from(aiFilterQueryVersion)
      .where(and(
        eq(aiFilterQueryVersion.configurationId, configuration.id),
        eq(aiFilterQueryVersion.revision, configuration.currentRevision),
      ))
      .limit(1);
    if (!queryVersion) throw new AiFilterNotFoundError();
    await tx
      .update(aiFilterConfiguration)
      .set({ status: "disabled", disabledAt: now, updatedAt: now })
      .where(eq(aiFilterConfiguration.id, configuration.id));
    await tx
      .update(aiFilterSegment)
      .set({
        status: "cancelled",
        stopReason: "cancelled",
        leaseOwner: null,
        leaseExpiresAt: null,
        completedAt: now,
        updatedAt: now,
      })
      .where(and(
        eq(aiFilterSegment.watchlistId, input.watchlistId),
        sql`${aiFilterSegment.status} IN ('pending', 'processing', 'paused_entitlement', 'paused_budget', 'paused_provider', 'paused_kill')`,
      ));
    await tx.insert(aiFilterEvent).values({
      watchlistId: input.watchlistId,
      ownerId: input.ownerId,
      queryVersionId: queryVersion.id,
      type: "disabled",
      payload: {},
      idempotencyKey: `query:${queryVersion.id}:disabled:${now.toISOString()}`,
      createdAt: now,
    });
  });
}

function publicStatus(
  enabled: boolean,
  segmentStatus: string | null,
  stopReason: string | null,
): AiFilterOwnerState["status"] {
  if (!enabled) return "disabled";
  switch (segmentStatus) {
    case "pending":
    case "processing":
    case "completed":
      return "processing";
    case "caught_up":
      return "caught_up";
    case "paused_budget":
      return "paused_budget";
    case "paused_entitlement":
      return "paused_entitlement";
    case "paused_provider":
      return stopReason === "cache_singleflight_wait"
        ? "waiting_for_jev"
        : "provider_unavailable";
    case "paused_kill":
      return "paused_kill";
    case "cancelled":
      return "cancelled";
    case "failed":
      return "failed";
    default:
      return "idle";
  }
}

export async function getAiFilterOwnerState(input: {
  ownerId: string;
  watchlistId: string;
  now?: Date;
}): Promise<AiFilterOwnerState> {
  const now = input.now ?? new Date();
  return db.transaction(async (tx) => {
    await ownedWatchlist(tx, input.ownerId, input.watchlistId);
    const [configuration] = await tx
      .select()
      .from(aiFilterConfiguration)
      .where(and(
        eq(aiFilterConfiguration.watchlistId, input.watchlistId),
        eq(aiFilterConfiguration.ownerId, input.ownerId),
      ))
      .limit(1);
    if (!configuration) throw new AiFilterNotFoundError();
    const [queryVersion] = await tx
      .select()
      .from(aiFilterQueryVersion)
      .where(and(
        eq(aiFilterQueryVersion.configurationId, configuration.id),
        eq(aiFilterQueryVersion.revision, configuration.currentRevision),
      ))
      .limit(1);
    if (!queryVersion) throw new AiFilterNotFoundError();
    const [[latestSegment], [counts], [budget], [event]] = await Promise.all([
      tx
        .select({
          status: aiFilterSegment.status,
          selectionOffset: aiFilterSegment.selectionOffset,
          scannedCount: aiFilterSegment.scannedCount,
          cursor: aiFilterSegment.cursor,
          stopReason: aiFilterSegment.stopReason,
        })
        .from(aiFilterSegment)
        .where(and(
          eq(aiFilterSegment.watchlistId, input.watchlistId),
          eq(aiFilterSegment.queryVersionId, queryVersion.id),
        ))
        .orderBy(desc(aiFilterSegment.createdAt))
        .limit(1),
      tx
        .select({
          total: sql<number>`count(*)::integer`,
          accepted: sql<number>`count(*) FILTER (WHERE COALESCE(${aiFilterDecision.userOverride}, ${aiFilterDecision.modelDecision}) = 'accepted')::integer`,
          rejected: sql<number>`count(*) FILTER (WHERE COALESCE(${aiFilterDecision.userOverride}, ${aiFilterDecision.modelDecision}) = 'rejected')::integer`,
        })
        .from(aiFilterDecision)
        .where(and(
          eq(aiFilterDecision.watchlistId, input.watchlistId),
          eq(aiFilterDecision.queryVersionId, queryVersion.id),
          gt(aiFilterDecision.expiresAt, now),
        )),
      tx
        .select({
          actualNanodollars: aiFilterBudgetAccount.actualNanodollars,
          reservedNanodollars: aiFilterBudgetAccount.reservedNanodollars,
        })
        .from(aiFilterBudgetAccount)
        .where(and(
          eq(aiFilterBudgetAccount.scope, "user"),
          eq(aiFilterBudgetAccount.scopeKey, input.ownerId),
          eq(aiFilterBudgetAccount.monthStart, monthStartUtc(now)),
        ))
        .limit(1),
      tx
        .select({ sequence: aiFilterEvent.sequence })
        .from(aiFilterEvent)
        .where(and(
          eq(aiFilterEvent.watchlistId, input.watchlistId),
          eq(aiFilterEvent.queryVersionId, queryVersion.id),
        ))
        .orderBy(desc(aiFilterEvent.sequence))
        .limit(1),
    ]);
    const entitled = await activeEntitlement(tx, input.ownerId, now);
    const enabled = configuration.status === "enabled";
    return Object.freeze({
      watchlistId: input.watchlistId,
      enabled,
      entitled,
      query: queryVersion.queryText,
      queryRevision: queryVersion.revision,
      queryVersionId: queryVersion.id,
      status: publicStatus(
        enabled,
        latestSegment?.status ?? null,
        latestSegment?.stopReason ?? null,
      ),
      counts: Object.freeze({
        accepted: counts?.accepted ?? 0,
        rejected: counts?.rejected ?? 0,
        total: counts?.total ?? 0,
      }),
      progress: Object.freeze({
        selectionOffset: latestSegment?.selectionOffset ?? 0,
        scannedCount: latestSegment?.scannedCount ?? 0,
        completedCount: latestSegment?.cursor ?? 0,
        stopReason: latestSegment?.stopReason ?? null,
      }),
      budget: Object.freeze({
        actualNanodollars: budget?.actualNanodollars ?? 0,
        reservedNanodollars: budget?.reservedNanodollars ?? 0,
      }),
      lastCaughtUpAt: configuration.lastCaughtUpAt?.toISOString() ?? null,
      latestEventSequence: event?.sequence ?? 0,
    });
  });
}

export async function listAiFilterEvents(input: {
  ownerId: string;
  watchlistId: string;
  after: number;
  limit?: number;
}) {
  if (!Number.isSafeInteger(input.after) || input.after < 0) {
    throw new TypeError("AI filter event cursor is invalid");
  }
  const limit = Math.min(100, Math.max(1, input.limit ?? 50));
  await db.transaction((tx) => ownedWatchlist(tx, input.ownerId, input.watchlistId));
  return db
    .select({
      sequence: aiFilterEvent.sequence,
      type: aiFilterEvent.type,
      payload: aiFilterEvent.payload,
      createdAt: aiFilterEvent.createdAt,
    })
    .from(aiFilterEvent)
    .where(and(
      eq(aiFilterEvent.watchlistId, input.watchlistId),
      gt(aiFilterEvent.sequence, input.after),
    ))
    .orderBy(aiFilterEvent.sequence)
    .limit(limit)
    .then((events) => events.map((event) => ({
      ...event,
      createdAt: event.createdAt.toISOString(),
    })));
}

export async function getAiFilterEstimateContext(input: {
  ownerId: string;
  watchlistId: string;
  now?: Date;
}) {
  const now = input.now ?? new Date();
  return db.transaction(async (tx) => {
    await ownedWatchlist(tx, input.ownerId, input.watchlistId);
    const [[budget], isEntitled] = await Promise.all([
      tx
        .select({
          actualNanodollars: aiFilterBudgetAccount.actualNanodollars,
          reservedNanodollars: aiFilterBudgetAccount.reservedNanodollars,
        })
        .from(aiFilterBudgetAccount)
        .where(and(
          eq(aiFilterBudgetAccount.scope, "user"),
          eq(aiFilterBudgetAccount.scopeKey, input.ownerId),
          eq(aiFilterBudgetAccount.monthStart, monthStartUtc(now)),
        ))
        .limit(1),
      activeEntitlement(tx, input.ownerId, now),
    ]);
    return {
      entitled: isEntitled,
      actualNanodollars: budget?.actualNanodollars ?? 0,
      reservedNanodollars: budget?.reservedNanodollars ?? 0,
    };
  });
}
