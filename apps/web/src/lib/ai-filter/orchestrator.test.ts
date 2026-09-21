import { describe, expect, it, vi } from "vitest";

import {
  normalizeClassifierInputV1,
  type NormalizedClassifierInputV1,
} from "./classifier-input";
import type {
  AiFilterExecutionRepository,
  AiFilterResolution,
} from "./orchestrator";
import { executeAiFilterSegment } from "./orchestrator";
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
