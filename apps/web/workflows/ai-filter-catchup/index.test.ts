import { beforeEach, describe, expect, it, vi } from "vitest";

const runCatchupStep = vi.hoisted(() => vi.fn());
vi.mock("./steps", () => ({ runCatchupStep }));

import { aiFilterCatchupWorkflow } from ".";

const input = {
  ownerId: "owner-1",
  watchlistId: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  leaseOwner: "worker-1",
};

describe("AI filter catch-up workflow", () => {
  beforeEach(() => vi.clearAllMocks());

  it("auto-chains completed pages until the service reports caught up", async () => {
    runCatchupStep
      .mockResolvedValueOnce({ status: "continue", segmentId: "segment-1" })
      .mockResolvedValueOnce({ status: "continue", segmentId: "segment-2" })
      .mockResolvedValueOnce({ status: "caught_up", segmentId: "segment-3" });

    await expect(aiFilterCatchupWorkflow(input)).resolves.toEqual({
      status: "caught_up",
      segmentId: "segment-3",
    });
    expect(runCatchupStep).toHaveBeenCalledTimes(3);
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
