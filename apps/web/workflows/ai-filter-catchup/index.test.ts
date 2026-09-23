import { beforeEach, describe, expect, it, vi } from "vitest";

const runCatchupStep = vi.hoisted(() => vi.fn());
vi.mock("./steps", () => ({ runCatchupStep }));

import { aiFilterCatchupWorkflow } from ".";

const input = {
  ownerId: "owner-1",
  watchlistId: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  leaseOwner: "worker-1",
  demandTargetOffset: 500,
};

describe("AI filter catch-up workflow", () => {
  beforeEach(() => vi.clearAllMocks());

  it("prefetches the runway but never runs more than ten bounded segments", async () => {
    runCatchupStep.mockResolvedValue({ status: "continue", segmentId: "segment" });

    await expect(aiFilterCatchupWorkflow(input)).resolves.toEqual({
      status: "continue",
      segmentId: "segment",
    });
    expect(runCatchupStep).toHaveBeenCalledTimes(10);
  });

  it("stops chaining on a typed budget pause", async () => {
    runCatchupStep.mockResolvedValue({
      status: "paused_budget",
      segmentId: "segment-1",
    });
    await expect(aiFilterCatchupWorkflow(input)).resolves.toMatchObject({
      status: "paused_budget",
    });
    expect(runCatchupStep).toHaveBeenCalledOnce();
  });
});
