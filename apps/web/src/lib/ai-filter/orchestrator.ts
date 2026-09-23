import { buildAiFilterCacheIdentity } from "./cache-key";
import type { NormalizedClassifierInputV1 } from "./classifier-input";
import type { AiFilterDecisionValue } from "./contract";
import { type JevBatchResult, JevClientError } from "./jev-client";
import {
  JEV_BATCH_SIZE,
  JEV_INPUT_PRICE_NANODOLLARS_PER_TOKEN,
  JEV_MAX_CALL_RESERVATION_NANODOLLARS,
  JEV_MAX_INPUT_TOKENS_PER_CALL,
  jevCostNanodollars,
} from "./policy";

export type AiFilterExecutionContext = Readonly<{
  ownerId: string;
  watchlistId: string;
  queryVersionId: string;
  segmentId: string;
  queryText: string;
  leaseOwner: string;
}>;

export type AiFilterExecutionCandidate = Readonly<{
  candidateId: string;
  postingFirstSeenAt: string;
  expiresAt: string;
  classifierInput: NormalizedClassifierInputV1;
}>;

export type AiFilterCandidateBinding = AiFilterExecutionCandidate & Readonly<{
  cacheKey: string;
}>;

export type AiFilterCacheHit = Readonly<{
  binding: AiFilterCandidateBinding;
  decision: AiFilterDecisionValue;
}>;

export type AiFilterResolution = Readonly<{
  persistedDecisionCount: number;
  cacheHits: readonly AiFilterCacheHit[];
  claims: readonly AiFilterCandidateBinding[];
  waitingCacheKeys: readonly string[];
}>;

export type AiFilterBudgetReservation = Readonly<{
  idempotencyKey: string;
  reservedNanodollars: number;
}>;

export type AiFilterBudgetResult =
  | Readonly<{ status: "reserved"; reservation: AiFilterBudgetReservation }>
  | Readonly<{ status: "denied"; scope: "user" | "project" }>;

export interface AiFilterExecutionRepository {
  resolve(input: {
    context: AiFilterExecutionContext;
    candidates: readonly AiFilterCandidateBinding[];
    now: Date;
  }): Promise<AiFilterResolution>;
  materializeCacheHits(input: {
    context: AiFilterExecutionContext;
    hits: readonly AiFilterCacheHit[];
    now: Date;
  }): Promise<void>;
  reserveBudget(input: {
    context: AiFilterExecutionContext;
    cacheKeys: readonly string[];
    idempotencyKey: string;
    amountNanodollars: number;
    now: Date;
  }): Promise<AiFilterBudgetResult>;
  completeProviderBatch(input: {
    context: AiFilterExecutionContext;
    bindings: readonly AiFilterCandidateBinding[];
    result: JevBatchResult;
    reservation: AiFilterBudgetReservation;
    actualNanodollars: number;
    now: Date;
  }): Promise<void>;
  failProviderBatch(input: {
    context: AiFilterExecutionContext;
    bindings: readonly AiFilterCandidateBinding[];
    reservation: AiFilterBudgetReservation;
    code: string;
    uncertain: boolean;
    providerAttempts: number;
    ambiguousAttempts: number;
    now: Date;
  }): Promise<void>;
  finishSegment(input: {
    context: AiFilterExecutionContext;
    status:
      | "completed"
      | "paused_budget"
      | "paused_provider"
      | "paused_kill";
    stopReason: string | null;
    completedCount: number;
    telemetry: AiFilterMutableCounters;
    now: Date;
  }): Promise<void>;
}

export interface AiFilterJevClassifier {
  classify(input: {
    normalizedQuery: string;
    jobs: readonly NormalizedClassifierInputV1[];
    signal?: AbortSignal;
  }): Promise<JevBatchResult>;
}

export type AiFilterSegmentOutcome = Readonly<{
  status: "completed" | "paused_budget" | "paused_provider" | "paused_kill";
  persistedDecisionHits: number;
  globalCacheHits: number;
  globalCacheWaits: number;
  jevCalls: number;
  jevJobs: number;
  inputTokens: number;
  outputTokens: number;
  actualNanodollars: number;
}>;

type AiFilterMutableCounters = {
  -readonly [Key in Exclude<keyof AiFilterSegmentOutcome, "status">]:
    AiFilterSegmentOutcome[Key];
};

function batch<T>(values: readonly T[], size: number): T[][] {
  const batches: T[][] = [];
  for (let offset = 0; offset < values.length; offset += size) {
    batches.push(values.slice(offset, offset + size));
  }
  return batches;
}

function reservationKey(
  segmentId: string,
  leaseOwner: string,
  cacheKeys: readonly string[],
): string {
  // A resumed segment may legitimately re-claim a batch whose previous
  // worker died after reserving spend. The execution lease distinguishes
  // that retry while preserving idempotency within one workflow attempt.
  return `${segmentId}:${leaseOwner}:${cacheKeys.join(":")}`;
}

function initialOutcome(
  resolution: AiFilterResolution,
): AiFilterMutableCounters {
  return {
    persistedDecisionHits: resolution.persistedDecisionCount,
    globalCacheHits: resolution.cacheHits.length,
    globalCacheWaits: resolution.waitingCacheKeys.length,
    jevCalls: 0,
    jevJobs: 0,
    inputTokens: 0,
    outputTokens: 0,
    actualNanodollars: 0,
  };
}

function assertCandidates(
  candidates: readonly AiFilterExecutionCandidate[],
  now: Date,
): void {
  if (candidates.length > 50) throw new RangeError("AI filter segment exceeds 50 candidates");
  const ids = new Set<string>();
  for (const candidate of candidates) {
    if (candidate.candidateId !== candidate.classifierInput.payload.candidateId) {
      throw new TypeError("AI filter candidate binding does not match normalized input");
    }
    if (ids.has(candidate.candidateId)) {
      throw new TypeError("AI filter segment contains a duplicate candidate");
    }
    ids.add(candidate.candidateId);
    const firstSeen = Date.parse(candidate.postingFirstSeenAt);
    const expires = Date.parse(candidate.expiresAt);
    if (
      !Number.isFinite(firstSeen) ||
      !Number.isFinite(expires) ||
      expires <= firstSeen ||
      expires <= now.getTime()
    ) {
      throw new TypeError("AI filter candidate retention window is invalid");
    }
  }
}

function finalOutcome(
  status: AiFilterSegmentOutcome["status"],
  counters: AiFilterMutableCounters,
): AiFilterSegmentOutcome {
  return Object.freeze({ status, ...counters });
}

/**
 * Executes one durable, at-most-50-decision segment. Persistence owns
 * singleflight claims and budget transactions; this core owns ordering and
 * guarantees that a provider call is never made before reservation succeeds.
 */
export async function executeAiFilterSegment(input: {
  context: AiFilterExecutionContext;
  candidates: readonly AiFilterExecutionCandidate[];
  hmacSecret: string;
  repository: AiFilterExecutionRepository;
  classifier: AiFilterJevClassifier;
  executionEnabled: boolean;
  now?: Date;
  signal?: AbortSignal;
}): Promise<AiFilterSegmentOutcome> {
  const now = input.now ?? new Date();
  assertCandidates(input.candidates, now);
  const bindings = input.candidates.map((candidate) => Object.freeze({
    ...candidate,
    cacheKey: buildAiFilterCacheIdentity({
      queryText: input.context.queryText,
      classifierInput: candidate.classifierInput,
      hmacSecret: input.hmacSecret,
    }).cacheKey,
  }));
  const resolution = await input.repository.resolve({
    context: input.context,
    candidates: bindings,
    now,
  });
  const counters = initialOutcome(resolution);

  if (resolution.cacheHits.length > 0) {
    await input.repository.materializeCacheHits({
      context: input.context,
      hits: resolution.cacheHits,
      now,
    });
  }
  if (!input.executionEnabled && resolution.claims.length > 0) {
    await input.repository.finishSegment({
      context: input.context,
      status: "paused_kill",
      stopReason: "kill_switch_active",
      completedCount: counters.persistedDecisionHits + counters.globalCacheHits,
      telemetry: counters,
      now,
    });
    return finalOutcome("paused_kill", counters);
  }

  for (const claimedBatch of batch(resolution.claims, JEV_BATCH_SIZE)) {
    const idempotencyKey = reservationKey(
      input.context.segmentId,
      input.context.leaseOwner,
      claimedBatch.map((candidate) => candidate.cacheKey),
    );
    const budget = await input.repository.reserveBudget({
      context: input.context,
      cacheKeys: claimedBatch.map((candidate) => candidate.cacheKey),
      idempotencyKey,
      amountNanodollars: JEV_MAX_CALL_RESERVATION_NANODOLLARS,
      now,
    });
    if (budget.status === "denied") {
      await input.repository.finishSegment({
        context: input.context,
        status: "paused_budget",
        stopReason: `${budget.scope}_budget_exhausted`,
        completedCount:
          counters.persistedDecisionHits +
          counters.globalCacheHits +
          counters.jevJobs,
        telemetry: counters,
        now,
      });
      return finalOutcome("paused_budget", counters);
    }

    let result: JevBatchResult;
    try {
      result = await input.classifier.classify({
        normalizedQuery: buildAiFilterCacheIdentity({
          queryText: input.context.queryText,
          classifierInput: claimedBatch[0]!.classifierInput,
          hmacSecret: input.hmacSecret,
        }).normalizedQuery,
        jobs: claimedBatch.map((candidate) => candidate.classifierInput),
        signal: input.signal,
      });
    } catch (error) {
      const clientError = error instanceof JevClientError ? error : null;
      const uncertain = (clientError?.ambiguousFailedAttempts ?? 0) > 0;
      await input.repository.failProviderBatch({
        context: input.context,
        bindings: claimedBatch,
        reservation: budget.reservation,
        code: clientError?.code ?? "provider_unavailable",
        uncertain,
        providerAttempts: clientError?.attempts ?? 0,
        ambiguousAttempts: clientError?.ambiguousFailedAttempts ?? 0,
        now,
      });
      await input.repository.finishSegment({
        context: input.context,
        status: "paused_provider",
        stopReason: clientError?.code ?? "provider_unavailable",
        completedCount:
          counters.persistedDecisionHits +
          counters.globalCacheHits +
          counters.jevJobs,
        telemetry: counters,
        now,
      });
      return finalOutcome("paused_provider", counters);
    }

    const knownCost = jevCostNanodollars(
      result.usage.inputTokens,
      result.usage.outputTokens,
    );
    const uncertainCost =
      result.ambiguousFailedAttempts *
      JEV_MAX_INPUT_TOKENS_PER_CALL *
      JEV_INPUT_PRICE_NANODOLLARS_PER_TOKEN;
    const actualNanodollars = knownCost + uncertainCost;
    if (actualNanodollars > budget.reservation.reservedNanodollars) {
      await input.repository.failProviderBatch({
        context: input.context,
        bindings: claimedBatch,
        reservation: budget.reservation,
        code: "usage_exceeded_reservation",
        uncertain: true,
        providerAttempts: result.attempts,
        ambiguousAttempts: result.ambiguousFailedAttempts,
        now,
      });
      await input.repository.finishSegment({
        context: input.context,
        status: "paused_provider",
        stopReason: "usage_exceeded_reservation",
        completedCount:
          counters.persistedDecisionHits +
          counters.globalCacheHits +
          counters.jevJobs,
        telemetry: counters,
        now,
      });
      return finalOutcome("paused_provider", counters);
    }
    await input.repository.completeProviderBatch({
      context: input.context,
      bindings: claimedBatch,
      result,
      reservation: budget.reservation,
      actualNanodollars,
      now,
    });
    counters.jevCalls += 1;
    counters.jevJobs += claimedBatch.length;
    counters.inputTokens += result.usage.inputTokens;
    counters.outputTokens += result.usage.outputTokens;
    counters.actualNanodollars += actualNanodollars;
  }

  const status = resolution.waitingCacheKeys.length > 0
    ? "paused_provider"
    : "completed";
  await input.repository.finishSegment({
    context: input.context,
    status,
    stopReason: status === "completed" ? null : "cache_singleflight_wait",
    completedCount:
      counters.persistedDecisionHits + counters.globalCacheHits + counters.jevJobs,
    telemetry: counters,
    now,
  });
  return finalOutcome(status, counters);
}
