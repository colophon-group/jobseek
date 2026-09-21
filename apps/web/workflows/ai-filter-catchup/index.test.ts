import { beforeEach, describe, expect, it, vi } from "vitest";

const runCatchupStep = vi.hoisted(() => vi.fn());
vi.mock("./steps", () => ({ runCatchupStep }));

import { aiFilterCatchupWorkflow } from ".";

const input = {
  ownerId: "owner-1",
  watchlistId: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  leaseOwner: "worker-1",
  demandTargetOffset: 60,
};

describe("AI filter catch-up workflow", () => {
  beforeEach(() => vi.clearAllMocks());

  it("prefetches ahead but never runs more than two bounded segments", async () => {
    runCatchupStep
      .mockResolvedValueOnce({ status: "continue", segmentId: "segment-1" })
      .mockResolvedValueOnce({ status: "continue", segmentId: "segment-2" });

    await expect(aiFilterCatchupWorkflow(input)).resolves.toEqual({
      status: "continue",
      segmentId: "segment-2",
    });
    expect(runCatchupStep).toHaveBeenCalledTimes(2);
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
