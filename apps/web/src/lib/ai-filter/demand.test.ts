import { describe, expect, it } from "vitest";

import {
  AI_FILTER_PREFETCH_CANDIDATES,
  aiFilterDemandIsCovered,
  aiFilterDemandTarget,
} from "./demand";

describe("AI filter scroll demand", () => {
  it("keeps a selective-feed runway without crossing the candidate ceiling", () => {
    expect(AI_FILTER_PREFETCH_CANDIDATES).toBe(500);
    expect(aiFilterDemandTarget(0)).toBe(500);
    expect(aiFilterDemandTarget(9_980)).toBe(10_000);
  });

  it("does not rerun a range that is already covered", () => {
    expect(aiFilterDemandIsCovered({
      targetOffset: 80,
      selectionOffset: 50,
      scannedCount: 50,
      caughtUp: false,
    })).toBe(true);
    expect(aiFilterDemandIsCovered({
      targetOffset: 120,
      selectionOffset: 50,
      scannedCount: 50,
      caughtUp: false,
    })).toBe(false);
  });

  it("treats an exhausted feed as covered at every later offset", () => {
    expect(aiFilterDemandIsCovered({
      targetOffset: 10_000,
      selectionOffset: 0,
      scannedCount: 17,
      caughtUp: true,
    })).toBe(true);
  });
});
