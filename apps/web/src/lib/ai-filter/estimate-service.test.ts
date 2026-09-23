import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

const mocks = vi.hoisted(() => ({
  countAiFilterCandidates: vi.fn(),
  getAiFilterEstimateContext: vi.fn(),
}));

vi.mock("./candidate-loader", () => ({
  countAiFilterCandidates: mocks.countAiFilterCandidates,
}));
vi.mock("./configuration-service", () => ({
  getAiFilterEstimateContext: mocks.getAiFilterEstimateContext,
}));

import { getAiFilterEstimate } from "./estimate-service";

const owner = {
  ownerId: "owner-1",
  watchlistId: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
};

describe("AI filter estimate", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.getAiFilterEstimateContext.mockResolvedValue({
      entitled: false,
      actualNanodollars: 0,
      reservedNanodollars: 0,
    });
  });

  it("does not count candidates for an ineligible account", async () => {
    await expect(getAiFilterEstimate(owner)).resolves.toMatchObject({
      candidateCount: null,
      estimatedNanodollars: null,
      entitled: false,
    });
    expect(mocks.countAiFilterCandidates).not.toHaveBeenCalled();
  });

  it("counts candidates after confirming entitlement", async () => {
    mocks.getAiFilterEstimateContext.mockResolvedValue({
      entitled: true,
      actualNanodollars: 0,
      reservedNanodollars: 0,
    });
    mocks.countAiFilterCandidates.mockResolvedValue(100);

    await expect(getAiFilterEstimate(owner)).resolves.toMatchObject({
      candidateCount: 100,
      entitled: true,
    });
    expect(mocks.countAiFilterCandidates).toHaveBeenCalledWith(owner);
  });
});
