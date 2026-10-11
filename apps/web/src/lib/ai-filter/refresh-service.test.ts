import { beforeEach, describe, expect, it, vi } from "vitest";
import { withTestEnv } from "@/test-utils/env";
vi.mock("server-only", () => ({}));
import { runAiFilterRefreshSweep } from "./refresh-service";
withTestEnv({
  VERCEL_ENV: "production",
  AI_FILTER_ENABLED: "true",
  AI_FILTER_JEV_1_13_0_ENABLED: "true",
  TYPESAFE_AI_TOKEN: "fixture",
  AI_FILTER_CACHE_HMAC_SECRET: "x".repeat(32),
});
const target = {
  ownerId: "owner",
  watchlistId: "watchlist",
  queryVersionId: "query",
  kind: "freshness" as const,
  demandTargetOffset: 10000,
  generation: "1",
  leaseOwner: "shared-worker:fixture",
};
const deps = {
  claimWorker: vi.fn(),
  releaseWorker: vi.fn(),
  repair: vi.fn(),
  assertScope: vi.fn(),
  configure: vi.fn(),
  load: vi.fn(),
  caughtUp: vi.fn(),
  enqueue: vi.fn(),
  claim: vi.fn(),
  finish: vi.fn(),
  step: vi.fn(),
};
beforeEach(() => {
  vi.resetAllMocks();
  deps.claimWorker.mockResolvedValue(target.leaseOwner);
  deps.repair.mockResolvedValue([]);
  deps.claim.mockResolvedValue(null);
  deps.step.mockResolvedValue({ status: "continue", segmentId: "segment" });
});
describe("fixed shared worker", () => {
  it.each([1, 10, 100])(
    "idle demand with %s watchlists creates no processing runs",
    async () => {
      const result = await runAiFilterRefreshSweep({ dependencies: deps });
      expect(result.started).toBe(0);
      expect(deps.step).not.toHaveBeenCalled();
      expect(deps.claimWorker).toHaveBeenCalledOnce();
    },
  );
  it("never fans out or exceeds five checkpoints regardless of backlog", async () => {
    deps.claim.mockResolvedValue(target);
    const result = await runAiFilterRefreshSweep({ dependencies: deps });
    expect(result.claimed).toBe(5);
    expect(deps.step).toHaveBeenCalledTimes(5);
    expect(deps.step).toHaveBeenCalledWith(
      expect.objectContaining({ candidateLimit: 5 }),
    );
    expect(deps.releaseWorker).toHaveBeenCalledWith(target.leaseOwner, false);
  });
  it("joins an existing slot without preparing scopes", async () => {
    deps.claimWorker.mockResolvedValue(null);
    await runAiFilterRefreshSweep({ dependencies: deps });
    expect(deps.repair).not.toHaveBeenCalled();
  });
  it("persists failed work and releases the slot for the circuit breaker", async () => {
    deps.claim.mockResolvedValueOnce(target);
    deps.step.mockRejectedValue(new Error("private detail"));
    const result = await runAiFilterRefreshSweep({ dependencies: deps });
    expect(deps.finish).toHaveBeenCalledWith(
      target,
      "failed",
      "worker_unavailable",
    );
    expect(deps.releaseWorker).toHaveBeenCalledWith(target.leaseOwner, true);
    expect(JSON.stringify(result)).not.toContain("private detail");
  });
  it.each([
    ["VERCEL_ENV", "preview"],
    ["AI_FILTER_ENABLED", "false"],
    ["AI_FILTER_JEV_1_13_0_ENABLED", "false"],
    ["TYPESAFE_AI_TOKEN", ""],
    ["AI_FILTER_CACHE_HMAC_SECRET", "short"],
  ])("fails closed for %s", async (key, value) => {
    vi.stubEnv(key, value);
    try {
      expect(
        (await runAiFilterRefreshSweep({ dependencies: deps })).status,
      ).toBe("off");
      expect(deps.claimWorker).not.toHaveBeenCalled();
    } finally {
      vi.unstubAllEnvs();
    }
  });
  it("enqueues only discovered candidates in a bounded repair", async () => {
    deps.repair.mockResolvedValue([
      {
        ...target,
        configurationId: "config",
        query: "query",
        candidateLanguages: ["en"],
      },
    ]);
    deps.configure.mockResolvedValue({ queryVersionId: "query" });
    deps.load.mockResolvedValue({ scannedCount: 0 });
    await runAiFilterRefreshSweep({ dependencies: deps });
    expect(deps.enqueue).not.toHaveBeenCalled();
    deps.load.mockResolvedValue({ scannedCount: 1 });
    await runAiFilterRefreshSweep({ dependencies: deps });
    expect(deps.enqueue).toHaveBeenCalledOnce();
  });
});
