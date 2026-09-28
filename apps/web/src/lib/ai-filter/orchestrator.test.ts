import { afterEach, describe, expect, it, vi } from "vitest";

import {
  normalizeClassifierInputV1,
  type NormalizedClassifierInputV1,
} from "./classifier-input";
import type {
  AiFilterExecutionRepository,
  AiFilterResolution,
} from "./orchestrator";
import { AiFilterMiningPolicyError, executeAiFilterSegment } from "./orchestrator";
import { JevClientError } from "./jev-client";
import { JEV_MAX_CALL_RESERVATION_NANODOLLARS } from "./policy";

const context = {
  ownerId: "user-1",
  watchlistId: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  queryVersionId: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
  segmentId: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
  queryText: "remote Rust role",
  leaseOwner: "worker-1",
} as const;
const hmacSecret = "test-only-cache-hmac-secret-with-32-bytes";

function candidate(index: number) {
  const digit = String(index).padStart(12, "0");
  const candidateId = `11111111-1111-4111-8111-${digit}`;
  return {
    candidateId,
    postingFirstSeenAt: "2026-09-20T00:00:00.000Z",
    expiresAt: "2026-10-20T00:00:00.000Z",
    classifierInput: normalizeClassifierInputV1({
      candidateId,
      title: `Engineer ${index}`,
      companyName: "Acme",
      descriptionHtml: "<p>Build reliable systems.</p>",
      selectedDescriptionLocale: "en",
    }),
  };
}

function repository(resolutionFactory: (
  candidates: Parameters<AiFilterExecutionRepository["resolve"]>[0]["candidates"],
) => AiFilterResolution): AiFilterExecutionRepository {
  return {
    resolve: vi.fn(async ({ candidates }) => resolutionFactory(candidates)),
    materializeCacheHits: vi.fn(async () => undefined),
    reserveBudget: vi.fn(async ({ idempotencyKey, amountNanodollars }) => ({
      status: "reserved" as const,
      reservation: { idempotencyKey, reservedNanodollars: amountNanodollars },
    })),
    completeProviderBatch: vi.fn(async () => undefined),
    failProviderBatch: vi.fn(async () => undefined),
    finishSegment: vi.fn(async () => undefined),
  };
}

function jevResult(jobs: readonly ReturnType<typeof candidate>[]) {
  return {
    model: "jev-1.13.0" as const,
    promptVersion: "jev-job-fit-choice-v1" as const,
    decisions: jobs.map((job) => ({
      candidateId: job.candidateId,
      decision: "accepted" as const,
    })),
    usage: { inputTokens: 1_000, outputTokens: 20 },
    attempts: 1,
    ambiguousFailedAttempts: 0,
    latencyMs: 100,
  };
}

describe("AI filter segment orchestrator", () => {
  afterEach(() => vi.restoreAllMocks());

  it.each([
    {
      error: new JevClientError("invalid_request", {
        status: 402,
        attempts: 1,
        cause: new Error("private provider response and token"),
      }),
      expected: { code: "invalid_request", status: 402, attempts: 1, ambiguousAttempts: 0 },
    },
    {
      error: new JevClientError("provider_unavailable", {
        status: 503,
        attempts: 2,
        ambiguousFailedAttempts: 2,
        cause: new Error("private provider response and token"),
      }),
      expected: { code: "provider_unavailable", status: 503, attempts: 2, ambiguousAttempts: 2 },
    },
    {
      error: Object.assign(new Error("private provider response and token"), {
        code: "private error code",
        status: "private status",
      }),
      expected: { code: "provider_unavailable", status: null, attempts: 0, ambiguousAttempts: 0 },
    },
  ])("logs only allowlisted provider metadata: $expected.code/$expected.status", async ({ error, expected }) => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const repo = repository(bindings => ({
      persistedDecisionCount: 0, cacheHits: [], claims: bindings, waitingCacheKeys: [],
    }));
    const result = await executeAiFilterSegment({
      assertMiningAllowed: async () => undefined,
      context,
      candidates: [candidate(1)],
      hmacSecret,
      repository: repo,
      classifier: { classify: vi.fn().mockRejectedValue(error) },
      executionEnabled: true,
    });

    expect(result.status).toBe("paused_provider");
    expect(warn.mock.calls).toEqual([[
      "[ai-filter] provider batch failed",
      { segmentId: context.segmentId, ...expected },
    ]]);
    expect(repo.failProviderBatch).toHaveBeenCalledWith(expect.objectContaining({
      code: expected.code,
      providerAttempts: expected.attempts,
      ambiguousAttempts: expected.ambiguousAttempts,
      uncertain: expected.ambiguousAttempts > 0,
    }));
    expect(repo.finishSegment).toHaveBeenCalledWith(expect.objectContaining({
      status: "paused_provider", stopReason: expected.code,
    }));
  });

  it("never calls Jev on refresh when every decision is already materialized", async () => {
    const candidates = [candidate(1), candidate(2)];
    const repo = repository(() => ({
      persistedDecisionCount: 2,
      cacheHits: [],
      claims: [],
      waitingCacheKeys: [],
    }));
    const classifier = { classify: vi.fn() };
    const result = await executeAiFilterSegment({
      assertMiningAllowed: async () => undefined,
      context,
      candidates,
      hmacSecret,
      repository: repo,
      classifier,
      executionEnabled: true,
      now: new Date("2026-09-21T00:00:00.000Z"),
    });
    expect(result).toMatchObject({
      status: "completed",
      persistedDecisionHits: 2,
      jevCalls: 0,
    });
    expect(classifier.classify).not.toHaveBeenCalled();
    expect(repo.reserveBudget).not.toHaveBeenCalled();
  });

  it("materializes cross-user global-cache hits for free", async () => {
    const candidates = [candidate(1)];
    const repo = repository((bindings) => ({
      persistedDecisionCount: 0,
      cacheHits: [{ binding: bindings[0]!, decision: "rejected" }],
      claims: [],
      waitingCacheKeys: [],
    }));
    const classifier = { classify: vi.fn() };
    const result = await executeAiFilterSegment({
      assertMiningAllowed: async () => undefined,
      context,
      candidates,
      hmacSecret,
      repository: repo,
      classifier,
      executionEnabled: true,
    });
    expect(result.globalCacheHits).toBe(1);
    expect(repo.materializeCacheHits).toHaveBeenCalledTimes(1);
    expect(repo.reserveBudget).not.toHaveBeenCalled();
    expect(classifier.classify).not.toHaveBeenCalled();
  });

  it("reserves budget before each five-job Jev call and persists results", async () => {
    const candidates = Array.from({ length: 7 }, (_, index) => candidate(index + 1));
    const repo = repository((bindings) => ({
      persistedDecisionCount: 0,
      cacheHits: [],
      claims: bindings,
      waitingCacheKeys: [],
    }));
    const calls: string[] = [];
    vi.mocked(repo.reserveBudget).mockImplementation(async ({ idempotencyKey, amountNanodollars }) => {
      calls.push("reserve");
      return {
        status: "reserved",
        reservation: { idempotencyKey, reservedNanodollars: amountNanodollars },
      };
    });
    const classifier = {
      classify: vi.fn(async ({ jobs }: { jobs: readonly NormalizedClassifierInputV1[] }) => {
        calls.push("jev");
        const selected = candidates.filter((candidate) =>
          jobs.some((job) => job.payload.candidateId === candidate.candidateId),
        );
        return jevResult(selected);
      }),
    };
    const result = await executeAiFilterSegment({
      assertMiningAllowed: async () => undefined,
      context,
      candidates,
      hmacSecret,
      repository: repo,
      classifier,
      executionEnabled: true,
    });
    expect(calls).toEqual(["reserve", "jev", "reserve", "jev"]);
    expect(classifier.classify.mock.calls.map(([arg]) => arg.jobs.length)).toEqual([5, 2]);
    expect(repo.reserveBudget).toHaveBeenCalledWith(expect.objectContaining({
      amountNanodollars: JEV_MAX_CALL_RESERVATION_NANODOLLARS,
    }));
    expect(repo.completeProviderBatch).toHaveBeenCalledTimes(2);
    expect(result).toMatchObject({ status: "completed", jevCalls: 2, jevJobs: 7 });
  });

  it("uses a fresh reservation identity after a segment lease is resumed", async () => {
    const candidates = [candidate(1)];
    const reservationKeys: string[] = [];
    const run = async (leaseOwner: string) => {
      const repo = repository((bindings) => ({
        persistedDecisionCount: 0,
        cacheHits: [],
        claims: bindings,
        waitingCacheKeys: [],
      }));
      vi.mocked(repo.reserveBudget).mockImplementation(
        async ({ idempotencyKey, amountNanodollars }) => {
          reservationKeys.push(idempotencyKey);
          return {
            status: "reserved",
            reservation: {
              idempotencyKey,
              reservedNanodollars: amountNanodollars,
            },
          };
        },
      );
      await executeAiFilterSegment({
      assertMiningAllowed: async () => undefined,
        context: { ...context, leaseOwner },
        candidates,
        hmacSecret,
        repository: repo,
        classifier: { classify: vi.fn(async () => jevResult(candidates)) },
        executionEnabled: true,
      });
    };

    await run("lease-a");
    await run("lease-b");

    expect(reservationKeys).toHaveLength(2);
    expect(reservationKeys[0]).not.toBe(reservationKeys[1]);
  });

  it("pauses before Jev when the monthly spend reservation is denied", async () => {
    const candidates = [candidate(1)];
    const repo = repository((bindings) => ({
      persistedDecisionCount: 0,
      cacheHits: [],
      claims: bindings,
      waitingCacheKeys: [],
    }));
    vi.mocked(repo.reserveBudget).mockResolvedValue({ status: "denied", scope: "user" });
    const classifier = { classify: vi.fn() };
    const result = await executeAiFilterSegment({
      assertMiningAllowed: async () => undefined,
      context,
      candidates,
      hmacSecret,
      repository: repo,
      classifier,
      executionEnabled: true,
    });
    expect(result.status).toBe("paused_budget");
    expect(classifier.classify).not.toHaveBeenCalled();
    expect(repo.finishSegment).toHaveBeenCalledWith(expect.objectContaining({
      status: "paused_budget",
      stopReason: "user_budget_exhausted",
    }));
  });

  it("honors the kill switch after free cache work and before paid work", async () => {
    const candidates = [candidate(1), candidate(2)];
    const repo = repository((bindings) => ({
      persistedDecisionCount: 0,
      cacheHits: [{ binding: bindings[0]!, decision: "accepted" }],
      claims: [bindings[1]!],
      waitingCacheKeys: [],
    }));
    const classifier = { classify: vi.fn() };
    const result = await executeAiFilterSegment({
      assertMiningAllowed: async () => undefined,
      context,
      candidates,
      hmacSecret,
      repository: repo,
      classifier,
      executionEnabled: false,
    });
    expect(result).toMatchObject({ status: "paused_kill", globalCacheHits: 1 });
    expect(repo.materializeCacheHits).toHaveBeenCalledTimes(1);
    expect(repo.reserveBudget).not.toHaveBeenCalled();
    expect(classifier.classify).not.toHaveBeenCalled();
  });

  it("conservatively charges and strands a claim after an ambiguous paid response", async () => {
    const candidates = [candidate(1)];
    const repo = repository((bindings) => ({
      persistedDecisionCount: 0,
      cacheHits: [],
      claims: bindings,
      waitingCacheKeys: [],
    }));
    const classifier = {
      classify: vi.fn(async () => {
        throw new JevClientError("invalid_response", {
          ambiguousFailedAttempts: 1,
        });
      }),
    };

    const result = await executeAiFilterSegment({
      assertMiningAllowed: async () => undefined,
      context,
      candidates,
      hmacSecret,
      repository: repo,
      classifier,
      executionEnabled: true,
    });

    expect(result.status).toBe("paused_provider");
    expect(repo.failProviderBatch).toHaveBeenCalledWith(expect.objectContaining({
      code: "invalid_response",
      uncertain: true,
      ambiguousAttempts: 1,
    }));
  });
});


describe("TDM restrictions on retained classifier inputs", () => {
  it("blocks cache reuse when an already loaded candidate becomes reserved", async () => {
    const repo = repository(bindings => ({ persistedDecisionCount: 0, cacheHits: [{ binding: bindings[0]!, decision: "accepted" }], claims: [], waitingCacheKeys: [] }));
    const classifier = { classify: vi.fn() };
    const result = await executeAiFilterSegment({
      context, candidates: [candidate(1)], hmacSecret, repository: repo, classifier, executionEnabled: true,
      assertMiningAllowed: async () => { throw new AiFilterMiningPolicyError("tdm_reserved"); },
    });
    expect(result.status).toBe("paused_provider");
    expect(repo.resolve).not.toHaveBeenCalled();
    expect(repo.materializeCacheHits).not.toHaveBeenCalled();
    expect(classifier.classify).not.toHaveBeenCalled();
  });
  it("rechecks after budget reservation and never sends newly restricted text", async () => {
    const repo = repository(bindings => ({ persistedDecisionCount: 0, cacheHits: [], claims: bindings, waitingCacheKeys: [] }));
    const check = vi.fn().mockResolvedValueOnce(undefined).mockRejectedValueOnce(new AiFilterMiningPolicyError("tdm_reserved"));
    const classifier = { classify: vi.fn() };
    const result = await executeAiFilterSegment({
      context, candidates: [candidate(1)], hmacSecret, repository: repo, classifier, executionEnabled: true,
      assertMiningAllowed: check,
    });
    expect(result.status).toBe("paused_provider");
    expect(repo.reserveBudget).toHaveBeenCalledTimes(1);
    expect(classifier.classify).not.toHaveBeenCalled();
    expect(repo.failProviderBatch).toHaveBeenCalledWith(expect.objectContaining({ code: "tdm_reserved", uncertain: false, providerAttempts: 0 }));
  });
});
