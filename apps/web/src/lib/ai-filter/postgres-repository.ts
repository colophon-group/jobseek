import "server-only";

import { randomUUID } from "node:crypto";
import {
  and,
  eq,
  gt,
  inArray,
  isNull,
  lt,
  ne,
  or,
  sql,
} from "drizzle-orm";

import { db } from "@/db";
import {
  aiFilterBudgetAccount,
  aiFilterConfiguration,
  aiFilterDecision,
  aiFilterEvent,
  aiFilterGlobalCache,
  aiFilterSegment,
  aiFilterUsageLedger,
  aiFilterQueryVersion,
  subscription,
  watchlist,
} from "@/db/schema";
import {
  CLASSIFIER_INPUT_NORMALIZER_VERSION,
  CLASSIFIER_INPUT_SCHEMA_VERSION,
} from "./classifier-input";
import type {
  AiFilterBudgetResult,
  AiFilterExecutionRepository,
  AiFilterResolution,
} from "./orchestrator";
import {
  AI_FILTER_CACHE_KEY_VERSION,
  AI_FILTER_PRICE_VERSION,
  AI_FILTER_PROMPT_VERSION,
  JEV_MODEL,
  exceedsAiFilterBudget,
} from "./policy";

const CACHE_LEASE_MS = 5 * 60 * 1_000;
const UNCERTAIN_RETRY_COOLDOWN_MS = CACHE_LEASE_MS;
const PROJECT_SCOPE_KEY = JEV_MODEL;

type Transaction = Parameters<Parameters<typeof db.transaction>[0]>[0];

export class AiFilterAuthorizationError extends Error {
  constructor() {
    super("AI filter resource was not found");
    this.name = "AiFilterAuthorizationError";
  }
}

export class AiFilterRepositoryError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "AiFilterRepositoryError";
  }
}

function monthStartUtc(now: Date): Date {
  return new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 1));
}

async function advisoryLock(tx: Transaction, value: string): Promise<void> {
  await tx.execute(
    sql`SELECT pg_advisory_xact_lock(hashtextextended(${value}, 731_931))`,
  );
}

async function assertSpendAuthorized(
  tx: Transaction,
  input: {
    ownerId: string;
    watchlistId: string;
    queryVersionId: string;
    now: Date;
  },
): Promise<void> {
  const [row] = await tx
    .select({ id: watchlist.id })
    .from(watchlist)
    .innerJoin(
      subscription,
      and(
        eq(subscription.userId, watchlist.userId),
        eq(subscription.status, "active"),
        eq(subscription.plan, "unlimited"),
        or(isNull(subscription.endsAt), gt(subscription.endsAt, input.now)),
      ),
    )
    .innerJoin(
      aiFilterConfiguration,
      and(
        eq(aiFilterConfiguration.watchlistId, watchlist.id),
        eq(aiFilterConfiguration.ownerId, watchlist.userId),
        eq(aiFilterConfiguration.status, "enabled"),
      ),
    )
    .innerJoin(
      aiFilterQueryVersion,
      and(
        eq(aiFilterQueryVersion.configurationId, aiFilterConfiguration.id),
        eq(aiFilterQueryVersion.revision, aiFilterConfiguration.currentRevision),
      ),
    )
    .where(
      and(
        eq(watchlist.id, input.watchlistId),
        eq(watchlist.userId, input.ownerId),
        eq(aiFilterQueryVersion.id, input.queryVersionId),
      ),
    )
    .limit(1);
  if (!row) throw new AiFilterAuthorizationError();
}

async function assertExecutionClaim(
  tx: Transaction,
  input: {
    ownerId: string;
    watchlistId: string;
    queryVersionId: string;
    segmentId: string;
    leaseOwner: string;
    cacheKeys: readonly string[];
    now: Date;
  },
): Promise<void> {
  const [segment] = await tx
    .select({ id: aiFilterSegment.id })
    .from(aiFilterSegment)
    .where(and(
      eq(aiFilterSegment.id, input.segmentId),
      eq(aiFilterSegment.ownerId, input.ownerId),
      eq(aiFilterSegment.watchlistId, input.watchlistId),
      eq(aiFilterSegment.queryVersionId, input.queryVersionId),
      eq(aiFilterSegment.status, "processing"),
      eq(aiFilterSegment.leaseOwner, input.leaseOwner),
      gt(aiFilterSegment.leaseExpiresAt, input.now),
    ))
    .for("update")
    .limit(1);
  if (!segment) throw new AiFilterAuthorizationError();

  const claims = await tx
    .select({ cacheKey: aiFilterGlobalCache.cacheKey })
    .from(aiFilterGlobalCache)
    .where(and(
      inArray(aiFilterGlobalCache.cacheKey, [...input.cacheKeys]),
      eq(aiFilterGlobalCache.status, "pending"),
      eq(aiFilterGlobalCache.leaseOwner, input.leaseOwner),
      gt(aiFilterGlobalCache.leaseExpiresAt, input.now),
    ))
    .for("update");
  if (new Set(claims.map((claim) => claim.cacheKey)).size !== input.cacheKeys.length) {
    throw new AiFilterRepositoryError("AI filter cache claim was lost before spend");
  }
}

function candidatePairKey(candidateId: string, contentIdentity: string): string {
  return `${candidateId}:${contentIdentity}`;
}

function safeFailureCode(code: string): string {
  return /^[a-z][a-z0-9_]{0,63}$/.test(code)
    ? code
    : "provider_unavailable";
}

export class PostgresAiFilterExecutionRepository
  implements AiFilterExecutionRepository {
  constructor(private readonly executionPolicy: {
    user: number | null;
    project: number | null;
  }) {
    for (const [scope, limit] of [
      ["user", executionPolicy.user],
      ["project", executionPolicy.project],
    ] as const) {
      if (limit !== null && (!Number.isSafeInteger(limit) || limit <= 0)) {
        throw new RangeError(`AI filter ${scope} budget must be a positive safe integer`);
      }
    }
  }

  async resolve(
    input: Parameters<AiFilterExecutionRepository["resolve"]>[0],
  ): Promise<AiFilterResolution> {
    return db.transaction(async (tx) => {
      const candidateIds = input.candidates.map((candidate) => candidate.candidateId);
      const persisted = candidateIds.length === 0
        ? []
        : await tx
            .select({
              candidateId: aiFilterDecision.candidateId,
              contentIdentity: aiFilterDecision.contentIdentity,
            })
            .from(aiFilterDecision)
            .where(and(
              eq(aiFilterDecision.watchlistId, input.context.watchlistId),
              eq(aiFilterDecision.queryVersionId, input.context.queryVersionId),
              inArray(aiFilterDecision.candidateId, candidateIds),
              gt(aiFilterDecision.expiresAt, input.now),
            ));
      const persistedKeys = new Set(
        persisted.map((decision) =>
          candidatePairKey(decision.candidateId, decision.contentIdentity)),
      );
      const matchedPersistedCount = input.candidates.reduce(
        (count, candidate) => count + Number(persistedKeys.has(candidatePairKey(
          candidate.candidateId,
          candidate.classifierInput.contentIdentity,
        ))),
        0,
      );
      const unresolved = input.candidates.filter((candidate) =>
        !persistedKeys.has(candidatePairKey(
          candidate.candidateId,
          candidate.classifierInput.contentIdentity,
        )),
      );
      if (unresolved.length === 0) {
        return {
          persistedDecisionCount: matchedPersistedCount,
          cacheHits: [],
          claims: [],
          waitingCacheKeys: [],
        };
      }

      const existingRows = await tx
        .select()
        .from(aiFilterGlobalCache)
        .where(inArray(
          aiFilterGlobalCache.cacheKey,
          unresolved.map((candidate) => candidate.cacheKey),
        ));
      const existingByKey = new Map(
        existingRows.map((row) => [row.cacheKey, row]),
      );
      const cacheHits: AiFilterResolution["cacheHits"][number][] = [];
      const claims: AiFilterResolution["claims"][number][] = [];
      const waitingCacheKeys: string[] = [];
      const leaseExpiresAt = new Date(input.now.getTime() + CACHE_LEASE_MS);

      for (const candidate of unresolved) {
        const existing = existingByKey.get(candidate.cacheKey);
        if (
          existing?.status === "ready" &&
          existing.decision &&
          existing.expiresAt.getTime() > input.now.getTime()
        ) {
          cacheHits.push({ binding: candidate, decision: existing.decision });
          continue;
        }

        if (!existing) {
          const inserted = await tx
            .insert(aiFilterGlobalCache)
            .values({
              cacheKey: candidate.cacheKey,
              keyVersion: AI_FILTER_CACHE_KEY_VERSION,
              contentIdentity: candidate.classifierInput.contentIdentity,
              status: "pending",
              model: JEV_MODEL,
              promptVersion: AI_FILTER_PROMPT_VERSION,
              schemaVersion: CLASSIFIER_INPUT_SCHEMA_VERSION,
              normalizerVersion: CLASSIFIER_INPUT_NORMALIZER_VERSION,
              leaseOwner: input.context.leaseOwner,
              leaseExpiresAt,
              expiresAt: new Date(candidate.expiresAt),
            })
            .onConflictDoNothing()
            .returning({ cacheKey: aiFilterGlobalCache.cacheKey });
          if (inserted.length === 1) {
            claims.push(candidate);
            continue;
          }
        }

        const [unresolvedReservation] = await tx
          .select({
            id: aiFilterUsageLedger.id,
            status: aiFilterUsageLedger.status,
            ownerId: aiFilterUsageLedger.ownerId,
            monthStart: aiFilterUsageLedger.monthStart,
            reservedNanodollars: aiFilterUsageLedger.reservedNanodollars,
          })
          .from(aiFilterUsageLedger)
          .where(and(
            inArray(aiFilterUsageLedger.status, ["reserved", "uncertain"]),
            sql`${candidate.cacheKey} = ANY(${aiFilterUsageLedger.cacheKeys})`,
          ))
          .for("update")
          .limit(1);

        const pendingLeaseExpired = Boolean(
          existing?.status === "pending" &&
          existing.leaseExpiresAt &&
          existing.leaseExpiresAt.getTime() < input.now.getTime(),
        );
        const uncertainCooldownElapsed = Boolean(
          existing?.status === "failed" &&
          existing.failureCode === "uncertain_provider_unavailable" &&
          existing.updatedAt.getTime() <=
            input.now.getTime() - UNCERTAIN_RETRY_COOLDOWN_MS,
        );
        let reservationBlocksClaim = Boolean(unresolvedReservation);
        if (
          unresolvedReservation?.status === "reserved" &&
          (pendingLeaseExpired || !existing)
        ) {
          // The worker disappeared after reserving spend. Conservatively
          // charge the full reservation (the provider may have received the
          // request), release the account reservation, and let a new worker
          // retry the expired cache claim. Otherwise one orphaned ledger row
          // strands these candidates forever on cache_singleflight_wait.
          const reconciled = await tx
            .update(aiFilterUsageLedger)
            .set({
              status: "uncertain",
              actualNanodollars: unresolvedReservation.reservedNanodollars,
              ambiguousAttempts: sql`${aiFilterUsageLedger.ambiguousAttempts} + 1`,
              reconciledAt: input.now,
            })
            .where(and(
              eq(aiFilterUsageLedger.id, unresolvedReservation.id),
              eq(aiFilterUsageLedger.status, "reserved"),
            ))
            .returning({ id: aiFilterUsageLedger.id });
          if (reconciled.length === 1) {
            await this.reconcileAccounts(tx, {
              ownerId: unresolvedReservation.ownerId,
              monthStart: unresolvedReservation.monthStart,
              reservedNanodollars: unresolvedReservation.reservedNanodollars,
              actualNanodollars: unresolvedReservation.reservedNanodollars,
              now: input.now,
            });
            reservationBlocksClaim = false;
          }
        } else if (
          unresolvedReservation?.status === "uncertain" &&
          (pendingLeaseExpired || uncertainCooldownElapsed || !existing)
        ) {
          reservationBlocksClaim = false;
        }
        if (reservationBlocksClaim) {
          waitingCacheKeys.push(candidate.cacheKey);
          continue;
        }

        const reclaimed = await tx
          .update(aiFilterGlobalCache)
          .set({
            status: "pending",
            decision: null,
            failureCode: null,
            leaseOwner: input.context.leaseOwner,
            leaseExpiresAt,
            updatedAt: input.now,
          })
          .where(and(
            eq(aiFilterGlobalCache.cacheKey, candidate.cacheKey),
            gt(aiFilterGlobalCache.expiresAt, input.now),
            or(
              and(
                eq(aiFilterGlobalCache.status, "pending"),
                lt(aiFilterGlobalCache.leaseExpiresAt, input.now),
              ),
              and(
                eq(aiFilterGlobalCache.status, "failed"),
                or(
                  isNull(aiFilterGlobalCache.failureCode),
                  ne(aiFilterGlobalCache.failureCode, "uncertain_provider_unavailable"),
                  and(
                    eq(
                      aiFilterGlobalCache.failureCode,
                      "uncertain_provider_unavailable",
                    ),
                    lt(
                      aiFilterGlobalCache.updatedAt,
                      new Date(
                        input.now.getTime() - UNCERTAIN_RETRY_COOLDOWN_MS,
                      ),
                    ),
                  ),
                ),
              ),
            ),
          ))
          .returning({ cacheKey: aiFilterGlobalCache.cacheKey });
        if (reclaimed.length === 1) claims.push(candidate);
        else waitingCacheKeys.push(candidate.cacheKey);
      }

      return {
        persistedDecisionCount: matchedPersistedCount,
        cacheHits,
        claims,
        waitingCacheKeys,
      };
    });
  }

  async materializeCacheHits(
    input: Parameters<AiFilterExecutionRepository["materializeCacheHits"]>[0],
  ): Promise<void> {
    if (input.hits.length === 0) return;
    await db.insert(aiFilterDecision).values(input.hits.map(({ binding, decision }) => ({
      watchlistId: input.context.watchlistId,
      ownerId: input.context.ownerId,
      queryVersionId: input.context.queryVersionId,
      segmentId: input.context.segmentId,
      candidateId: binding.candidateId,
      contentIdentity: binding.classifierInput.contentIdentity,
      cacheKey: binding.cacheKey,
      modelDecision: decision,
      postingFirstSeenAt: new Date(binding.postingFirstSeenAt),
      decidedAt: input.now,
      expiresAt: new Date(binding.expiresAt),
      updatedAt: input.now,
    }))).onConflictDoNothing();
  }

  async reserveBudget(
    input: Parameters<AiFilterExecutionRepository["reserveBudget"]>[0],
  ): Promise<AiFilterBudgetResult> {
    return db.transaction(async (tx) => {
      await advisoryLock(tx, `ai-filter-reservation:${input.idempotencyKey}`);
      // Serialize the final spend authorization with enable/query-change/
      // disable so a configuration transition cannot slip between the check
      // and creation of a billable reservation.
      await tx.execute(
        sql`SELECT pg_advisory_xact_lock(hashtextextended(${`ai-filter-config:${input.context.watchlistId}`}, 119_027))`,
      );
      await assertSpendAuthorized(tx, {
        ownerId: input.context.ownerId,
        watchlistId: input.context.watchlistId,
        queryVersionId: input.context.queryVersionId,
        now: input.now,
      });
      await assertExecutionClaim(tx, {
        ...input.context,
        cacheKeys: input.cacheKeys,
        now: input.now,
      });
      const [existing] = await tx
        .select({
          status: aiFilterUsageLedger.status,
          reservedNanodollars: aiFilterUsageLedger.reservedNanodollars,
        })
        .from(aiFilterUsageLedger)
        .where(eq(aiFilterUsageLedger.idempotencyKey, input.idempotencyKey))
        .limit(1);
      if (existing) {
        if (existing.status !== "reserved") {
          throw new AiFilterRepositoryError("AI filter reservation is already final");
        }
        return {
          status: "reserved",
          reservation: {
            idempotencyKey: input.idempotencyKey,
            reservedNanodollars: existing.reservedNanodollars,
          },
        };
      }

      const monthStart = monthStartUtc(input.now);
      const accountKeys = [
        { scope: "project" as const, scopeKey: PROJECT_SCOPE_KEY },
        { scope: "user" as const, scopeKey: input.context.ownerId },
      ];
      await tx.insert(aiFilterBudgetAccount).values(accountKeys.map((key) => ({
        ...key,
        monthStart,
      }))).onConflictDoNothing();
      const accounts = await tx
        .select()
        .from(aiFilterBudgetAccount)
        .where(and(
          eq(aiFilterBudgetAccount.monthStart, monthStart),
          or(
            and(
              eq(aiFilterBudgetAccount.scope, "project"),
              eq(aiFilterBudgetAccount.scopeKey, PROJECT_SCOPE_KEY),
            ),
            and(
              eq(aiFilterBudgetAccount.scope, "user"),
              eq(aiFilterBudgetAccount.scopeKey, input.context.ownerId),
            ),
          ),
        ))
        .for("update");
      const accountByScope = new Map(accounts.map((account) => [account.scope, account]));
      for (const scope of ["user", "project"] as const) {
        const account = accountByScope.get(scope);
        if (!account) throw new AiFilterRepositoryError("AI filter budget account is missing");
        const limit = this.executionPolicy[scope];
        if (exceedsAiFilterBudget({
          actualNanodollars: account.actualNanodollars,
          reservedNanodollars: account.reservedNanodollars,
          nextReservationNanodollars: input.amountNanodollars,
          limitNanodollars: limit,
        })) {
          return { status: "denied", scope };
        }
      }

      await tx
        .update(aiFilterBudgetAccount)
        .set({
          reservedNanodollars:
            sql`${aiFilterBudgetAccount.reservedNanodollars} + ${input.amountNanodollars}`,
          updatedAt: input.now,
        })
        .where(and(
          eq(aiFilterBudgetAccount.monthStart, monthStart),
          or(
            and(
              eq(aiFilterBudgetAccount.scope, "project"),
              eq(aiFilterBudgetAccount.scopeKey, PROJECT_SCOPE_KEY),
            ),
            and(
              eq(aiFilterBudgetAccount.scope, "user"),
              eq(aiFilterBudgetAccount.scopeKey, input.context.ownerId),
            ),
          ),
        ));
      await tx.insert(aiFilterUsageLedger).values({
        id: randomUUID(),
        idempotencyKey: input.idempotencyKey,
        ownerId: input.context.ownerId,
        segmentId: input.context.segmentId,
        status: "reserved",
        monthStart,
        model: JEV_MODEL,
        priceVersion: AI_FILTER_PRICE_VERSION,
        cacheKeys: [...input.cacheKeys],
        reservedNanodollars: input.amountNanodollars,
        actualNanodollars: 0,
      });
      return {
        status: "reserved",
        reservation: {
          idempotencyKey: input.idempotencyKey,
          reservedNanodollars: input.amountNanodollars,
        },
      };
    });
  }

  async completeProviderBatch(
    input: Parameters<AiFilterExecutionRepository["completeProviderBatch"]>[0],
  ): Promise<void> {
    await db.transaction(async (tx) => {
      const ledger = await this.lockReservedLedger(tx, input.reservation.idempotencyKey);
      if (!ledger) return;
      if (input.actualNanodollars > ledger.reservedNanodollars) {
        throw new AiFilterRepositoryError("Jev usage exceeded its reservation");
      }
      const decisionByCandidate = new Map(
        input.result.decisions.map((decision) => [decision.candidateId, decision.decision]),
      );
      if (decisionByCandidate.size !== input.bindings.length) {
        throw new AiFilterRepositoryError("Jev decision count does not match cache claims");
      }
      for (const binding of input.bindings) {
        const decision = decisionByCandidate.get(binding.candidateId);
        if (!decision) throw new AiFilterRepositoryError("Jev decision attribution failed");
        const updated = await tx
          .update(aiFilterGlobalCache)
          .set({
            status: "ready",
            decision,
            leaseOwner: null,
            leaseExpiresAt: null,
            inputTokens: input.result.usage.inputTokens,
            outputTokens: input.result.usage.outputTokens,
            priceVersion: AI_FILTER_PRICE_VERSION,
            failureCode: null,
            updatedAt: input.now,
          })
          .where(and(
            eq(aiFilterGlobalCache.cacheKey, binding.cacheKey),
            eq(aiFilterGlobalCache.status, "pending"),
            eq(aiFilterGlobalCache.leaseOwner, input.context.leaseOwner),
          ))
          .returning({ cacheKey: aiFilterGlobalCache.cacheKey });
        if (updated.length !== 1) {
          throw new AiFilterRepositoryError("AI filter cache claim was lost");
        }
        await tx.insert(aiFilterDecision).values({
          watchlistId: input.context.watchlistId,
          ownerId: input.context.ownerId,
          queryVersionId: input.context.queryVersionId,
          segmentId: input.context.segmentId,
          candidateId: binding.candidateId,
          contentIdentity: binding.classifierInput.contentIdentity,
          cacheKey: binding.cacheKey,
          modelDecision: decision,
          postingFirstSeenAt: new Date(binding.postingFirstSeenAt),
          decidedAt: input.now,
          expiresAt: new Date(binding.expiresAt),
          updatedAt: input.now,
        }).onConflictDoNothing();
      }
      await this.reconcileAccounts(tx, {
        ownerId: input.context.ownerId,
        monthStart: ledger.monthStart,
        reservedNanodollars: ledger.reservedNanodollars,
        actualNanodollars: input.actualNanodollars,
        now: input.now,
      });
      await tx
        .update(aiFilterUsageLedger)
        .set({
          status: "reconciled",
          actualNanodollars: input.actualNanodollars,
          inputTokens: input.result.usage.inputTokens,
          outputTokens: input.result.usage.outputTokens,
          providerAttempts: input.result.attempts,
          ambiguousAttempts: input.result.ambiguousFailedAttempts,
          reconciledAt: input.now,
        })
        .where(eq(aiFilterUsageLedger.idempotencyKey, input.reservation.idempotencyKey));
      await tx.insert(aiFilterEvent).values({
        watchlistId: input.context.watchlistId,
        ownerId: input.context.ownerId,
        queryVersionId: input.context.queryVersionId,
        type: "batch_completed",
        payload: {
          count: input.bindings.length,
          model: input.result.model,
          inputTokens: input.result.usage.inputTokens,
          outputTokens: input.result.usage.outputTokens,
          actualNanodollars: input.actualNanodollars,
          attempts: input.result.attempts,
          ambiguousFailedAttempts: input.result.ambiguousFailedAttempts,
          latencyMs: Math.round(input.result.latencyMs),
        },
        idempotencyKey: `batch:${input.reservation.idempotencyKey}:completed`,
        createdAt: input.now,
      }).onConflictDoNothing();
    });
  }

  async failProviderBatch(
    input: Parameters<AiFilterExecutionRepository["failProviderBatch"]>[0],
  ): Promise<void> {
    await db.transaction(async (tx) => {
      const ledger = await this.lockReservedLedger(tx, input.reservation.idempotencyKey);
      if (!ledger) return;
      const actualNanodollars = input.uncertain ? ledger.reservedNanodollars : 0;
      await this.reconcileAccounts(tx, {
        ownerId: input.context.ownerId,
        monthStart: ledger.monthStart,
        reservedNanodollars: ledger.reservedNanodollars,
        actualNanodollars,
        now: input.now,
      });
      const code = safeFailureCode(input.code);
      await tx
        .update(aiFilterGlobalCache)
        .set({
          status: "failed",
          decision: null,
          leaseOwner: null,
          leaseExpiresAt: null,
          failureCode: input.uncertain
            ? "uncertain_provider_unavailable"
            : code,
          updatedAt: input.now,
        })
        .where(and(
          inArray(
            aiFilterGlobalCache.cacheKey,
            input.bindings.map((binding) => binding.cacheKey),
          ),
          eq(aiFilterGlobalCache.status, "pending"),
          eq(aiFilterGlobalCache.leaseOwner, input.context.leaseOwner),
        ));
      await tx
        .update(aiFilterUsageLedger)
        .set({
          status: input.uncertain ? "uncertain" : "released",
          actualNanodollars,
          providerAttempts: input.providerAttempts,
          ambiguousAttempts: input.ambiguousAttempts,
          reconciledAt: input.now,
        })
        .where(eq(aiFilterUsageLedger.idempotencyKey, input.reservation.idempotencyKey));
    });
  }

  async finishSegment(
    input: Parameters<AiFilterExecutionRepository["finishSegment"]>[0],
  ): Promise<void> {
    await db.transaction(async (tx) => {
      const updated = await tx
        .update(aiFilterSegment)
        .set({
          status: input.status,
          stopReason: input.stopReason,
          cursor: input.completedCount,
          leaseOwner: null,
          leaseExpiresAt: null,
          completedAt: input.status === "completed" ? input.now : null,
          updatedAt: input.now,
        })
        .where(and(
          eq(aiFilterSegment.id, input.context.segmentId),
          eq(aiFilterSegment.ownerId, input.context.ownerId),
          eq(aiFilterSegment.watchlistId, input.context.watchlistId),
          eq(aiFilterSegment.queryVersionId, input.context.queryVersionId),
          eq(aiFilterSegment.status, "processing"),
          eq(aiFilterSegment.leaseOwner, input.context.leaseOwner),
        ))
        .returning({ id: aiFilterSegment.id });
      if (updated.length !== 1) return;
      await tx.insert(aiFilterEvent).values({
        watchlistId: input.context.watchlistId,
        ownerId: input.context.ownerId,
        queryVersionId: input.context.queryVersionId,
        type: input.status,
        payload: {
          completedCount: input.completedCount,
          stopReason: input.stopReason,
          ...input.telemetry,
        },
        idempotencyKey:
          `segment:${input.context.segmentId}:${input.status}:${input.completedCount}`,
        createdAt: input.now,
      }).onConflictDoNothing();
    });
  }

  private async lockReservedLedger(tx: Transaction, idempotencyKey: string) {
    await advisoryLock(tx, `ai-filter-reservation:${idempotencyKey}`);
    const [ledger] = await tx
      .select()
      .from(aiFilterUsageLedger)
      .where(eq(aiFilterUsageLedger.idempotencyKey, idempotencyKey))
      .for("update")
      .limit(1);
    if (!ledger) throw new AiFilterRepositoryError("AI filter reservation is missing");
    return ledger.status === "reserved" ? ledger : null;
  }

  private async reconcileAccounts(
    tx: Transaction,
    input: {
      ownerId: string;
      monthStart: Date;
      reservedNanodollars: number;
      actualNanodollars: number;
      now: Date;
    },
  ): Promise<void> {
    const rows = await tx
      .update(aiFilterBudgetAccount)
      .set({
        reservedNanodollars:
          sql`${aiFilterBudgetAccount.reservedNanodollars} - ${input.reservedNanodollars}`,
        actualNanodollars:
          sql`${aiFilterBudgetAccount.actualNanodollars} + ${input.actualNanodollars}`,
        updatedAt: input.now,
      })
      .where(and(
        eq(aiFilterBudgetAccount.monthStart, input.monthStart),
        gt(aiFilterBudgetAccount.reservedNanodollars, input.reservedNanodollars - 1),
        or(
          and(
            eq(aiFilterBudgetAccount.scope, "project"),
            eq(aiFilterBudgetAccount.scopeKey, PROJECT_SCOPE_KEY),
          ),
          and(
            eq(aiFilterBudgetAccount.scope, "user"),
            eq(aiFilterBudgetAccount.scopeKey, input.ownerId),
          ),
        ),
      ))
      .returning({ scope: aiFilterBudgetAccount.scope });
    if (rows.length !== 2) {
      throw new AiFilterRepositoryError("AI filter budget reconciliation lost an account");
    }
  }
}
