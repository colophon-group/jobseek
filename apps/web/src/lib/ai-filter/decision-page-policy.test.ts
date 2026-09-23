import { describe, expect, it } from "vitest";

import {
  projectSharedAcceptedPage,
  sharedAcceptedPageBounds,
} from "./decision-page-policy";

describe("shared AI filter result pagination", () => {
  it("caps anonymous reads at the twentieth result", () => {
    expect(sharedAcceptedPageBounds({
      anonymous: true,
      offset: 0,
      limit: 100,
    })).toMatchObject({ exhausted: false, limit: 20, maxOffset: 20 });
    expect(sharedAcceptedPageBounds({
      anonymous: true,
      offset: 19,
      limit: 100,
    })).toMatchObject({ exhausted: false, limit: 1, maxOffset: 20 });
    expect(sharedAcceptedPageBounds({
      anonymous: true,
      offset: 20,
      limit: 1,
    })).toEqual({ exhausted: true, nextOffset: 20 });
  });

  it("does not cap signed-in shared viewers", () => {
    expect(sharedAcceptedPageBounds({
      anonymous: false,
      offset: 20,
      limit: 100,
    })).toMatchObject({ exhausted: false, limit: 100, maxOffset: null });
  });

  it("omits internal decision fields from shared results", () => {
    const posting = { id: "job-1" };
    const decisions = [{
      posting,
      decisionId: "private-id",
      modelDecision: "rejected",
      userOverride: "accepted",
      decidedAt: "2026-09-23T00:00:00.000Z",
    }];
    expect(projectSharedAcceptedPage({
      decisions,
      total: 25,
      nextOffset: 20,
      hasMore: true,
    }, 20)).toEqual({
      decisions: [{ posting }],
      total: 25,
      nextOffset: 20,
      hasMore: false,
    });
  });
});
